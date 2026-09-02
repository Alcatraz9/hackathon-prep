"""Generated Playwright test for scenario 648e6a14-a08."""
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
    set_active_version('v1')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        state = run_test(page, BASE_URL, Metrics(), scenario_id='648e6a14-a08')
        print(state.status)
        browser.close()
    server.shutdown()
    if state.status != "PASSED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
