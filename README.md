# Aivar

Aivar is an API-first Playwright test automation base with selector self-healing, prompt-driven scenario generation, generated Python tests, and screenshot comparison.

The current goal is a functionally ready foundation for experimentation and integration. Production hardening, authentication, distributed execution, and deployment automation are intentionally deferred.

## What Works

- Generate a test scenario draft from a natural-language prompt through an API.
- Review or edit the draft through an API, then approve it.
- Store ordered scenarios and `stepDetails` in versioned JSON.
- Generate a runnable Python Playwright test from an approved scenario.
- Execute steps individually and attempt healing only when selector resolution fails.
- Persist verified locator memory and run history.
- Capture screenshot baselines and compare later screenshots pixel-by-pixel.
- Store persisted data in JSON by default or SQLite through environment configuration.
- Stream run and healing events through Server-Sent Events (SSE).

Visual differences are reported separately from functional failures and do not trigger selector healing.

## Project Structure

```text
app/
  fixtures/             v1 baseline, v2 selector changes, v3 product bug
  runtime/              ACTIVE_VERSION
  server.py             local fixture server
backend/
  server.py             FastAPI API and SSE server
  runner_process.py     isolated Playwright subprocess
framework/
  constants.py          shared paths, ports, selectors, events, and settings
  scenarios.py          scenario artifacts and draft generation
  runner.py             JSON step execution and step-level healing
  healer.py             DOM candidate extraction and selector verification
  visual.py             screenshot baselines and pixel comparison
  storage.py            JSON/SQLite persistence adapter
data/
  scenarios.json        scenario drafts and approved scenarios
  tests.json            locator memory
  runs.json             run history and events
artifacts/
  generated/            generated Python Playwright tests
  reports/              screenshot baselines, actuals, and diffs
scripts/
  scenario_direct.py   direct compatibility/demo runner
```

## Requirements

- Python 3.11 or newer
- Playwright and Chromium
- FastAPI and Uvicorn for the API server
- Pillow for screenshot comparison
- Optional: Groq or OpenAI API credentials for live AI calls

## Setup

From the repository root:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install playwright fastapi uvicorn requests pillow
python -m playwright install chromium
Copy-Item .env.example .env
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install playwright fastapi uvicorn requests pillow
python -m playwright install chromium
cp .env.example .env
```

Do not commit `.env`. It is ignored by Git.

## Environment Variables

The values below are available in [.env.example](.env.example). Copy that file to `.env` and update only the settings you need.

```dotenv
# AI provider credentials. Leave placeholders when using the local fallback.
GROQ_API_KEY=your_groq_api_key_here
OPENAI_API_KEY=your_openai_api_key_here

# Provider/model settings.
GROQ_MODEL=openai/gpt-oss-120b

# Runtime policy:
# PROD = Groq for AI tasks and configured database storage.
# DEV  = Groq for AI tasks and JSON storage.
# MOCK = no AI calls and JSON storage.
ENVIRONMENT=MOCK

# PROD database configuration. Required when ENVIRONMENT=PROD.
# AIVAR_DATABASE_URL=sqlite:///C:/Users/you/Aivar/data/aivar.db
```

### Storage Options

`DEV` and `MOCK` use JSON and write to `data/scenarios.json`, `data/tests.json`, and `data/runs.json`.

To use SQLite instead, set:

```dotenv
ENVIRONMENT=PROD
AIVAR_DATABASE_URL=sqlite:///C:/Users/you/Aivar/data/aivar.db
```

The API and runner do not change. The same storage interface can receive PostgreSQL or another database adapter later.

## Run The Local Demo

Use `MOCK` to exercise the complete local workflow without an API key:

```bash
ENVIRONMENT=MOCK python scripts/scenario_direct.py
```

On Windows PowerShell:

```powershell
$env:ENVIRONMENT = "MOCK"
python scripts\scenario_direct.py
```

The demo runs the v1 baseline, v2 DOM-change fixture, and v3 product-bug fixture. In `DEV` or `PROD`, scenario generation and selector healing call Groq.

## Run The API Server

```bash
python backend/server.py
```

The API listens on `http://localhost:8090` by default. The fixture pages are served internally on `http://localhost:8765`.

## API Workflow

### 1. Generate a scenario draft

```bash
curl -X POST http://localhost:8090/scenarios/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Log in, inspect delayed orders, and open order details","fixtureVersion":"v1"}'
```

The response contains a `scenarioId`, status `DRAFT`, and ordered `stepDetails`. Each step includes a `stepId`, action, selector details, fingerprint, data, postcondition, and optional screenshot checkpoint.

### 2. Retrieve or edit the draft

```bash
curl http://localhost:8090/scenarios/<scenario_id>

curl -X PATCH http://localhost:8090/scenarios/<scenario_id> \
  -H "Content-Type: application/json" \
  -d '{"description":"Reviewed by the test owner"}'
```

### 3. Approve the scenario

```bash
curl -X POST http://localhost:8090/scenarios/<scenario_id>/approve
```

Only approved scenarios can be generated or executed.

### 4. Generate a Python Playwright test

```bash
curl -X POST http://localhost:8090/scenarios/<scenario_id>/generate-test
```

The generated file is written under `artifacts/generated/` and loads the approved scenario JSON at runtime.

### 5. Start a run

```bash
curl -X POST http://localhost:8090/runs \
  -H "Content-Type: application/json" \
  -d '{"scenarioId":"<scenario_id>"}'
```

The response contains a `run_id`. The fixture version comes from the approved scenario. A request can explicitly provide `{"version":"v2"}` for the compatibility runner path.

### 6. Stream run events

```bash
curl -N http://localhost:8090/runs/<run_id>/stream
```

Events include `STEP_STARTED`, `STEP_PASSED`, `STEP_FAILED`, `HEALING_STARTED`, `HEAL_ACCEPTED`, `HEAL_REJECTED`, `VISUAL_CHECKED`, `RUN_COMPLETE`, and `STREAM_END`.

## Fixture Versions

- `v1`: stable selectors, successful login, deterministic dashboard, and baseline visuals.
- `v2`: equivalent behavior with renamed selectors and changed presentation. This exercises selector healing, repeated row actions, modal flow, filters, delayed status, and large-page candidate extraction.
- `v3`: v1 selectors and visuals with an intentional invalid authentication result. This should fail as a product assertion and must not invoke healing.

All fixture data is local and deterministic. The pages include a dashboard, orders table, search, status/date filters, empty state, load-more response, settings, modal editing, repeated row controls, and delayed synchronization status.

## Screenshot Baselines

Scenario steps can declare a screenshot checkpoint. The first successful checkpoint creates a baseline. Later runs write actual screenshots and, when pixels differ beyond the configured threshold, a diff image.

```text
artifacts/reports/
  baselines/<scenario>/
  actual/<run_id>/
  diffs/<run_id>/
```

The current default pixel-difference threshold is defined in `framework/constants.py`. Screenshot differences are warnings and are stored in the run record; they do not cause selector healing.

## Validation Commands

```bash
python -m py_compile app/server.py backend/server.py backend/runner_process.py framework/*.py scripts/scenario_direct.py artifacts/generated/*.py
python -m json.tool data/scenarios.json > /dev/null
python -m json.tool data/tests.json > /dev/null
python -m json.tool data/runs.json > /dev/null
python artifacts/generated/<generated_test>.py
```

## Current Scope

This repository is functionally ready as a base for API-driven experimentation and extension. The following are intentionally future work:

- production authentication and authorization
- PostgreSQL or other server database adapters
- distributed/concurrent run isolation
- schema migration tooling
- advanced iframe and shadow-root selector support
- broader automated test coverage and CI setup
- production logging, monitoring, deployment, and secret management
