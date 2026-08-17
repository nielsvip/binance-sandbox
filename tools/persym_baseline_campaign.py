#!/usr/bin/env python3
"""persym_baseline_campaign.py — 24/7 stocks per-(symbol, side) baseline + OFAT campaign.

USER MANDATE 2026-07-18 (stocks first, crypto later):
  Phase 1  BASELINE: every (sym, side) in the point-in-time trb universe
           (tools/universe_registry.py) gets a faithful Tier-2 run on current
           settings -> key_baseline rows (gain/mo, b&h/mo, delta) in
           data/param_results_stocks.db + canonical CSVs (metrics_guard).
           Off-universe sides from the same runs are stored under campaign
           '<c>_offuni' so the final curated-vs-random universe comparison
           costs zero extra compute.
  Phase 2  OFAT: one param at a time vs the baseline, per-symbol rows recorded
           (not just pooled) -> ranges + HOPE suggestions + inert detection.
           Includes the EXTREME-STOP packs (frozen DC 4h/D, BB 4h/D/W with
           near-entry stops disabled) as STOP_PACK cells.
  Phase 3  report: relevance ranking (prune candidates need inert-wiring proof
           first), XLSX export, digest MD for the email.
  compare-universe: pooled metrics universe-keys vs all-keys ('random').

Dedupe: every cell checks param_results_store.already_tested + the resumable
CSV — nothing is ever re-run (Bible §4; user: stop chasing tails).
Every row carries full overrides_json + 4-file md5 stamp (Bible §12.4/§12.5).

Run on S1 ONLY, wrapped:  tools/run_seq_test.sh persym_campaign \
    python3 tools/persym_baseline_campaign.py baseline && ... ofat
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not SBX.exists():
    SBX = Path(__file__).resolve().parent.parent
PY = os.environ.get("V8_PYTHON", "/home/niels/.conda/envs/binance_env/bin/python")
if not Path(PY).exists():
    PY = sys.executable
sys.path.insert(0, str(SBX))
sys.path.insert(0, str(SBX / "tools"))
import metrics_guard as mg  # noqa: E402
import param_results_store as prs  # noqa: E402
import universe_registry as ur  # noqa: E402
from c5_matrix_contract import (  # noqa: E402
    C5_ADDITIONAL_CONTRACT_FILES,
    C5_MATRIX_CAMPAIGN,
    C5_MATRIX_CONTRACT_VERSION,
    C5_MATRIX_RECIPE_VERSION,
    c5_contract_fingerprint,
)

MODE = "tradier"
ACCOUNT = "trb"
START = os.environ.get("PSC_START", "2024-01-01")
CAMPAIGN = os.environ.get("PSC_CAMPAIGN", "stocks_baseline_v1")
TRADES_ROOT = Path(os.environ.get("PSC_TRADES_ROOT", str(SBX / "data" / "sweep_results" / f"persym_campaign_{CAMPAIGN}_trades")))
RESULTS_DIR = SBX / "data" / "sweep_results"
STAMP_FILES = ["backtest_v8_engine.py", "tradier_manage.py", "wt_dc_delta.py", "config_tradier.py"]
MATRIX_CONTRACT_VERSION = C5_MATRIX_CONTRACT_VERSION
MATRIX_RECIPE_VERSION = C5_MATRIX_RECIPE_VERSION
CAPITAL_ACCOUNTING_VERSION = "avg-trade-deployed-2000-v1"
MATRIX_RECIPE_DEPENDENCY_FILE = (
    SBX / "data" / "reports" / "switch_lab_catalog_20260729.json  # alias: SWITCH_MATRIX_INTERDEPENDENCY_20260729.json kept for backwards compat"
)
MATRIX_NPZ_DIR = Path(
    os.environ.get(
        "PSC_MATRIX_NPZ_DIR",
        str(SBX / "data" / "matrix_npz" / "stocks_repaired_20260725_c2"),
    )
)
MATRIX_END_DATE = os.environ.get("PSC_MATRIX_END_DATE", "2026-07-21")
# Only code/data capable of changing an exact-engine schedule or P&L belongs in
# the result identity.  Campaign/daemon/store code is still process-guarded
# below, but workbook, queue, and reporting edits must never invalidate exact
# evidence that was already computed with unchanged engine inputs.
MATRIX_CONTRACT_FILES = STAMP_FILES + [
    "ordinary_ladder_contract.py",
    "stock_v8_override_contract.py",
    "backtest_v8_harness.py",
    "mtf_exit_timing.py",
    "reentry_contract.py",
    "tradier_entry_contract.py",
    "tradier_route_contract.py",
    "wt_dc_entry_scorer.py",
    "tools/backtest_data_contract.py",
]
MATRIX_ORCHESTRATION_FILES = [
    "tools/persym_baseline_campaign.py",
    "tools/param_matrix_daemon.py",
    "tools/param_results_store.py",
]
MATRIX_PROCESS_GUARD_FILES = list(
    dict.fromkeys(
        MATRIX_CONTRACT_FILES
        + list(C5_ADDITIONAL_CONTRACT_FILES)
        + MATRIX_ORCHESTRATION_FILES
    )
)

# Vetted c2 rows produced after the final override-precedence/receipt deployment.
# They remain admissible only while every accepted exact source and the key's
# frozen NPZ still match the forensic manifest below.  This is compatibility,
# not a blanket fingerprint allowlist.
_C2_ACCEPTED_SOURCE_SHA256 = {
    "backtest_v8_engine.py": "85c49a15ce056bc0103fae57858a4f08fbfa16234672c7c105ef1b7721eea6cb",
    "tradier_manage.py": "a049e13fa4d4dabbf1d5ac3205393a85e8d1bc9c69d668ff15c79210a9cd6475",
    "wt_dc_delta.py": "afa4cdfd8d6b6ff77be9420a672355d98361f33661274f4e0c67789a8c039204",
    "config_tradier.py": "57b81e71e472608a007e0c044118799abbd1df26c97a6d1c733827bdf56536aa",
    "stock_v8_override_contract.py": "c9f89bff2dfa8938a5ed530b973028e8cb173f0e31cb925e436dbc27e716fd0d",
    "backtest_v8_harness.py": "4d8f9e00f672ccfd661ea92adcd92857714914538ea9a7676e4518e06155aeb2",
    "mtf_exit_timing.py": "5b7f61991b9f1f58bb2e35281e33c9195c15e783178434c37f206aa254430d6e",
    "reentry_contract.py": "64d607cc50f92f6f59cc77fc696379e42b1d27cd9fe8b147f558b11d977a2179",
    "wt_dc_entry_scorer.py": "23f0d4936d7110076a67f8d7728e49dbc7c0619d466b5374634ae9439e4b4a37",
    "tools/backtest_data_contract.py": "9af01aa7c68dbdf0f14fb7298a0ed6cfc84f5dde3b88a1894a51a566913f1629",
}
_C2_ACCEPTED_NPZ_SHA256 = {
    "ACN": "c38b4c82ecb1b8abb0c04693ac882f94fdfbbc6ff5ea978c5542da1d3aad5e3d",
    "LAC": "35028cad578d9b7e92ebb6013d3f1684ec289c7a2eb46d33c8b5e68e1faac11d",
    "MU": "f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3",
    "NVDA": "7ca961e9650b41cc98bb363eaae2e58bb767a65f376cc44493c0c15ba3c45b56",
    "TTD": "56d874e0784a1e777fd6e69d61fe3b95ae1909fad875c84afe8d0d5d39c9f217",
    "VT": "4c7ff8d9008d8a4ad271b70a1413a7c62197ad64122c6d714fcb35b959579cce",
}
_C2_ACCEPTED_FINGERPRINTS = {
    ("ACN", "SHORT"): "tradier-matrix-c2-20260725:c2c268d17c9c7f93d5a847b8a80993b722324a9916830068f4e287365eda4676",
    ("LAC", "SHORT"): "tradier-matrix-c2-20260725:1e3944ce0db63fb24c1c89a585f2409358bfc59240f310cf38db91caa7b94de0",
    ("MU", "LONG"): "tradier-matrix-c2-20260725:4b0d58d6475255bea62e6551ff4a52d0c5e97f3d695ace28f5af57adaf781c51",
    ("NVDA", "LONG"): "tradier-matrix-c2-20260725:1b934d66018a2cb2ce32bd4d68fb679e4d96e84bc3c65a27be7f514d667060f3",
    ("TTD", "SHORT"): "tradier-matrix-c2-20260725:a5ebc926259152654adb79cca58b9149da4429d05a33a0a18a41caa2dd0773a7",
    ("VT", "LONG"): "tradier-matrix-c2-20260725:f5f784d02c008a5a43eb56bdca13a5a0ea93367f6df0754824eca73a80b58cf0",
}


def _matrix_process_source_signature():
    """Content identity of code loaded by this worker.

    Mac and S1 are continuously synchronized.  A byte-identical rsync may
    replace a file or normalize its mtime while an exact run is active; mtime
    is therefore not executable identity.  Hashing bytes still fails closed on
    every real source mutation without discarding valid work after metadata-only
    synchronization.
    """
    out = []
    # Build this from the live component lists rather than the import-time
    # convenience constant. Tests, staged cutovers, and rented workers may
    # intentionally supply a different orchestration slice.
    files = list(
        dict.fromkeys(
            MATRIX_CONTRACT_FILES
            + list(C5_ADDITIONAL_CONTRACT_FILES)
            + MATRIX_ORCHESTRATION_FILES
        )
    )
    for rel in files:
        path = SBX / rel
        try:
            payload = path.read_bytes()
            out.append((rel, len(payload), hashlib.sha256(payload).hexdigest()))
        except OSError:
            out.append((rel, -1, "ABSENT"))
    return tuple(out)


_MATRIX_PROCESS_SOURCE_SIGNATURE = _matrix_process_source_signature()

# Extreme-stop packs (user 2026-07-18): channel/band extremes as the ONLY stop,
# near-entry-price stops disabled to cut churn. Engine reads dc_low_{tf}/bb_{field}_{tf}
# generically (backtest_v8_engine ~7896) so D/W TFs need no engine change.
NEAR_ENTRY_OFF = {"PARTIAL_PROFIT_LOCK_ENABLED": False, "BREAKEVEN_DC_LOW4_ENABLED": False,
                  "EXIT_PREEMPTIVE_BREAKEVEN_ENABLED": False, "MTF_ATR_TRAIL_ENABLED": False}
STOP_PACKS = {
    "NEAR_ENTRY_OFF_ONLY": dict(NEAR_ENTRY_OFF),
    "DC_FROZEN_4H": {**NEAR_ENTRY_OFF, "DC_LOW_FROZEN_STOP_ENABLED": True, "DC_LOW_FROZEN_STOP_TF": "4h", "DC_LOW_FROZEN_STOP_USE_4BAR": False},
    "DC_FROZEN_D": {**NEAR_ENTRY_OFF, "DC_LOW_FROZEN_STOP_ENABLED": True, "DC_LOW_FROZEN_STOP_TF": "D", "DC_LOW_FROZEN_STOP_USE_4BAR": False},
    "BB_FROZEN_4H": {**NEAR_ENTRY_OFF, "BB_FROZEN_STOP_ENABLED": True, "BB_FROZEN_STOP_TF": "4h", "BB_FROZEN_STOP_FIELD": "lower"},
    "BB_FROZEN_D": {**NEAR_ENTRY_OFF, "BB_FROZEN_STOP_ENABLED": True, "BB_FROZEN_STOP_TF": "D", "BB_FROZEN_STOP_FIELD": "lower"},
    "BB_FROZEN_W": {**NEAR_ENTRY_OFF, "BB_FROZEN_STOP_ENABLED": True, "BB_FROZEN_STOP_TF": "W", "BB_FROZEN_STOP_FIELD": "lower"},
}
# RIDE packs (USER 2026-07-19 band mandate): kill the 2nd-layer trend-scalping exits found by the
# ARM_LONG capture audit (GR_HTF 259pp / SRS 172pp / PEAK_GIVEBACK 133pp / DYN_SCORE 60pp /
# WT_CROSSUNDER 5m+15m), then add frozen-D stop, upper-band harvest, and bottom-band entry.
_RIDE_EXITS_OFF = {**NEAR_ENTRY_OFF, "GR_HTF_DIRECT_EXIT_ENABLED": False, "STRUCTURAL_RANGE_SHIFT_EXIT": False,
                   "PEAK_GIVEBACK_PROTECTION_ENABLED": False, "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": False,
                   "WT_CROSSUNDER_FINAL_ENABLED": False}
_RIDE_DC_D = {**_RIDE_EXITS_OFF, "DC_LOW_FROZEN_STOP_ENABLED": True, "DC_LOW_FROZEN_STOP_TF": "D", "DC_LOW_FROZEN_STOP_USE_4BAR": False}
STOP_PACKS["RIDE_EXITS_OFF"] = _RIDE_EXITS_OFF
STOP_PACKS["RIDE_DC_D"] = _RIDE_DC_D
STOP_PACKS["RIDE_DELTA_OFF"] = {**_RIDE_EXITS_OFF, "DELTA_EXIT_ENABLED": False}
STOP_PACKS["RIDE_BAND_HARVEST"] = {**_RIDE_DC_D, "LR_BAND_HARVEST_ENABLED": True, "LR_BAND_HARVEST_FRAC": 1.0}
# R2_MIN=0.0: lrL_r2_D>=0.5 co-occurs with lower-band+rising-slope on ZERO ARM bars (measured
# 2026-07-20) — the default 0.7 gate makes the band entry structurally unfireable on D.
STOP_PACKS["RIDE_BAND_FULL"] = {**_RIDE_DC_D, "LR_BAND_HARVEST_ENABLED": True, "LR_BAND_HARVEST_FRAC": 1.0,
                                 "LR_BAND_ENTRY_ENABLED": True, "LR_BAND_ENTRY_R2_MIN": 0.0,
                                 "DELTA_EXIT_ENABLED": False,
                                 "WT_DC_HTF_GATE": "none", "HTF_ALIGN_REQUIRED_TRADIER": 0}
# REGIME (USER 2026-07-20 "catch EVERY upswing, sell every downswing"): long whenever the D channel
# rises (entry anywhere below REGIME_MAX_PB, MTF gate bypassed for LR_BAND reasons), harvest the top
# band, full profit-exit on slope flip. TRIM variant harvests 50% (holds a core).
STOP_PACKS["RIDE_REGIME"] = {**STOP_PACKS["RIDE_BAND_FULL"], "LR_BAND_REGIME_ENABLED": True,
                              "LR_BAND_SLOPE_FLIP_EXIT_ENABLED": True}
STOP_PACKS["RIDE_REGIME_TRIM"] = {**STOP_PACKS["RIDE_REGIME"], "LR_BAND_HARVEST_FRAC": 0.5}
# _L: LONG-only — shorts were squatting the symbol (has_opposing_pos) and blocking every
# LONG band entry during upswings (2026-07-20 LRBAND probe finding).
STOP_PACKS["RIDE_REGIME_L"] = {**STOP_PACKS["RIDE_REGIME"], "TRADIER_LONG_ONLY_ENTRIES": True}
STOP_PACKS["RIDE_REGIME_TRIM_L"] = {**STOP_PACKS["RIDE_REGIME_TRIM"], "TRADIER_LONG_ONLY_ENTRIES": True}
# PRIO packs (2026-07-20): band entry evaluated FIRST + slope/depth sizing — the arrow-lab
# grid (ARM 5.8x b&h sized) says band+slope context must outrank the momentum scorer.
STOP_PACKS["RIDE_PRIO"] = {**STOP_PACKS["RIDE_REGIME_L"], "LR_BAND_ENTRY_PRIORITY": True,
                            "LR_BAND_ENTRY_LO": 0.6, "LR_BAND_REGIME_MAX_PB": 0.6}
STOP_PACKS["RIDE_PRIO_WIDE"] = {**STOP_PACKS["RIDE_PRIO"], "LR_BAND_REGIME_MAX_PB": 0.85,
                                 "LR_BAND_HARVEST_HI": 0.95}
# ARROW packs (2026-07-20): the ported lab decision function as the PRIMARY entry.
_ARROW_BASE = {**STOP_PACKS["RIDE_REGIME_L"], "MTF_ARROW_ENTRY_ENABLED": True, "MTF_ARROW_THETA": 0.3}
STOP_PACKS["ARROW_TH03"] = dict(_ARROW_BASE)
STOP_PACKS["ARROW_TH05"] = {**_ARROW_BASE, "MTF_ARROW_THETA": 0.5}
STOP_PACKS["ARROW_1H_HEAVY"] = {**_ARROW_BASE, "MTF_ARROW_WEIGHTS": {"1h": 0.5, "4h": 0.25, "D": 0.25}}
STOP_PACKS["ARROW_NOSLOPE"] = {**_ARROW_BASE, "MTF_ARROW_SLOPE_LAMBDA": 0.0}
# NPZ lrL_pct_b sits much higher than the lab's own channel (ARM p50=0.86) so depth-scores
# are ~0.04 — theta must be calibrated to the NPZ scale, not the lab scale (2026-07-20).
STOP_PACKS["ARROW_TH005"] = {**_ARROW_BASE, "MTF_ARROW_THETA": 0.05}
# ARROW_LAB packs (2026-07-21): the FULL lab system — confirm-triggered entry (now ported)
# + lab trail exit, competing pack exits OFF for faithfulness. Trail can close at a loss
# (lab semantics) so the noloss bypass list is extended pack-scoped.
_NOLOSS_BYPASS_PLUS_TRAIL = ['R1_', 'R2_', 'R3_HTF_FLIP', 'R4_STDEV_MACRO', 'HEDGE_FAILED', 'MTF_ATR_TRAIL', 'MTF_DC_REJECT', 'MTF_BB_REJECT', 'MTF_GR_WT_EXIT', 'GR_HTF_DIRECT_EXIT', 'LIQUIDATION', 'EMERGENCY_DC1H_BREACH', 'EMERGENCY', 'PARABOLIC_EXIT', 'GAIN_EROSION', 'STRUCTURAL_RANGE_SHIFT', 'DD_BOUNCE_STOP', 'REENTRY_BREAKOUT', 'OVERNIGHT_GAP_HEDGE_REMOVE', 'PARTIAL_PROFIT_LOCK', 'EOD_FORCE_FLAT', 'MTF_ARROW_TRAIL']
_ARROW_LAB = {**_ARROW_BASE, "MTF_ARROW_TRAIL_EXIT_ENABLED": True,
              "UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS": _NOLOSS_BYPASS_PLUS_TRAIL,
              "LR_BAND_HARVEST_ENABLED": False, "LR_BAND_SLOPE_FLIP_EXIT_ENABLED": False,
              "DC_LOW_FROZEN_STOP_ENABLED": False}
STOP_PACKS["ARROW_LAB"] = _ARROW_LAB
STOP_PACKS["ARROW_LAB_1H"] = {**_ARROW_LAB, "MTF_ARROW_WEIGHTS": {"1h": 0.5, "4h": 0.25, "D": 0.25}}
STOP_PACKS["ARROW_LAB_C1"] = {**_ARROW_LAB, "MTF_ARROW_CONFIRM_PCT": 1.0}
STOP_PACKS["ARROW_LAB_TH05"] = {**_ARROW_LAB, "MTF_ARROW_THETA": 0.5}
# ARROW_PURE (2026-07-21 morning): ARROW_LAB still churned (ARM TIM 1.6% vs lab 44%,
# 804 trades vs 426) because R1/R2/R3 default-ON exits fire on bounce-off-low entries.
# PURE = the trail is the ONLY exit, exactly the lab. Backtest-only; live untouched.
_ARROW_PURE = {**_ARROW_LAB,
               "R1_DC_LOW4_3M_EMERGENCY_ENABLED": False,
               "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED": False,
               "R3_HTF_FLIP_EXIT_ENABLED": False,
               "R3_HTF_FLIP_4H_TIER_ENABLED": False,
               "EXIT_STRUCT_DC_BREAK_ENABLED": False}
STOP_PACKS["ARROW_PURE"] = _ARROW_PURE
STOP_PACKS["ARROW_PURE_C1"] = {**_ARROW_PURE, "MTF_ARROW_CONFIRM_PCT": 1.0}
STOP_PACKS["ARROW_PURE_1H"] = {**_ARROW_PURE, "MTF_ARROW_WEIGHTS": {"1h": 0.5, "4h": 0.25, "D": 0.25}}
STOP_PACKS["ARROW_PURE_C3"] = {**_ARROW_PURE, "MTF_ARROW_CONFIRM_PCT": 3.0}
# ARROW_ONLY (2026-07-21 07:45): the PURE cells were still churned because (a) trail-state
# reset bug (fixed in tradier_manage — opened_at|entry_price key) and (b) only 42/807
# entries were arrow — GR_HTF/LR_BAND/WT_DC entries fired first and the trail chopped
# them. ONLY = arrow is the sole entry, trail the sole exit. The faithful lab replica.
_ARROW_ONLY = {**_ARROW_PURE,
               "GR_HTF_DIRECT_ENTRY_ENABLED": False,
               "LR_BAND_ENTRY_ENABLED": False,
               "LR_BAND_ENTRY_PRIORITY": False,
               "LR_BAND_REGIME_ENABLED": False,
               # WT_DC fallback suppressed via the PATH-SCOPED k5m cap (=0 blocks that path
               # only); WT_DC_ENTRY_THRESHOLD=999 blocked EVERY entry incl. arrow at the
               # engine execute gate (ARROW_ONLY HAO 0-trade forensic, 2026-07-21 08:50).
               "WT_DC_ENTRY_K5M_MAX_LONG": 0.0,
               "DELTA_EXIT_ENABLED": False,
               "EXIT_STRUCT_TF": "None",
               "LONG_STRUCT_EXIT_TF": "None",
               "LR_PCTB_D_LONG_ENTRY_ENABLED": False,
               "RZ_BREAKOUT_ENTRY_ENABLED": False}
STOP_PACKS["ARROW_ONLY"] = _ARROW_ONLY
STOP_PACKS["ARROW_ONLY_C1"] = {**_ARROW_ONLY, "MTF_ARROW_CONFIRM_PCT": 1.0}
STOP_PACKS["ARROW_ONLY_1H"] = {**_ARROW_ONLY, "MTF_ARROW_WEIGHTS": {"1h": 0.5, "4h": 0.25, "D": 0.25}}
STOP_PACKS["ARROW_ONLY_C3"] = {**_ARROW_ONLY, "MTF_ARROW_CONFIRM_PCT": 3.0}
# MU_LONG evidence 2026-07-20: 60/80 trades exited LR_BAND_SLOPE_FLIP after 0.1h at +0.0x%.
# DEADBAND arms — slope must be decisively negative AND position held first.
_DB = {**STOP_PACKS["RIDE_REGIME_L"], "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY": 0.05, "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN": 240.0}
STOP_PACKS["RIDE_DEADBAND"] = _DB
STOP_PACKS["RIDE_DEADBAND_WIDE"] = {**_DB, "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY": 0.15, "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN": 1440.0}
STOP_PACKS["RIDE_DEADBAND_HOLD"] = {**_DB, "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY": 0.10, "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN": 4320.0}
# MU_LONG 2026-07-20: the ONLY loser worse than -0.5% was STRUCT_BREAK_DC_1h_LOW (-2.66%) — a 1h
# Donchian stop closing an intact DAILY trend (EXIT_STRUCT_DC_BREAK_ENABLED, tradier_manage ~6948),
# same LTF-stop-kills-HTF-trend class as the near-entry stops. HYBRID_STRUCT_EXIT_D also fires on
# unrealized -2.6..-4.9% (LONG_STRUCT_EXIT_TF="D").
STOP_PACKS["RIDE_DB_NOSTRUCT"] = {**_DB, "EXIT_STRUCT_DC_BREAK_ENABLED": False}
STOP_PACKS["RIDE_DB_NOSTRUCT_WIDE"] = {**_DB, "EXIT_STRUCT_DC_BREAK_ENABLED": False,
                                        "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY": 0.15,
                                        "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN": 1440.0}
# ROKU wants the OPPOSITE deadband (24.5%->12.7% when loosened; -14.4pp down-absorption):
# ═══ LEAN BASELINE (USER 2026-07-20: "add switches back one by one so we never fall back to
# the 10%-of-b&h results; fix the culprits at once"). The production config STRUCTURALLY forbids
# the proven 5m-arrow system: 72h min hold + 8 trades/day cap vs a system holding minutes at
# ~9 trades/day. THIS is why every engine cell lands at ~2% of b&h. LEAN opens the throughput
# caps + entry gates and turns the trend-scalping exits off; OFAT then adds each of the 1759
# switches back ON TOP of a working baseline instead of perturbing a broken one.
LEAN_BASELINE = {
    **_RIDE_EXITS_OFF,
    "TRADIER_LONG_ONLY_ENTRIES": True,
    # throughput caps (the hard blockers)
    "TRADIER_MIN_HOLD_MINUTES": 0.0, "TRA_MIN_HOLD_MINUTES": 0.0,
    "MIN_HOLD_MINUTES_TRADIER": 0.0, "BOUNCE_TOP_MIN_HOLD_MINUTES": 0.0,
    "TRADES_PER_SYM_PER_DAY_MAX": 999, "TRADIER_REOPEN_WAIT_S": 0.0,
    # entry gates (each individually re-testable by OFAT afterwards)
    "WT_DC_HTF_GATE": "none", "HTF_ALIGN_REQUIRED_TRADIER": 0,
    "COMBINED_STOCH_GATE_TRADIER": 100, "TRADIER_ENTRY_SCORE_THRESHOLD": 0,
    "ENTRY_SCORE_THRESHOLD": 0, "WT_DC_ENTRY_THRESHOLD": 10,
    "GR_HTF_DIRECT_ENTRY_SCORE_MIN": 5.0, "MTF_ARMED_ENTRY_ENABLED": False,
    "DELTA_EXIT_ENABLED": False,
}
STOP_PACKS["LEAN_BASELINE"] = LEAN_BASELINE
STOP_PACKS["LEAN_PLUS_HOLD30"] = {**LEAN_BASELINE, "MIN_HOLD_MINUTES_TRADIER": 30.0}
STOP_PACKS["LEAN_PLUS_CAP8"] = {**LEAN_BASELINE, "TRADES_PER_SYM_PER_DAY_MAX": 8}
STOP_PACKS["LEAN_PLUS_HTFGATE"] = {**LEAN_BASELINE, "WT_DC_HTF_GATE": "4h_D"}
STOP_PACKS["LEAN_PLUS_ALIGN2"] = {**LEAN_BASELINE, "HTF_ALIGN_REQUIRED_TRADIER": 2}
STOP_PACKS["RIDE_DB_TIGHT"] = {**STOP_PACKS["RIDE_REGIME_L"], "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY": 0.0,
                                "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN": 30.0, "EXIT_STRUCT_DC_BREAK_ENABLED": False}
STOP_PACKS["ARROW_TH015"] = {**_ARROW_BASE, "MTF_ARROW_THETA": 0.15}


# TF-EXCLUSION packs (USER 2026-07-19): remove ONE timeframe from every TF-list
# knob the stocks engine consults — "does dropping this TF improve the key?"
# Config-level (live-appliable), NOT data blanking. 5m stays the bar base.
_TF_KNOBS = {
    "R2_TF_LIST": ["1h", "4h", "D"],
    "WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
    "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
    "GOLDEN_RULE_ACTIVATION_TF_LIST": ["D", "4h"],
    "GOLDEN_RULE_ENTRY_TF_LIST": ["1h", "15m", "5m"],
    "GR_V5_HTF_TFS": ["4h", "D", "W"],
    "GR_V5_LTF_TFS": ["5m", "15m", "1h"],
    "MTF_ARMED_HTF_LIST": "1h,4h,D,W",
    "STDEV_BREAKOUT_HTF_LIST": ["D", "4h"],
    "STDEV_BREAKOUT_RETEST_TF_LIST": ["1h", "15m"],
    "STDEV_BOUNCE_HTF_LIST": ["D", "4h"],
    "SQUEEZE_FIRE_TFS": ["1h", "4h"],
}


def _without_tf(val, tf):
    if isinstance(val, str):
        sep = "+" if "+" in val else ","
        parts = [x for x in val.split(sep) if x != tf]
        return sep.join(parts) if parts != val.split(sep) else None
    parts = [x for x in val if x != tf]
    return parts if len(parts) != len(val) else None


TF_EXCLUDE_PACKS = {}
for _tf in ("5m", "15m", "1h", "4h", "D", "W"):
    _ovr = {}
    for _k, _v in _TF_KNOBS.items():
        _nv = _without_tf(_v, _tf)
        if _nv is not None and _nv:
            _ovr[_k] = _nv
    if _ovr:
        TF_EXCLUDE_PACKS[f"TF_EXCLUDE_{_tf}"] = _ovr


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp():
    parts = []
    for f in STAMP_FILES:
        p = SBX / f
        parts.append(f"{f.split('.')[0]}:{hashlib.md5(p.read_bytes()).hexdigest()[:8]}" if p.exists() else f"{f}:absent")
    return "|".join(parts)


def _matrix_contract_signature(sym):
    """Cheap cache key that changes whenever a contract input changes on disk."""
    paths = [
        SBX / rel
        for rel in (
            MATRIX_CONTRACT_FILES + list(C5_ADDITIONAL_CONTRACT_FILES)
        )
    ]
    paths.append(MATRIX_RECIPE_DEPENDENCY_FILE)
    paths.append(MATRIX_NPZ_DIR / f"{sym.upper()}.npz")
    signature = []
    for path in paths:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_size, stat.st_mtime_ns))
        except OSError:
            signature.append((str(path), -1, -1))
    return tuple(signature)


def _matrix_activation_dependency_contract():
    """Canonical executable dependency slice; descriptions do not affect identity."""
    try:
        payload = json.loads(MATRIX_RECIPE_DEPENDENCY_FILE.read_text())
        rows = payload.get("paths", [])
        contract = {
            str(row["param"]): sorted(
                str(dep)
                for dep in (row.get("activation_dependencies") or [])
                if not str(dep).startswith("CONTRACT_")
            )
            for row in rows
            if isinstance(row, dict) and row.get("param")
        }
        return json.dumps(
            contract, sort_keys=True, separators=(",", ":")
        ).encode()
    except (OSError, json.JSONDecodeError, TypeError):
        return b"<ABSENT_OR_INVALID_ACTIVATION_DEPENDENCIES>"


@lru_cache(maxsize=256)
def _matrix_contract_fingerprint_cached(sym, side, signature):
    """Hash a stable input snapshot; ``signature`` invalidates the process cache."""
    del signature  # used only as the lru key
    if MATRIX_CONTRACT_VERSION == C5_MATRIX_CONTRACT_VERSION:
        npz = MATRIX_NPZ_DIR / f"{sym.upper()}.npz"
        if not npz.is_file():
            raise FileNotFoundError(
                f"c5 frozen NPZ is required for {sym.upper()}: {npz}"
            )
        return c5_contract_fingerprint(
            SBX,
            sym,
            side,
            MATRIX_CONTRACT_FILES,
            npz_sha256=hashlib.sha256(npz.read_bytes()).hexdigest(),
        )
    h = hashlib.sha256()
    h.update(
        f"{MATRIX_CONTRACT_VERSION}|{sym.upper()}|{side.upper()}|"
        f"end_exclusive={MATRIX_END_DATE}|recipe={MATRIX_RECIPE_VERSION}".encode()
    )
    h.update(_matrix_activation_dependency_contract())
    for rel in MATRIX_CONTRACT_FILES:
        path = SBX / rel
        h.update(rel.encode())
        h.update(path.read_bytes() if path.exists() else b"<ABSENT>")
    npz = MATRIX_NPZ_DIR / f"{sym.upper()}.npz"
    # Bind the logical artifact, not an installation-specific absolute path.
    # The same immutable NPZ and exact code must identify the same result on a
    # rented worker, S1, or a developer machine.
    h.update(f"matrix_npz/{sym.upper()}.npz".encode())
    h.update(npz.read_bytes() if npz.exists() else b"<ABSENT>")
    return f"{MATRIX_CONTRACT_VERSION}:{h.hexdigest()}"


def matrix_contract_fingerprint(sym, side):
    """Hash every executable/data input required by the repaired matrix contract.

    ``stamp()`` is retained for backwards-compatible cache labelling.  This stronger digest
    additionally binds the closed-HTF/re-entry contract, the exact symbol NPZ and the intended
    side.  SWITCH_MATRIX reports use it to keep every pre-fix row historical.
    """
    return _matrix_contract_fingerprint_cached(
        sym.upper(), side.upper(), _matrix_contract_signature(sym)
    )


@lru_cache(maxsize=256)
def _c2_accepted_inputs_match(sym, signature):
    """Verify that a legacy c2 receipt still names today's exact inputs."""
    del signature
    expected_npz = _C2_ACCEPTED_NPZ_SHA256.get(sym.upper())
    if not expected_npz:
        return False
    for rel, expected in _C2_ACCEPTED_SOURCE_SHA256.items():
        path = SBX / rel
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    npz = MATRIX_NPZ_DIR / f"{sym.upper()}.npz"
    return (
        npz.exists()
        and hashlib.sha256(npz.read_bytes()).hexdigest() == expected_npz
    )


def matrix_contract_fingerprints(sym, side):
    """Return every fingerprint admissible for the current exact inputs.

    The first value is the portable c3 execution fingerprint used for new
    rows.  A vetted c2 fingerprint is also returned only when its complete
    exact source/NPZ manifest still matches.  Reporting/orchestration edits
    therefore neither erase valid evidence nor weaken engine-drift checks.
    """
    sym, side = sym.upper(), side.upper()
    out = {matrix_contract_fingerprint(sym, side)}
    if MATRIX_CONTRACT_VERSION == C5_MATRIX_CONTRACT_VERSION:
        return out
    legacy = _C2_ACCEPTED_FINGERPRINTS.get((sym, side))
    if legacy and _c2_accepted_inputs_match(sym, _matrix_contract_signature(sym)):
        out.add(legacy)
    return out


def matrix_contract_matches(actual, sym, side):
    """Whether ``actual`` is valid evidence for today's exact execution inputs."""
    return bool(actual) and actual in matrix_contract_fingerprints(sym, side)


def parse_v8_result(path):
    """Parse the canonical one-line V8_RESULT into numeric telemetry."""
    p = Path(path)
    if not p.exists():
        return {}
    line = p.read_text().strip()
    if "V8_RESULT:" not in line:
        return {}
    out = {}
    for token in line.split("V8_RESULT:", 1)[1].strip().split():
        if "=" not in token:
            continue
        key, raw = token.split("=", 1)
        try:
            out[key] = float(raw)
        except ValueError:
            out[key] = raw
    return out


def matrix_run_audit(
    sym,
    side,
    trades,
    result,
    contract_fingerprint=None,
    all_trades=None,
    allow_no_real_close_control=False,
):
    """Fail closed on every structural condition needed for a truthful matrix row."""
    from backtest_data_contract import audit_ladder_result, audit_npz

    side = side.upper()
    data = audit_npz(
        sym,
        str(MATRIX_NPZ_DIR / f"{sym.upper()}.npz"),
        profile="ladder",
        start=START,
    )
    sizing = audit_ladder_result(result, side)
    structural = audit_ladder_result(result, side, require_sizing=False)
    reasons = list(data.errors)
    blocking = list(data.errors)
    if not result:
        reasons.append("missing canonical V8_RESULT telemetry")
        blocking.append("missing canonical V8_RESULT telemetry")
    if not trades:
        reasons.append("no intended-side trade/MTM record")
        blocking.append("no intended-side trade/MTM record")
    elif str(trades[0].get("entry_reason") or "") != "V8_LADDER_INITIAL_BH_SEED":
        reasons.append("first position is not the mandatory B&H seed")
        blocking.append("first position is not the mandatory B&H seed")
    no_real_close = int(float(result.get("real_closes", 0) or 0)) < 1
    if no_real_close:
        reasons.append("no real close: exit/re-entry lifecycle was not exercised")
    terminal_reentry_pending = int(
        float(result.get("reentry_pending", 0) or 0)
    )
    terminal_reclaim_pending = int(
        float(result.get("reclaim_pending", 0) or 0)
    )
    if terminal_reentry_pending:
        reasons.append(
            "mandatory re-entry is right-censored at the fixed end of data"
        )
    if terminal_reclaim_pending:
        reasons.append(
            "mandatory reclaim obligation is right-censored at the fixed end of data"
        )
    if int(float(result.get("reentry_violations", 0) or 0)) != 0:
        reasons.append("mandatory re-entry crossed its permitted overshoot")
        blocking.append("mandatory re-entry crossed its permitted overshoot")
    sizing_present = (
        int(float(result.get("sized_open_events", 0) or 0)) > 0
        and float(result.get("max_requested_mult", 0) or 0) > 0
    )
    capacity = float(result.get("strategy_capacity_usd", 0) or 0)
    max_notional = float(result.get("max_open_notional", 0) or 0)
    capacity_respected = capacity > 0 and max_notional <= capacity * 1.01
    has_capacity_clamps = (
        int(float(result.get("size_clamp_count", 0) or 0)) > 0
        or float(result.get("requested_fill_ratio", 0) or 0) < 0.90
    )
    if not structural.get("valid"):
        reasons.append("side-isolation/trade/re-entry structural contract failed")
        blocking.append("side-isolation/trade/re-entry structural contract failed")
    if not sizing_present:
        reasons.append("sizing telemetry missing")
        blocking.append("sizing telemetry missing")
    if not capacity_respected:
        reasons.append(
            f"strategy capacity exceeded or absent: max={max_notional} capacity={capacity}"
        )
        blocking.append("strategy capacity exceeded or absent")
    if has_capacity_clamps:
        # A clamp is a red result characteristic and often the very bug a knob must repair.
        # Preserve the row for matrix search, but never call it clean/promotable.
        reasons.append("capacity clamps or sub-90% requested/fill ratio observed")
    if MATRIX_CONTRACT_VERSION == C5_MATRIX_CONTRACT_VERSION:
        from c5_matrix_safety import audit_c5_matrix_safety

        c5_safety = audit_c5_matrix_safety(
            all_trades if all_trades is not None else trades,
            result,
            intended_side=side,
            capacity_usd=16000.0,
            # A fixed-window backtest can end after a valid exit but before the
            # next permitted re-entry.  That is ordinary right censoring, not a
            # forgotten obligation.  It remains acceptable only when the
            # engine reports zero overshoot violations; live violations still
            # fail immediately above and in c5_matrix_safety.
            allow_terminal_pending=True,
        )
        if not c5_safety["pass"]:
            reasons.extend(c5_safety["reasons"])
            blocking.extend(c5_safety["reasons"])
    else:
        c5_safety = None
    status = (
        "FAIL"
        if not data.valid or not structural.get("valid") or blocking
        else (
            "PASS_WITH_CAPACITY_CLAMPS"
            if has_capacity_clamps
            else ("INCOMPLETE_NO_REAL_CLOSE" if no_real_close else "PASS")
        )
    )
    if MATRIX_CONTRACT_VERSION == C5_MATRIX_CONTRACT_VERSION and (
        (no_real_close and not allow_no_real_close_control) or has_capacity_clamps
    ):
        status = "FAIL"
        blocking.append(
            "c5 requires a real close and unclamped >=90% fill lifecycle"
        )
    elif (
        MATRIX_CONTRACT_VERSION == C5_MATRIX_CONTRACT_VERSION
        and no_real_close
        and allow_no_real_close_control
        and not blocking
    ):
        # The ladder-only baseline is a comparison control, not a candidate.
        # Requiring that control to exercise an exit is circular: it prevents
        # the exit-search matrix from ever testing the first exit. Candidates
        # still fail closed on zero real closes via the default argument.
        status = "PASS_CONTROL_NO_CLOSE"
    audit = {
        "status": status,
        "contract_version": MATRIX_CONTRACT_VERSION,
        "fixed_end_exclusive": MATRIX_END_DATE,
        "frozen_npz": str(MATRIX_NPZ_DIR / f"{sym.upper()}.npz"),
        "contract_fingerprint": (
            contract_fingerprint or matrix_contract_fingerprint(sym, side)
        ),
        "symbol": sym.upper(),
        "side": side,
        "data_contract": {
            "valid": data.valid,
            "errors": list(data.errors),
            "warnings": list(data.warnings),
            "stats": data.stats,
        },
        "result_contract": sizing,
        "structural_result_contract": structural,
        "capacity_respected": capacity_respected,
        "has_capacity_clamps": has_capacity_clamps,
        "allow_no_real_close_control": bool(allow_no_real_close_control),
        "terminal_right_censored": bool(
            (terminal_reentry_pending or terminal_reclaim_pending)
            and int(float(result.get("reentry_violations", 0) or 0)) == 0
        ),
        "c5_safety": c5_safety,
        "result": result,
        "reasons": reasons,
        "code_stamp": stamp(),
        "trade_fingerprint": prs.trades_fingerprint(
            trades, contract_version=MATRIX_CONTRACT_VERSION
        ),
    }
    return audit


def collapse_intervals(intervals_list, censor_start=None):
    """Registry reconstruction is a LOWER BOUND (no git history pre-Jul-2026) — using
    its gappy intervals as a hard filter shredded 2024-2026 trades to zero (digest
    all-zeros 2026-07-19). Collapse to one interval [first evidence .. last/open).
    CENSORING: a key first seen at the registry-wide earliest snapshot has UNKNOWN
    prior membership -> assume member since window START. Keys provably added later
    keep their true start. Never-member keys still excluded."""
    if not intervals_list:
        return []
    starts = [s for s, _ in intervals_list]
    ends = [e for _, e in intervals_list]
    first = min(starts)
    if censor_start and str(first)[:10] <= str(censor_start)[:10]:
        first = START + "T00:00:00Z"
    return [(first, None if any(e is None for e in ends) else max(e for e in ends if e))]


# Change-tracked registry began 2026-07-18 (Mac cron snapshots-on-change). Every
# seed before that (ledger monthly reconstruction, s2 2026-05-08 dump, trc 06-26)
# is a censored SNAPSHOT of a long-standing universe, not a membership-change
# event — first-evidence there proves nothing about join date.
REGISTRY_LIVE_TRACKING_START = "2026-07-18"


def registry_censor_start(intervals):
    return REGISTRY_LIVE_TRACKING_START


def is_in_intervals(entry_ts, intervals_list):
    if not intervals_list: return False
    for start_iso, end_iso in intervals_list:
        try:
            start_t = datetime.strptime(start_iso.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S%z").timestamp()
            end_t = datetime.strptime(end_iso.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S%z").timestamp() if end_iso else float("inf")
            if start_t <= entry_ts <= end_t: return True
        except Exception: pass
    return False


def years_since(start):
    t0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return max(0.02, (datetime.now(timezone.utc) - t0).days / 365.25)


def free_mb():
    try:
        out = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            if ln.startswith("Mem:"):
                return int(ln.split()[6])
    except Exception:
        pass
    return 99999


def sym_years_and_bh(sym, start):
    """Per-symbol data span (years) + side-aware b&h floor (Bible §12.1), from the
    SAME NPZ the engine ran on. Klines files are unusable here (short tails, mixed
    formats) and years_since(START) lies for symbols whose data starts later (e.g.
    A.npz starts 2026-04) — gain/mo must be normalized by the ACTUAL span."""
    import numpy as np
    p = (
        MATRIX_NPZ_DIR / f"{sym}.npz"
        if CAMPAIGN == C5_MATRIX_CAMPAIGN
        else SBX / "backtest_v8" / "indicators" / f"{sym}.npz"
    )
    try:
        z = np.load(p, allow_pickle=True)
        ts = z["timestamps"]
        close = z["close_5m"] if "close_5m" in z.files else z["close"]
        t0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        mask = (ts >= t0) & (close > 0)
        if CAMPAIGN == C5_MATRIX_CAMPAIGN:
            end_ts = datetime.strptime(MATRIX_END_DATE, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            ).timestamp()
            mask &= ts < end_ts
        if mask.sum() < 2:
            return None, None
        c = close[mask]
        yrs = max(0.02, float(ts[mask][-1] - ts[mask][0]) / (365.25 * 86400))
        return yrs, float(c[-1] / c[0] - 1.0) * 100.0
    except Exception:
        return None, None


def universe_keys():
    rows = ur._load_rows()
    keys = set()
    for r in rows:
        if r.get("account") == ACCOUNT:
            side = r.get("side", "").upper()
            for s in r.get("symbols", []):
                keys.add(f"{s}_{side}")
    if not keys:
        raise SystemExit("universe_registry empty — run 'universe_registry.py seed' + rsync data/universe_history to S1")
    npz_dir = SBX / "backtest_v8" / "indicators"
    out = sorted(k for k in keys if (npz_dir / (k.rsplit("_", 1)[0] + ".npz")).exists() and (TRADES_ROOT / "__BASELINE__" / f"cell__{k.rsplit('_', 1)[0]}.jsonl").exists())
    return out


def artifact_stamp(cell_tag, sym, fallback):
    """Stamp of the code that ACTUALLY produced this sym's cached trades. Cache
    predating stamping is labeled so rows never claim newer code than ran."""
    p = TRADES_ROOT / cell_tag / f"stamp__{sym}.txt"
    if p.exists():
        return p.read_text().strip()
    return "cache-prestamp|" + fallback


def cached_override_matches(cell_dir, sym, requested):
    """True only when a cached exact run used the requested effective override.

    A code+NPZ fingerprint cannot distinguish two parameter recipes executed
    under the same tag. Reusing such a cache silently makes changed knobs
    return identical numbers. Compare parsed mappings (not JSON formatting)
    and fail closed on missing or malformed provenance.
    """
    path = Path(cell_dir) / f"override__{sym}.json"
    try:
        prior = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(prior, dict) and prior == dict(requested)


# NOT batched on purpose (measured 2026-07-21): running 4 symbols in ONE engine invocation
# costs ~29min vs ~36.7min for 4 single-symbol runs — only ~20% saved, at 1.4GB RSS vs 632MB.
# And it is not equivalent: one invocation shares capital, position slots and cross-symbol
# ranking, so a symbol's trades would differ from the single-symbol runs every existing cell
# and baseline was built from. A 20% saving is not worth cells that cannot be compared.
def run_symbol(
    sym,
    overrides,
    cell_tag,
    timeout=3600,
    min_avail=8000,
    side=None,
    require_matrix_contract=False,
    allow_no_real_close_control=False,
):
    """One faithful Tier-2 engine run.

    The repaired matrix lane passes ``side`` and ``require_matrix_contract=True``.  That mode
    seeds the B&H floor, prohibits the opposite side, validates current NPZ causality and
    refuses candidate caches/results without a complete real-close/re-entry
    lifecycle. ``allow_no_real_close_control`` is reserved for the isolated
    ladder-only comparison baseline.
    """
    side = str(side or "").upper()
    if side and side not in ("LONG", "SHORT"):
        raise ValueError(f"invalid side {side!r}")
    run_contract_fp = None
    if require_matrix_contract:
        if _matrix_process_source_signature() != _MATRIX_PROCESS_SOURCE_SIGNATURE:
            # A daemon imports the engine/audit modules once but can live for days.  Continuing
            # after one of those files changes would stamp old in-memory behavior with the new
            # on-disk fingerprint.  Exit so the watchdog starts a fresh interpreter.
            raise SystemExit(
                "matrix contract source changed after worker start; restart required"
            )
        run_contract_fp = matrix_contract_fingerprint(sym, side)
    cell_dir = TRADES_ROOT / cell_tag
    cell_dir.mkdir(parents=True, exist_ok=True)
    jsonl = cell_dir / f"cell__{sym}.jsonl"
    result_file = cell_dir / f"v8result__{sym}.txt"
    if require_matrix_contract and jsonl.exists():
        prior = load_matrix_run_audit(cell_tag, sym)
        expected_fp = run_contract_fp
        override_matches = cached_override_matches(cell_dir, sym, overrides)
        if (
            not prior
            or prior.get("contract_fingerprint") != expected_fp
            or not override_matches
        ):
            # Preserve, but never reuse, a cache from another code/NPZ/override
            # contract. The old runner reused identical tags after source or
            # recipe changes and silently relabelled stale trades as current.
            # PID+nanosecond suffix keeps concurrent evidence recoverable.
            mismatch = (
                "contract"
                if not prior or prior.get("contract_fingerprint") != expected_fp
                else "override"
            )
            suffix = f".{mismatch}_mismatch.{os.getpid()}.{time.time_ns()}"
            for stale in (
                jsonl,
                result_file,
                cell_dir / f"audit__{sym}.json",
                cell_dir / f"stamp__{sym}.txt",
                cell_dir / f"override__{sym}.json",
            ):
                if stale.exists():
                    stale.rename(stale.with_name(stale.name + suffix))
    if not jsonl.exists():
        while free_mb() < min_avail:
            time.sleep(30)
        ovr = cell_dir / f"override__{sym}.json"
        ovr.write_text(json.dumps(overrides))
        env = dict(os.environ)
        env.update({"V8_OVERRIDE_FILE": str(ovr), "V8_TRADES_OUT_DIR": str(cell_dir),
                    "V8_TRADES_RUN_ID": "cell", "V8_SWEEP_MODE": "1",
                    "V8_BACKTEST_OVERRIDE_PRECEDENCE": "1",
                    "V8_RATE_GUARD_DISABLED": "1", "V8_BACKTEST_DISK_CACHE": "1",
                    "V8_RESULT_FILE": str(result_file)})
        if require_matrix_contract:
            env["V8_BACKTEST_END_DATE"] = MATRIX_END_DATE
            env["V8_MATRIX_CONTRACT_VERSION"] = MATRIX_CONTRACT_VERSION
        if side:
            env.update({
                "V8_LADDER_ONLY_SIDE": side.lower(),
                "V8_LADDER_FORCE_INITIAL_SIDE": side,
            })
            env.pop("V8_SIDE_GATE_DISABLED", None)
        else:
            # Legacy universe-comparison callers intentionally collect both sides.  They are
            # historical only and never satisfy the repaired matrix contract.
            env["V8_SIDE_GATE_DISABLED"] = "1"
        cmd = ["timeout", str(timeout), "nice", "-n", "18", PY,
               str(SBX / "backtest_v8_engine.py"), "--mode", MODE, "--account", ACCOUNT,
               "--start", START, "--capital", "10000.0", "--symbols", sym]
        if require_matrix_contract:
            cmd += ["--npz-dir", str(MATRIX_NPZ_DIR)]
        # Keep the engine's actual stdout/stderr beside the cell receipt.  A
        # previous DEVNULL sink reduced every engine crash to the useless text
        # ``rc=1 no JSONL`` and left a 24/7 fleet retrying the same fault.  The
        # log is diagnostic only and is never accepted as result evidence.
        engine_log = cell_dir / f"engine__{sym}.log"
        with engine_log.open("wb") as diagnostic:
            rc = subprocess.run(
                cmd,
                cwd=str(SBX),
                env=env,
                stdout=diagnostic,
                stderr=subprocess.STDOUT,
            ).returncode
        if jsonl.exists() or rc == 0:
            (cell_dir / f"stamp__{sym}.txt").write_text(stamp())
        if not jsonl.exists():
            if rc == 0:
                jsonl.touch()  # clean run, genuinely zero trades — record honestly
            else:
                # engine died/killed/timed out before writing trades — MUST NOT record
                # a fake "tested, 0 trades" cell (dedupe would make it permanent).
                # None = caller skips this sym; the next cron pass retries.
                print(f"[engine-fail] {cell_tag}/{sym} rc={rc} no JSONL — skipped, will retry", flush=True)
                return None
        if require_matrix_contract:
            final_contract_fp = matrix_contract_fingerprint(sym, side)
            if (
                _matrix_process_source_signature() != _MATRIX_PROCESS_SOURCE_SIGNATURE
                or final_contract_fp != run_contract_fp
            ):
                suffix = f".contract_changed_during_run.{os.getpid()}.{time.time_ns()}"
                for stale in (
                    jsonl,
                    result_file,
                    cell_dir / f"stamp__{sym}.txt",
                    cell_dir / f"override__{sym}.json",
                ):
                    if stale.exists():
                        stale.rename(stale.with_name(stale.name + suffix))
                raise SystemExit(
                    "matrix contract changed during engine run; result quarantined"
                )
    by_side = {"LONG": [], "SHORT": []}
    if jsonl.exists():
        for ln in jsonl.read_text().splitlines():
            try:
                t = json.loads(ln)
            except Exception:
                continue
            if t.get("pnl_pct") is None:
                continue
            trade_side = str(t.get("position_side") or t.get("side", "")).upper()
            if trade_side in by_side:
                by_side[trade_side].append(t)
    if require_matrix_contract:
        intended = by_side.get(side, [])
        audit = matrix_run_audit(
            sym, side, intended, parse_v8_result(result_file),
            contract_fingerprint=run_contract_fp,
            all_trades=by_side["LONG"] + by_side["SHORT"],
            allow_no_real_close_control=allow_no_real_close_control,
        )
        audit_path = cell_dir / f"audit__{sym}.json"
        audit_path.write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n")
        if audit["status"] == "FAIL":
            print(
                f"[matrix-contract-fail] {cell_tag}/{sym}_{side}: "
                + "; ".join(audit["reasons"]),
                flush=True,
            )
            return None
    return by_side


def load_matrix_run_audit(cell_tag, sym):
    path = TRADES_ROOT / cell_tag / f"audit__{sym}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def hold_metrics(trades, years, m):
    """time_in_mkt_pct + capture_vs_bh (USER 2026-07-19: the ball-dropped metrics)."""
    sec = sum(max(0, (t.get("exit_ts") or t.get("entry_ts", 0)) - t.get("entry_ts", 0)) for t in trades)
    m["time_in_mkt_pct"] = round(sec / (years * 365.25 * 86400) * 100.0, 2) if years else None
    bh = m.get("bh_pct")
    m["capture_vs_bh"] = round(m["acc_gain_pct"] / bh, 3) if bh is not None and abs(bh) > 1.0 else None
    return m


def key_metrics(rets, years, bh_long, side):
    months = years * 12.0
    acc = sum(rets)
    gain_mo = acc / months if months else 0.0
    bh = (bh_long if side == "LONG" else (-bh_long if bh_long is not None else None))
    bh_mo = (bh / months) if bh is not None else None
    return {"pool_sharpe": (mg.pool_sharpe(rets) if len(rets) >= 2 else 0.0),
            "trades": len(rets), "acc_gain_pct": round(acc, 4),
            "gain_per_mo": round(gain_mo, 4),
            "bh_pct": (round(bh, 2) if bh is not None else None),
            "bh_per_mo": (round(bh_mo, 4) if bh_mo is not None else None),
            "delta_gain_mo_vs_bh": (round(gain_mo - bh_mo, 4) if bh_mo is not None else None)}


def capital_key_metrics(
    trades,
    years,
    bh_long_price_pct,
    side,
    result=None,
    capital=10000.0,
    benchmark_deployed=2000.0,
):
    """Side-isolated metrics with equal $2k strategy/B&H capital.

    Exact-engine fills intentionally vary from the $2k seed up to the ladder
    capacity.  Comparing their raw dollars with a $2k B&H leg rewards leverage,
    not path quality.  Preserve the realised dollar P&L and relative sizing, but
    scale the complete ledger so its arithmetic mean deployed notional per
    completed trade is exactly ``benchmark_deployed``.  B&H deploys the same
    amount, so its percentage return is the side-aware price return less one
    round-trip cost.

    ``capital`` remains in the signature for caller compatibility; it is not a
    performance denominator under this accounting contract.
    """
    del capital
    months = years * 12.0
    pnl_usd = sum(float(t.get("pnl_usd", 0) or 0) for t in trades)
    deployed_by_trade = []
    for trade in trades:
        open_events = [
            event
            for event in (trade.get("action_events") or [])
            if str(event.get("action") or "").upper()
            in {
                "OPEN",
                "QUICK_OPEN",
                "HEDGE_OPEN",
                "AUGMENT",
                "QUICK_AUGMENT",
                "HEDGE_AUGMENT",
                "REENTER",
                "QUICK_REENTER",
                "REENTRY",
                "QUICK_REENTRY",
            }
        ]
        deployed = sum(
            abs(
                float(event.get("executed_qty", 0) or 0)
                * float(event.get("price", 0) or 0)
            )
            for event in open_events
        )
        if deployed <= 0:
            deployed = abs(
                float(trade.get("executed_open_qty", 0) or 0)
                * float(trade.get("entry_price", 0) or 0)
            )
        if deployed <= 0 and abs(float(trade.get("pnl_pct", 0) or 0)) > 1e-12:
            # Historical exact c1-c4 ledgers predate explicit quantity/action
            # receipts but persist both dollar and percentage P&L from the same
            # entry-value denominator. Reconstruct that denominator exactly.
            deployed = abs(
                float(trade.get("pnl_usd", 0) or 0)
                / (float(trade["pnl_pct"]) / 100.0)
            )
        if deployed <= 0:
            raise ValueError(
                "capital accounting requires a positive executed entry notional "
                "for every completed trade"
            )
        deployed_by_trade.append(deployed)
    if not deployed_by_trade:
        raise ValueError("capital accounting requires at least one completed trade")
    average_deployed = sum(deployed_by_trade) / len(deployed_by_trade)
    normalization_factor = benchmark_deployed / average_deployed
    normalized_pnl_usd = pnl_usd * normalization_factor
    gain = normalized_pnl_usd / benchmark_deployed * 100.0
    raw_bh = (
        bh_long_price_pct
        if side.upper() == "LONG"
        else (-bh_long_price_pct if bh_long_price_pct is not None else None)
    )
    rt_cost_pct = next(
        (float(t.get("round_trip_cost_pct")) for t in trades if t.get("round_trip_cost_pct") is not None),
        0.06,
    )
    bh = (
        raw_bh - rt_cost_pct
        if raw_bh is not None
        else None
    )
    gain_mo = gain / months if months else 0.0
    bh_mo = bh / months if bh is not None and months else None
    normalized_returns = [
        float(trade.get("pnl_usd", 0) or 0) / average_deployed * 100.0
        for trade in trades
    ]
    tim_key = f"time_in_mkt_{side.lower()}_pct"
    tim = result.get(tim_key) if result else None
    # Keep the computed C5 metrics at native float precision all the way into
    # ``param_cells``.  Rounding here used to collapse genuinely different
    # action schedules onto the same 4-decimal matrix result.  Presentation
    # layers may format these values, but the evidence store must not discard
    # information before collision/uniqueness checks run.
    return {
        "pool_sharpe": (
            mg.pool_sharpe(normalized_returns)
            if len(normalized_returns) >= 2
            else 0.0
        ),
        "max_dd_pct": _max_dd_pct(normalized_returns),
        "trades": len(trades),
        "acc_gain_pct": gain,
        "gain_per_mo": gain_mo,
        "bh_pct": bh,
        "bh_per_mo": bh_mo,
        "delta_gain_mo_vs_bh": (
            gain_mo - bh_mo if bh_mo is not None else None
        ),
        "time_in_mkt_pct": (float(tim) if tim is not None else None),
        "capture_vs_bh": (
            gain / bh if bh is not None and abs(bh) > 1e-12 else None
        ),
        "capital_accounting_version": CAPITAL_ACCOUNTING_VERSION,
        "benchmark_deployed_usd": float(benchmark_deployed),
        "average_deployed_usd": average_deployed,
        "capital_normalization_factor": normalization_factor,
        "raw_pnl_usd": pnl_usd,
        "normalized_pnl_usd": normalized_pnl_usd,
    }


def _max_dd_pct(rets):
    cum = peak = 0.0
    dd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def write_canonical(label, rets_by_key, years, extra=None, once=True):
    if once:
        marker = TRADES_ROOT / f".pooled_written__{label.replace('/', '_')}"
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(now_iso())
    rets_by_key = {k: v for k, v in rets_by_key.items() if v}
    if sum(len(v) for v in rets_by_key.values()) < 2:
        return
    row = mg.standard_metric_set(rets_by_key, years)
    row["max_dd_pct"] = round(max((_max_dd_pct(v) for v in rets_by_key.values()), default=0.0), 2)
    row.update({"label": label, "status": "ok"})
    row.update(extra or {})
    mg.write_sharpe_row(RESULTS_DIR / f"persym_campaign_{CAMPAIGN}.csv", row, mode=MODE)


def cmd_baseline(args):
    keys = universe_keys()
    syms = sorted({k.rsplit("_", 1)[0] for k in keys})
    # USER 2026-07-20: focus the full 1759-param OFAT on named keys first (ARM/MU/ROKU/NVDA),
    # then generalize to the universe. PSC_SYMS=MU,ARM,... overrides the universe selection.
    _focus = [s.strip().upper() for s in os.environ.get("PSC_SYMS", "").split(",") if s.strip()]
    if _focus:
        syms = _focus
    if args.syms_limit:
        syms = syms[:args.syms_limit]
    years = years_since(START)
    st = stamp()
    con = prs.connect()
    print(f"[baseline] {len(syms)} syms / {len(keys)} universe keys, start={START}, stamp={st}", flush=True)
    intervals = ur.membership_intervals(ACCOUNT)
    censor = registry_censor_start(intervals)
    pooled_uni, pooled_all, pooled_random = {}, {}, {}
    for i, sym in enumerate(syms):
        by_side = run_symbol(sym, {}, "__BASELINE__", timeout=args.timeout, min_avail=args.min_avail)
        if by_side is None:
            continue
        row_st = artifact_stamp("__BASELINE__", sym, st)
        sym_years, bh_long = sym_years_and_bh(sym, START)
        sym_years = sym_years or years
        for side, trades in by_side.items():
            bkey = f"{sym}_{side}"
            sym_intervals = collapse_intervals(intervals.get((sym, side), []), censor)
            uni_trades = [t for t in trades if is_in_intervals(t.get("entry_ts", 0), sym_intervals)]
            uni_rets = [float(t["pnl_pct"]) for t in uni_trades]
            all_rets = [float(t["pnl_pct"]) for t in trades]
            if sym_intervals:
                uni_start = max(START, str(sym_intervals[0][0])[:10])
                uni_years, uni_bh = sym_years_and_bh(sym, uni_start)
                uni_years = uni_years or sym_years
                m_uni = hold_metrics(uni_trades, uni_years, key_metrics(uni_rets, uni_years, uni_bh if uni_bh is not None else bh_long, side))
                prs.upsert_baseline(con, {"mode": MODE, "account": ACCOUNT, "symbol": sym, "side": side, "campaign": CAMPAIGN, "ts": now_iso(), "window_start": uni_start, "years": round(uni_years, 3), **m_uni, "overrides_json": "{}", "stamp": row_st, "source_file": f"persym_campaign/{CAMPAIGN}/__BASELINE__"})
                m_all = hold_metrics(trades, sym_years, key_metrics(all_rets, sym_years, bh_long, side))
                prs.upsert_baseline(con, {"mode": MODE, "account": ACCOUNT, "symbol": sym, "side": side, "campaign": CAMPAIGN + "_random", "ts": now_iso(), "window_start": START, "years": round(sym_years, 3), **m_all, "overrides_json": "{}", "stamp": row_st, "source_file": f"persym_campaign/{CAMPAIGN}/__BASELINE__"})
                pooled_uni[bkey] = uni_rets
                pooled_random[bkey] = all_rets
            else:
                m_off = hold_metrics(trades, sym_years, key_metrics(all_rets, sym_years, bh_long, side))
                prs.upsert_baseline(con, {"mode": MODE, "account": ACCOUNT, "symbol": sym, "side": side, "campaign": CAMPAIGN + "_offuni", "ts": now_iso(), "window_start": START, "years": round(sym_years, 3), **m_off, "overrides_json": "{}", "stamp": row_st, "source_file": f"persym_campaign/{CAMPAIGN}/__BASELINE__"})
                pooled_all[bkey] = all_rets
        con.commit()
        if (i + 1) % 10 == 0:
            print(f"[baseline] {i+1}/{len(syms)} done", flush=True)
    write_canonical(f"{CAMPAIGN}__BASELINE__universe", pooled_uni, years, {"overrides_json": "{}", "verdict": ""})
    write_canonical(f"{CAMPAIGN}__BASELINE__random", pooled_random, years, {"overrides_json": "{}", "verdict": ""})
    write_canonical(f"{CAMPAIGN}__BASELINE__off_universe", pooled_all, years, {"overrides_json": "{}", "verdict": ""})
    con.close()
    print("[baseline] DONE", flush=True)


def load_params(manifest_path, limit=0):
    man = json.loads(Path(manifest_path).read_text())["params"]
    params = [(n, v["test_values"]) for n, v in man.items() if v.get("sweepable") and v.get("test_values")]
    # USER 2026-07-21: the WT1-cross trigger is ALWAYS the first param tested in any matrix.
    kw = ["WT_3M_FORCE_OPEN", "SHORT", "LONG", "STOP", "FROZEN", "EXIT", "R1_", "R2_", "DC_", "BB_",
          "GIVEBACK", "PEAK", "HOLD", "REENTRY", "COOLDOWN", "ENTRY", "WT_DC", "GR_", "SCORE", "ALIGN", "MIN_IND"]

    def prio(n):
        u = n.upper()
        return next((i for i, k in enumerate(kw) if k in u), len(kw))
    params.sort(key=lambda kv: (prio(kv[0]), kv[0]))
    return params[:limit] if limit else params


def cmd_ofat(args):
    keys = universe_keys()
    syms = sorted({k.rsplit("_", 1)[0] for k in keys})
    _focus = [s.strip().upper() for s in os.environ.get("PSC_SYMS", "").split(",") if s.strip()]
    if _focus:
        syms = _focus
    if args.syms_limit:
        syms = syms[:args.syms_limit]
    years = years_since(START)
    st = stamp()
    con = prs.connect()
    base = {r[0] + "_" + r[1]: r[2] for r in con.execute(
        "SELECT symbol, side, gain_per_mo FROM key_baseline WHERE mode=? AND campaign=?", (MODE, CAMPAIGN))}
    if not base:
        raise SystemExit("no baselines — run 'baseline' first")
    stale = con.execute("SELECT COUNT(*) FROM key_baseline WHERE mode=? AND campaign=? AND bh_pct IS NULL",
                        (MODE, CAMPAIGN)).fetchone()[0]
    if stale:
        raise SystemExit(f"OFAT refused: {stale} baseline rows lack bh_pct (written by a pre-fix pass) — "
                         "the next cron baseline pass recomputes them from cached runs, then OFAT proceeds")
    cells = [("STOP_PACK", name, cfg) for name, cfg in STOP_PACKS.items()]
    cells += [("TF_EXCLUDE", name.replace("TF_EXCLUDE_", ""), cfg) for name, cfg in TF_EXCLUDE_PACKS.items()]
    manifest = args.manifest or str(SBX / f"data/param_sweep_manifest_{MODE}.json")
    for pname, values in load_params(manifest, args.param_limit):
        for v in values:
            cells.append((pname, str(v), {pname: v}))
    print(f"[ofat] {len(cells)} cells x {len(syms)} syms, stamp={st}", flush=True)
    intervals = ur.membership_intervals(ACCOUNT)
    censor = registry_censor_start(intervals)
    for ci, (pname, vlabel, ovr) in enumerate(cells):
        tag = f"{pname}__{vlabel}".replace("/", "_")[:120]
        pooled = {}
        for sym in syms:
            need = [s for s in ("LONG", "SHORT") if f"{sym}_{s}" in keys and
                    not prs.already_tested(con, MODE, sym, s, pname, vlabel)]
            if not need:
                continue
            by_side = run_symbol(sym, ovr, tag, timeout=args.timeout, min_avail=args.min_avail)
            if by_side is None:
                continue
            row_st = artifact_stamp(tag, sym, st)
            sym_years, bh_long = sym_years_and_bh(sym, START)
            sym_years = sym_years or years
            for side in need:
                sym_intervals = collapse_intervals(intervals.get((sym, side), []), censor)
                uni_trades = [t for t in by_side[side] if is_in_intervals(t.get("entry_ts", 0), sym_intervals)]
                uni_rets = [float(t["pnl_pct"]) for t in uni_trades]
                if sym_intervals:
                    u_start = max(START, str(sym_intervals[0][0])[:10])
                    u_years, u_bh = sym_years_and_bh(sym, u_start)
                    u_years = u_years or sym_years
                else:
                    u_years, u_bh = sym_years, bh_long
                m = hold_metrics(uni_trades, u_years, key_metrics(uni_rets, u_years, u_bh if u_bh is not None else bh_long, side))
                bkey = sym + "_" + side
                dvb = (m["gain_per_mo"] - base[bkey]) if bkey in base else None
                try:
                    vnum = float(vlabel)
                except ValueError:
                    vnum = None
                prs.insert_cell(con, {"mode": MODE, "symbol": sym, "side": side, "campaign": CAMPAIGN,
                                      "param": pname, "value_json": vlabel, "value_num": vnum, **m,
                                      "delta_vs_baseline_gain_mo": (round(dvb, 4) if dvb is not None else None),
                                      "ts": now_iso(), "overrides_json": json.dumps(ovr), "stamp": row_st,
                                      "source_file": f"persym_campaign/{CAMPAIGN}/{tag}"})
                pooled[bkey] = uni_rets
            con.commit()
        if pooled:
            write_canonical(f"{CAMPAIGN}__{tag}", pooled, years,
                            {"overrides_json": json.dumps(ovr), "verdict": ""})
        print(f"[ofat {ci+1}/{len(cells)}] {tag} pooled_trades={sum(len(v) for v in pooled.values())}", flush=True)
        if args.max_hours and (time.time() - START_TS) > args.max_hours * 3600:
            print("[ofat] max-hours reached, exiting cleanly (resumable)", flush=True)
            break
    con.close()


def bh_capture_scoreboard(con, top_n=40):
    rows = con.execute(
        "SELECT symbol, side, campaign, gain_per_mo, bh_per_mo, trades, pool_sharpe, time_in_mkt_pct, years "
        "FROM key_baseline WHERE mode=? AND campaign NOT LIKE '%_offuni' ORDER BY bh_per_mo DESC", (MODE,)).fetchall()
    best_cell = {}
    for r in con.execute("SELECT symbol, side, param, value_json, gain_per_mo, trades FROM param_cells WHERE mode=?", (MODE,)):
        k = (r[0], r[1])
        if k not in best_cell or (r[4] or -1e9) > (best_cell[k]["cell_gain_mo"] or -1e9):
            best_cell[k] = {"cell": f"{r[2]}={r[3]}", "cell_gain_mo": r[4], "cell_trades": r[5]}
    seen, out = set(), []
    for symbol, side, campaign, gain_mo, bh_mo, trades, ps, tim, years in rows:
        key = (symbol, side)
        if key in seen or (bh_mo or 0) <= 0:
            continue
        seen.add(key)
        bc = best_cell.get(key, {})
        cap = round((gain_mo or 0.0) / bh_mo, 4) if bh_mo else None
        cell_cap = round((bc.get("cell_gain_mo") or 0.0) / bh_mo, 4) if bh_mo and bc.get("cell_gain_mo") is not None else None
        out.append({"key": f"{symbol}_{side}", "campaign": campaign, "bh_per_mo": round(bh_mo, 3),
                    "gain_per_mo": round(gain_mo or 0.0, 4), "capture_vs_bh": cap, "trades": trades,
                    "pool_sharpe": round(ps or 0.0, 3), "time_in_mkt_pct": tim, "years": years,
                    "best_cell": bc.get("cell"), "best_cell_gain_mo": bc.get("cell_gain_mo"),
                    "best_cell_capture": cell_cap})
        if len(out) >= top_n:
            break
    return out


def cmd_report(args):
    con = prs.connect()
    out = prs.export_xlsx(con, SBX / "data" / "reports" / "PARAM_BASELINE_STOCKS.xlsx", MODE, None)
    rel = prs.relevance_ranking(con, MODE)
    inert = prs.inert_params(con, MODE)
    nb = con.execute("SELECT COUNT(*) FROM key_baseline WHERE mode=?", (MODE,)).fetchone()[0]
    nc = con.execute("SELECT COUNT(*) FROM param_cells WHERE mode=?", (MODE,)).fetchone()[0]
    board = bh_capture_scoreboard(con)
    (SBX / "data" / "reports" / "stocks_bh_capture.json").write_text(json.dumps({"ts": now_iso(), "mode": MODE, "rows": board}))
    md = [f"# PERSYM CAMPAIGN DIGEST — {now_iso()}",
          f"baselines={nb} param_cells={nc} xlsx={out}",
          "## B&H WINNERS — capture scoreboard (gain/mo vs b&h/mo; capture<1 = DEFECT per Bible §12.1)",
          "| key | b&h/mo | gain/mo | capture | trades | tim% | best cell | cell gain/mo | cell capture |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += ["| {key} | {bh_per_mo} | {gain_per_mo} | {capture_vs_bh} | {trades} | {time_in_mkt_pct} | {best_cell} | {best_cell_gain_mo} | {best_cell_capture} |".format(**d) for d in board[:25]]
    md += ["## Top relevance (max |Δ gain/mo| across keys)"]
    md += [f"- {d['param']}: max_spread={d['max_spread']:.3f} keys_moved={d['n_keys_moved']}/{d['n_keys']}" for d in rel[:20]]
    md += ["## INERT (wiring check required before pruning)"] + [f"- {p}" for p in inert]
    p = SBX / "data" / "reports" / "persym_campaign_digest.md"
    p.write_text("\n".join(md))
    print(str(p))
    con.close()


def cmd_compare_universe(args):
    """Curated point-in-time universe vs 'random' (all keys) on identical baseline runs."""
    con = prs.connect()
    years = years_since(START)
    out = {}
    for camp, tag in ((CAMPAIGN, "universe"), (CAMPAIGN + "_random", "random"), (CAMPAIGN + "_offuni", "off_universe")):
        rows = con.execute("SELECT pool_sharpe, trades, acc_gain_pct, gain_per_mo, delta_gain_mo_vs_bh "
                           "FROM key_baseline WHERE mode=? AND campaign=?", (MODE, camp)).fetchall()
        if rows:
            out[tag] = {"n_keys": len(rows), "mean_pool_sharpe": round(sum(r[0] or 0 for r in rows) / len(rows), 4),
                        "trades": sum(r[1] or 0 for r in rows), "acc_gain_pct": round(sum(r[2] or 0 for r in rows), 2),
                        "gain_per_mo_sum": round(sum(r[3] or 0 for r in rows), 3),
                        "mean_delta_vs_bh_mo": round(sum(r[4] or 0 for r in rows if r[4] is not None) / max(1, sum(1 for r in rows if r[4] is not None)), 4)}
    print(json.dumps({"years": round(years, 2), **out}, indent=1))
    (SBX / "data" / "reports" / "universe_vs_random_baseline.json").write_text(json.dumps(out))
    con.close()


START_TS = time.time()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("baseline", "ofat", "report", "compare-universe"):
        s = sub.add_parser(name)
        s.add_argument("--timeout", type=int, default=3600)
        s.add_argument("--min-avail", type=int, default=8000)
        s.add_argument("--manifest", default=None)
        s.add_argument("--param-limit", type=int, default=0)
        s.add_argument("--syms-limit", type=int, default=0)
        s.add_argument("--max-hours", type=float, default=0)
    args = ap.parse_args()
    {"baseline": cmd_baseline, "ofat": cmd_ofat, "report": cmd_report,
     "compare-universe": cmd_compare_universe}[args.cmd](args)


if __name__ == "__main__":
    main()
