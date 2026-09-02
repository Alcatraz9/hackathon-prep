"""Subprocess runner that executes a single test run and emits JSON events
on stdout. Designed to be launched by the FastAPI backend as a separate
process so Playwright runs in its own OS process (avoids threaded greenlet
issues).

Usage: python backend/runner_process.py <run_id> <version> [scenario_id]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from playwright.sync_api import sync_playwright
from framework.constants import BASE_URL, DEFAULT_FIXTURE_VERSION
from framework.runner import run_test
from framework.metrics import Metrics

from app.server import set_active_version


def emit_stdout(event_type, data):
    print(json.dumps({"type": event_type, "data": data}, default=str), flush=True)


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"type": "RUN_ERROR", "data": {"error": "missing args"}}))
        sys.exit(2)
    run_id = sys.argv[1]
    version = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_FIXTURE_VERSION
    scenario_id = sys.argv[3] if len(sys.argv) > 3 else None

    metrics = Metrics()
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            # Ensure the app serves the requested version for this run
            try:
                set_active_version(version)
            except Exception:
                pass
            # Emit RUN_STARTED
            emit_stdout("RUN_STARTED", {"run_id": run_id, "version": version})
            # run_test will call emit(run_id, event_type, data)
            def proc_emit(rid, event_type, data):
                # include run_id in forwarded payload for clarity
                payload = {**data, "run_id": rid}
                emit_stdout(event_type, payload)

            run_test(page, BASE_URL, metrics, emit=proc_emit, scenario_id=scenario_id)
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
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
        emit_stdout("RUN_ERROR", {"error": str(e), "trace": tb})
    finally:
        emit_stdout("STREAM_END", metrics.summary())


if __name__ == "__main__":
    main()
