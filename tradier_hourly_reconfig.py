#!/usr/bin/env python3
"""tradier_hourly_reconfig — rolling 7-day per-symbol config optimizer for trb/trc.

Runs on S2 (tradier sweeps machine). Every hour:
  1. For each symbol in symbols_trb_{long,short}.json (and trc variants):
       a. Load the symbol's NPZ.
       b. Run candidate configs (BEST baseline + per_sym winner from _candidates/).
       c. Evaluate on the last 7 days of NPZ data.
       d. Pick winner by time-weighted pool_sharpe (staleness-penalised).
  2. Write data/hourly_reconfig/trb/active_config.json  (and trc variant).
     Format: {"AAPL_LONG": {"winning_tag": "...", "wsharpe": X, "trades": N, "overrides": {...}}, ...}

The active_config.json is read by tradier_manage.py's _get_tradier_sym_cfg() at runtime
(mtime-cached, no restart required).
"""
from __future__ import annotations

def _bible125_stamp():
    import hashlib as _h, os as _os
    base = _os.path.dirname(_os.path.abspath(__file__))
    parts = []
    for tag, f in (("eng", "backtest_v8_engine.py"), ("tm", "tradier_manage.py"), ("wdd", "wt_dc_delta.py"), ("cfgt", "config_tradier.py")):
        try:
            parts.append(tag + ":" + _h.md5(open(_os.path.join(base, f), "rb").read()).hexdigest()[:10])
        except Exception:
            parts.append(tag + ":?")
    return "+".join(parts)



import json
import math
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
# 2026-05-18: redirected from BANNED v8_quick_engine (OPUS_VOMIT) to vec_engine_v1
# via quick_engine_compat shim. Modern engine is sample-floor-honest + gated by
# metrics_guard. Per-trade JSONL contract preserved (V8_TRADES_OUT_DIR side-channel).
from quick_engine_compat import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
CAND_DIR = ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates"
OUT_DIR_TRB = ROOT / "data" / "hourly_reconfig" / "trb"
OUT_DIR_TRC = ROOT / "data" / "hourly_reconfig" / "trc"

# Fix B 2026-05-18: global per_sym_active_config.json — first-writer-wins shared
# state across all daemons (crypto flz_hourly_reconfig + tradier_hourly_reconfig).
# Live tradier_manage._get_tradier_sym_cfg() at lines 468-488 already reads it.
GLOBAL_PER_SYM_CFG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"

SYMBOLS_LONG_TRB  = ROOT / "symbols_trb_long.json"
SYMBOLS_SHORT_TRB = ROOT / "symbols_trb_short.json"
SYMBOLS_LONG_TRC  = ROOT / "symbols_trc_long.json"
SYMBOLS_SHORT_TRC = ROOT / "symbols_trc_short.json"

WINDOW_DAYS = 14.0          # 2 weeks — tradier stocks trade infrequently; 7D gave 0 trades
MIN_TRADES_FOR_OPINION = 10  # 2026-07-08 GAINMO: raised 2→10. Opinions from 2 trades are noise-grade (metrics_guard floor is 30; a 7-14d stock window cannot honestly reach 30, so 10 = compromise — anything below simply keeps the baseline, which is the honest default). Sub-30 remains [DIAGNOSTIC]-grade by CLAUDE.md.
RATE_GUARD_DISABLED = "1"
WSHARPE_TRADE_FLOOR = 0.0   # lowered from 0.7; disabling on <0 only — small sample can't hit 0.7


_LONG_ONLY_OVR = {"LONG_ENABLED": True,  "SHORT_ENABLED": False,
                  "WT_DC_LONG_ENABLED": True,  "WT_DC_SHORT_ENABLED": False}
_SHORT_ONLY_OVR = {"LONG_ENABLED": False, "SHORT_ENABLED": True,
                   "WT_DC_LONG_ENABLED": False, "WT_DC_SHORT_ENABLED": True}
_BOTH_OVR = {"LONG_ENABLED": True, "SHORT_ENABLED": True,
             "WT_DC_LONG_ENABLED": True, "WT_DC_SHORT_ENABLED": True}


def _prune_old_engine_runs(engine_runs_dir: Path, keep: int = 2) -> None:
    """Delete all but the `keep` most recent timestamped engine-run directories."""
    dirs = sorted(engine_runs_dir.glob("*"), key=lambda p: p.name) if engine_runs_dir.exists() else []
    for old in dirs[:-keep] if keep > 0 else dirs:
        try:
            shutil.rmtree(old)
        except Exception as e:
            print(f"  [prune] could not remove {old}: {e}")


def load_symbols_file(path: Path) -> List[str]:
    if path.exists():
        return json.loads(path.read_text())
    return []


def load_candidates(sym: str) -> List[Tuple[str, Dict]]:
    """Load per-sym winner JSONs from _candidates/ as sweep candidates."""
    cands: List[Tuple[str, Dict]] = []
    if not CAND_DIR.exists():
        return cands
    for p in CAND_DIR.glob(f"trb_{sym}_*_winner.json"):
        try:
            c = json.loads(p.read_text())
            tag = f"extra_{p.stem}"
            cands.append((tag, {k: v for k, v in c.items() if not k.startswith("_")}))
        except Exception:
            pass
    return cands


def load_safe_override(path: Path) -> Dict:
    with path.open() as f:
        d = json.load(f)
    return {k: v for k, v in d.items() if not k.startswith("_")}


def build_candidates(sym: str, can_long: bool, can_short: bool) -> List[Tuple[str, Dict]]:
    """Build the candidate list: side-specific baselines + per-sym winner JSONs."""
    cands: List[Tuple[str, Dict]] = []
    base_long_label = "baseline_LONG" if can_long else "baseline_SHORT"

    if can_long:
        cands.append(("baseline_LONG", dict(_LONG_ONLY_OVR)))
    if can_short:
        cands.append(("baseline_SHORT", dict(_SHORT_ONLY_OVR)))
    if can_long and can_short:
        cands.append(("baseline_BOTH", dict(_BOTH_OVR)))

    # Fix C 2026-05-18: layer NEW knob clusters on top of each side-baseline so
    # they are evaluated for both LONG and SHORT permutations. Knob names verified
    # against config_tradier.py; HEDGE_* + DC_BB_D_REV omitted (stocks have no
    # same-symbol hedge; DC_BB_D_BREAK_REVERSE is crypto-only).
    TRADIER_NEW_KNOB_CLUSTERS = [
        ("R1R2_strict", {
            # Live R1 is prohibited after the 2026-08-03 churn incident.
            # Research sweeps may test it in isolated override files, but the
            # hourly live writer must never resurrect it.
            "R1_DC_LOW4_3M_EMERGENCY_ENABLED": False,
            "R1_NEWBORN_WINDOW_MIN": -1.0,
            "R1_USE_DC_4BAR": True,
            "R1_TF": "5m",
            "WT_VEL_DECEL_RATIO": 0.4,
            "WT_VEL_USE_DECEL_RATIO_ONLY": True,
        }),
        ("R1R2_loose", {
            "R1_DC_LOW4_3M_EMERGENCY_ENABLED": False,
            "R1_NEWBORN_WINDOW_MIN": -1.0,
            "R1_USE_DC_4BAR": False,
            "WT_VEL_DECEL_RATIO": 0.7,
            "WT_VEL_USE_DECEL_RATIO_ONLY": False,
        }),
        ("RULE_A_on", {
            "BREAKOUT_RETEST_ARMED_ENABLED": True,
            "WT_3M_FORCE_OPEN_ENABLED": False,
            "HTF_TREND_VETO_ENABLED": True,
        }),
        ("PPL_v2_tight", {
            "PARTIAL_PROFIT_LOCK_ENABLED": True,
            "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER": 0.3,
            "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER": 0.5,
            "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER": 0.02,
            "PARTIAL_PROFIT_LOCK_FRAC_TRADIER": 0.625,
        }),
        ("PPL_v2_loose", {
            "PARTIAL_PROFIT_LOCK_ENABLED": True,
            "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER": 0.6,
            "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER": 0.9,
            "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER": 0.05,
            "PARTIAL_PROFIT_LOCK_FRAC_TRADIER": 0.5,
        }),
        ("NOLOSS_WT5of5", {
            "NOLOSS_BYPASS_WT_5OF5_ENABLED": True,
            "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 5,
        }),
        ("STDEV_MACRO_veto", {
            "STDEV_MACRO_ENTRY_VETO_ENABLED": True,
            "STDEV_MACRO_AUGMENT_VETO_ENABLED": True,
            "STDEV_MACRO_R4_EXIT_ENABLED": False,
        }),
        ("DUP_GUARD_GAIN", {
            "DUP_GUARD_USE_GAIN_GATE": True,
            "DUP_GUARD_GAIN_MULTIPLIER": 0.5,
        }),
    ]
    side_bases: List[Tuple[str, Dict]] = []
    if can_long:
        side_bases.append(("LONG", dict(_LONG_ONLY_OVR)))
    if can_short:
        side_bases.append(("SHORT", dict(_SHORT_ONLY_OVR)))
    for side_label, side_base in side_bases:
        for tag, deltas in TRADIER_NEW_KNOB_CLUSTERS:
            fused = dict(side_base)
            fused.update(deltas)
            cands.append((f"{tag}_{side_label}", fused))

    # Load per-sym sweeper-found winners from _candidates/
    extra = load_candidates(sym)
    cands.extend(extra)
    return cands


def npz_window(z: dict, days: float) -> Tuple[int, int, float]:
    """Return (start_ts, end_ts, actual_years) for filtering recent trades."""
    if "timestamps" in z:
        ts_arr = z["timestamps"]
    else:
        first_key = next(iter(z))
        ts_arr = z[first_key]
    if len(ts_arr) < 2:
        return 0, 0, 0.0
    end_ts = int(ts_arr[-1])
    start_ts = end_ts - int(days * 86400)
    actual_years = days / 365.25
    return start_ts, end_ts, actual_years


def time_weighted_sharpe(returns_with_ts: List[Tuple[float, int]], ref_ts: int) -> float:
    if len(returns_with_ts) < 2:
        return 0.0
    rs = np.array([r for r, _ in returns_with_ts], dtype=np.float64)
    ts = np.array([t for _, t in returns_with_ts], dtype=np.float64)
    days_ago = np.maximum(0.0, (ref_ts - ts) / 86400.0)
    w = np.power(2.0, -days_ago)
    w_sum = w.sum()
    if w_sum <= 0:
        return 0.0
    wmean = float((rs * w).sum() / w_sum)
    wvar = float((w * (rs - wmean) ** 2).sum() / w_sum)
    wstd = math.sqrt(max(wvar, 0.0))
    return (wmean / wstd) if wstd > 1e-12 else 0.0


def run_candidate(sym: str, ovr: Dict, run_dir: Path, run_id: str,
                  npz: dict, start_ts: int, end_ts: int) -> Tuple[float, int, List[Tuple[float, int]]]:
    """Returns (wsharpe, n_trades, [(pnl_pct, exit_ts), ...])."""
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = RATE_GUARD_DISABLED
    cfg = QuickConfig()
    cfg.MODE = "tradier"
    for k, v in ovr.items():
        if not k.startswith("_"):
            try:
                setattr(cfg, k, v)
            except Exception:
                pass
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] {sym} {run_id}: {e}")
        return 0.0, 0, []

    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rts: List[Tuple[float, int]] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    ets = int(rec.get("exit_ts", 0) or 0)
                    if start_ts <= ets <= end_ts:
                        rts.append((float(rec.get("pnl_pct", 0.0)), ets))
                except Exception:
                    pass

    if len(rts) < MIN_TRADES_FOR_OPINION:
        return 0.0, len(rts), rts
    ws = time_weighted_sharpe(rts, end_ts)
    return ws, len(rts), rts


def run_symbol(sym: str, can_long: bool, can_short: bool,
               run_root: Path) -> Optional[Tuple[str, Dict]]:
    """Run all candidates for a symbol. Return (winning_tag, result_dict) or None."""
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists():
        return None

    # [2026-07-03] allow_pickle: NPZs regenerated ~Jun 16 contain object arrays — the default
    # False made EVERY symbol fail ("Object arrays cannot be loaded"), freezing live per_sym
    # configs at 2026-06-15 while mtimes kept refreshing. Trusted local files only.
    z = np.load(str(npz_path), allow_pickle=True)
    npz = {k: z[k] for k in z.files}
    z.close()

    start_ts, end_ts, _ = npz_window(npz, WINDOW_DAYS)
    if start_ts == 0:
        return None

    cands = build_candidates(sym, can_long, can_short)
    if not cands:
        return None

    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)

    # 2026-07-08 GAINMO (USER): winner = max summed gain among wsharpe>0 candidates
    # (churn law — unconstrained gain-max selects fee-bleed churn); wsharpe-best fallback
    # when no candidate is sharpe-positive.
    best_ws = -1e9
    best_gain = -1e18
    best_tag: str = ""
    best_ovr: Dict = {}
    best_n = 0
    best_rts: List[Tuple[float, int]] = []
    fb_ws = -1e9
    fb = None

    for i, (tag, ovr) in enumerate(cands, 1):
        run_id = f"reconf_{sym}_{tag}_{i:03d}"
        ws, n, rts = run_candidate(sym, ovr, run_dir, run_id, npz, start_ts, end_ts)
        if n < MIN_TRADES_FOR_OPINION:
            continue
        gain = sum(r for r, _ in rts)
        if ws > 0.0 and gain > best_gain:
            best_gain = gain
            best_ws = ws
            best_tag = tag
            best_ovr = ovr
            best_n = n
            best_rts = rts
        if ws > fb_ws:
            fb_ws = ws
            fb = (ws, tag, ovr, n, rts)

    if not best_tag and fb is not None:
        best_ws, best_tag, best_ovr, best_n, best_rts = fb

    if not best_tag:
        return None

    raw_returns = [r for r, _ in best_rts]
    total_pnl = sum(raw_returns)
    return best_tag, {
        "winning_tag": best_tag,
        "wsharpe": round(float(best_ws), 4),
        "trades": best_n,
        "total_pnl_pct": round(total_pnl, 4),
        "overrides": {k: v for k, v in best_ovr.items() if not k.startswith("_")},
        "settings_stamp": _bible125_stamp(),
        "raw_returns": raw_returns[:200],  # keep recent sample for audit
        "updated_at": int(time.time()),
    }


def run_account(account: str, long_file: Path, short_file: Path, out_dir: Path,
               priority_syms: Optional[List[str]] = None,
               only_syms: Optional[List[str]] = None) -> None:
    long_syms = set(load_symbols_file(long_file))
    short_syms = set(load_symbols_file(short_file))
    all_syms = sorted(long_syms | short_syms)
    all_syms = [s for s in all_syms if (NPZ_DIR / f"{s}.npz").exists()]

    if only_syms:
        all_syms = [s for s in all_syms if s in set(only_syms)]

    # Process priority symbols first (e.g. open positions that need immediate config update)
    if priority_syms:
        pset = set(priority_syms)
        all_syms = [s for s in priority_syms if s in set(all_syms)] + \
                   [s for s in all_syms if s not in pset]

    run_root = out_dir / "_engine_runs" / str(int(time.time()))
    run_root.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    active_path = out_dir / "active_config.json"
    existing: Dict = {}
    if active_path.exists():
        try:
            existing = json.loads(active_path.read_text())
        except Exception:
            pass

    label = f"priority={priority_syms[:4]}…" if priority_syms else "all"
    print(f"[{account}] {len(all_syms)} symbols (long={len(long_syms)}, short={len(short_syms)}) [{label}]")
    t0 = time.time()
    updated = 0

    for sym in all_syms:
        can_long = sym in long_syms
        can_short = sym in short_syms
        try:
            result = run_symbol(sym, can_long, can_short, run_root)
            if result:
                best_tag, entry = result
                # Derive side from the override dict — split("_")[1] was returning "trb"
                # for extra_trb_SYM_* tags from load_candidates. Use LONG/SHORT_ENABLED.
                ovr = entry.get("overrides", {})
                l_on = ovr.get("LONG_ENABLED", True)
                s_on = ovr.get("SHORT_ENABLED", True)
                if l_on and not s_on:
                    write_sides = ["LONG"]
                elif s_on and not l_on:
                    write_sides = ["SHORT"]
                else:
                    write_sides = ["LONG", "SHORT"]  # BOTH — write under both keys
                if entry["wsharpe"] < WSHARPE_TRADE_FLOOR:
                    entry["overrides"].update({
                        "LONG_ENABLED": False, "SHORT_ENABLED": False,
                        "WT_DC_LONG_ENABLED": False, "WT_DC_SHORT_ENABLED": False,
                    })
                    entry["_trade_gate"] = f"BELOW_FLOOR wsharpe={entry['wsharpe']:.4f}<{WSHARPE_TRADE_FLOOR}"
                for side_tag in write_sides:
                    key = f"{sym}_{side_tag}"
                    prev_ws = existing.get(key, {}).get("wsharpe", -1)
                    existing[key] = entry
                    if abs(entry["wsharpe"] - prev_ws) > 0.05:
                        gate = entry.get("_trade_gate", "")
                        print(f"  {sym} {key}: wsharpe {prev_ws:.3f} → {entry['wsharpe']:.3f} "
                              f"trades={entry['trades']} pnl={entry['total_pnl_pct']:+.2f}%"
                              f"{' [GATED]' if gate else ''}")
                updated += 1
        except Exception as e:
            print(f"  {sym} EXC: {e}")

    with active_path.open("w") as f:
        json.dump(existing, f, indent=2)

    # 2026-05-18 USER MANDATE: chart-before-live gate. Stage to
    # _pending_per_sym_active_config.json + render per-(sym,side) HTML for review.
    # Promotion to live per_sym_active_config.json is via promote_pending_per_sym.py.
    try:
        from _pending_review_chart import (
            stash_trade_jsonl, render_pending_chart, write_manifest, upsert_pending_cfg,
        )
    except Exception as _exc:
        print(f"[{account}] _pending_review_chart import FAILED: {_exc}")
        _prune_old_engine_runs(out_dir / "_engine_runs", keep=2)
        elapsed = time.time() - t0
        print(f"[{account}] done: {updated} updated, {len(existing)} total entries, {elapsed:.1f}s")
        return

    cycle_id = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    n_added, n_skipped_present, total = upsert_pending_cfg(
        new_entries=existing, source_daemon="tradier_hourly_reconfig",
        source_account=account, source_cycle=cycle_id,
    )
    print(f"[{account}] pending per_sym_active_config: +{n_added} new (sym,side); {n_skipped_present} skipped (already pending); total={total}")

    # Render charts for ALL entries this cycle. We re-scan the engine run_root
    # for each (sym, side, winning_tag) JSONL.
    manifest_entries = []
    n_charts = 0
    n_chart_errors = 0
    for sym_side, decision in existing.items():
        try:
            sym_part, side_part = sym_side.rsplit("_", 1)
        except ValueError:
            continue
        winning_tag = decision.get("winning_tag", "")
        # tradier daemon's run_id pattern from line ~283: f"reconf_{sym}_{tag}_{i:03d}"
        sym_run_dir = run_root / sym_part
        records = []
        if sym_run_dir.exists():
            for jp in sym_run_dir.glob(f"reconf_{sym_part}_{winning_tag}_*__{sym_part}.jsonl"):
                with jp.open() as _jf:
                    for _ln in _jf:
                        try:
                            _r = json.loads(_ln)
                            if (_r.get("side") or "").upper() == side_part.upper():
                                records.append(_r)
                        except Exception:
                            pass
        if not records:
            manifest_entries.append((sym_part, side_part, "tradier",
                                     Path(f"<no trades for {sym_side}>"), decision))
            continue
        stash_trade_jsonl(sym_part, side_part, records)
        try:
            html_path = render_pending_chart(sym_part, side_part, "tradier",
                                             records, decision)
            manifest_entries.append((sym_part, side_part, "tradier", html_path, decision))
            n_charts += 1
        except Exception as _exc:
            n_chart_errors += 1
            print(f"[{account}] chart render FAILED {sym_part} {side_part}: {_exc}")
    write_manifest(manifest_entries)
    print(f"[{account}] pending charts rendered: {n_charts} ok, {n_chart_errors} errors.")

    _prune_old_engine_runs(out_dir / "_engine_runs", keep=2)
    elapsed = time.time() - t0
    print(f"[{account}] done: {updated} updated, {len(existing)} total entries, {elapsed:.1f}s")


def _get_open_positions_trb() -> List[str]:
    """Return symbols with open trb positions from today's decisions JSONL."""
    try:
        import re
        from datetime import datetime, timezone
        dec_dir = ROOT / "data" / "decisions"
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        dec_file = dec_dir / f"decisions_trb_{today}.jsonl"
        if not dec_file.exists():
            files = sorted(dec_dir.glob("decisions_trb_*.jsonl"))
            dec_file = files[-1] if files else None
        if not dec_file:
            return []
        last_event: dict = {}
        with open(dec_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    pk = rec.get("position_key", "")
                    if "trb:" not in pk:
                        continue
                    last_event[pk] = rec.get("action", "")
                except Exception:
                    pass
        open_syms = []
        for pk, action in last_event.items():
            if "CLOSE" not in action and "HOLD" in action or "OPEN" in action or "WAIT" in action:
                sym = pk.split(":")[-1]
                sym = re.sub(r"_(LONG|SHORT)$", "", sym)
                if sym not in open_syms:
                    open_syms.append(sym)
        return open_syms
    except Exception:
        return []


def main() -> int:
    import argparse
    global WINDOW_DAYS
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="trb", help="comma-separated accounts (default trb)")
    ap.add_argument("--daemon", action="store_true", help="run continuously every hour")
    ap.add_argument("--interval-minutes", type=int, default=60)
    ap.add_argument("--window-days", type=float, default=14.0, help="window in days (default 14.0)")
    ap.add_argument("--priority-syms", default="", help="comma-separated symbols to process first (overrides auto-detect)")
    ap.add_argument("--syms", default="", help="comma-separated symbols to process (skip all others)")
    args = ap.parse_args()
    WINDOW_DAYS = args.window_days
    os.environ["RATE_GUARD_DISABLED"] = RATE_GUARD_DISABLED

    accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
    account_map = {
        "trb": (SYMBOLS_LONG_TRB, SYMBOLS_SHORT_TRB, OUT_DIR_TRB),
        "trc": (SYMBOLS_LONG_TRC, SYMBOLS_SHORT_TRC, OUT_DIR_TRC),
    }
    only_syms = [s.strip() for s in args.syms.split(",") if s.strip()] or None

    def _priority_for(acct: str) -> List[str]:
        if args.priority_syms:
            return [s.strip() for s in args.priority_syms.split(",") if s.strip()]
        if acct == "trb":
            return _get_open_positions_trb()
        return []

    def run_once():
        for acct in accounts:
            if acct not in account_map:
                print(f"Unknown account: {acct}")
                continue
            long_f, short_f, out_d = account_map[acct]
            prio = _priority_for(acct)
            if prio:
                print(f"[{acct}] priority symbols: {prio}")
            run_account(acct, long_f, short_f, out_d,
                        priority_syms=prio, only_syms=only_syms)

    if args.daemon:
        print(f"[tradier_hourly_reconfig] daemon started, interval={args.interval_minutes}min, "
              f"window={WINDOW_DAYS}d, min_trades={MIN_TRADES_FOR_OPINION}")
        while True:
            t_cycle = time.time()
            run_once()
            elapsed = time.time() - t_cycle
            sleep_sec = max(0, args.interval_minutes * 60 - elapsed)
            print(f"[tradier_hourly_reconfig] sleeping {sleep_sec/60:.1f}min until next cycle")
            time.sleep(sleep_sec)
    else:
        run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
