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

MODE = "tradier"
ACCOUNT = "trb"
START = "2024-01-01"
CAMPAIGN = os.environ.get("PSC_CAMPAIGN", "stocks_baseline_v1")
TRADES_ROOT = Path(os.environ.get("PSC_TRADES_ROOT", str(SBX / "data" / "sweep_results" / f"persym_campaign_{CAMPAIGN}_trades")))
RESULTS_DIR = SBX / "data" / "sweep_results"
STAMP_FILES = ["backtest_v8_engine.py", "tradier_manage.py", "wt_dc_delta.py", "config_tradier.py"]

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
    p = SBX / "backtest_v8" / "indicators" / f"{sym}.npz"
    try:
        z = np.load(p, allow_pickle=True)
        ts = z["timestamps"]
        close = z["close_5m"] if "close_5m" in z.files else z["close"]
        t0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        mask = (ts >= t0) & (close > 0)
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


# NOT batched on purpose (measured 2026-07-21): running 4 symbols in ONE engine invocation
# costs ~29min vs ~36.7min for 4 single-symbol runs — only ~20% saved, at 1.4GB RSS vs 632MB.
# And it is not equivalent: one invocation shares capital, position slots and cross-symbol
# ranking, so a symbol's trades would differ from the single-symbol runs every existing cell
# and baseline was built from. A 20% saving is not worth cells that cannot be compared.
def run_symbol(sym, overrides, cell_tag, timeout=3600, min_avail=8000):
    """One faithful Tier-2 engine run for one symbol (both sides). Returns trades-by-side."""
    cell_dir = TRADES_ROOT / cell_tag
    cell_dir.mkdir(parents=True, exist_ok=True)
    jsonl = cell_dir / f"cell__{sym}.jsonl"
    result_file = cell_dir / f"v8result__{sym}.txt"
    if not jsonl.exists():
        while free_mb() < min_avail:
            time.sleep(30)
        ovr = cell_dir / f"override__{sym}.json"
        ovr.write_text(json.dumps(overrides))
        env = dict(os.environ)
        env.update({"V8_OVERRIDE_FILE": str(ovr), "V8_TRADES_OUT_DIR": str(cell_dir),
                    "V8_TRADES_RUN_ID": "cell", "V8_SWEEP_MODE": "1",
                    "V8_RATE_GUARD_DISABLED": "1", "V8_BACKTEST_DISK_CACHE": "1",
                    "V8_RESULT_FILE": str(result_file),
                    # both sides simulated on purpose: off-universe sides feed the
                    # curated-vs-random comparison (_offuni rows) at zero extra compute
                    "V8_SIDE_GATE_DISABLED": "1"})
        cmd = ["timeout", str(timeout), "nice", "-n", "18", PY,
               str(SBX / "backtest_v8_engine.py"), "--mode", MODE, "--account", ACCOUNT,
               "--start", START, "--capital", "10000.0", "--symbols", sym]
        rc = subprocess.run(cmd, cwd=str(SBX), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
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
    by_side = {"LONG": [], "SHORT": []}
    if jsonl.exists():
        for ln in jsonl.read_text().splitlines():
            try:
                t = json.loads(ln)
            except Exception:
                continue
            if t.get("pnl_pct") is None:
                continue
            side = str(t.get("position_side") or t.get("side", "")).upper()
            if side in by_side:
                by_side[side].append(t)
    return by_side


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
