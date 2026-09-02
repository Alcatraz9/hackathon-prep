"""Self-healing engine - implements spec section 5 algorithm:

  1. Catch failure (done by caller, which classifies it first).
  2. [caller already confirmed this is a DOM_LOCATOR_ERROR]
  3. DOM extraction: page.evaluate() -> pruned interactive-node JSON tree.
    4. AI inference: stateless Groq call in DEV/PROD or deterministic mock in MOCK.
  5. Verification:
       - if confidence < 0.85 -> HEAL_REJECTED, terminate step.
       - else attempt the candidate selector, validate a post-action
         assertion (element resolves + is actionable).
       - success -> HEAL_ACCEPTED, persist to locator memory.
       - failure -> HEAL_REJECTED, terminate step.

`emit` is an optional callback(event_type: str, data: dict) used to stream
SSE events (HEALING_STARTED, HEAL_ACCEPTED, HEAL_REJECTED) to the frontend.
"""
from framework import ai_client
from framework.constants import CONFIDENCE_THRESHOLD

def extract_dom_context(page) -> list:
    """Pruned interactive-node tree: tag, selector, type, role, label,
    placeholder, visible text. Kept small deliberately (feeds the <500 token
    healing payload budget)."""
    # NOTE: spec section 3 scopes dom_context to "interactive DOM context
    # (buttons, inputs, links, ARIA roles)". In practice, assert_text steps
    # target plain text nodes (e.g. a status <p>), which aren't interactive -
    # so a strictly-interactive-only context can never heal them (confirmed
    # by running this POC: the status_message assertion had no valid
    # candidate to match against). Extending the context to include elements
    # with an id/role and non-empty text is a deliberate deviation worth
    # flagging back to the spec - see summary notes.
    return page.eval_on_selector_all(
        "input, button, a, textarea, select, [role], p[id], span[id], div[id]",
        """(els) => els.map(el => {
            try {
                const label = el.labels && el.labels[0] ? el.labels[0].innerText : null;
                let selector = null;
                const unique = candidate => document.querySelectorAll(candidate).length === 1;
                const tag = el.tagName.toLowerCase();
                if (el.id && unique('#' + CSS.escape(el.id))) selector = '#' + CSS.escape(el.id);
                else if (el.getAttribute && el.getAttribute('data-testid') && unique('[data-testid="' + el.getAttribute('data-testid') + '"]')) selector = '[data-testid="' + el.getAttribute('data-testid') + '"]';
                else if (el.name && unique(tag + '[name="' + el.name + '"]')) selector = tag + '[name="' + el.name + '"]';
                else if (el.getAttribute && el.getAttribute('aria-label') && unique('[aria-label="' + el.getAttribute('aria-label') + '"]')) selector = '[aria-label="' + el.getAttribute('aria-label') + '"]';
                else if (el.classList.length) {
                    const classes = Array.from(el.classList).map(value => '.' + CSS.escape(value)).join('');
                    if (unique(tag + classes)) selector = tag + classes;
                }
                if (!selector) {
                    const parts = [];
                    let current = el;
                    while (current && current.nodeType === 1 && parts.length < 6) {
                        let part = current.tagName.toLowerCase();
                        if (current.parentElement) {
                            const siblings = Array.from(current.parentElement.children).filter(child => child.tagName === current.tagName);
                            if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
                        }
                        parts.unshift(part);
                        const candidate = parts.join(' > ');
                        if (unique(candidate)) { selector = candidate; break; }
                        current = current.parentElement;
                    }
                }
                if (!selector) return null;

                // collect data-* attributes
                const dataAttrs = {};
                for (let i = 0; i < el.attributes.length; i++){
                    const a = el.attributes[i];
                    if (a.name.startsWith('data-')){
                        dataAttrs[a.name] = a.value;
                    }
                }

                const parent = el.parentElement;
                const parent_text = parent && parent.innerText ? parent.innerText.trim().slice(0, 80) : null;

                return {
                    selector: selector,
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type'),
                    role: el.getAttribute('role') || null,
                    aria_label: el.getAttribute('aria-label') || null,
                    aria_labelledby: el.getAttribute('aria-labelledby') || null,
                    placeholder: el.getAttribute('placeholder') || null,
                    data: dataAttrs,
                    class: el.className || null,
                    label_text: label,
                    visible_text: el.innerText ? el.innerText.trim().slice(0, 80) : null,
                    parent_text: parent_text,
                    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                    enabled: !el.disabled,
                };
            } catch (e) { return null; }
        }).filter(Boolean)""",
    )


def heal(page, action: str, original_intent: str, failed_selector: str, metrics, verify_fn, emit=None, fingerprint=None) -> dict:
    """Runs the full healing algorithm for one broken step.

    `verify_fn(selector) -> bool` actually performs the step's real action
    (fill/click/assert) against the candidate selector and reports whether
    it succeeded. Per spec step 5, HEAL_ACCEPTED must only fire once this
    real post-action assertion passes - not merely once the element is
    visible/enabled, which can still be the *wrong* element.

    Returns {"accepted": bool, "selector": str|None, "confidence": float,
             "reasoning": str, "source": str}.
    """
    if emit:
        emit("HEALING_STARTED", {"failed_selector": failed_selector, "action": action})

    dom_context = extract_dom_context(page)
    if emit:
        try:
            # emit a compact dom_context for debugging (truncate long fields)
            compact = []
            for d in dom_context[:40]:
                compact.append({
                    "selector": d.get("selector"),
                    "tag": d.get("tag"),
                    "role": d.get("role"),
                    "visible_text": (d.get("visible_text") or None),
                    "label_text": (d.get("label_text") or None),
                })
            emit("HEALING_CONTEXT", {"dom_context": compact, "count": len(dom_context)})
        except Exception:
            pass
    if fingerprint:
        for candidate in dom_context:
            candidate["target_fingerprint"] = fingerprint
    result = ai_client.request_healing(action, original_intent, failed_selector, dom_context, metrics)

    candidate = result.get("candidate_selector")
    confidence = result.get("confidence", 0.0)
    reasoning = result.get("reasoning", "")
    source = result.get("source", "unknown")

    if not candidate or confidence < CONFIDENCE_THRESHOLD:
        if emit:
            emit("HEAL_REJECTED", {
                "reason": "below_confidence_threshold" if candidate else "no_candidate",
                "confidence": confidence, "threshold": CONFIDENCE_THRESHOLD, "source": source,
            })
        return {"accepted": False, "selector": None, "confidence": confidence, "reasoning": reasoning, "source": source}

    try:
        matches = page.locator(candidate).count()
    except Exception:
        matches = 0
    if matches != 1:
        if emit:
            emit("HEAL_REJECTED", {"reason": "candidate_not_unique", "candidate_selector": candidate, "match_count": matches, "source": source})
        return {"accepted": False, "selector": None, "confidence": confidence, "reasoning": reasoning, "source": source}

    el = page.query_selector(candidate)
    tag_name = (el.get_attribute("tagName") or "").lower() if el is not None else ""
    if el is not None:
        tag_name = el.evaluate("element => element.tagName.toLowerCase()")
    allowed_tags = {
        "fill": {"input", "textarea", "select"},
        "click": {"button", "a", "select", "option"},
        "assert_text": {"p", "span", "div", "section", "article", "h1", "h2", "h3", "td", "label"},
    }
    if tag_name not in allowed_tags.get(action, set()):
        if emit:
            emit("HEAL_REJECTED", {"reason": "candidate_wrong_element_type", "candidate_selector": candidate, "tag": tag_name, "source": source})
        return {"accepted": False, "selector": None, "confidence": confidence, "reasoning": reasoning, "source": source}
    if el is None or (action != "assert_text" and not el.is_visible()) or (action in ("fill", "click") and not el.is_enabled()):
        if emit:
            emit("HEAL_REJECTED", {"reason": "candidate_not_actionable", "candidate_selector": candidate, "source": source})
        return {"accepted": False, "selector": None, "confidence": confidence, "reasoning": reasoning, "source": source}

    try:
        action_ok = verify_fn(candidate)
    except Exception:
        action_ok = False

    if not action_ok:
        if emit:
            emit("HEAL_REJECTED", {"reason": "post_action_assertion_failed", "candidate_selector": candidate, "source": source})
        return {"accepted": False, "selector": None, "confidence": confidence, "reasoning": reasoning, "source": source}

    if emit:
        emit("HEAL_ACCEPTED", {
            "old_selector": failed_selector, "new_selector": candidate,
            "confidence": confidence, "reasoning": reasoning, "source": source,
        })
    return {"accepted": True, "selector": candidate, "confidence": confidence, "reasoning": reasoning, "source": source}
