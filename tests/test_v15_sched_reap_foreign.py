"""Reap safety: the fleet must never kill a pilot run it does not own.

Measured 2026-10-15 on s2: `pilots()` grouped processes by --sym-side alone, so the S2 version-gate bench
(champion + candidate of the SAME sym_side) and the tools/v15_throughput_proof arms joined the production
group. The reaper then saw several "roots" and TERMed the ones that momentarily had no linked worker as
"orphaned workers (parent gone)" — the gate's CHRUSDT_LONG bench came back gain=None / 0.0 fit+OOS, and both
arms of a DOTUSDT_LONG A/B were SIGTERMed mid-board (/tmp/v15_termed_1506680, /tmp/v15_termed_1506682).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_fleet_scheduler as _SCHED


def _host_ns():
    """pilots() / the reaper live in the HOST_PY source string the orchestrator ships to each worker, so the
    only honest way to test them is to exec that exact string (mode 'noop' runs no branch)."""
    ns = {"__name__": "host_py_under_test"}
    _argv = sys.argv[:]
    sys.argv = ["host_py", "noop"]
    try:
        exec(compile(_SCHED.HOST_PY, "HOST_PY", "exec"), ns)
    finally:
        sys.argv = _argv
    return ns


S = type("S", (), _host_ns())

PROD = "/home/niels/v15_run32_20261011/progress"


def test_production_pilot_is_not_foreign():
    assert S._pilot_is_foreign({"V15_PROGRESS_DIR": PROD}, PROD) is False
    assert S._pilot_is_foreign({"V15_PROGRESS_DIR": PROD + "/"}, PROD) is False


def test_pilot_without_progress_dir_belongs_to_this_round():
    assert S._pilot_is_foreign({}, PROD) is False
    assert S._pilot_is_foreign({"V15_PROGRESS_DIR": ""}, PROD) is False


def test_gate_bench_run_is_foreign():
    env = {"V15_PROGRESS_DIR": "/home/niels/v15_bench/runs/20261014__gate_X_champ/CHRUSDT_LONG/progress"}
    assert S._pilot_is_foreign(env, PROD) is True


def test_throughput_proof_arm_is_foreign_by_marker_and_by_dir():
    assert S._pilot_is_foreign({"V15_ISOLATED_PROOF": "1"}, PROD) is True
    assert S._pilot_is_foreign({"V15_OUT_DIR": "/home/niels/v15_throughput_proof/x/arm_a/out"}, PROD) is True
    assert S._pilot_is_foreign({"V15_PROGRESS_DIR": "/home/niels/v15_throughput_proof/x/arm_a/progress"}, PROD) is True


def test_isolated_marker_wins_even_on_the_production_dir():
    assert S._pilot_is_foreign({"V15_ISOLATED_PROOF": "1", "V15_PROGRESS_DIR": PROD}, PROD) is True


def test_unknown_production_dir_fails_open_to_owned():
    # no ~/v15_current_progress_dir.txt on this host -> only the explicit isolation markers make a run foreign,
    # so the reaper keeps working exactly as before on a host without a round file.
    assert S._pilot_is_foreign({"V15_PROGRESS_DIR": "/anything"}, None) is False
    assert S._pilot_is_foreign({"V15_ISOLATED_PROOF": "1"}, None) is True


def test_old_round_dir_is_foreign():
    assert S._pilot_is_foreign({"V15_PROGRESS_DIR": "/home/niels/v15_run31_20261008/progress"}, PROD) is True
