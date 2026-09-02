import sys
import json
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright
from app.server import start_server, set_active_version
from framework.runner import run_test
from framework.metrics import Metrics
from framework.constants import APP_PORT, TESTS_PATH
from framework.storage import create_store


def main():
    create_store().reset()

    print(f"GROQ_API_KEY present:   {bool(os.environ.get('GROQ_API_KEY'))}")
    print(f"OPENAI_API_KEY present: {bool(os.environ.get('OPENAI_API_KEY'))}")
    if os.environ.get("ENVIRONMENT", "MOCK").upper() == "MOCK":
        print("-> ENVIRONMENT=MOCK: scenario generation and healing use local deterministic behavior.\n")

    start_server(APP_PORT)
    base_url = f"http://localhost:{APP_PORT}/"

    scenarios = [
        ("v1", "v1_healthy_baseline"),
        ("v2", "v2_dom_changed_self_heal"),
        ("v3", "v3_product_bug_fail_fast"),
    ]

    overall_t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for version, label in scenarios:
            page = browser.new_page()
            set_active_version(version)
            metrics = Metrics()
            t0 = time.time()
            state = run_test(page, base_url, metrics, emit=None)
            elapsed = time.time() - t0
            page.close()

            print(f"=== {label} ===")
            print(f"  status: {state.status}   wall_time: {elapsed:.3f}s")
            for e in state.events:
                if e["type"] in ("HEALING_STARTED", "HEAL_ACCEPTED", "HEAL_REJECTED", "STEP_FAILED"):
                    print(f"    [{e['t']:.3f}s] {e['type']}: {json.dumps(e['data'])}")
            m = metrics.summary()
            print(f"  llm_calls={m['llm_calls']} heuristic_calls={m['heuristic_calls']} "
                  f"tokens_in={m['tokens_in']} tokens_out={m['tokens_out']} "
                  f"calls_by_source={m['calls_by_source']}")
            print()

    print(f"TOTAL wall time for all 3 scenarios: {time.time() - overall_t0:.3f}s")

    print("\nFinal persisted tests.json (locator memory):")
    print(open(TESTS_PATH).read())


if __name__ == "__main__":
    main()
