#!/usr/bin/env python3
"""build_symbol_configs — per-symbol config archive from hourly_reconfig outputs.

User directive 2026-05-01: "config folder per sym with config_yyyymmddhhmmss
and the ultimate config.py for that symbol according to 7D hourly test".

For each cycle dir in data/hourly_reconfig/<acct>/runs/<cycle>/:
  - groups trade JSONLs by (sym, side, variation)
  - computes weighted pool_sharpe per variation (last-day=1.0, 6d-ago=1/64)
  - picks the winning variation for each (sym, side)
  - writes:
      data/symbol_configs/<SYM>/config_<cycle>.json   (snapshot per cycle)
      data/symbol_configs/<SYM>/BEST.json             (latest winner — the "ultimate")
      data/symbol_configs/<SYM>/HISTORY.csv           (append-row per cycle/side)

Run modes:
  python3 build_symbol_configs.py --once       # process all cycles since last marker
  python3 build_symbol_configs.py --cycle DIR  # process one cycle dir
  python3 build_symbol_configs.py --backfill   # process EVERY cycle (idempotent)

All metrics route through metrics_guard. Sub-floor results carry [DIAGNOSTIC] tag.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import metrics_guard

HOURLY_BASE = ROOT / "data" / "hourly_reconfig"
OUT_BASE = ROOT / "data" / "symbol_configs"
MARKER = OUT_BASE / "_last_processed.txt"

# Per CLAUDE.md: 7D test weights last day at 1.0, 6 days ago at 1/64.
def _weight_for_day(days_ago: float) -> float:
    return 2.0 ** (-max(0.0, days_ago))


def _parse_filename(stem: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse '<SYM>__<SIDE>__<variation>__<filesym>' from stem.
    Returns (sym, side, variation, filesym) or None."""
    parts = stem.split("__")
    if len(parts) < 4:
        return None
    # Last token is the trade-file's symbol; everything before parses as
    # SYM, SIDE, variation (variation may contain underscores but no `__`).
    filesym = parts[-1]
    sym = parts[0]
    side = parts[1] if len(parts) > 1 else "UNKNOWN"
    variation = "__".join(parts[2:-1]) or "default"
    return sym, side, variation, filesym


def _weighted_pool_sharpe(trades: List[Dict], cycle_ts_unix: float) -> Tuple[float, Dict]:
    """Compute pool_sharpe weighted so today's trades count 1.0 and 6 days ago 1/64.
    Returns (weighted_sharpe, stats_dict)."""
    if not trades:
        return 0.0, {"trades": 0}
    pnls = []
    weights = []
    pos_count = 0
    for t in trades:
        pnl = float(t.get("pnl_pct", 0) or 0)
        pnls.append(pnl)
        if pnl > 0:
            pos_count += 1
        ts = float(t.get("exit_ts", t.get("entry_ts", 0)) or 0)
        if ts > 0:
            days_ago = max(0.0, (cycle_ts_unix - ts) / 86400.0)
        else:
            days_ago = 7.0
        weights.append(_weight_for_day(days_ago))
    if not pnls:
        return 0.0, {"trades": 0}
    sw = sum(weights) or 1.0
    mean = sum(p * w for p, w in zip(pnls, weights)) / sw
    var = sum(w * (p - mean) ** 2 for p, w in zip(pnls, weights)) / sw
    sd = math.sqrt(max(var, 0.0))
    sharpe = (mean / sd) if sd > 0 else 0.0
    eq = []
    cum = 0.0
    peak = -1e18
    max_dd = 0.0
    for p in pnls:
        cum += p
        eq.append(cum)
        if cum > peak: peak = cum
        if peak - cum > max_dd: max_dd = peak - cum
    return sharpe, {
        "trades": len(pnls),
        "weighted_pool_sharpe": round(sharpe, 4),
        "wr": round(pos_count / len(pnls), 4),
        "total_gain_pct": round(sum(pnls), 4),
        "max_dd_pct": round(max_dd, 4),
        "best_trade_pct": round(max(pnls), 4),
        "worst_trade_pct": round(min(pnls), 4),
        "first_entry_ts": min(int(t.get("entry_ts", 0) or 0) for t in trades),
        "last_exit_ts": max(int(t.get("exit_ts", 0) or 0) for t in trades),
    }


def _load_trade_jsonl(path: Path) -> List[Dict]:
    out = []
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _cycle_unix_ts(cycle_dir: Path) -> float:
    """Parse YYYYMMDD_HHMMSS from dir name → unix ts."""
    name = cycle_dir.name
    try:
        dt = datetime.strptime(name, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return cycle_dir.stat().st_mtime if cycle_dir.exists() else time.time()


def process_cycle(cycle_dir: Path, account: str) -> Dict[str, int]:
    """Process one cycle dir; emit per-symbol sidecars. Returns counts."""
    if not cycle_dir.is_dir():
        return {"syms": 0, "files": 0}
    cycle_id = cycle_dir.name
    cycle_ts = _cycle_unix_ts(cycle_dir)
    grouped: Dict[Tuple[str, str], List[Tuple[str, Path]]] = defaultdict(list)  # (sym,side) → [(variation, path)]
    n_files = 0
    for p in cycle_dir.glob("*.jsonl"):
        n_files += 1
        parsed = _parse_filename(p.stem)
        if not parsed:
            continue
        sym, side, variation, filesym = parsed
        if sym != filesym:
            # Different sym in name vs file-tail (e.g. extra_btc_*); skip secondary files,
            # only count the file where the strategy IS for that primary sym.
            continue
        grouped[(sym, side)].append((variation, p))
    n_syms_processed = 0
    for (sym, side), variants in grouped.items():
        # Compute weighted pool_sharpe per variation; pick winner.
        scored = []
        for variation, p in variants:
            trades = _load_trade_jsonl(p)
            sharpe, stats = _weighted_pool_sharpe(trades, cycle_ts)
            scored.append({
                "variation": variation,
                "weighted_pool_sharpe": sharpe,
                "stats": stats,
                "source_file": str(p.relative_to(ROOT)),
            })
        scored.sort(key=lambda x: x["weighted_pool_sharpe"], reverse=True)
        if not scored:
            continue
        winner = scored[0]
        snapshot = {
            "cycle_id": cycle_id,
            "cycle_ts_unix": int(cycle_ts),
            "cycle_iso": datetime.fromtimestamp(cycle_ts, tz=timezone.utc).isoformat(),
            "sym": sym,
            "side": side,
            "account": account,
            "winner_variation": winner["variation"],
            "winner_weighted_pool_sharpe": winner["weighted_pool_sharpe"],
            "winner_tier": metrics_guard.tier_name(winner["weighted_pool_sharpe"]),
            "winner_stats": winner["stats"],
            "winner_source": winner["source_file"],
            "all_variations_scored": [
                {"variation": s["variation"], "weighted_pool_sharpe": s["weighted_pool_sharpe"], "trades": s["stats"].get("trades", 0)}
                for s in scored
            ],
            "_meta": {
                "produced_by": "build_symbol_configs.py",
                "produced_utc": datetime.utcnow().isoformat() + "Z",
                "weighting": "2.0 ** (-days_ago) — today=1.0, 6d-ago=1/64",
                "metrics_guard_audit": "weighted_pool_sharpe is per-trade Sharpe with time-decay weights — NOT annualized, ±5 cap not applied to weighted variant. Use winner_tier for quality band.",
            },
        }
        sym_dir = OUT_BASE / sym
        sym_dir.mkdir(parents=True, exist_ok=True)
        # 1. Per-cycle snapshot.
        (sym_dir / f"config_{cycle_id}__{side}.json").write_text(json.dumps(snapshot, indent=2))
        # 2. BEST.json (latest winner, per-side merge).
        best_path = sym_dir / "BEST.json"
        best_doc = {}
        if best_path.exists():
            try:
                best_doc = json.loads(best_path.read_text())
            except Exception:
                best_doc = {}
        best_doc["sym"] = sym
        best_doc["last_updated_utc"] = datetime.utcnow().isoformat() + "Z"
        best_doc.setdefault("by_side", {})[side] = snapshot
        # If both sides present, mark which has higher weighted_pool_sharpe.
        sides = best_doc.get("by_side", {})
        if sides:
            best_side = max(sides, key=lambda s: float(sides[s].get("winner_weighted_pool_sharpe", 0)))
            best_doc["best_side"] = best_side
            best_doc["best_weighted_pool_sharpe"] = float(sides[best_side].get("winner_weighted_pool_sharpe", 0))
            best_doc["best_tier"] = metrics_guard.tier_name(best_doc["best_weighted_pool_sharpe"])
        best_path.write_text(json.dumps(best_doc, indent=2))
        # 3. HISTORY.csv append.
        hist_path = sym_dir / "HISTORY.csv"
        is_new = not hist_path.exists()
        with hist_path.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["cycle_id", "cycle_iso", "side", "account",
                            "winner_variation", "weighted_pool_sharpe", "tier",
                            "trades", "wr", "total_gain_pct", "max_dd_pct"])
            stats = winner["stats"]
            w.writerow([cycle_id, snapshot["cycle_iso"], side, account,
                        winner["variation"], round(winner["weighted_pool_sharpe"], 4),
                        snapshot["winner_tier"], stats.get("trades", 0),
                        round(stats.get("wr", 0), 4),
                        round(stats.get("total_gain_pct", 0), 2),
                        round(stats.get("max_dd_pct", 0), 4)])
        n_syms_processed += 1
    return {"syms": n_syms_processed, "files": n_files, "cycle_id": cycle_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", help="process one cycle dir")
    ap.add_argument("--once", action="store_true", help="process cycles since last marker")
    ap.add_argument("--backfill", action="store_true", help="process EVERY cycle in hourly_reconfig (idempotent)")
    ap.add_argument("--account", help="restrict to one account")
    args = ap.parse_args()
    OUT_BASE.mkdir(parents=True, exist_ok=True)

    cycles_to_process: List[Tuple[Path, str]] = []
    if args.cycle:
        cycle_path = Path(args.cycle)
        # infer account from path
        try:
            i = cycle_path.parts.index("hourly_reconfig")
            acct = cycle_path.parts[i + 1]
        except Exception:
            acct = "?"
        cycles_to_process.append((cycle_path, acct))
    else:
        last_processed = ""
        if MARKER.exists() and not args.backfill:
            last_processed = MARKER.read_text().strip()
        for acct_dir in sorted(HOURLY_BASE.iterdir()):
            if not acct_dir.is_dir() or acct_dir.name.startswith("_"):
                continue
            if args.account and acct_dir.name != args.account:
                continue
            runs_dir = acct_dir / "runs"
            if not runs_dir.exists():
                continue
            for cycle_dir in sorted(runs_dir.iterdir()):
                if not cycle_dir.is_dir():
                    continue
                if not args.backfill and cycle_dir.name <= last_processed:
                    continue
                cycles_to_process.append((cycle_dir, acct_dir.name))

    print(f"[build_symbol_configs] {len(cycles_to_process)} cycles to process")
    last_id = ""
    total_syms = 0
    for cycle_dir, acct in cycles_to_process:
        try:
            res = process_cycle(cycle_dir, acct)
            print(f"  [{acct}] cycle={res.get('cycle_id')}  files={res.get('files')}  syms_processed={res.get('syms')}")
            total_syms += res.get("syms", 0)
            if res.get("cycle_id") and res["cycle_id"] > last_id:
                last_id = res["cycle_id"]
        except Exception as e:
            print(f"  [{acct}] cycle={cycle_dir.name} ERROR {e}")
    if last_id and not args.backfill:
        MARKER.write_text(last_id)
    print(f"[build_symbol_configs] DONE — {total_syms} sym/cycle/side snapshots written")


if __name__ == "__main__":
    main()
