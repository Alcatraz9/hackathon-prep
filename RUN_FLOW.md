Runbook: End-to-end flow for Aivar self-healing tests

Purpose
- A concise step-by-step summary of the runtime flow, edits performed, and reproduction steps. Intended to be fed to an LLM for review or used by a human reader.

Workspace files of interest
- framework/__init__.py                 : auto-loads .env on import
- framework/env.py                      : .env loader
- app/server.py                         : serves fixtures via app/runtime/ACTIVE_VERSION
- app/fixtures/v1.html, v2.html, v3.html: deterministic test pages with different DOMs/behavior
- backend/server.py                     : FastAPI (SSE) entrypoint (POST /runs, GET /runs/{id}/stream)
- backend/runner_process.py             : subprocess runner using Playwright (writes JSON events to stdout)
- framework/runner.py                   : executes test steps and calls healer on DOM_LOCATOR_ERROR
- framework/healer.py                   : extracts DOM context, calls ai_client, verifies candidate and emits HEAL_* events
- framework/ai_client.py                : builds payload/prompt, calls Groq (SDK or HTTP), parses JSON response
- framework/state.py                    : RunState, load/save tests.json and runs.json
- framework/constants.py                : shared paths, ports, selectors, credentials, and event names
- data/scenarios.json                   : API-generated and approved scenario artifacts
- data/tests.json                       : persisted locator memory (baseline locators)
- data/runs.json                        : persisted run histories and events
- framework/storage.py                  : JSON/SQLite persistence adapter selected by environment
- .env                                  : ENVIRONMENT, Groq settings, and PROD database URL

High-level runtime flow (numbered)
1. Env load
   - Importing the `framework` package triggers `framework/__init__.py` which calls `load_env()`.
   - `load_env()` reads `.env` at repo root and populates `os.environ` (does NOT overwrite existing env vars).

2. Start supporting servers
   - `app.start_server()` starts a small HTTP server on port 8765 and serves the active version HTML.
   - The file `app/ACTIVE_VERSION` is used by `app.server` to switch between `v1.html` and `v2.html`.

3. Requesting a run (backend)
   - A client posts `POST /runs` to `backend/server.py` (or developer runs runner subprocess directly for debugging).
   - `backend.server` spawns `backend/runner_process.py` as a separate process and opens an SSE stream to forward runner stdout events.

4. Runner subprocess and Playwright
   - `backend/runner_process.py` starts `sync_playwright()` inside the subprocess, launches the browser, creates `page`, ensures `app` serves the requested version by calling `set_active_version(version)`, then calls `framework.runner.run_test(page, BASE_URL, metrics, emit=proc_emit)`.
   - Runner writes structured events (JSON lines) to stdout by calling `emit_stdout(event_type, data)`.

5. Test execution (framework/runner.py)
   - `RunState` loads `tests.json` at run start to get `locators` baseline.
   - The runner interprets approved scenario `stepDetails`; it retains `LOGIN_STEPS` as a compatibility fallback.
   - On a playwright `TimeoutError` / failure to attach, the error is classified; DOM_LOCATOR_ERROR triggers the healer.

6. Healing flow (framework/healer.py)
   - `extract_dom_context(page)` executes `page.eval_on_selector_all(...)` at runtime (PLAYWRIGHT) to collect candidate elements and metadata:
     - For each candidate: selector, tag, type, role, aria_label, aria_labelledby, placeholder, data-*, class, label_text, visible_text, parent_text.
     - The function intentionally limits returned candidates (slice/40) to constrain token budget.
   - `heal()` emits `HEALING_STARTED` and `HEALING_CONTEXT` SSE events (compact dom_context for debugging) and calls `ai_client.request_healing(action, original_intent, failed_selector, dom_context, metrics)`.
   - After `ai_client` returns a `candidate_selector` and `confidence`, healer verifies the candidate on the same Playwright `page` via `verify_fn(candidate_selector)` which performs the actual action (fill/click/assert) and checks outcomes. Only a passing verification results in `HEAL_ACCEPTED` and persistence; otherwise `HEAL_REJECTED`.

7. LLM client flow (framework/ai_client.py)
   - `build_healing_payload()` constructs a minimal payload:
     {
       "failed_action": action,
       "original_intent": original_intent,
       "failed_selector": failed_selector,
       "dom_context": dom_context
     }
     It performs a crude token-size estimate and trims `dom_context` if the payload exceeds ~500 tokens.
   - `_prompt_for(payload)` builds the human-friendly prompt. It now includes:
     - A short instruction describing the task.
     - Explicit `DOM candidate fields` descriptions so the model understands each attribute (selector, tag, role, aria_label, label_text, visible_text, parent_text).
     - The `failed_action`, `original_intent`, `failed_selector` values.
     - A `candidates` JSON array with compact metadata (from dom_context slice).
     - An explicit requirement to `Respond with ONLY a JSON object exactly matching this shape` with `candidate_selector`, `confidence`, `reasoning`.
   - The code prints debug lines before calling the model: resolved model list, compact payload summary, full payload JSON, and a prompt preview.
   - Current permanent behavior (as modified): the client calls only the model defined in `GROQ_MODEL` (no fallback). The `GROQ_ONLY` flag was formerly supported for forcing a single call; now the code is permanently single-source unless `SELFHEAL_DEMO_MODE=mock_llm`.
   - Response parsing: code attempts to extract text content from multiple SDK/HTTP shapes and `json.loads()` the first text field into a dict; if parsing fails it wraps content into `reasoning` and sets `confidence: 0.0`.

8. Persistence and metrics
   - `RunState.flush()` writes locator memory and run records through `framework.storage`.
   - `DEV` and `MOCK` use JSON files. `PROD` requires `AIVAR_DATABASE_URL` and uses the configured database adapter.
   - `framework/metrics` receives token usage and call counts from `ai_client`.

9. Observability & logging (what was added)
   - `ai_client` now prints: resolved model list, compact payload, full payload JSON (groq_full_payload), prompt preview, and SDK/HTTP call logs and errors.
   - `healer` emits `HEALING_CONTEXT` with compact dom_context for each heal attempt.
   - The runner subprocess prints JSON events that `backend.server` forwards via SSE.

Environment policy
- `ENVIRONMENT=PROD`: Groq is required for scenario generation and healing; `AIVAR_DATABASE_URL` is required for database persistence.
- `ENVIRONMENT=DEV`: Groq is required for scenario generation and healing; JSON files are used for persistence.
- `ENVIRONMENT=MOCK`: no AI calls are made; JSON files and the deterministic scenario template are used.
- `GROQ_MODEL`: Groq model used for scenario generation and selector healing.
- `CONFIDENCE_THRESHOLD` (in `framework/constants.py`): default 0.85.

Reproduction commands (local)
- Start the app server (serves v1 by default):

```bash
python - <<'PY'
from app.server import start_server
start_server()
PY
```

- Run a single subprocess runner (direct/executable debug):

```bash
# runs test steps against v1 or v2 depending on the second arg
python -u backend/runner_process.py E2E v1
python -u backend/runner_process.py E2E v2
```

- To run via backend SSE (if backend.server is active):

```bash
curl -s -X POST http://127.0.0.1:8090/runs -H "Content-Type: application/json" -d '{"version":"v2"}'
# then stream events
curl -N http://127.0.0.1:8090/runs/<run_id>/stream
```

- To force the mock LLM and demonstrate accepted heals:

```bash
SELFHEAL_DEMO_MODE=mock_llm python -u backend/runner_process.py E2E v2
```

What I changed (summary)
- `framework/healer.py` — enriched `extract_dom_context` to include aria/data/class/parent_text and emit richer `HEALING_CONTEXT`.
- `framework/ai_client.py` —
  - switched the prompt to include explicit DOM field descriptions and strict JSON response instructions;
  - added debug logs (resolved model list, compact payload, full payload, prompt preview);
  - removed OpenAI/fallback paths and enforced single Groq model calls (unless `SELFHEAL_DEMO_MODE` is set).
- `tests.json` — reset to `DEFAULT_LOCATORS` (v1) to ensure the first run against v1 passes during testing.

Typical troubleshooting checklist for the LLM path
1. Confirm `.env` was loaded and `GROQ_MODEL` points to the intended model.
2. Check runner stdout for `groq_full_payload` to verify dom_context and intent are correct.
3. Check `groq_prompt_preview` to verify the prompt includes field descriptions and that `candidates` contains the selectors you expect.
4. If response confidence is low:
   - verify the model exists/accessible for the account (404 or permission errors appear in logs),
   - try a different accessible model and re-run,
   - or use `SELFHEAL_DEMO_MODE=mock_llm` to validate the rest of the pipeline.
5. If model returns formatting errors or non-JSON: include an example JSON answer in the prompt to reduce hallucination.

Notes for LLM review
- The prompt now explicitly documents the meaning of each `dom_context` field and requires a strict JSON response irreducible to plain prose — this reduces parsing errors.
- The `dom_context` is built at runtime by Playwright and passed directly; there is no constant payload string.
- The system is designed to prefer single-shot stateless calls (no chat history) and to keep payloads bounded (token budget).

If you want I can:
- produce a smaller `prompt_example` snippet to add to the prompt (example JSON response),
- revert to a fallback strategy (Groq -> OpenAI -> heuristic), or
- create a separate `docs/llm_prompt.md` that contains the exact prompt template and a few example payloads/responses.

End of runbook.
