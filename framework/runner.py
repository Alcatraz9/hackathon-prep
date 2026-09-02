from playwright.sync_api import TimeoutError as PWTimeoutError

from framework import healer
from framework.error_classifier import classify, LogicAssertionError
from framework.state import RunState, INTENTS
from framework.scenarios import get_scenario
from framework.constants import SELECTOR_TIMEOUT_MS, TEST_EMAIL, TEST_PASSWORD
from framework.visual import capture_checkpoint

LOGIN_STEPS = [
    {"action": "fill", "key": "email_input", "value": TEST_EMAIL},
    {"action": "fill", "key": "password_input", "value": TEST_PASSWORD},
    {"action": "click", "key": "submit_button", "value": None},
    {"action": "assert_text", "key": "status_message", "expected_contains": "Welcome back!"},
]


def _resolve(page, selector: str, timeout_ms=SELECTOR_TIMEOUT_MS):
    """Raises playwright's TimeoutError (classified as DOM_LOCATOR_ERROR) if
    the selector never attaches."""
    page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
    return page.query_selector(selector)


def _scenario_steps(scenario_id):
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise ValueError(f"unknown scenario_id: {scenario_id}")
    if scenario.get("status") != "APPROVED":
        raise ValueError("scenario must be approved before execution")
    steps = []
    for scenario_case in scenario.get("scenarios", []):
        for detail in scenario_case.get("stepDetails", []):
            selector_details = detail.get("selectorDetails") or {}
            steps.append({
                "stepId": detail.get("stepId"),
                "scenario": detail.get("scenario", scenario_case.get("scenario")),
                "action": detail.get("action"),
                "key": detail.get("stepId"),
                "selector": selector_details.get("selector"),
                "intent": selector_details.get("intent", detail.get("stepId", "scenario step")),
                "value": detail.get("value"),
                "expected_contains": detail.get("expected_contains"),
                "postcondition": detail.get("postcondition"),
                "screenshot": detail.get("screenshot"),
                "fingerprint": detail.get("fingerprint") or {},
            })
    if not steps:
        raise ValueError("approved scenario contains no executable steps")
    return steps


def run_test(page, base_url: str, metrics, emit=None, scenario_id=None) -> RunState:
    state = RunState()
    steps = _scenario_steps(scenario_id) if scenario_id else [
        {**step, "stepId": step["key"], "selector": None, "intent": INTENTS.get(step["key"], step["key"])}
        for step in LOGIN_STEPS
    ]

    def log(event_type, data):
        state.log(event_type, data)
        if emit:
            emit(state.run_id, event_type, data)

    log("RUN_STARTED", {"base_url": base_url})
    page.goto(base_url)

    for step in steps:
        key = step["key"]
        action = step["action"]
        selector = step.get("selector") or state.locators.get(key)
        if not selector:
            raise ValueError(f"step has no selector: {key}")
        log("STEP_STARTED", {"action": action, "key": key, "stepId": step.get("stepId"), "selector": selector})

        try:
            if action == "fill":
                el = _resolve(page, selector)
                el.fill(step.get("value"))
            elif action == "click":
                el = _resolve(page, selector)
                el.click()
            elif action == "assert_text":
                el = _resolve(page, selector)
                actual = (el.inner_text() or "").strip()
                if step.get("expected_contains") not in actual:
                    raise LogicAssertionError(
                        f"expected text containing '{step.get('expected_contains')}', got '{actual}'"
                    )

        except Exception as exc:
            error_type = classify(exc)

            if error_type == "HARD_PRODUCT_BUG":
                log("STEP_FAILED", {"key": key, "error_type": "HARD_PRODUCT_BUG", "message": str(exc)})
                state.status = "FAILED_HARD_BUG"
                state.steps_result.append({"key": key, "passed": False, "reason": "HARD_PRODUCT_BUG"})
                state.flush()
                return state

            # DOM_LOCATOR_ERROR -> attempt self-healing. verify_fn performs
            # the REAL step action against the candidate and reports success,
            # so HEAL_ACCEPTED only fires once the actual step truly works.
            def verify_fn(candidate_selector, _action=action, _step=step):
                cel = page.query_selector(candidate_selector)
                if cel is None:
                    return False
                if _action == "fill":
                    cel.fill(_step.get("value"))
                    return True
                elif _action == "click":
                    cel.click()
                    return True
                elif _action == "assert_text":
                    actual = (cel.inner_text() or "").strip()
                    return _step.get("expected_contains") in actual
                return False

            heal_result = healer.heal(
                page,
                action=action,
                original_intent=(step.get("intent") or INTENTS.get(key, key))
                + (f"; target label: {(step.get('fingerprint') or {}).get('label')}" if (step.get('fingerprint') or {}).get('label') else "")
                + (f"; expected text: {step.get('expected_contains')}" if action == "assert_text" else ""),
                failed_selector=selector,
                metrics=metrics,
                verify_fn=verify_fn,
                fingerprint=step.get("fingerprint"),
                emit=lambda etype, data: log(etype, {**data, "key": key}),
            )

            if not heal_result["accepted"]:
                log("STEP_FAILED", {"key": key, "error_type": "DOM_LOCATOR_ERROR", "healed": False})
                state.status = "FAILED_HEAL_REJECTED"
                state.steps_result.append({"key": key, "passed": False, "reason": "HEAL_REJECTED"})
                state.flush()
                return state

            # HEAL_ACCEPTED -> the real action already succeeded inside verify_fn; persist memory
            state.locators[key] = heal_result["selector"]

        log("STEP_PASSED", {"key": key})
        state.steps_result.append({"key": key, "passed": True})
        screenshot = step.get("screenshot")
        if screenshot:
            visual_result = capture_checkpoint(
                page,
                scenario_id or "legacy-login",
                screenshot.get("checkpointId", key),
                state.run_id,
                full_page=bool(screenshot.get("fullPage", False)),
                update_baseline=bool(screenshot.get("updateBaseline", False)),
            )
            metrics.add_visual_check(diff=visual_result.get("status") == "DIFF")
            state.visual_results.append(visual_result)
            log("VISUAL_CHECKED", visual_result)

    state.status = "PASSED"
    log("RUN_COMPLETE", {"status": state.status})
    state.flush()
    return state
