"""Environment loader for the Aivar project.

Loads variables from a top-level `.env` file into `os.environ`.
Tries to use python-dotenv if available; otherwise falls back to a simple parser.
"""
from __future__ import annotations

import os
from pathlib import Path

from framework.constants import REPO_ROOT


def load_env(env_path: str | None = None) -> None:
    """Load environment variables from `.env` at the repository root.

    - If a variable is already set in the process environment, it is NOT
      overwritten.
    - If `python-dotenv` is available, it will be used (honors more cases).
    """
    if env_path is None:
        env_path = str(Path(REPO_ROOT) / ".env")

    # Try python-dotenv first for robustness
    try:
        from dotenv import load_dotenv as _load_dotenv  # type: ignore

        _load_dotenv(env_path, override=False)
        return
    except Exception:
        pass

    p = Path(env_path)
    if not p.exists():
        return

    for raw in p.read_text(encoding="utf8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Do not overwrite existing environment variables
        if key and key not in os.environ:
            os.environ[key] = val


__all__ = ["load_env"]
