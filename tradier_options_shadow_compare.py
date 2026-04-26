"""End-of-day comparator + scorer + mutator for tradier_options_shadow_runner.

Reads data/options_shadow/*/decisions_<date>.jsonl and produces:
  - Per-variant summary table
  - Symbol-level diff (which symbols each variant uniquely proposed)
  - Diff vs live OCC fills (data/options_state/<date>.jsonl)
  - data/options_shadow/compare_<date>.json
  - data/options_shadow/scores_<date>.json (via tradier_options_shadow_scorer)
  - data/options_shadow/suggestions_<date>.md (human-readable EOD report)
  - data/options_shadow/variants.json — auto-mutated for tomorrow

CLI:
  python3 tradier_options_shadow_compare.py                # today UTC
  python3 tradier_options_shadow_compare.py --date 20260426
  python3 tradier_options_shadow_compare.py --no-score     # skip scorer (legacy mode)
"""
import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

BASE_PATH = Path(__file__).resolve().parent
SHADOW_DIR = BASE_PATH / "data" / "options_shadow"
STATE_DIR = BASE_PATH / "data" / "options_state"


def _read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out: List[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception as e:
                print(f"[WARN] {path.name}: parse line {e}", file=sys.stderr)
    return out


def _aggregate_variant(records: List[dict]) -> dict:
    n_proposed = 0
    n_calls = 0
    n_puts = 0
    n_blocked = 0
    n_skip = 0
    total_premium = 0.0
    score_sum = 0.0
    score_n = 0
    proposed_symbols: Set[str] = set()
    proposed_occs: Set[str] = set()
    n_cycles = len(records)
    last_market_open = False
    for rec in records:
        s = rec.get("summary") or {}
        n_proposed += int(s.get("n_buy", 0) or 0)
        n_calls += int(s.get("n_calls_proposed", 0) or 0)
        n_puts += int(s.get("n_puts_proposed", 0) or 0)
        n_skip += int(s.get("n_skip", 0) or 0)
        total_premium += float(s.get("total_proposed_premium", 0) or 0)
        if s.get("avg_opp_score"):
            score_sum += float(s["avg_opp_score"])
            score_n += 1
        last_market_open = bool(rec.get("market_open"))
        for d in rec.get("decisions", []) or []:
            if d.get("action") == "BUY":
                sym = d.get("symbol", "")
                occ = d.get("occ_symbol", "")
                if sym:
                    proposed_symbols.add(sym)
                if occ:
                    proposed_occs.add(occ)
        for o in rec.get("opportunities", []) or []:
            sym = o.get("symbol", "")
            if sym:
                proposed_symbols.add(sym)
        # error count
        if rec.get("error"):
            n_blocked += 1
    return {
        "n_cycles": n_cycles,
        "n_proposed": n_proposed,
        "n_calls": n_calls,
        "n_puts": n_puts,
        "n_skip": n_skip,
        "n_errors": n_blocked,
        "total_proposed_premium": round(total_premium, 2),
        "avg_premium_per_cycle": round(total_premium / n_cycles, 2) if n_cycles else 0,
        "avg_opp_score": round(score_sum / score_n, 2) if score_n else 0,
        "unique_symbols_proposed": sorted(proposed_symbols),
        "n_unique_symbols": len(proposed_symbols),
        "unique_occs_proposed": sorted(proposed_occs),
        "n_unique_occs": len(proposed_occs),
        "last_market_open": last_market_open,
    }


def _live_actual_for_date(date_str: str) -> dict:
    """Read data/options_state/<YYYYMMDD>.jsonl and pull all OCCs that EVER appeared
    that day along with their first observed cost basis."""
    f = STATE_DIR / f"{date_str}.jsonl"
    if not f.exists():
        return {"present": False, "occs": [], "n_occs": 0}
    seen: Dict[str, dict] = {}
    for rec in _read_jsonl(f):
        for p in rec.get("positions", []) or []:
            occ = p.get("occ") or p.get("occ_symbol") or p.get("symbol", "")
            if not occ or occ in seen:
                continue
            seen[occ] = {
                "occ": occ,
                "symbol": p.get("symbol", ""),
                "option_type": p.get("option_type", ""),
                "qty": p.get("qty", 0),
                "cost_basis": p.get("cost_basis", 0),
                "account_key": p.get("account_key", ""),
                "first_seen": rec.get("ts"),
            }
    return {"present": True, "occs": list(seen.values()), "n_occs": len(seen)}


def _print_summary_table(per_variant: Dict[str, dict]) -> None:
    rows = []
    for name, agg in per_variant.items():
        rows.append((name, agg["n_proposed"], agg["n_calls"], agg["n_puts"],
                     agg["n_unique_symbols"], agg["total_proposed_premium"],
                     agg["avg_opp_score"], agg["n_cycles"], agg["n_errors"]))
    rows.sort(key=lambda r: r[5], reverse=True)
    hdr = ("variant", "n_buy", "n_call", "n_put", "n_sym", "$_total", "avg_score", "cycles", "err")
    widths = [max(len(str(r[i])) for r in [hdr] + rows) for i in range(len(hdr))]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*hdr))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt.format(*[str(x) for x in r]))


def _symbol_diff(per_variant: Dict[str, dict]) -> Dict[str, List[str]]:
    """For each variant return symbols only that variant proposed (not in any other)."""
    all_syms_by_var = {n: set(a.get("unique_symbols_proposed") or []) for n, a in per_variant.items()}
    union = set().union(*all_syms_by_var.values()) if all_syms_by_var else set()
    out: Dict[str, List[str]] = {}
    for name, syms in all_syms_by_var.items():
        others = set().union(*[s for n, s in all_syms_by_var.items() if n != name]) if len(all_syms_by_var) > 1 else set()
        unique = sorted(syms - others)
        common = sorted(syms & others)
        out[name] = {
            "unique_to_this_variant": unique,
            "shared_with_others": common,
            "missing_vs_union": sorted(union - syms),
        }
    return out


def _live_vs_shadow_diff(live: dict, per_variant: Dict[str, dict]) -> Dict[str, dict]:
    if not live.get("present"):
        return {"note": "no live snapshot for this date"}
    live_syms = {p["symbol"] for p in live["occs"] if p.get("symbol")}
    live_occs = {p["occ"] for p in live["occs"] if p.get("occ")}
    out: Dict[str, dict] = {}
    for name, agg in per_variant.items():
        var_syms = set(agg.get("unique_symbols_proposed") or [])
        var_occs = set(agg.get("unique_occs_proposed") or [])
        out[name] = {
            "live_only_symbols": sorted(live_syms - var_syms),
            "shadow_only_symbols": sorted(var_syms - live_syms),
            "overlap_symbols": sorted(var_syms & live_syms),
            "live_only_occs": sorted(live_occs - var_occs),
            "shadow_only_occs": sorted(var_occs - live_occs),
            "overlap_occs": sorted(var_occs & live_occs),
        }
    return out


async def _run_scorer(date_str: str, per_variant: Dict[str, dict]) -> Optional[dict]:
    """Call the scorer module for EOD scoring + mutation + suggestions."""
    try:
        from tradier_options_shadow_scorer import score_and_mutate, _collect_proposals
    except ImportError as e:
        print(f"  scorer unavailable: {e}", file=sys.stderr)
        return None
    # Need raw records again — re-read for proposals
    per_variant_proposals: Dict[str, list] = {}
    for var_dir in sorted(SHADOW_DIR.iterdir()):
        if not var_dir.is_dir() or var_dir.name not in per_variant:
            continue
        f = var_dir / f"decisions_{date_str}.jsonl"
        records = _read_jsonl(f)
        per_variant_proposals[var_dir.name] = _collect_proposals(records)
    variants_path = SHADOW_DIR / "variants.json"
    variants = {}
    if variants_path.exists():
        try:
            variants = json.loads(variants_path.read_text())
        except Exception:
            variants = {}
    return await score_and_mutate(date_str, per_variant_proposals, variants, variants_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYYMMDD; default = today UTC")
    ap.add_argument("--no-score", action="store_true",
                    help="skip scoring/mutation pass (legacy compare-only)")
    args = ap.parse_args()
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    print(f"\n=== options shadow comparison for {date_str} ===\n")
    if not SHADOW_DIR.exists():
        print(f"  no shadow dir at {SHADOW_DIR}")
        sys.exit(0)
    per_variant: Dict[str, dict] = {}
    for var_dir in sorted(SHADOW_DIR.iterdir()):
        if not var_dir.is_dir():
            continue
        f = var_dir / f"decisions_{date_str}.jsonl"
        records = _read_jsonl(f)
        if not records:
            continue
        per_variant[var_dir.name] = _aggregate_variant(records)
    if not per_variant:
        print(f"  no variant data for {date_str}")
        sys.exit(0)
    print("--- per-variant summary ---")
    _print_summary_table(per_variant)
    print("\n--- symbol diff (unique to each variant vs others) ---")
    sym_diff = _symbol_diff(per_variant)
    for name, d in sym_diff.items():
        print(f"  {name:24s} unique={d['unique_to_this_variant']}")
    live = _live_actual_for_date(date_str)
    print(f"\n--- live actuals (data/options_state/{date_str}.jsonl) ---")
    if live["present"]:
        print(f"  {live['n_occs']} OCCs observed today live")
        for p in live["occs"]:
            print(f"    {p['account_key']:3s} {p['occ']:25s} qty={p['qty']} cb=${p['cost_basis']}")
    else:
        print(f"  no live state file for {date_str}")
    print("\n--- live vs shadow diff ---")
    ls_diff = _live_vs_shadow_diff(live, per_variant)
    if isinstance(ls_diff, dict) and ls_diff.get("note"):
        print(f"  {ls_diff['note']}")
    else:
        for name, d in ls_diff.items():
            print(f"  {name}:")
            print(f"    overlap_syms={d['overlap_symbols']}  shadow_only_syms={d['shadow_only_symbols']}  live_only_syms={d['live_only_symbols']}")
    out_payload = {
        "date": date_str,
        "ts": datetime.now(timezone.utc).isoformat(),
        "per_variant": per_variant,
        "symbol_diff": sym_diff,
        "live": live,
        "live_vs_shadow_diff": ls_diff,
    }
    out_path = SHADOW_DIR / f"compare_{date_str}.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(out_payload, indent=2, default=str))
    tmp.replace(out_path)
    print(f"\nwrote {out_path}")
    # Score, mutate, and emit suggestions (NEW)
    if not args.no_score:
        print("\n--- scoring + mutation pass ---")
        scores = asyncio.run(_run_scorer(date_str, per_variant))
        if scores:
            live_hr = scores["live_score"].get("hit_rate", 0) * 100
            print(f"  live hit rate: {live_hr:.1f}%  ({scores['live_score'].get('n_proposals',0)} proposals)")
            print(f"  variants scored: {len(scores['per_variant_scores'])}")
            print(f"  mutations applied for tomorrow: {scores['n_mutations']}")
            print(f"  suggestions report: {scores['suggestions_path']}")


if __name__ == "__main__":
    main()
