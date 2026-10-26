"""Switchless invariant that prevents opening an already active position."""


def blocks_open(position_amount, minimum_amount=0.0):
    try:
        amount = abs(float(position_amount or 0.0))
        minimum = max(0.0, float(minimum_amount or 0.0))
    except (TypeError, ValueError):
        return False
    return amount > minimum
