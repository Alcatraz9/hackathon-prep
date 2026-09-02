"""Stateless AI healing client.

Per spec section 3:
  - Primary: Groq, llama-3.3-70b-versatile, response_format=json_object
  - Fallback: OpenAI, gpt-4o-mini, structured output, used if Groq errors/rate-limits
  - Each call is a single, history-free inference (no prior turns sent)
  - Payload capped under ~500 tokens: failed action/intent, failed selector,
    pre-extracted interactive DOM context only

NOTE (sandbox constraint): this environment's pip index blocks installing the
`groq` and `openai` SDKs (and `fastapi`/`uvicorn`). Only a small allowlist
(playwright, requests, ...) is installable in some sandboxes. So these calls
may be made as raw HTTPS requests via `requests` against the same REST
endpoints the SDKs wrap (api.groq.com / api.openai.com) — functionally
identical payloads/auth, just without the SDK convenience wrapper. Swap in
the SDKs freely outside constrained environments.
raw HTTPS requests via `requests` against the same REST endpoints the SDKs
wrap (api.groq.com / api.openai.com) — functionally identical payloads/auth,
just without the SDK convenience wrapper. Swap in the SDKs freely outside
this sandbox.
"""
import json
import os
import time
import requests
from framework.constants import FIXTURES_DIR, GROQ_MODEL, GROQ_URL, OPENAI_MODEL, OPENAI_URL, TOKEN_BUDGET, DOM_CONTEXT_LIMIT
from framework.environment import ai_enabled, current_environment

# Prefer SDKs when available. We'll try to use the `openai` SDK for OpenAI
# and also to target Groq by switching its `api_base` to Groq's endpoint if
# present. If the SDKs are not installed, fall back to the existing
# requests-based implementation so this repository still runs in constrained
# sandboxes (this repo previously relied on raw HTTPS calls for portability).
try:
    import openai  # type: ignore
    _OPENAI_SDK = True
except Exception:
    openai = None
    _OPENAI_SDK = False

RESPONSE_SCHEMA_HINT = {
    "candidate_selector": "string",
    "confidence": 0.0,
    "reasoning": "string",
}


def _estimate_tokens(s: str) -> int:
    # rough approximation (~4 chars/token); good enough for a budget guardrail
    return max(1, len(s) // 4)


def build_healing_payload(action: str, original_intent: str, failed_selector: str, dom_context: list) -> dict:
    """Builds the minimal, stateless healing payload. Truncates dom_context
    if needed to stay under the ~500 token budget."""
    intent_words = set(_re_split(original_intent.lower()))

    def priority(candidate):
        target = candidate.get("target_fingerprint") or {}
        label = str(target.get("label") or "").lower()
        visible = str(candidate.get("visible_text") or "").lower()
        candidate_words = set(_re_split(" ".join([
            visible,
            str(candidate.get("label_text") or ""),
            str(candidate.get("selector") or ""),
        ])))
        score = len(intent_words & candidate_words)
        if candidate.get("visible"):
            score += 5
        if candidate.get("enabled", True):
            score += 1
        if label and label in visible:
            score += 20
        if action == "click" and candidate.get("tag") in {"button", "a", "select", "option"}:
            score += 2
        if action == "fill" and candidate.get("tag") in {"input", "textarea", "select"}:
            score += 2
        if action == "assert_text" and candidate.get("tag") not in {"input", "textarea", "select", "option"}:
            score += 2
        return score

    ranked_context = sorted(list(dom_context), key=priority, reverse=True)
    payload = {
        "failed_action": action,
        "original_intent": original_intent,
        "failed_selector": failed_selector,
        "dom_context": ranked_context,
    }

    def size(p):
        return _estimate_tokens(json.dumps(p))

    # trim least-informative context entries first if we're over budget
    while size(payload) > TOKEN_BUDGET and payload["dom_context"]:
        payload["dom_context"].pop()

    return payload


def _prompt_for(payload: dict) -> str:
    # Include rich candidate metadata so the LLM can reason semantically.
    dom = payload.get("dom_context", [])
    compact = []
    for d in dom[:DOM_CONTEXT_LIMIT]:
        compact.append({
            "selector": d.get("selector"),
            "tag": d.get("tag"),
            "role": d.get("role"),
            "aria_label": d.get("aria_label"),
            "label_text": d.get("label_text"),
            "visible_text": d.get("visible_text"),
            "parent_text": d.get("parent_text"),
        })
    # Provide explicit field descriptions so the model understands the
    # meaning of each attribute in `dom_context` and the expected JSON
    # response shape. This reduces hallucination and enforces the candidate
    # to be one of the provided selectors.
    field_help = (
        "DOM candidate fields:\n"
        "  - selector: a CSS selector string uniquely identifying the element (e.g. '#foo').\n"
        "  - tag: the element tag name (e.g. 'input', 'button', 'p').\n"
        "  - role: ARIA role if present (e.g. 'button').\n"
        "  - aria_label: aria-label text if present.\n"
        "  - label_text: visible label text associated with the element.\n"
        "  - visible_text: innerText truncated for context.\n"
        "  - parent_text: text from the parent element to give surrounding context.\n\n"
    )

    instruction = (
        "A browser test step failed to resolve its selector. You are given: the failed "
        "action (fill/click/assert), the original human intent describing what the step is trying to do, "
        "the failed selector string, and a compact list of candidate elements in `dom_context` with metadata.\n\n"
    )

    return (
        instruction
        + field_help
        + f"failed_action: {payload['failed_action']}\n"
        + f"original_intent: {payload['original_intent']}\n"
        + f"failed_selector: {payload['failed_selector']}\n\n"
        + f"candidates: {json.dumps(compact)}\n\n"
        + "Task: From the `candidates` list choose the single best replacement selector that achieves the original intent. "
        + "Respond with ONLY a JSON object exactly matching this shape (no extra keys, no prose):\n"
        + f"{json.dumps(RESPONSE_SCHEMA_HINT)}\n"
        + "The `candidate_selector` value MUST be exactly one of the 'selector' strings from `candidates` or null. "
        + "Provide `confidence` as a decimal between 0 and 1 and `reasoning` as a short explanation."
    )


def _call_groq(payload: dict, metrics):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    # Prefer using the OpenAI SDK (works for Groq by switching api_base),
    # otherwise fall back to a raw HTTP POST as before.
    prompt = _prompt_for(payload)
    # Log the exact payload and resolved model candidates for debugging
    try:
        model_env = os.environ.get("GROQ_MODEL")
        # candidates_env = os.environ.get("GROQ_MODEL_CANDIDATES")
        # resolved = [m.strip() for m in candidates_env.split(",")] if candidates_env else [model_env or GROQ_MODEL, "gpt-4o-mini", GROQ_MODEL]
        resolved = model_env
        print(f"  [ai_client] resolved_groq_model_list={resolved}")
        # print compact payload (dom_context may be large)
        # compact_payload = {k: payload[k] for k in ("failed_action", "original_intent", "failed_selector") if k in payload}
        # compact_payload["dom_context_count"] = len(payload.get("dom_context") or [])
        # print(f"  [ai_client] groq_payload_compact={compact_payload}")
        # Also print the full payload JSON for debugging (may be large)
        # try:
        #     print("  [ai_client] groq_full_payload=\n" + json.dumps(payload, indent=2, ensure_ascii=False))
        # except Exception:
        #     print("  [ai_client] groq_full_payload=<unserializable payload>")
        # print(f"  [ai_client] groq_prompt_preview={prompt[:1000].replace('\n', ' ')}")
    except Exception:
        pass
    # Try only the single model specified in the environment. No fallbacks.
    model_env_val = os.environ.get("GROQ_MODEL")
    model_list = [model_env_val or GROQ_MODEL]

    best_result = None
    best_conf = -1.0

    def _call_for_model(model):
        # internal helper to call Groq for a single model and return parsed dict
        data = None
        if _OPENAI_SDK:
            try:
                if hasattr(openai, "OpenAI"):
                    print(f"  [ai_client] calling Groq via OpenAI SDK (base_url) model={model}")
                    client = openai.OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
                    if getattr(client, "chat", None) and getattr(client.chat, "completions", None):
                        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
                        data = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)
                    else:
                        resp = client.responses.create(model=model, input=prompt)
                        data = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)
                else:
                    print(f"  [ai_client] calling Groq via legacy OpenAI SDK path model={model}")
                    old_base = getattr(openai, "api_base", None)
                    old_key = getattr(openai, "api_key", None)
                    try:
                        openai.api_base = "https://api.groq.com/openai/v1"
                        openai.api_key = api_key
                        resp = openai.ChatCompletion.create(model=model, messages=[{"role": "user", "content": prompt}])
                        data = resp if isinstance(resp, dict) else resp.to_dict()
                    finally:
                        if old_base is not None:
                            openai.api_base = old_base
                        elif hasattr(openai, "api_base"):
                            try:
                                delattr(openai, "api_base")
                            except Exception:
                                pass
                        openai.api_key = old_key
            except Exception as e:
                print(f"  [ai_client] Groq SDK call failed for model={model}: {e}")
                data = None

        if data is None:
            # fallback to HTTP
            try:
                print(f"  [ai_client] calling Groq via HTTP fallback model={model}")
                body = {"model": model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]}
                r = requests.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"  [ai_client] Groq call failed for model={model}: {e}")
                return None

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        metrics.add_llm_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), source="groq")

        # extract text
        def _extract_text_from_resp(d):
            try:
                if isinstance(d, dict) and "choices" in d and d["choices"]:
                    ch = d["choices"][0]
                    if isinstance(ch, dict) and "message" in ch and isinstance(ch["message"], dict) and "content" in ch["message"]:
                        return ch["message"]["content"]
                    if isinstance(ch, dict) and "text" in ch:
                        return ch["text"]
                if isinstance(d, dict) and "output" in d:
                    out = d["output"]
                    if isinstance(out, list) and out:
                        first = out[0]
                        if isinstance(first, dict) and "content" in first:
                            c = first["content"]
                            if isinstance(c, list) and c:
                                for item in c:
                                    if isinstance(item, dict) and "text" in item:
                                        return item["text"]
                                return json.dumps(c)
                return json.dumps(d)
            except Exception:
                return json.dumps(d)

        content = _extract_text_from_resp(data)
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {"candidate_selector": None, "confidence": 0.0, "reasoning": content}
        if not isinstance(parsed, dict):
            parsed = {"candidate_selector": None, "confidence": float(parsed) if isinstance(parsed, (int, float)) else 0.0, "reasoning": str(parsed)}
        parsed["source"] = f"groq:{model}"
        return parsed

    # iterate (single) model, return its parsed result
    for model in model_list:
        if not model:
            continue
        res = _call_for_model(model)
        if res is None:
            continue
        conf = res.get("confidence", 0.0) or 0.0
        if conf > best_conf:
            best_conf = conf
            best_result = res
    return best_result

    # fallback: raw HTTP request (existing behavior)
    body = {"model": model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]}
    try:
        print("  [ai_client] calling Groq via HTTP fallback")
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [ai_client] Groq call failed, will try fallback: {e}")
        return None

    usage = data.get("usage", {})
    metrics.add_llm_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), source="groq")
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except Exception:
        parsed = {"candidate_selector": None, "confidence": 0.0, "reasoning": content}
    if not isinstance(parsed, dict):
        parsed = {"candidate_selector": None, "confidence": float(parsed) if isinstance(parsed, (int, float)) else 0.0, "reasoning": str(parsed)}
    parsed["source"] = f"groq:{model}"
    return parsed


def _call_openai(payload: dict, metrics):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    prompt = _prompt_for(payload)
    model = os.environ.get("OPENAI_MODEL", OPENAI_MODEL)
    if _OPENAI_SDK:
        try:
            if hasattr(openai, "OpenAI"):
                client = openai.OpenAI(api_key=api_key)
                if getattr(client, "chat", None) and getattr(client.chat, "completions", None):
                    resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
                    data = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)
                else:
                    resp = client.responses.create(model=model, input=prompt)
                    data = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)
            else:
                openai.api_key = api_key
                resp = openai.ChatCompletion.create(model=model, messages=[{"role": "user", "content": prompt}])
                data = resp if isinstance(resp, dict) else resp.to_dict()
        except Exception as e:
            print(f"  [ai_client] OpenAI SDK call failed: {e}")
            data = None

        if data is None:
            return None

        # extract content using shared helper above
        def _extract_text_from_resp_openai(d):
            try:
                # chat.completions style
                if "choices" in d and d["choices"]:
                    ch = d["choices"][0]
                    if isinstance(ch, dict):
                        if "message" in ch and isinstance(ch["message"], dict) and "content" in ch["message"]:
                            return ch["message"]["content"]
                        if "text" in ch:
                            return ch["text"]
                # responses API
                if "output" in d:
                    try:
                        out = d["output"]
                        if isinstance(out, list) and out:
                            first = out[0]
                            if isinstance(first, dict) and "content" in first:
                                c = first["content"]
                                if isinstance(c, list) and c:
                                    for item in c:
                                        if isinstance(item, dict) and "text" in item:
                                            return item["text"]
                                    return json.dumps(c)
                    except Exception:
                        pass
                return json.dumps(d)
            except Exception:
                return json.dumps(d)

        content = _extract_text_from_resp_openai(data)
        # try to parse JSON content returned by model
        try:
            parsed = json.loads(content)
        except Exception:
            # If model returned non-JSON, wrap it
            parsed = {"candidate_selector": None, "confidence": 0.0, "reasoning": content}
        parsed["source"] = f"openai:{OPENAI_MODEL}"
        return parsed

    # fallback to raw HTTP
    body = {"model": model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [ai_client] OpenAI fallback call failed: {e}")
        return None

    usage = data.get("usage", {})
    metrics.add_llm_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), source="openai")
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    parsed["source"] = f"openai:{OPENAI_MODEL}"
    return parsed


_STOPWORDS = {"the", "a", "an", "of", "input", "field", "button", "text"}


def _re_split(s):
    import re as _re
    return _re.findall(r"[a-z0-9]+", s)


def _keywords(s):
    if not s:
        return set()
    return {w for w in _re_split(str(s).lower()) if w and w not in _STOPWORDS}


def _best_candidate_by_keywords(original_intent: str, dom_context: list, action=None):
    """Shared non-LLM matching logic used by both the offline heuristic
    fallback and the mock-LLM demo mode: keyword overlap between the
    intent and each candidate's label/placeholder/visible-text/type/id,
    since a real LLM would use roughly this signal (plus real semantic
    understanding, which this proxy lacks)."""
    intent_kw = _keywords(original_intent)
    best, best_score = None, -1.0
    for c in dom_context:
        if action == "click" and c.get("tag") not in {"button", "a", "select", "option"}:
            continue
        if action == "fill" and c.get("tag") not in {"input", "textarea", "select"}:
            continue
        if action == "assert_text" and c.get("tag") in {"input", "textarea", "select", "option"}:
            continue
        id_kw = _keywords((c.get("selector") or "").lstrip("#").replace("-", " ").replace("_", " "))
        cand_kw = (
            _keywords(c.get("label_text"))
            | _keywords(c.get("placeholder"))
            | _keywords(c.get("visible_text"))
            | _keywords(c.get("type"))
            | id_kw
        )
        target = c.get("target_fingerprint") or {}
        if target.get("tag") and c.get("tag") != target.get("tag"):
            continue
        target_label_kw = _keywords(target.get("label"))
        candidate_text_kw = _keywords(c.get("visible_text")) | _keywords(c.get("label_text"))
        overlap = len(intent_kw & cand_kw)
        score = min(overlap * 0.35, 0.8)
        if target_label_kw & candidate_text_kw:
            score += 0.7
        if target.get("tag") and target.get("tag") == c.get("tag"):
            score += 0.1
        if target.get("role") and target.get("role") == c.get("role"):
            score += 0.1
        if c.get("type") and c.get("type") in intent_kw:
            score += 0.2
        if score > best_score:
            best, best_score = c, score
    return best, max(best_score, 0.0)


def _heuristic_fallback(payload: dict, metrics):
    """Used only when neither GROQ_API_KEY nor OPENAI_API_KEY is set, so the
    POC can still be exercised end-to-end offline. Real deployments always
    hit Groq/OpenAI per the spec, which will score genuine matches far more
    reliably than this keyword-overlap proxy."""
    best, best_score = _best_candidate_by_keywords(payload["original_intent"], payload["dom_context"], payload.get("failed_action"))
    metrics.add_heuristic_call()
    if not best or best_score <= 0:
        return {"candidate_selector": None, "confidence": 0.0, "reasoning": "no keyword overlap with any candidate", "source": "heuristic"}
    return {
        "candidate_selector": best.get("selector"),
        "confidence": round(min(best_score, 0.97), 2),
        "reasoning": f"heuristic keyword-overlap match against label/placeholder/type/id (score={best_score:.2f})",
        "source": "heuristic",
    }


def _mock_llm(payload: dict, metrics):
    """MOCK-only helper. Simulates what a real
    Groq/OpenAI call would return for a well-matched candidate, so the
    HEAL_ACCEPTED path can be demonstrated without a live API key. Uses the
    same keyword-overlap proxy as the heuristic fallback, just reported with
    a fixed high confidence to simulate a confident LLM judgment. Token
    counts are estimated from payload/response size, not real usage -
    labeled with source='mock_llm' so this is never confused with a real
    inference result."""
    intent = payload["original_intent"]
    best = None
    marker = "; target label: "
    if marker in intent:
        target_label = intent.split(marker, 1)[1].split(";", 1)[0].strip().lower()
        matching = [
            candidate for candidate in payload["dom_context"]
            if target_label and target_label in (candidate.get("visible_text") or "").lower()
            and (payload.get("failed_action") != "click" or candidate.get("tag") in {"button", "a", "select", "option"})
        ]
        if len(matching) == 1:
            best = matching[0]
    if best is None:
        best, _ = _best_candidate_by_keywords(intent, payload["dom_context"], payload.get("failed_action"))
    if best is None:
        target = next((candidate.get("target_fingerprint") for candidate in payload["dom_context"] if candidate.get("target_fingerprint")), {})
        typed = [candidate for candidate in payload["dom_context"] if target.get("tag") and candidate.get("tag") == target.get("tag")]
        if len(typed) == 1:
            best = typed[0]
    reasoning = f"Simulated LLM judgment: best semantic match for '{intent}' among candidates."
    response_json = json.dumps({
        "candidate_selector": best.get("selector") if best else None,
        "confidence": 0.93,
        "reasoning": reasoning,
    })
    tokens_in = _estimate_tokens(_prompt_for(payload))
    tokens_out = _estimate_tokens(response_json)
    metrics.add_llm_usage(tokens_in, tokens_out, source="mock_llm")
    return {
        "candidate_selector": best.get("selector") if best else None,
        "confidence": 0.93,
        "reasoning": reasoning,
        "source": "mock_llm (SIMULATED - not a real API call)",
    }


def _extract_chat_json(data):
    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise ValueError("Groq returned no choices")
    content = choices[0].get("message", {}).get("content")
    if not content:
        raise ValueError("Groq returned empty content")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Groq returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("Groq scenario response must be a JSON object")
    return result


def _normalize_fingerprint(step):
    """Fill the required stable fields when a model omits fingerprint metadata."""
    selector_details = step.get("selectorDetails") or {}
    selector = selector_details.get("selector", "")
    action = step.get("action")
    fingerprint = step.get("fingerprint")
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    if action == "fill":
        tag, role = "input", "textbox"
    elif action == "click":
        tag, role = "button", "button"
    else:
        tag, role = "p", "status"
    fingerprint.setdefault("tag", tag)
    fingerprint.setdefault("role", role)
    fingerprint.setdefault("label", selector_details.get("intent", step.get("scenario", "scenario target")))
    fingerprint.setdefault("input_type", None)
    fingerprint.setdefault("ancestor", "document")
    fingerprint["volatile_attributes"] = ["id", "class", "name"]
    step["fingerprint"] = fingerprint


def generate_scenario_with_groq(prompt: str, fixture_version: str, metrics=None) -> dict:
    """Ask Groq to analyze the fixture and produce executable scenario JSON."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(f"GROQ_API_KEY is required when ENVIRONMENT={current_environment()}")
    fixture_path = os.path.join(FIXTURES_DIR, f"{fixture_version}.html")
    with open(fixture_path, encoding="utf-8") as handle:
        fixture = handle.read()[:24000]
    schema = {
        "scenarios": [{
            "scenario": "short scenario name",
            "stepDetails": [{
                "stepId": "stable-kebab-case-id",
                "scenario": "scenario name",
                "action": "fill|click|assert_text",
                "selectorDetails": {"selector": "CSS selector", "intent": "human intent"},
                "fingerprint": {"tag": "", "role": "", "label": "", "input_type": "", "ancestor": ""},
                "value": "optional fill value",
                "expected_contains": "optional assertion text",
                "postcondition": {"type": "value_present|text_contains"},
                "screenshot": {"checkpointId": "optional checkpoint", "fullPage": False}
            }]
        }]
    }
    instruction = (
        "Analyze the supplied webpage fixture and the user's test request. Generate the meaningful executable "
        "happy path and alternate/validation/error paths that are supported by the fixture. Use only fill, click, "
        "and assert_text actions. Every step must include selectorDetails and a stable DOM/semantic fingerprint. "
        "Return ONLY valid JSON matching this shape, with no markdown or extra keys:\n"
        + json.dumps(schema)
        + "\nUser request: " + prompt
        + "\nFixture version: " + fixture_version
        + "\nWebpage fixture:\n" + fixture
    )
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": os.environ.get("GROQ_MODEL", GROQ_MODEL), "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": instruction}]},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if metrics is not None:
        usage = data.get("usage", {})
        metrics.add_llm_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), source="groq_scenario")
    result = _extract_chat_json(data)
    scenarios = result.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Groq scenario response must contain a non-empty scenarios list")
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not scenario.get("stepDetails"):
            raise ValueError("each generated scenario must contain stepDetails")
        for step in scenario["stepDetails"]:
            if not isinstance(step, dict) or not step.get("stepId") or step.get("action") not in {"fill", "click", "assert_text"}:
                raise ValueError("generated steps must contain stepId and a supported action")
            if not isinstance(step.get("selectorDetails"), dict) or not step["selectorDetails"].get("selector"):
                raise ValueError("generated steps must contain selectorDetails.selector")
            _normalize_fingerprint(step)
    return result


def request_healing(action: str, original_intent: str, failed_selector: str, dom_context: list, metrics) -> dict:
    """Stateless healing call using Groq in DEV/PROD or the local mock in MOCK.
    Returns dict: candidate_selector, confidence, reasoning, source.
    """
    payload = build_healing_payload(action, original_intent, failed_selector, dom_context)

    t0 = time.time()
    if not ai_enabled():
        result = _mock_llm(payload, metrics)
    else:
        # DEV and PROD use Groq (the model set in GROQ_MODEL env).
        result = _call_groq(payload, metrics)
        if result is None:
            # Do NOT fall back to OpenAI or heuristics; return a clear no-response result
            result = {"candidate_selector": None, "confidence": 0.0, "reasoning": "no response from groq (no fallbacks configured)", "source": "groq"}
    result["_latency_s"] = round(time.time() - t0, 4)
    result["_payload_tokens_est"] = _estimate_tokens(json.dumps(payload))
    return result
