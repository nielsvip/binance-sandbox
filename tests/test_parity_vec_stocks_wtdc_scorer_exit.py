"""Lane D parity: stocks evaluate_stop WT_DC scorer exit chain — vec twin == live on the same synthetic inputs.

Live: wt_dc_exit_scorer.score_exit (tiers), tradier_manage._parabolic_state, STOCK_MIN_HOLD (+dc_15m bypass),
NOLOSS_HOLD (+WT 5of5 bypass). Vec: vec_decisions/stocks_wtdc_scorer_exit.py.
"""
import ast
import pathlib
import sys
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _cfg(**kw):
    import v12_quick_engine as V
    c = V.QuickConfig(); c.apply_tradier_defaults()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _safe(npz, k, n, default=0.0):
    a = npz.get(k)
    if isinstance(a, np.ndarray) and a.shape[:1] == (n,):
        return a
    return np.full(n, default, dtype=float)


def _rand_npz(n=2500, seed=11):
    rng = np.random.default_rng(seed)
    z = {}
    for tf in ("5m", "15m", "1h", "4h", "D"):
        z["wt1_%s" % tf] = rng.normal(0, 30, n)
        z["wt2_%s" % tf] = rng.normal(0, 30, n)
    z["stoch_k_1h"] = rng.uniform(0, 100, n); z["stoch_k_4h"] = rng.uniform(0, 100, n)
    z["dc_position_1h"] = rng.uniform(0, 1, n); z["dc_position_4h"] = rng.uniform(0, 1, n)
    z["rsi_4h"] = rng.uniform(0, 100, n); z["rsi_1h"] = rng.uniform(0, 100, n)
    z["bb_pct_b_4h"] = rng.uniform(-0.2, 1.2, n)
    for k in ("rsi_4h", "rsi_1h", "bb_pct_b_4h"):
        z[k][rng.random(n) < 0.03] = 0.0
    return z, n


def _live_parabolic():
    src = (ROOT / "tradier_manage.py").read_text()
    tree = ast.parse(src)
    fn = next(nd for nd in tree.body if isinstance(nd, ast.FunctionDef) and nd.name == "_parabolic_state")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "tradier_manage._parabolic_state", "exec"), ns)
    return ns["_parabolic_state"]


def test_score_array_equals_live_score_exit():
    import wt_dc_exit_scorer as W
    import vec_decisions.stocks_wtdc_scorer_exit as X
    z, n = _rand_npz()
    for mn in (5, 4, 3):
        cfg = _cfg(EXIT_SCORER_MIN_CONDITIONS=mn)
        for is_long in (True, False):
            sc = X.score_array(z, n, is_long, cfg)
            for i in range(0, n, 3):
                ind = {k: float(v[i]) for k, v in z.items()}
                live, _ = W.score_exit(ind, is_long, 100.0, cfg=cfg)
                assert float(sc[i]) == float(live), (mn, is_long, i, sc[i], live)


def test_parabolic_equals_live():
    import vec_decisions.stocks_wtdc_scorer_exit as X
    live = _live_parabolic()
    z, n = _rand_npz()
    cfg = _cfg()
    up, dn = X.parabolic_masks(z, n, cfg, _safe)
    for i in range(n):
        ind = {k: float(v[i]) for k, v in z.items()}
        lu, ld, _, _ = live(ind, cfg)
        assert (bool(up[i]), bool(dn[i])) == (lu, ld), i


def test_min_hold_and_dc15_bypass():
    import vec_decisions.stocks_wtdc_scorer_exit as X
    assert X.min_hold_minutes(_cfg(), True) == 240.0 and X.min_hold_minutes(_cfg(), False) == 60.0
    assert X.min_hold_blocks(30, 100, 99, 101, True, 240)
    assert not X.min_hold_blocks(30, 98.9, 99, 101, True, 240)   # px < dc_low_15m bypass
    assert not X.min_hold_blocks(240, 100, 99, 101, True, 240)
    assert X.min_hold_blocks(30, 100, 99, 101, False, 60)
    assert not X.min_hold_blocks(30, 101.1, 99, 101, False, 60)  # px > dc_high_15m bypass
    assert X.min_hold_blocks(30, 100, 0, 0, True, 240)            # missing channel -> no bypass


def test_noloss_hold_and_wt_bypass_equal_live_rule():
    import vec_decisions.stocks_wtdc_scorer_exit as X
    cfg = _cfg()
    assert cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER == 0.01 and cfg.NOLOSS_BYPASS_WT_5OF5_ENABLED and cfg.NOLOSS_BYPASS_WT_5OF5_MIN_TFS == 3
    for gain in (-2.0, 0.0, 0.009, 0.01, 0.5):
        for against in range(6):
            live_hold = (gain < 0.01) and not (against >= 3)
            assert X.noloss_holds(gain, against, cfg) == live_hold, (gain, against)
    assert not X.noloss_holds(-5.0, 0, _cfg(NOLOSS_MIN_PROFIT_PCT_TRADIER=0.0))


def test_against_count_matches_live_loop():
    import vec_decisions.stocks_wtdc_scorer_exit as X
    z, n = _rand_npz(400)
    for is_long in (True, False):
        c = X.against_count_array(z, n, is_long, _safe)
        for i in range(n):
            exp = sum(1 for tf in ("5m", "15m", "1h", "4h", "D") if ((z["wt1_" + tf][i] < z["wt2_" + tf][i]) if is_long else (z["wt1_" + tf][i] > z["wt2_" + tf][i])))
            assert c[i] == exp


def test_live_source_anchors():
    src = (ROOT / "tradier_manage.py").read_text()
    for needle in ("_exit_threshold = _cfg_auto('WT_DC_EXIT_THRESHOLD', 20)", "STOCK_MIN_HOLD(", "NOLOSS_HOLD_", "WT_DC_EXIT_PARABOLIC_BYPASS", "_dc15_bypass = True", "for _tf in ('5m', '15m', '1h', '4h', 'D'):"):
        assert needle in src, needle


def test_engine_defaults_and_multi_tf_gating():
    import v12_quick_engine as V
    t = _cfg()
    assert t.STOCKS_WTDC_SCORER_EXIT_ENABLED is True and t.MULTI_TF_EXIT_ENABLED_TRADIER is False and t.WT_DC_EXIT_THRESHOLD == 30
    assert V.QuickConfig().MULTI_TF_EXIT_ENABLED is False  # director ruling 2026-10-06: brand-new switch default = today-live
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_swx.score_array(npz, n, is_long, cfg)" in src and "WT_DC_EXIT_{float(_wsx_score[i]):.0f}" in src


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_"):
            f(); print("PASS", k)
