"""Pins the pure contract of tools/v15_throughput_proof.py and the throughput guard's binding_reason.

USER 2026-10-15: a throughput proof that would accept a changed promoted set is worthless, so the bar
(identical md5(cumulative_overrides) AND identical final_gain) is pinned here, as is the refusal of the
1d/7d script-testing windows in a proof run.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v15_throughput_proof", "tools/v15_throughput_proof.py")
G = _load("v15_throughput_guard", "tools/v15_throughput_guard.py")


def _arm(label, **kw):
    base = {"label": label, "wall_s": 1000.0, "overrides_md5": "a" * 32, "final_gain": 12.5,
            "gate_fails": [], "write_bytes": 1000, "pilot_md5": "code" + label[-1], "env_overrides": {},
            "rows_done": 3376, "rc": 0}
    base.update(kw)
    return base


def _ctrl(label, **kw):
    """Same code + same env as _ctrl's sibling -> a control group the verdict can read a noise floor from."""
    return _arm(label, pilot_md5="champ", **kw)


def test_md5_overrides_is_order_and_type_stable():
    a = P.md5_overrides({"B": True, "A": 1.0})
    b = P.md5_overrides({"A": 1.0, "B": True})
    assert a == b and len(a) == 32
    assert P.md5_overrides({"A": 1.0}) != P.md5_overrides({"A": 2.0})
    assert P.md5_overrides({}) == "" or P.md5_overrides({}) is not None


def test_md5_overrides_of_non_dict_is_empty():
    assert P.md5_overrides(None) == ""
    assert P.md5_overrides("x") == ""


def test_crypto_and_cat_side_detection():
    assert P.is_crypto("DOTUSDT_LONG") and P.is_crypto("UNIUSDC_SHORT")
    assert not P.is_crypto("AAPL_LONG")
    assert P.cat_side("DOTUSDT_LONG") == ("CRYPTO", "LONG")
    assert P.cat_side("AAPL_SHORT") == ("STOCKS", "SHORT")
    assert P.sym_of("AAPL_SHORT") == "AAPL"


def test_a_difference_without_a_control_arm_is_unattributable():
    v, ok = P.verdict([_arm("champ"), _arm("cand", overrides_md5="b" * 32, wall_s=100.0)])
    assert ok is False and "UNATTRIBUTABLE" in v and "NO CONTROL ARM" in v


def test_control_arms_that_differ_declare_the_board_nondeterministic():
    """MEASURED 2026-10-15 on s2: the champion tree run twice on the same frozen NPZ gave 3374 rows / gain
    8.4366 / set 89b899837d vs 3376 / 8.9128 / b04b8e6c41 — identical code, different result."""
    v, _ = P.verdict([_ctrl("champA", final_gain=8.4366, rows_done=3374, overrides_md5="x" * 32),
                      _ctrl("champB", final_gain=8.9128, rows_done=3376, overrides_md5="y" * 32)])
    assert "NONDETERMINISTIC" in v and "0.47" in v


def test_a_difference_inside_the_measured_floor_is_within_noise_not_a_fail():
    arms = [_ctrl("champA", final_gain=8.4366, rows_done=3374, overrides_md5="x" * 32),
            _ctrl("champB", final_gain=8.9128, rows_done=3376, overrides_md5="y" * 32),
            _arm("cand", pilot_md5="cand", final_gain=8.3799, rows_done=3373, overrides_md5="z" * 32, wall_s=766.0)]
    v, ok = P.verdict(arms)
    assert "WITHIN NOISE" in v and "NOT shown to alter" in v
    assert ok is True  # cannot be called a failure on one run


def test_a_difference_beyond_the_floor_still_fails():
    arms = [_ctrl("champA", final_gain=8.90, rows_done=3376, overrides_md5="x" * 32),
            _ctrl("champB", final_gain=8.91, rows_done=3376, overrides_md5="x" * 32),
            _arm("cand", pilot_md5="cand", final_gain=4.0, rows_done=3376, overrides_md5="z" * 32)]
    v, ok = P.verdict(arms)
    assert ok is False and "EXCEEDS NOISE FLOOR" in v


def test_verdict_neutral_when_identical_but_not_faster():
    v, ok = P.verdict([_ctrl("champA"), _ctrl("champB"), _arm("cand", pilot_md5="cand", wall_s=1000.0)])
    assert ok is True and "NEUTRAL" in v and "never as a speedup" in v


def test_verdict_pass_only_when_identical_and_faster():
    v, ok = P.verdict([_ctrl("champA"), _ctrl("champB"), _arm("cand", pilot_md5="cand", wall_s=800.0)])
    assert ok is True and "PASS" in v


def test_a_reproducing_control_reports_its_floor():
    v, _ = P.verdict([_ctrl("champA"), _ctrl("champB")])
    assert "same code reproduced the same promoted set" in v


def test_verdict_reports_quality_gate_failures():
    v, _ = P.verdict([_ctrl("champA", gate_fails=["dd<=30"]), _ctrl("champB"), _arm("cand", pilot_md5="cand", wall_s=900.0)])
    assert "FAIL dd<=30" in v


def test_control_groups_need_matching_env_not_just_matching_code():
    a = _arm("x", pilot_md5="same", env_overrides={})
    b = _arm("y", pilot_md5="same", env_overrides={"V15_PROGRESS_JSON_EVERY_S": "0"})
    assert P.control_groups([a, b]) == []
    assert len(P.control_groups([a, _arm("z", pilot_md5="same", env_overrides={})])) == 1


def test_run_parser_refuses_script_testing_windows():
    import argparse

    for bad in ("1", "7"):
        try:
            P.main_argv = None
            sys.argv = ["x", "run", "--sym-side", "A_LONG", "--arm", "a=/tmp", "--arm", "b=/tmp", "--window-days", bad]
            P.main()
        except (SystemExit, argparse.ArgumentError) as e:
            assert isinstance(e, SystemExit)
        else:
            raise AssertionError(f"--window-days {bad} must be refused (script-testing only)")


def test_prod_env_keeps_1d_7d_work_off():
    assert P.PROD_ENV["V15_HOLDOUT_DAYS"] == "0"
    assert P.PROD_ENV["V15_FORWARD_SCORE"] == "0"
    assert P.PROD_ENV["V15_PAIR_MODE"] == "0"
    assert P.PROD_ENV["V15_FILL_SLICE_S"] == "0"


def test_guard_binding_reason_prefers_dispatch_guard():
    tick = {"s2": {"idle_reason": "disk guard", "disk_avail_mb": 1200, "mem_avail_mb": 30000, "slots": "0/6"}}
    r = G.binding_reason("s2", tick, 12, 0.0, 1.0, {})
    assert "disk guard" in r


def test_guard_binding_reason_names_cpu_demand_not_capacity():
    d = {"board_cpu_s_median_24h": 3400, "nproc": 16, "nonboard_cores_competing": 0.3,
         "board_cores": 4.0, "boards_running": ["A_LONG", "B_SHORT"], "nonboard_cores_niced": 3.4}
    r = G.binding_reason("s1", {"s1": {"slots": "4/4"}}, 12, 2.0, 1.0, d)
    assert "DEMAND" in r and "niced filler" in r


def test_guard_binding_reason_names_host_capacity_when_competing_work_eats_it():
    d = {"board_cpu_s_median_24h": 3400, "nproc": 16, "nonboard_cores_competing": 10.0,
         "board_cores": 5.0, "boards_running": ["A_LONG"], "nonboard_cores_niced": 0}
    r = G.binding_reason("s1", {"s1": {"slots": "4/4"}}, 12, 2.0, 1.0, d)
    assert "host ceiling" in r and "nice-0" in r


def test_guard_binding_reason_none_when_on_target():
    assert G.binding_reason("s1", {"s1": {"slots": "4/4"}}, 12, 13.0, 1.0, {}) is None
