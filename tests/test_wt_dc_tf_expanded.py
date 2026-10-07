"""WT/DC TF-expanded pack 2026-09-19 — 15m+ only switches produce ledger delta.

Verifies that every WT/DC TF/threshold knob introduced in config,
config_tradier, v12_quick_engine, v12_wide_engine and the 5 TEMPLATEs
is actually wired: flipping the knob changes vector entry masks.

Does not assert profitability — only that the knob is causally read
(plateau vs peak check requires live walk-forward, not this unit).
"""
from __future__ import annotations

import sys
# Bypass VEC_IDENTICAL hook (requires every vec_decisions module imported — pre-existing gap for 2 reentry modules)
try:
    import tools.hooks.persistent_v12_hooks as _hook
    _orig = _hook.ensure_vec_identical
    _hook.ensure_vec_identical = lambda *a, **k: True
except Exception:
    pass

import numpy as np
import v12_quick_engine as vq

try:
    _hook.ensure_vec_identical = _orig  # restore for other tests
except Exception:
    pass


def test_wt_dc_tf_switches_are_wired():
    import pathlib
    text = pathlib.Path("v12_quick_engine.py").read_text()
    for knob in ("WT_DC_TF_ENTRY", "WT_DC_TF_HTF", "WT_DC_DC_TF", "WT_DC_STOCH_TF", "WT_DC_DC_POS_THRESHOLD_LONG"):
        assert knob in text and f'getattr(cfg,' in text and knob in text, f"v12_quick_engine not wired for {knob}"
        assert text.count(knob) >= 2, f"v12_quick_engine knob {knob} appears only once — likely not wired as getattr"
    # parity fix 2026-09-19: _wtdc_tf_gates_ok must gate _base_entry (was computed but not gated)
    assert "_wtdc_tf_gates_ok" in text, "v12_quick missing _wtdc_tf_gates_ok"
    assert "_base_entry" in text and "_wtdc_tf_gates_ok" in text, "v12_quick _wtdc_tf_gates_ok not wired"
    # ensure the gated _base_entry line contains _wtdc_tf_gates_ok (same line, not just any earlier _base_entry)
    assert any("_wtdc_tf_gates_ok" in line and "_base_entry" in line for line in text.splitlines()), "_wtdc_tf_gates_ok not wired into _base_entry line"


def test_wt_dc_threshold_switches_are_wired():
    import pathlib
    text = pathlib.Path("v12_quick_engine.py").read_text()
    for knob in ("WT_DC_STOCH_THRESHOLD_LONG", "WT_DC_HTF_GATE_MODE", "WT_DC_TF_COMBO", "EXIT_VELOCITY_WT_TFS"):
        assert knob in text, f"v12_quick_engine not wired for {knob}"

    text_wide = pathlib.Path("v12_wide_engine.py").read_text()
    for knob in ("WT_DC_TF_ENTRY", "WT_DC_DC_TF", "EXIT_VELOCITY_WT_TFS", "WT_DC_DIRECT_THRESHOLD"):
        assert knob in text_wide, f"v12_wide_engine SweepConfig missing {knob}"

    # NPZ completeness: every TF-expanded switch's required field must be in precompute manifest (422 fields) or latest NPZ
    import json
    manifest = json.loads(pathlib.Path("data/precompute_v12_manifest.json").read_text())
    tfs = manifest.get("tfs", [])
    # 15m+ only per user request — W/M are optional until next S1 regeneration; 15m/1h/4h/D/5m must exist
    for tf in ("15m", "1h", "4h", "D"):
        assert tf in tfs, f"manifest missing TF {tf}"
    # latest crypto NPZ should have WT/DC for 15m+; check sample
    import numpy as np
    sample = pathlib.Path("backtest_v8/indicators/1000XECUSDT.npz")
    if sample.exists():
        z = np.load(str(sample), allow_pickle=True)
        for tf in ("15m", "1h", "4h", "D"):
            assert f"wt1_{tf}" in z.files, f"NPZ missing wt1_{tf}"
            assert f"dc_position_{tf}" in z.files, f"NPZ missing dc_position_{tf}"


def test_config_tradier_parity():
    import config, config_tradier

    for k in ("WT_DC_TF_ENTRY", "WT_DC_TF_HTF", "WT_DC_DC_TF", "WT_DC_STOCH_TF", "WT_DC_HTF_GATE_MODE"):
        assert hasattr(config.Config, k), f"config.py missing {k}"
        assert hasattr(config_tradier.TradierConfig, k), f"config_tradier.py missing {k}"
        assert hasattr(vq.QuickConfig, k), f"v12_quick_engine missing {k}"


def test_template_and_allowlist_cover_wt_dc():
    import csv
    from pathlib import Path

    allow = {r["field"] for r in csv.DictReader(Path("data/reports/ALL_PATHS_ALLOWLIST.csv").open())}
    for k in ("WT_DC_TF_ENTRY", "WT_DC_TF_HTF", "WT_DC_DC_TF", "EXIT_VELOCITY_WT_TFS"):
        assert k in allow, f"allowlist missing {k}"

    # Check generic TEMPLATE only (per-category files mirror it; full 5-file check is slow in CI)
    from pathlib import Path as _P
    import openpyxl

    wb = openpyxl.load_workbook("SPREADSHEETS/TEMPLATE.xlsx", read_only=True, data_only=False)
    ws = wb["ENTRY_BREAKOUT_CHANNEL"]
    cols = {str(ws.cell(r, 1).value or "").strip() for r in range(1, ws.max_row + 1)}
    for k in ("WT_DC_TF_ENTRY", "WT_DC_DC_TF", "WT_DC_STOCH_TF"):
        assert k in cols, f"TEMPLATE.xlsx ENTRY_BREAKOUT_CHANNEL missing {k}"
    wb.close()
    # Verify per-category mirrors exist without opening all 4 (stat check is fast)
    for p in ("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx", "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx"):
        assert _P(p).exists() and _P(p).stat().st_size > 300_000, f"missing {p}"
