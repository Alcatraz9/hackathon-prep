import time
import uuid

from framework.constants import DEFAULT_LOCATORS, INTENTS
from framework.storage import create_store


def load_test_memory() -> dict:
    """Baseline locator memory read at the START of a run (persisted store)."""
    memory = create_store().read_test_memory()
    if memory is None:
        return {"locators": dict(DEFAULT_LOCATORS)}
    return memory


class RunState:
    """Everything for one run lives in memory here. Nothing touches disk
    until flush() is called at run completion (or hard-bug termination)."""

    def __init__(self, run_id: str = None):
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.started_at = time.time()
        self.locators = dict(load_test_memory()["locators"])
        self.events = []
        self.status = "RUNNING"
        self.steps_result = []
        self.visual_results = []

    def log(self, event_type: str, data: dict):
        self.events.append({"t": round(time.time() - self.started_at, 4), "type": event_type, "data": data})

    def flush(self):
        """Persist locator memory + this run's record. Only called once, at
        the end, per the 'in-memory during run, flush on completion' rule."""
        record = {
            "status": self.status,
            "duration_s": round(time.time() - self.started_at, 4),
            "events": self.events,
            "steps_result": self.steps_result,
            "visual_results": self.visual_results,
        }
        store = create_store()
        store.write_test_memory({"locators": self.locators})
        store.write_run(self.run_id, record)
