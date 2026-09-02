"""Generate a reproducible Python Playwright entry point for an approved scenario."""
import os

from framework.constants import APP_PORT, BASE_URL, DEFAULT_FIXTURE_VERSION, GENERATED_DIR, REPO_ROOT
from framework.scenarios import get_scenario


def generate_test(scenario_id):
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise ValueError("unknown scenario_id")
    if scenario.get("status") != "APPROVED":
        raise ValueError("scenario must be approved before test generation")

    os.makedirs(GENERATED_DIR, exist_ok=True)
    path = os.path.join(GENERATED_DIR, f"{scenario_id}.py")
    source = f'''"""Generated Playwright test for scenario {scenario_id}."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright

from app.server import set_active_version, start_server
from framework.constants import APP_PORT, BASE_URL
from framework.metrics import Metrics
from framework.runner import run_test


def main():
    server = start_server(APP_PORT)
    set_active_version({scenario.get("fixtureVersion", DEFAULT_FIXTURE_VERSION)!r})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        state = run_test(page, BASE_URL, Metrics(), scenario_id={scenario_id!r})
        print(state.status)
        browser.close()
    server.shutdown()
    if state.status != "PASSED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
'''
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(source)
    return {"path": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"), "language": "python", "status": "GENERATED"}