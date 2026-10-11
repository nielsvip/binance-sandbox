"""Corruption-monitor re-fire (USER 2026-10-11 fix-as-discovered): the Haiku position-corruption loop compared each position file against a startup snapshot and, on the flat branch, hit `elif prev and (...): pass` — the snapshot never advanced, so a broker-flat ghost with a stale entry_price fired CRITICAL + a disk append EVERY 10s loop forever (ang: 1678 fires, ~41/9min, accelerating tail churn). The loop now advances the snapshot to current state every step: a flat ghost fires exactly once per transition; restore-then-reflatten fires once per cycle (true signal).
Run with EZ_LOG_DIR=/tmp (repo pattern: log dir is not writable in sandbox).
"""
import ez_manage


FULL = {"positionAmt": 0.0, "entry_price": 6.35666667}
FLAT = {"positionAmt": 0.0, "entry_price": 0.0}


def test_transition_fires_on_ep_to_zero():
    assert ez_manage._corruption_transition(FULL, 0.0, 0.0) is True


def test_transition_fires_on_amt_to_zero():
    assert ez_manage._corruption_transition({"positionAmt": 79.5, "entry_price": 0.25}, 0.0, 0.25) is True


def test_transition_quiet_when_stable_or_first_seen():
    assert ez_manage._corruption_transition(FLAT, 0.0, 0.0) is False
    assert ez_manage._corruption_transition(None, 0.0, 0.0) is False
    assert ez_manage._corruption_transition(FULL, 0.0, 6.35666667) is False


def test_two_steps_no_refire():
    first = ez_manage._corruption_transition(FULL, 0.0, 0.0)
    second = ez_manage._corruption_transition(FLAT, 0.0, 0.0)
    assert (first, second) == (True, False)


def test_reflatten_after_restore_fires_again():
    assert ez_manage._corruption_transition(FULL, 0.0, 0.0) is True
    assert ez_manage._corruption_transition(FLAT, 0.0, 6.35) is False
    assert ez_manage._corruption_transition({"positionAmt": 0.0, "entry_price": 6.35}, 0.0, 0.0) is True
