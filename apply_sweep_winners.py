#!/usr/bin/env python3
"""Apply sweep winners to live Tradier config — READ-ONLY by default.

Reads latest top-N sector winners (across all sectors + sources), maps their
AND-conditions into a `TRADIER_V8Q_ENTRY_BLOCKS` style config stanza, and
emits a diff against config_tradier.py for manual review.

Never writes live config without --apply flag AND user explicit confirmation.
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
SECTORS = ["mix_12", "tech_big", "tech_growth", "metals_miners", "energy_oil", "industrials_ag", "financials_etfs", "misc_industrial"]
PICK = 3
BARS = 40000


def gather_winners(top_n=20, min_robust=0.5, source_weights=None):
    """Pull top-N from each sector DB across sources, rank by weighted robust."""
    source_weights = source_weights or {"validated": 1.0, "combined": 0.9, "sample": 0.6}
    d = BASE / "data" / "sweep_results"
    all_hits = []
    for sec in SECTORS:
        db = d / f"vec_sector_{sec}_p{PICK}_b{BARS}.sqlite"
        if not db.exists():
            continue
        c = sqlite3.connect(str(db))
        # sample
        for r in c.execute("SELECT side, combo, horizon, sharpe_robust, sharpe_avg, sharpe_pool, syms_included, n_trades_total, wr_avg FROM results WHERE sharpe_robust >= ? ORDER BY sharpe_robust DESC LIMIT ?", (min_robust, top_n)):
            all_hits.append({"src": "sample", "sec": sec, "side": r[0], "combo": r[1], "h": r[2], "robust": r[3], "avg": r[4], "pool": r[5], "syms": r[6], "n": r[7], "wr": r[8]})
        # validated
        try:
            for r in c.execute("SELECT side, combo, horizon, full_sharpe_robust, full_sharpe_avg, full_sharpe_pool, full_pos_syms, full_syms, full_trades, full_wr, dropoff_pct FROM validated_full WHERE full_sharpe_robust >= ? ORDER BY full_sharpe_robust DESC LIMIT ?", (min_robust, top_n)):
                all_hits.append({"src": "validated", "sec": sec, "side": r[0], "combo": r[1], "h": r[2], "robust": r[3], "avg": r[4], "pool": r[5], "pos": r[6], "tot": r[7], "n": r[8], "wr": r[9], "drop": r[10]})
        except sqlite3.OperationalError:
            pass
        # combined
        try:
            for r in c.execute("SELECT side, combo_merged, horizon, full_sharpe_robust, full_sharpe_avg, full_sharpe_pool, full_pos_syms, full_syms, full_trades, full_wr FROM combined_results WHERE full_sharpe_robust >= ? ORDER BY full_sharpe_robust DESC LIMIT ?", (min_robust, top_n)):
                all_hits.append({"src": "combined", "sec": sec, "side": r[0], "combo": r[1], "h": r[2], "robust": r[3], "avg": r[4], "pool": r[5], "pos": r[6], "tot": r[7], "n": r[8], "wr": r[9]})
        except sqlite3.OperationalError:
            pass
        c.close()
    for h in all_hits:
        h["weighted_robust"] = h["robust"] * source_weights.get(h["src"], 0.5)
    all_hits.sort(key=lambda h: h["weighted_robust"], reverse=True)
    return all_hits


def combo_to_config_dict(combo_str, side):
    """Parse a combo string (e.g. "bb15_lt10+dcx_1h+k1h_lt60") into a structured dict
    usable as an entry condition in config_tradier.py.
    """
    parts = [p.strip() for p in combo_str.split("+") if p.strip()]
    return {"side": side, "conditions": parts}


def emit_config_stanza(top_hits):
    """Emit python-literal config block to append to config_tradier.py."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# === TRADIER_V8Q_SWEEP_WINNERS auto-emitted {stamp} ===",
        f"# Source: apply_sweep_winners.py, from 8-sector × 2yr × pick-3 vectorized sweep",
        f"# DO NOT apply to live tradier_manage.py without manual review + user approval",
        "",
        "TRADIER_V8Q_SWEEP_WINNERS = [",
    ]
    for h in top_hits:
        n_trades = h.get("n", 0)
        lines.append(f"    {{ # sec={h['sec']} src={h['src']} robust={h['robust']:.3f} avg={h['avg']:.3f} pool={h['pool']:.3f} wr={h['wr']:.1f}% n={n_trades} h={h['h']}")
        lines.append(f"        'side': {h['side']!r},")
        lines.append(f"        'horizon_bars': {h['h']},")
        lines.append(f"        'conditions': {h['combo'].split('+')!r},")
        lines.append(f"        'combo_str': {h['combo']!r},")
        lines.append(f"        'source': {h['src']!r},")
        lines.append(f"        'sector': {h['sec']!r},")
        lines.append(f"        'metrics': {{'robust': {h['robust']:.4f}, 'avg': {h['avg']:.4f}, 'pool': {h['pool']:.4f}, 'wr': {h['wr']:.2f}, 'n': {n_trades}}},")
        lines.append(f"    }},")
    lines.append("]")
    lines.append("")
    lines.append(f"# Enforcement switch — MUST be set False until user reviews and enables")
    lines.append(f"TRADIER_V8Q_SWEEP_ENFORCE = False")
    lines.append(f"TRADIER_V8Q_SWEEP_MIN_ROBUST = 0.5  # only enforce combos above this at runtime")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=30, help="total winning combos to emit")
    ap.add_argument("--min-robust", type=float, default=0.5)
    ap.add_argument("--out", default=str(BASE / "config_tradier_sweep_winners.py"))
    ap.add_argument("--apply", action="store_true", help="append to config_tradier.py (requires user confirmation)")
    args = ap.parse_args()

    hits = gather_winners(top_n=args.top_n, min_robust=args.min_robust)
    print(f"Collected {len(hits)} winners from all sectors (robust >= {args.min_robust})")
    if not hits:
        print("No winners yet — orchestrator still running?")
        return

    # Dedup by combo_str — prefer best source
    seen = {}
    for h in hits:
        key = (h["side"], h["combo"], h["h"])
        if key not in seen or h["weighted_robust"] > seen[key]["weighted_robust"]:
            seen[key] = h
    top = sorted(seen.values(), key=lambda h: h["weighted_robust"], reverse=True)[:args.top_n]

    print(f"\nTop {len(top)} (after dedup, ranked by weighted_robust):")
    print(f"{'Rk':>3} {'Src':<9} {'Sec':<16} {'Sd':<2} {'Rob':>5} {'Avg':>5} {'Pool':>5} {'WR':>5} {'h':>4} {'N':>7}  Combo")
    for rank, h in enumerate(top, 1):
        print(f"{rank:>3} {h['src']:<9} {h['sec']:<16} {h['side']:<2} {h['robust']:>5.2f} {h['avg']:>5.2f} {h['pool']:>5.2f} {h['wr']:>4.1f}% {h['h']:>4} {h.get('n', 0):>7} {h['combo'][:70]}")

    stanza = emit_config_stanza(top)
    Path(args.out).write_text(stanza)
    print(f"\nWrote stanza to: {args.out}")
    print(f"\nTo review diff against config_tradier.py:  diff {args.out} <(grep TRADIER_V8Q /Users/niels/Documents/binance/config_tradier.py)")

    if args.apply:
        print("\n*** APPLY MODE: would append to config_tradier.py — NOT ACTUALLY DOING IT ***")
        print("*** Manual step: read the stanza, review, then paste into config_tradier.py ***")
        print("*** Switch TRADIER_V8Q_SWEEP_ENFORCE = True to activate ***")


if __name__ == "__main__":
    main()
