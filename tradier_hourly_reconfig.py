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

import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
CAND_DIR = ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates"
OUT_DIR_TRB = ROOT / "data" / "hourly_reconfig" / "trb"
OUT_DIR_TRC = ROOT / "data" / "hourly_reconfig" / "trc"

SYMBOLS_LONG_TRB  = ROOT / "symbols_trb_long.json"
SYMBOLS_SHORT_TRB = ROOT / "symbols_trb_short.json"
SYMBOLS_LONG_TRC  = ROOT / "symbols_trc_long.json"
SYMBOLS_SHORT_TRC = ROOT / "symbols_trc_short.json"

WINDOW_DAYS = 7.0
MIN_TRADES_FOR_OPINION = 5
RATE_GUARD_DISABLED = "1"
WSHARPE_TRADE_FLOOR = 0.7  # wsharpe below this → disable trading in overrides (all accounts)


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

    z = np.load(str(npz_path))
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

    best_ws = -1e9
    best_tag: str = ""
    best_ovr: Dict = {}
    best_n = 0
    best_rts: List[Tuple[float, int]] = []

    for i, (tag, ovr) in enumerate(cands, 1):
        run_id = f"reconf_{sym}_{tag}_{i:03d}"
        ws, n, rts = run_candidate(sym, ovr, run_dir, run_id, npz, start_ts, end_ts)
        if n >= MIN_TRADES_FOR_OPINION and ws > best_ws:
            best_ws = ws
            best_tag = tag
            best_ovr = ovr
            best_n = n
            best_rts = rts

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
        "raw_returns": raw_returns[:200],  # keep recent sample for audit
        "updated_at": int(time.time()),
    }


def run_account(account: str, long_file: Path, short_file: Path, out_dir: Path) -> None:
    long_syms = set(load_symbols_file(long_file))
    short_syms = set(load_symbols_file(short_file))
    all_syms = sorted(long_syms | short_syms)
    all_syms = [s for s in all_syms if (NPZ_DIR / f"{s}.npz").exists()]

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

    print(f"[{account}] {len(all_syms)} symbols (long={len(long_syms)}, short={len(short_syms)})")
    t0 = time.time()
    updated = 0

    for sym in all_syms:
        can_long = sym in long_syms
        can_short = sym in short_syms
        try:
            result = run_symbol(sym, can_long, can_short, run_root)
            if result:
                best_tag, entry = result
                side_tag = best_tag.split("_")[1] if "_" in best_tag else "BOTH"
                key = f"{sym}_{side_tag}"
                prev_ws = existing.get(key, {}).get("wsharpe", -1)
                # wsharpe < 0.7: config is still written (no error), but trading disabled.
                if entry["wsharpe"] < WSHARPE_TRADE_FLOOR:
                    entry["overrides"].update({
                        "LONG_ENABLED": False, "SHORT_ENABLED": False,
                        "WT_DC_LONG_ENABLED": False, "WT_DC_SHORT_ENABLED": False,
                    })
                    entry["_trade_gate"] = f"BELOW_FLOOR wsharpe={entry['wsharpe']:.4f}<{WSHARPE_TRADE_FLOOR}"
                existing[key] = entry
                updated += 1
                if abs(entry["wsharpe"] - prev_ws) > 0.05:
                    gate = entry.get("_trade_gate", "")
                    print(f"  {sym} {key}: wsharpe {prev_ws:.3f} → {entry['wsharpe']:.3f} "
                          f"trades={entry['trades']} pnl={entry['total_pnl_pct']:+.2f}%"
                          f"{' [GATED]' if gate else ''}")
        except Exception as e:
            print(f"  {sym} EXC: {e}")

    with active_path.open("w") as f:
        json.dump(existing, f, indent=2)

    _prune_old_engine_runs(out_dir / "_engine_runs", keep=2)
    elapsed = time.time() - t0
    print(f"[{account}] done: {updated} updated, {len(existing)} total entries, {elapsed:.1f}s")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="trb,trc",
                    help="comma-separated accounts (default trb,trc)")
    ap.add_argument("--daemon", action="store_true",
                    help="run continuously every hour")
    ap.add_argument("--interval-minutes", type=int, default=60)
    args = ap.parse_args()

    accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
    account_map = {
        "trb": (SYMBOLS_LONG_TRB, SYMBOLS_SHORT_TRB, OUT_DIR_TRB),
        "trc": (SYMBOLS_LONG_TRC, SYMBOLS_SHORT_TRC, OUT_DIR_TRC),
    }

    def run_once():
        for acct in accounts:
            if acct not in account_map:
                print(f"Unknown account: {acct}")
                continue
            long_f, short_f, out_d = account_map[acct]
            run_account(acct, long_f, short_f, out_d)

    if args.daemon:
        print(f"[tradier_hourly_reconfig] daemon started, interval={args.interval_minutes}min")
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
