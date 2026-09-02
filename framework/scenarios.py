"""Versioned scenario artifacts and API-facing draft generation."""
import time
import uuid

from framework.constants import (
    DEFAULT_FIXTURE_VERSION,
    FIXTURE_VERSIONS,
    SCENARIO_SCHEMA_VERSION,
    TEST_EMAIL,
    TEST_PASSWORD,
)
from framework.storage import create_store
from framework import ai_client
from framework.environment import ai_enabled, current_environment

SCENARIO_VERSION = SCENARIO_SCHEMA_VERSION


def _read_all():
    data = create_store().read_scenarios()
    if data.get("schema_version") != SCENARIO_VERSION or not isinstance(data.get("scenarios"), dict):
        raise ValueError("invalid scenarios.json schema")
    return data


def _write_all(data):
    create_store().write_scenarios(data)


def _fingerprint(tag, role, label, input_type=None, ancestor="login-form"):
    return {
        "tag": tag,
        "role": role,
        "label": label,
        "input_type": input_type,
        "ancestor": ancestor,
        "volatile_attributes": ["id", "class", "name"],
    }


def _step(step_id, action, selector, intent, fingerprint, **details):
    return {
        "stepId": step_id,
        "scenario": "Review and update an order",
        "action": action,
        "selectorDetails": {"selector": selector, "intent": intent},
        "fingerprint": fingerprint,
        **details,
    }


def generate_draft(prompt, fixture_version=DEFAULT_FIXTURE_VERSION):
    """Create a draft with Groq in DEV/PROD and a local fixture in MOCK."""
    scenario_id = str(uuid.uuid4())[:12]
    if ai_enabled():
        generated = ai_client.generate_scenario_with_groq(prompt, fixture_version)
        return {
            "schema_version": SCENARIO_VERSION,
            "scenarioId": scenario_id,
            "status": "DRAFT",
            "prompt": prompt,
            "fixtureVersion": fixture_version,
            "createdAt": time.time(),
            "updatedAt": time.time(),
            "scenarios": generated["scenarios"],
            "generatedTest": {"path": f"generated/{scenario_id}.py", "language": "python", "status": "PENDING"},
            "generation": {"source": "groq", "environment": current_environment()},
        }

    steps = [
        {
            "stepId": "login-email",
            "scenario": "Authenticate with valid credentials",
            "action": "fill",
            "selectorDetails": {"selector": "#email-input", "intent": "email address input"},
            "fingerprint": _fingerprint("input", "textbox", "Email", "email"),
            "value": TEST_EMAIL,
            "postcondition": {"type": "value_present"},
        },
        {
            "stepId": "login-password",
            "scenario": "Authenticate with valid credentials",
            "action": "fill",
            "selectorDetails": {"selector": "#password-input", "intent": "password input"},
            "fingerprint": _fingerprint("input", "textbox", "Password", "password"),
            "value": TEST_PASSWORD,
            "postcondition": {"type": "value_present"},
        },
        {
            "stepId": "login-submit",
            "scenario": "Authenticate with valid credentials",
            "action": "click",
            "selectorDetails": {"selector": "#login-submit", "intent": "sign-in button"},
            "fingerprint": _fingerprint("button", "button", "Sign in", ancestor="login-form"),
            "postcondition": {"type": "text_contains", "selector": "#status-msg", "value": "Welcome back!"},
        },
        {
            "stepId": "login-status",
            "scenario": "Authenticate with valid credentials",
            "action": "assert_text",
            "selectorDetails": {"selector": "#status-msg", "intent": "login result status"},
            "fingerprint": _fingerprint("p", "status", "login result", ancestor="login-form"),
            "expected_contains": "Welcome back!",
            "postcondition": {"type": "text_contains", "value": "Welcome back!"},
            "screenshot": {"checkpointId": "login-success", "fullPage": False},
        },
        _step("orders-navigation", "click", "#orders-tab", "Orders navigation tab", _fingerprint("button", "button", "Orders", ancestor="dashboard")),
        _step("order-search", "fill", "#order-search", "order search field", _fingerprint("input", "searchbox", "Search order or customer", "search", ancestor="orders-view"), value="NS-1002"),
        _step("order-details", "click", ".row-details", "View details action for the filtered order", _fingerprint("button", "button", "View details", ancestor="orders-body")),
        _step("order-dialog", "assert_text", "#dialog-order", "selected order details dialog", _fingerprint("p", "status", "selected order", ancestor="order-dialog"), expected_contains="NS-1002", postcondition={"type": "text_contains", "value": "NS-1002"}, screenshot={"checkpointId": "order-details", "fullPage": False}),
    ]
    now = time.time()
    return {
        "schema_version": SCENARIO_VERSION,
        "scenarioId": scenario_id,
        "status": "DRAFT",
        "prompt": prompt,
        "fixtureVersion": fixture_version,
        "createdAt": now,
        "updatedAt": now,
        "scenarios": [
            {
                "scenarioId": "valid-login",
                "scenario": "Valid login flow",
                "stepDetails": steps,
            }
        ],
        "generatedTest": {"path": f"generated/{scenario_id}.py", "language": "python", "status": "PENDING"},
    }


def create_draft(prompt, fixture_version=DEFAULT_FIXTURE_VERSION):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    if fixture_version not in FIXTURE_VERSIONS:
        raise ValueError("fixture_version must be v1, v2, or v3")
    draft = generate_draft(prompt.strip(), fixture_version)
    data = _read_all()
    data["scenarios"][draft["scenarioId"]] = draft
    _write_all(data)
    return draft


def get_scenario(scenario_id):
    return _read_all()["scenarios"].get(scenario_id)


def update_scenario(scenario_id, patch):
    data = _read_all()
    scenario = data["scenarios"].get(scenario_id)
    if scenario is None:
        return None
    if not isinstance(patch, dict):
        raise ValueError("scenario update must be an object")
    scenario.update(patch)
    scenario["updatedAt"] = time.time()
    data["scenarios"][scenario_id] = scenario
    _write_all(data)
    return scenario


def approve_scenario(scenario_id):
    scenario = get_scenario(scenario_id)
    if scenario is None:
        return None
    if not scenario.get("scenarios") or any(not item.get("stepDetails") for item in scenario["scenarios"]):
        raise ValueError("scenario must contain at least one scenario with steps")
    return update_scenario(scenario_id, {"status": "APPROVED"})
