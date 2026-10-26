"""Shared symbol loss-streak circuit breaker state transitions."""


def record_close(consecutive_losses, halt_until, outcome, close_ts, loss_limit, halt_seconds):
    losses = int(consecutive_losses or 0)
    until = float(halt_until or 0.0)
    try:
        result = float(outcome)
        now = float(close_ts)
    except (TypeError, ValueError):
        return losses, until
    if result < 0:
        losses += 1
    else:
        losses = 0
    try:
        limit = max(1, int(loss_limit))
        duration = max(0.0, float(halt_seconds))
    except (TypeError, ValueError):
        return losses, until
    if losses >= limit:
        losses = 0
        until = max(until, now + duration)
    return losses, until


def is_blocked(now, halt_until):
    try:
        return float(now) < float(halt_until or 0.0)
    except (TypeError, ValueError):
        return False
