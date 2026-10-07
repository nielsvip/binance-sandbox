"""Parity-cut 2026-10-04 regression locks (synthetic bars, runnable anywhere).

Locks the cut's behavior contracts without needing S1 NPZ:
- P0: MTF_ARMED False == True-with-suspend-off (was: zeroed core path).
- ENTRY_DC shared predicate: OFF inert, 15m mask exact.
- TARGET-DC master: default True neutral, False binds, reason classifier exact.
- MANDATORY_REENTRY_WT: gate binds per contract, disabled passes.
- STOP_FUNCTIONS_KILL: OFF inert, branch A fires, branch B needs k3/d3.
- FEE: crypto round-trip default 0.04 (USER ordered 0.08->0.04) + both getattr fallbacks.
- SHORT-BASE: file fossils killed all SHORT entries (MOM3/VIG/XUNDER); cells pinned to live truth.
Run: python3 vec_decisions/test_parity_cut_20261004.py
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _cfg(**kw):
    import v12_quick_engine as V
    c = V.QuickConfig()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _safe(npz, k, n, default=0.0):
    a = npz.get(k)
    if isinstance(a, np.ndarray) and len(a) == n:
        return a
    return np.full(n, default, dtype=float)


def _synth_npx(n=64, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return {
        "close": close, "dc_low_15m": close * 0.99, "dc_high_15m": close * 1.01,
        "wt1_1h": rng.normal(0, 1, n), "wt2_1h": rng.normal(0, 1, n),
        "wt1_4h": rng.normal(0, 1, n), "wt2_4h": rng.normal(0, 1, n),
        "wt1_15m": rng.normal(0, 1, n), "wt2_15m": rng.normal(0, 1, n),
        "wt_velocity_15m": rng.normal(0, 1, n),
    }


def test_mtf_armed_false_equals_true_no_suspend():
    import v12_quick_engine as V
    npz = _synth_npx()
    a = V.compute_entry_signals(npz, 64, True, _cfg(MTF_ARMED_ENTRY_ENABLED=False))
    b = V.compute_entry_signals(npz, 64, True, _cfg(MTF_ARMED_ENTRY_ENABLED=True, MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED=False))
    assert a.shape == b.shape == (64,)
    assert bool((a == b).all()), "P0: MTF False must equal True-with-suspend-off"


def test_mtf_armed_no_zeroing_branch():
    src = (ROOT / "v12_quick_engine.py").read_text()
    tree = ast.parse(src)
    hits = []

    class Find(ast.NodeVisitor):
        def visit_If(self, node):
            dump = ast.dump(node.test)
            if "MTF_ARMED_ENTRY_ENABLED" in dump:
                for stmt in node.body:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Assign):
                            t = ast.dump(sub.targets)
                            if "mtf_armed_ok" in t:
                                hits.append(ast.dump(sub.value))
            self.generic_visit(node)

    Find().visit(tree)
    assert not any("zeros" in h for h in hits), f"P0 reinverted: {hits}"


def test_entry_dc_off_inert_and_15m_exact():
    import vec_decisions.entry_dc_gate as g
    npz = _synth_npx()
    assert g.pass_mask(npz, 64, True, _cfg(ENTRY_DC_TF="OFF"), npz["close"], _safe) is None
    m = g.pass_mask(npz, 64, True, _cfg(ENTRY_DC_TF="15m", ENTRY_DC_BUFFER_PCT=0.10), npz["close"], _safe)
    assert m is not None and m.shape == (64,)
    lo, hi, c = npz["dc_low_15m"], npz["dc_high_15m"], npz["close"]
    expect = ((lo > 0) & (c >= lo * 1.001)) | ((hi > 0) & (c >= hi * 1.001))
    assert bool((m == expect).all()), "ENTRY_DC 15m mask must match verbatim transcription"


def test_target_dc_master_and_classifier():
    import vec_decisions.target_dc_reentry_gate as t
    assert t.enabled(_cfg()) is True
    assert t.enabled(_cfg(TARGET_DC_IMMEDIATE_REENTRY_ENABLED=False)) is False
    assert t.is_target_dc_reason("DAYTRADE_TARGET dc_15m_high -0.10% TARGET") is True
    assert t.is_target_dc_reason("SELL_TOP chop") is False
    trades = [{"exit_price": 100.0, "exit_reason": "DAYTRADE_TARGET dc_15m_high -0.10% TARGET"}]
    assert t.fires(_cfg(), True, 101.0, trades) is True
    assert t.fires(_cfg(TARGET_DC_IMMEDIATE_REENTRY_ENABLED=False), True, 101.0, trades) is False


def test_mrwt_disabled_passes_and_enabled_binds():
    import vec_decisions.mandatory_reentry_wt_vec as m
    npz = _synth_npx()
    pre = m.precompute(npz, 64, _safe)
    assert pre is not None
    assert m.leg_ok(_cfg(MANDATORY_REENTRY_WT_FILTER_ENABLED=False), True, 101.0, 100.0, 10, pre) is True
    outs = {m.leg_ok(_cfg(), True, 99.0, 100.0, i, pre) for i in range(64)}
    assert outs == {True, False}, f"enabled gate must bind on some bars, got {outs}"


def test_sfk_off_inert_branch_a_fires_branch_b_needs_k3():
    import vec_decisions.process_position_crypto__stop_functions_kill as s
    off = _cfg(LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED=False)
    assert s.check_stop_functions_kill(off, {}, True, 100.0, -6.0, 60.0, 600.0) == (False, "")
    on = _cfg(LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED=True)
    fire, reason = s.check_stop_functions_kill(on, {}, True, 100.0, -6.0, 60.0, 600.0)
    assert fire is True and reason.startswith("STOP_FUNCTIONS_KILL_g-6.00%")
    fire_b, _ = s.check_stop_functions_kill(on, {}, True, 100.0, -1.0, 60.0, 600.0)
    assert fire_b is False, "branch B needs k3/d3 (None here) -> inert per LG-13"


def test_raw_defaults_live_parity():
    c = _cfg()
    assert c.BASE_TF == "15m" and c.MTF_ARMED_ENTRY_ENABLED is False
    assert c.LIVE_ENTRY_ENGINE_ENABLED is False and c.LIVE_5m_trading_ENABLED is False
    assert c.HTF_ALIGNMENT_ENABLED is False and c.TARGET_DC_IMMEDIATE_REENTRY_ENABLED is True
    import v12_quick_engine as V
    t = V.QuickConfig()
    t.apply_tradier_defaults()
    assert t.BASE_TF == "5m" and t.LIVE_ENTRY_ENGINE_ENABLED is True


def test_fee_default_0_04_and_fallbacks():
    import re
    assert _cfg().CRYPTO_ROUND_TRIP_COMMISSION_PCT == 0.04, "USER ordered 0.08->0.04"
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert len(re.findall(r"CRYPTO_ROUND_TRIP_COMMISSION_PCT', 0\.08\)", src)) == 0, "stale 0.08 fallback resurrected"
    assert len(re.findall(r"CRYPTO_ROUND_TRIP_COMMISSION_PCT', 0\.04\)", src)) == 2, "both getattr fallbacks must be 0.04"
    assert re.search(r"CRYPTO_ROUND_TRIP_COMMISSION_PCT: float = 0\.04", src), "QuickConfig default must be 0.04"


def test_short_base_fossils_live_truth():
    import json
    import config as C
    import config_tradier as CT
    f = json.loads((ROOT / "data" / "cat_side_defaults_4.json").read_text())
    assert f["CRYPTO_SHORT"]["MOM3_FILTER_TF"] == "OFF" == C.Config.MOM3_FILTER_TF
    assert f["STOCKS_SHORT"]["MOM3_FILTER_TF"] == "OFF"
    assert f["CRYPTO_SHORT"]["VIGILANCE_GUARD_ENABLED"] is False and C.Config.VIGILANCE_GUARD_ENABLED is False
    assert f["STOCKS_SHORT"]["WT_CROSSUNDER_FINAL_ENABLED"] is True and CT.TradierConfig.WT_CROSSUNDER_FINAL_ENABLED is True
    c = _cfg()
    assert c.MOM3_FILTER_TF == "OFF" and c.VIGILANCE_GUARD_ENABLED is False and c.WT_CROSSUNDER_FINAL_ENABLED is True


def test_builder_fossil_pins_present():
    src = (ROOT / "tools" / "build_cat_side_defaults_4.py").read_text()
    assert "P0-FOSSIL" in src, "builder must pin SHORT killers to live truth"
    for k in ("MOM3_FILTER_TF", "VIGILANCE_GUARD_ENABLED", "WT_CROSSUNDER_FINAL_ENABLED"):
        assert k in src, f"builder pin missing {k}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"ALL {len(tests)} PASS")
