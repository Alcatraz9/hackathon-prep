import time
import json


class Metrics:
    def __init__(self):
        self.events = []
        self.tokens_in = 0
        self.tokens_out = 0
        self.llm_calls = 0
        self.heuristic_calls = 0
        self.visual_checks = 0
        self.visual_diffs = 0
        self.calls_by_source = {}
        self._t0 = time.time()

    def start(self, label: str):
        return {"label": label, "t0": time.time()}

    def end(self, span):
        elapsed = time.time() - span["t0"]
        self.events.append({"label": span["label"], "seconds": round(elapsed, 4)})
        return elapsed

    def add_llm_usage(self, tokens_in: int, tokens_out: int, source: str = "llm"):
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.llm_calls += 1
        self.calls_by_source[source] = self.calls_by_source.get(source, 0) + 1

    def add_heuristic_call(self):
        self.heuristic_calls += 1

    def add_visual_check(self, diff=False):
        self.visual_checks += 1
        if diff:
            self.visual_diffs += 1

    def total_elapsed(self):
        return round(time.time() - self._t0, 4)

    def summary(self) -> dict:
        return {
            "total_wall_seconds": self.total_elapsed(),
            "events": self.events,
            "llm_calls": self.llm_calls,
            "heuristic_calls": self.heuristic_calls,
            "visual_checks": self.visual_checks,
            "visual_diffs": self.visual_diffs,
            "calls_by_source": self.calls_by_source,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_in + self.tokens_out,
        }

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.summary(), f, indent=2)

    def print_summary(self):
        s = self.summary()
        print("\n=== METRICS SUMMARY ===")
        print(f"Total wall time: {s['total_wall_seconds']}s")
        for e in s["events"]:
            print(f"  - {e['label']}: {e['seconds']}s")
        print(f"LLM calls: {s['llm_calls']}  |  Heuristic fallback calls: {s['heuristic_calls']}")
        print(f"Tokens used: in={s['tokens_in']} out={s['tokens_out']} total={s['tokens_total']}")
