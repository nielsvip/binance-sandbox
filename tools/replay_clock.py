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
