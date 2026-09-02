"""Framework package initializer.

Automatically load environment variables from the repository `.env` when
the `framework` package is imported, so modules that read `os.environ` will
see values defined there.
"""
from .env import load_env

# Load env at import time (safe no-op if .env missing)
load_env()

__all__ = []
