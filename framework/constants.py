"""Shared configuration and domain constants for the Aivar runtime."""
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")
FIXTURES_DIR = os.path.join(APP_DIR, "fixtures")
RUNTIME_DIR = os.path.join(APP_DIR, "runtime")
ACTIVE_VERSION_PATH = os.path.join(RUNTIME_DIR, "ACTIVE_VERSION")
DATA_DIR = os.path.join(REPO_ROOT, "data")
ARTIFACTS_DIR = os.path.join(REPO_ROOT, "artifacts")
SCENARIOS_PATH = os.path.join(DATA_DIR, "scenarios.json")
TESTS_PATH = os.path.join(DATA_DIR, "tests.json")
RUNS_PATH = os.path.join(DATA_DIR, "runs.json")
GENERATED_DIR = os.path.join(ARTIFACTS_DIR, "generated")
REPORTS_DIR = os.path.join(ARTIFACTS_DIR, "reports")

APP_HOST = "localhost"
APP_PORT = 8765
BACKEND_HOST = "0.0.0.0"
BACKEND_PORT = 8090
BASE_URL = f"http://{APP_HOST}:{APP_PORT}/"

FIXTURE_VERSIONS = ("v1", "v2", "v3")
DEFAULT_FIXTURE_VERSION = "v1"

TEST_EMAIL = "healer-test@example.com"
TEST_PASSWORD = "hunter2"

DEFAULT_LOCATORS = {
    "email_input": "#email-input",
    "password_input": "#password-input",
    "submit_button": "#login-submit",
    "status_message": "#status-msg",
}

INTENTS = {
    "email_input": "the email address input field",
    "password_input": "the password input field",
    "submit_button": "the sign-in / submit button",
    "status_message": "the login result status message text",
}

EVENT_RUN_STARTED = "RUN_STARTED"
EVENT_STEP_STARTED = "STEP_STARTED"
EVENT_STEP_PASSED = "STEP_PASSED"
EVENT_STEP_FAILED = "STEP_FAILED"
EVENT_HEALING_STARTED = "HEALING_STARTED"
EVENT_HEALING_CONTEXT = "HEALING_CONTEXT"
EVENT_HEAL_ACCEPTED = "HEAL_ACCEPTED"
EVENT_HEAL_REJECTED = "HEAL_REJECTED"
EVENT_RUN_COMPLETE = "RUN_COMPLETE"
EVENT_RUN_ERROR = "RUN_ERROR"
EVENT_STREAM_END = "STREAM_END"
EVENT_STREAM_CLOSE = "close"
EVENT_VISUAL_BASELINE_CREATED = "VISUAL_BASELINE_CREATED"
EVENT_VISUAL_CHECKED = "VISUAL_CHECKED"

SCENARIO_SCHEMA_VERSION = 1
CONFIDENCE_THRESHOLD = 0.85
DOM_CONTEXT_LIMIT = 40
TOKEN_BUDGET = 500
SELECTOR_TIMEOUT_MS = 1500
VISUAL_DIFF_PIXEL_RATIO = 0.01

GROQ_MODEL = "meta-llama/llama-prompt-guard-2-86m"
OPENAI_MODEL = "gpt-4o-mini"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
STORAGE_BACKEND_ENV = "AIVAR_STORAGE_BACKEND"
DATABASE_URL_ENV = "AIVAR_DATABASE_URL"
ENVIRONMENT_ENV = "ENVIRONMENT"
