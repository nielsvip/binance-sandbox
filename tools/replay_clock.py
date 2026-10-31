from contextvars import ContextVar

_coverage = ContextVar("replay_bar_coverage", default=None)
def sim_datetime_class(real_datetime, sim_now, sim_utcnow):
    class SimDatetimeMeta(type(real_datetime)):
        def __instancecheck__(cls, instance):
            return isinstance(instance, real_datetime) or type.__instancecheck__(cls, instance)

    class SimDatetime(real_datetime, metaclass=SimDatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return sim_now(tz)

        @classmethod
        def utcnow(cls):
            return sim_utcnow()

    return SimDatetime


class ReplayEventLedger(list):
    def __init__(self, bar_clock, decision_clock):
        super().__init__()
        self.bar_clock = bar_clock
        self.decision_clock = decision_clock

    def append(self, event):
        event["bar_ts"] = float(self.bar_clock[0])
        event["decision_ts"] = float(self.decision_clock[0])
        super().append(event)

    def extend(self, events):
        for event in events:
            self.append(event)


def set_completed_bar_clock(bar_clock, decision_clock, bar_ts, duration_seconds):
    duration = float(duration_seconds)
    if duration < 0 or not __import__("math").isfinite(duration):
        raise ValueError("Replay completed-bar duration must be finite and nonnegative")
    bar_clock[0] = float(bar_ts)
    decision_clock[0] = float(bar_ts) + duration
    state = _coverage.get()
    if state is not None:
        state["visited"].append(float(bar_ts))


def begin_bar_coverage():
    return _coverage.set({"expected": None, "visited": []})

def end_bar_coverage(token):
    _coverage.reset(token)

def expect_replay_bars(timestamps):
    state = _coverage.get()
    if state is not None:
        state["expected"] = [float(value) for value in timestamps]

def replay_bar_coverage():
    import hashlib
    import struct
    state = _coverage.get() or {"expected": None, "visited": []}
    expected, visited = state["expected"], state["visited"]
    def digest(values):
        return hashlib.sha256(b"".join(struct.pack(">d", value) for value in values)).hexdigest()
    mismatch = next((i for i, pair in enumerate(zip(expected or [], visited)) if pair[0] != pair[1]), None)
    complete = expected is not None and bool(expected) and expected == visited
    return {"verdict": "COMPLETE" if complete else "INCOMPLETE", "expected_bars": len(expected) if expected is not None else None, "visited_bars": len(visited), "expected_timestamps_sha256": digest(expected) if expected is not None else None, "visited_timestamps_sha256": digest(visited), "first_bar": visited[0] if visited else None, "last_bar": visited[-1] if visited else None, "first_different_index": mismatch}
