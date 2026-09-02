"""Real-time telemetry backend.

Spec calls for FastAPI + SSE at /runs/{id}/stream. This module implements the
same SSE contract (text/event-stream, one JSON event per line) but using
FastAPI's `StreamingResponse`. The in-memory queue model from the previous
Flask implementation is preserved so replacing the runtime with Uvicorn is
straightforward.

Endpoints:
  POST /runs            -> starts a run against a given app version, returns {"run_id": ...}
  GET  /runs/{id}/stream -> text/event-stream of STEP_STARTED / HEALING_STARTED /
                            HEAL_ACCEPTED / HEAL_REJECTED / STEP_FAILED / RUN_COMPLETE
"""
import json
import os
import queue
import sys
import threading
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import subprocess
import shlex

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from playwright.sync_api import sync_playwright
from app.server import start_server, set_active_version
from framework.runner import run_test
from framework.metrics import Metrics
from framework.constants import APP_PORT, BACKEND_HOST, BACKEND_PORT, BASE_URL, DEFAULT_FIXTURE_VERSION, EVENT_STREAM_CLOSE
from framework.scenarios import approve_scenario, create_draft, get_scenario, update_scenario
from framework.test_generator import generate_test


app = FastAPI()
PORT = APP_PORT

# in-memory per-run event queues (SSE subscribers read from these)
_queues = {}
_lock = threading.Lock()

_server_started = False

# Single worker thread + job queue to own Playwright/browser usage. The
# worker creates Playwright and the browser once and executes run jobs
# sequentially to avoid cross-thread greenlet errors.
_job_queue = queue.Queue()
_worker_thread = None
_worker_started = False


def _playwright_worker():
    import traceback

    try:
        p = sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as e:
        tb = traceback.format_exc()
        # Drain any queued jobs and emit RUN_ERROR so clients don't hang.
        while not _job_queue.empty():
            try:
                job = _job_queue.get_nowait()
            except Exception:
                break
            if not job:
                continue
            run_id, version = job
            _emit(run_id, "RUN_ERROR", {"error": str(e), "trace": tb})
            q = _queues.get(run_id)
            if q is not None:
                q.put(None)
        return

    while True:
        job = _job_queue.get()
        if job is None:
            break
        run_id, version = job
        metrics = Metrics()
        try:
            set_active_version(version)
            page = browser.new_page()
            try:
                def emit(_ignored_run_id, event_type, data):
                    _emit(run_id, event_type, data)

                run_test(page, BASE_URL, metrics, emit=emit)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        except Exception as e:
            tb = traceback.format_exc()
            _emit(run_id, "RUN_ERROR", {"error": str(e), "trace": tb})
        finally:
            try:
                _emit(run_id, "STREAM_END", metrics.summary())
            except Exception:
                pass
            q = _queues.get(run_id)
            if q is not None:
                q.put(None)

    try:
        browser.close()
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass


def _ensure_worker_started():
    global _worker_thread, _worker_started
    with _lock:
        if not _worker_started:
            _worker_thread = threading.Thread(target=_playwright_worker, daemon=True)
            _worker_thread.start()
            _worker_started = True


def _ensure_server_started():
    """Ensure the local HTTP app server is started once. Playwright/browser
    are deliberately created per-run inside the worker thread to avoid
    cross-thread greenlet errors.
    """
    global _server_started
    with _lock:
        if not _server_started:
            start_server(PORT)
            _server_started = True


def _emit(run_id, event_type, data):
    q = _queues.get(run_id)
    if q:
        q.put({"type": event_type, "data": data})


@app.post("/scenarios/generate")
def generate_scenario(body: dict | None = None):
    body = body or {}
    try:
        scenario = create_draft(body.get("prompt"), body.get("fixtureVersion", DEFAULT_FIXTURE_VERSION))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(scenario, status_code=201)


@app.get("/scenarios/{scenario_id}")
def read_scenario(scenario_id: str):
    scenario = get_scenario(scenario_id)
    if scenario is None:
        return JSONResponse({"error": "unknown scenario_id"}, status_code=404)
    return JSONResponse(scenario)


@app.patch("/scenarios/{scenario_id}")
def edit_scenario(scenario_id: str, body: dict | None = None):
    try:
        scenario = update_scenario(scenario_id, body or {})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if scenario is None:
        return JSONResponse({"error": "unknown scenario_id"}, status_code=404)
    return JSONResponse(scenario)


@app.post("/scenarios/{scenario_id}/approve")
def approve_generated_scenario(scenario_id: str):
    try:
        scenario = approve_scenario(scenario_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if scenario is None:
        return JSONResponse({"error": "unknown scenario_id"}, status_code=404)
    return JSONResponse(scenario)


@app.post("/scenarios/{scenario_id}/generate-test")
def generate_scenario_test(scenario_id: str):
    try:
        artifact = generate_test(scenario_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    scenario = update_scenario(scenario_id, {"generatedTest": artifact})
    if scenario is None:
        return JSONResponse({"error": "unknown scenario_id"}, status_code=404)
    return JSONResponse({"scenarioId": scenario_id, "generatedTest": artifact})


@app.post("/runs")
def start_run(body: dict | None = None):
    body = body or {}
    scenario_id = body.get("scenarioId")
    scenario = get_scenario(scenario_id) if scenario_id else None
    if scenario_id and scenario is None:
        return JSONResponse({"error": "unknown scenario_id"}, status_code=404)
    if scenario and scenario.get("status") != "APPROVED":
        return JSONResponse({"error": "scenario must be approved before running"}, status_code=409)

    version = body.get("version") or (scenario or {}).get("fixtureVersion", DEFAULT_FIXTURE_VERSION)

    run_id = str(uuid.uuid4())[:8]
    _queues[run_id] = queue.Queue()

    # Ensure the local HTTP app is running
    _ensure_server_started()

    # Launch a subprocess that runs the actual test run. This keeps Playwright
    # isolated in a separate process to avoid cross-thread greenlet issues.
    runner_py = os.path.join(os.path.dirname(__file__), "runner_process.py")
    cmd = [sys.executable, runner_py, run_id, version]
    if scenario_id:
        cmd.append(scenario_id)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    # Reader thread: forward each JSON line emitted by the subprocess into
    # the SSE queue for this run_id.
    def reader():
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    # Non-JSON output: emit as RUN_ERROR text
                    _emit(run_id, "RUN_ERROR", {"error": line})
                    continue
                etype = obj.get("type")
                data = obj.get("data")
                _emit(run_id, etype, data)
            proc.wait()
        finally:
            q = _queues.get(run_id)
            if q is not None:
                q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    return JSONResponse({"run_id": run_id})


def _run_with_fixed_id(run_id, version):
    # Guard the whole run so any unexpected exception is surfaced to the
    # SSE stream rather than leaving clients waiting forever.
    metrics = Metrics()
    try:
        # Start the local HTTP app if needed (only once). Create a Playwright
        # instance + browser per thread so Playwright sync APIs and greenlets
        # stay within a single OS thread.
        _ensure_server_started()
        set_active_version(version)

        p = sync_playwright().start()
        browser = None
        try:
            browser = p.chromium.launch()
            page = browser.new_page()

            def emit(_ignored_run_id, event_type, data):
                _emit(run_id, event_type, data)

            try:
                run_test(page, BASE_URL, metrics, emit=emit)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            try:
                p.stop()
            except Exception:
                pass
    except Exception as e:
        import traceback

        tb = traceback.format_exc()
        _emit(run_id, "RUN_ERROR", {"error": str(e), "trace": tb})
    finally:
        try:
            _emit(run_id, "STREAM_END", metrics.summary())
        except Exception:
            pass
        q = _queues.get(run_id)
        if q is not None:
            q.put(None)


@app.get("/runs/{run_id}/stream")
def stream(run_id: str):
    q = _queues.get(run_id)
    if q is None:
        return JSONResponse({"error": "unknown run_id"}, status_code=404)

    def gen():
        while True:
            item = q.get()
            if item is None:
                yield f"event: {EVENT_STREAM_CLOSE}\ndata: {{}}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    # Run with: `uvicorn backend.server:app --port 8090`
    try:
        import uvicorn

        uvicorn.run("backend.server:app", host=BACKEND_HOST, port=BACKEND_PORT, reload=False)
    except Exception:
        # Uvicorn not installed; fall back to a simple message.
        print(f"uvicorn not installed. Run the app via: uvicorn backend.server:app --port {BACKEND_PORT}")
