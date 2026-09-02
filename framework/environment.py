"""Runtime environment policy for AI and persistence behavior."""
import os

from framework.constants import DATABASE_URL_ENV, ENVIRONMENT_ENV

ENVIRONMENTS = ("PROD", "DEV", "MOCK")


def current_environment():
    value = os.environ.get(ENVIRONMENT_ENV, "MOCK").strip().upper()
    if value not in ENVIRONMENTS:
        raise ValueError(f"ENVIRONMENT must be one of: {', '.join(ENVIRONMENTS)}")
    return value


def ai_enabled():
    return current_environment() != "MOCK"


def storage_backend():
    environment = current_environment()
    if environment in ("DEV", "MOCK"):
        return "json"
    if not os.environ.get(DATABASE_URL_ENV):
        raise ValueError(f"{DATABASE_URL_ENV} is required when ENVIRONMENT=PROD")
    return "sqlite"
