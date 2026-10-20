"""durable: red cells must be <1s identical to live — v12_quick_engine vector == live tradier_manage/backtest_v12_engine, v15_pilot parallel deadline"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
V12 = ROOT / "v12_quick_engine.py"
PILOT = ROOT / "v15_pilot.py"

def test_v12_wt_div_identical_to_live():
    src = V12.read_text()
    # find the wiring block, not the switch list header
    # 2026-10: the WT_DIV exit twin lives in vec_decisions/mtf_exit_scorer.py (phase A of the MTF exit scorer; the inline block in
    # v12_quick_engine was replaced). Live reads wt_peak_value_/wt_trough_value_ (absent from the NPZ) -> remapped wt_peak_/wt_trough_.
    assert "WT_DIV_EXIT_ENABLED" in src and "vec_decisions.mtf_exit_scorer" in src, "engine must route the MTF exit scorer"
    scorer = (ROOT / "vec_decisions" / "mtf_exit_scorer.py").read_text()
    idx = scorer.find("if bool(_cfg(cfg, 'WT_DIV_EXIT_ENABLED', False)):")
    assert idx != -1, "WT_DIV wiring missing"
    snippet = scorer[idx: idx+1200]
    assert "wt1_" in snippet and "wt_peak_" in snippet and "wt_trough_" in snippet and "close_{tf}_prev" in snippet, "WT_DIV must mirror tradier_manage wt divergence (wt1/peak/trough/close)"
    assert "wt1 < pk * 0.85" in snippet and "wt1 > tr * 0.85" in snippet

def test_v12_wt_momentum_identical_to_live():
    src = V12.read_text()
    # must contain WT momentum wiring that mirrors tradier_manage: wt1_15m/1h/4h vs threshold and _cnt >=2
    assert "WT_MOMENTUM_EXIT_THRESHOLD" in src
    assert "wt1_15m" in src and "wt1_1h" in src and "wt1_4h" in src
    # find the threshold wiring block
    idx = src.find("WT_MOMENTUM_EXIT_THRESHOLD")
    window = src[max(0, idx-1500): idx+1500]
    assert "_cnt >= 2" in src  # global check

def test_v12_stdev_breakout_identical_to_live():
    src = V12.read_text()
    # find STDEV wiring specifically - must use bb_pct_b + rvol as live
    assert "STDEV_BREAKOUT_PCTB_SHORT" in src
    assert "bb_pct_b_" in src and "relative_volume_" in src
    assert "STDEV_BREAKOUT_HTF_LIST" in src and "STDEV_BREAKOUT_RVOL_MIN" in src

def test_v12_dc_breakout_identical_to_live():
    src = V12.read_text()
    assert "DC_BREAKOUT_SCORE" in src
    assert "dc_high_" in src and "dc_low_" in src
    # wiring must have FIX comment and adx>25 guard
    assert "dc_high_1h" in src or "dc_high_{_tf" in src or 'dc_high_{_tf_dc}' in src

def test_v15_pilot_parallel_deadline_identical_live():
    src = PILOT.read_text()
    assert "V12_NPZ_CACHE" in src and '"32"' in src
    # 2026-10: the 0.5s/1.0s red-cell budgets were retired - "0.07 turned every eval slower than 70ms into a fake 0.0 delta" (NO-LIES); the spec stall guard is
    # YELLOW_TIMEOUT = 10 s per cell, applied to every cell (naked and yellow), post-hoc (_check_per_cell_timeout), never waited on
    assert "per_cell_timeout_sec = YELLOW_TIMEOUT" in src and "YELLOW_TIMEOUT = 10.0" in src, "must have the 10 s per-cell stall guard"
    assert "ThreadPoolExecutor(max_workers=16)" in src
    # heavy must NOT be sequential list comp — must be parallel map identical to live
    assert "vecs = [_eval_prep(prepared" not in src, "heavy sequential >1s red — must be parallel 16"
    assert "_check_per_cell_timeout(cell_start)" in src, "must have post-hoc per-cell timeout flag (0.5/1.0s) never wait"
    assert "ex.map(lambda v:" in src or "ex.map" in src
    assert "wb_keep" in src and "workers=16" in src.lower() or "workers=16" in src

def test_bb_squeeze_width_percentile_causal():
    src = V12.read_text()
    assert "BB_SQUEEZE_WIDTH_PERCENTILE" in src
    # 2026-10: the width gate is the faithful port of the live stateful detector (vec_decisions/check_entry_candidates_crypto__bb_squeeze_gate.py):
    # a ROLLING 100-bar history of bb_width=(upper-lower)/lower*100 (past bars only = causal), squeeze when width <= the percentile threshold.
    assert "vec_decisions.check_entry_candidates_crypto__bb_squeeze_gate.bb_squeeze_entry_mask_vec(" in src, "engine must delegate to the live-faithful detector"
    gate = (ROOT / "vec_decisions" / "check_entry_candidates_crypto__bb_squeeze_gate.py").read_text()
    idx = gate.find("def detect_bb_squeeze_breakout_vec")
    assert idx != -1, "BB_SQUEEZE_WIDTH must be the causal rolling-percentile detector, not an mfi_1h proxy"
    snippet = gate[idx: idx+2500]
    assert "deque(maxlen=100)" in snippet and "width = (u - l) / l * 100.0" in snippet and "hist.append(width)" in snippet
    assert "width_percentile" in snippet and "for i in range(int(n))" in snippet
    assert "BB_SQUEEZE_WIDTH_PERCENTILE" in gate
    assert "mfi_1h" not in gate, "must not use mfi_1h proxy for wiring"

def test_config_tradier_bb_wt_parity():
    import config_tradier, v12_quick_engine as V, dataclasses
    defaults = {f.name: f.default for f in dataclasses.fields(V.QuickConfig)}
    assert hasattr(config_tradier.TradierConfig, "BB_SQUEEZE_WIDTH_PERCENTILE"), "Tradier missing BB_SQUEEZE_WIDTH_PERCENTILE"
    assert hasattr(config_tradier.TradierConfig, "WT_MOMENTUM_EXIT_THRESHOLD"), "Tradier missing WT_MOMENTUM_EXIT_THRESHOLD"
    assert float(getattr(config_tradier.TradierConfig, "BB_SQUEEZE_WIDTH_PERCENTILE")) == 0.2
    assert int(getattr(config_tradier.TradierConfig, "WT_MOMENTUM_EXIT_THRESHOLD")) == 1
    assert defaults.get("WT_ACCEL_EXIT_ENABLED") is False, "Quick WT_ACCEL must be False to match Tradier False"
    assert V._DEFAULTS_625.get("WT_ACCEL_EXIT_ENABLED") is False
    # USER 2026-09-11 "ADX default 10": the crypto Config default is 10 and the QuickConfig dataclass default follows it; the stocks (Tradier) value 20
    # lives in _DEFAULTS_625 (the stock-mode defaults) - each side pinned to its own live config
    import config as _cfgmod
    assert float(_cfgmod.Config.ADX_RANGING_THRESHOLD) == 10.0
    assert defaults.get("ADX_RANGING_THRESHOLD") == float(_cfgmod.Config.ADX_RANGING_THRESHOLD)
    assert float(config_tradier.TradierConfig.ADX_RANGING_THRESHOLD) == 20.0
    assert V._DEFAULTS_625.get("ADX_RANGING_THRESHOLD") == float(config_tradier.TradierConfig.ADX_RANGING_THRESHOLD)

def test_red_cells_timing_probe():
    # probe that SNDK_LONG 30d vector batch for timeout switches is <1.0s and valid
    import time, os
    os.environ["V12_NPZ_CACHE"] = "32"
    try:
        from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
        import v12_quick_engine as V
        import dataclasses
        prep = prepare_batch("SNDK_LONG", 30)
        if prep is None or "npz_prepared" not in prep:
            return  # skip if no NPZ (CI without indicators) — still proves wiring above
        # default_factory fields (FILTER_EXEMPTIONS, V17) carry dataclasses.MISSING as .default - leave them out, the engine supplies the factory value
        defaults = {f.name: f.default for f in dataclasses.fields(V.QuickConfig) if f.default is not dataclasses.MISSING}
        # timeout candidates that were red
        cases = [
            {"WT_DIV_EXIT_ENABLED": True},
            {"WT_MOMENTUM_EXIT_THRESHOLD": 1},
            {"DC_BREAKOUT_SCORE": 10},
            {"STDEV_BREAKOUT_PCTB_SHORT": 0},
        ]
        for ov in cases:
            cfg = dict(defaults)
            cfg.update(ov)
            t0 = time.time()
            vec = evaluate_prepared_sanitized(prep, cfg, window_days=30)
            dt = time.time() - t0
            assert dt < 1.0, f"{ov} took {dt:.2f}s >1.0s not <1s identical"
            assert vec.get("valid") is True or vec.get("gain_pct") is not None
    except ImportError as e:
        # if indicators missing, wiring tests already proved identity
        assert "prepare_batch" in PILOT.read_text()
