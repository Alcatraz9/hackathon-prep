"""Error classification: decides whether a failure is healable (DOM locator
resolution problem) or a hard boundary that must fail fast without invoking
the LLM at all.

Healable (triggers self-healing):
  - TimeoutError waiting for a locator
  - Element handle not found / detached from DOM

Non-healable (HARD_PRODUCT_BUG, terminate immediately, no LLM call):
  - HTTP status errors (400/401/404/500...)
  - Explicit functional/logic assertion failures (value mismatches)
  - Network disconnects / gateway timeouts
"""
from playwright.sync_api import TimeoutError as PWTimeoutError


class HttpStatusError(Exception):
    def __init__(self, status_code, url):
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")


class LogicAssertionError(Exception):
    """Raised when the page behaved correctly (elements resolved fine) but
    produced the wrong result - e.g. expected text didn't match actual text.
    This is a product bug, not a broken locator, so it must never be healed.
    """
    pass


class NetworkError(Exception):
    pass


DOM_ERROR_MARKERS = ("not found", "detached", "waiting for locator", "resolved to 0 elements")


def classify(exc: Exception) -> str:
    if isinstance(exc, HttpStatusError):
        return "HARD_PRODUCT_BUG"
    if isinstance(exc, LogicAssertionError):
        return "HARD_PRODUCT_BUG"
    if isinstance(exc, NetworkError):
        return "HARD_PRODUCT_BUG"
    if isinstance(exc, PWTimeoutError):
        return "DOM_LOCATOR_ERROR"
    msg = str(exc).lower()
    if any(marker in msg for marker in DOM_ERROR_MARKERS):
        return "DOM_LOCATOR_ERROR"
    # unknown exception types are treated conservatively as hard bugs -
    # we never want to silently "heal" over something we don't understand
    return "HARD_PRODUCT_BUG"
