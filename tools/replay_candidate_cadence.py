"""Flat-position candidate cadence for scalar backtest replay."""


def flat_candidate_period(value="1"):
    return max(1, int(value or "1"))


def flat_candidate_due(step, every=1):
    return int(step) % max(1, int(every)) == 0
