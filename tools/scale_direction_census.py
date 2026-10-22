#!/usr/bin/env python3
"""Per-producer direction census of REDUCE / AUGMENT events in published board charts.

For each reason family: how often does it fire while the position is in PROFIT (price moved
favourably since entry) vs in LOSS, and on a favourable vs adverse 4-bar price move.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ARR = re.compile(r"const (CLOSE|EVENTS) = (\[.*?\]);\n", re.S)
FAMILY = [
    (re.compile(r"^SATOSHIT"), "SATOSHIT_EXIT_PARTIAL (momentum-strength partial)"),
    (re.compile(r"^WT_CROSS_EXIT"), "WT_CROSS_EXIT (1h WT turns against)"),
    (re.compile(r"^REGIME_REDUCE|^QUICK_REDUCE_STRONG|^REDUCE_TO_FLAT|^HLR_TOP"), "REGIME/QUICK/HLR reduce block"),
    (re.compile(r"^PPL_"), "PARTIAL_PROFIT_LOCK (take-profit ladder)"),
    (re.compile(r"^HAIKU_REDUCE"), "HAIKU_REDUCE (gain fell below threshold)"),
    (re.compile(r"^STDEV_REJECT"), "STDEV_REJECT_EXIT"),
    (re.compile(r"^PEAK_GIVEBACK|^BREAKEVEN|^DD_BOUNCE"), "giveback / breakeven protection"),
    (re.compile(r"^HAIKU_WINNER_AUG|^HAIKU_REENTER"), "HAIKU augment"),
    (re.compile(r"^UAG|^GAIN_LADDER"), "UAG gain ladder augment"),
    (re.compile(r"^AUGMENT_AT_LOSS|^AAL"), "AUGMENT_AT_LOSS"),
]


def family(reason):
    for rx, name in FAMILY:
        if rx.search(reason or ""):
            return name
    return (reason or "")[:32] or "(no reason)"


def main(paths):
    tab = defaultdict(lambda: {"profit": 0, "loss": 0, "fav": 0, "adv": 0, "n": 0})
    boards = 0
    for p in paths:
        name = Path(p).name.upper()
        is_long = "_LONG" in name
        if not is_long and "_SHORT" not in name:
            continue
        txt = Path(p).read_text(errors="replace")
        d = {}
        for m in ARR.finditer(txt):
            try:
                d[m.group(1)] = json.loads(m.group(2))
            except Exception:
                pass
        close, events = d.get("CLOSE"), d.get("EVENTS")
        if not close or not events:
            continue
        boards += 1
        sgn = 1.0 if is_long else -1.0
        entry = None
        for e in sorted([x for x in events if isinstance(x, dict) and x.get("bar") is not None],
                        key=lambda x: (float(x.get("ts") or 0), 0 if str(x.get("type")).upper() == "OPEN" else 1)):
            ty = str(e.get("type") or "").upper()
            b = int(e.get("bar") or 0)
            px = float(e.get("price") or 0) or (close[b] if 0 <= b < len(close) else 0)
            if px <= 0:
                continue
            if ty == "OPEN":
                entry = px
                continue
            if ty not in ("AUGMENT", "REDUCE") or not entry:
                continue
            key = (ty, family(str(e.get("reason") or "")))
            row = tab[key]
            row["n"] += 1
            exc = sgn * (px - entry)
            row["profit" if exc > 0 else "loss"] += 1
            b0 = max(0, b - 4)
            slope = sgn * (close[b] - close[b0]) if 0 <= b < len(close) else 0.0
            row["fav" if slope > 0 else "adv"] += 1
    print(f"boards parsed: {boards}")
    print(f"{'act':<8} {'producer':<46} {'n':>7} {'in profit':>11} {'in loss':>9} {'fav move':>10} {'adv move':>10}")
    for (ty, fam), r in sorted(tab.items(), key=lambda kv: -kv[1]["n"]):
        if r["n"] < 5:
            continue
        print(f"{ty:<8} {fam:<46} {r['n']:>7} {r['profit'] / r['n'] * 100:>10.1f}% {r['loss'] / r['n'] * 100:>8.1f}% "
              f"{r['fav'] / r['n'] * 100:>9.1f}% {r['adv'] / r['n'] * 100:>9.1f}%")
    for ty in ("REDUCE", "AUGMENT"):
        tot = sum(r["n"] for (t, _f), r in tab.items() if t == ty)
        prof = sum(r["profit"] for (t, _f), r in tab.items() if t == ty)
        print(f"TOTAL {ty}: {tot} events, {prof / tot * 100:.1f}% while the position was in profit" if tot else f"TOTAL {ty}: 0")


if __name__ == "__main__":
    main(sys.argv[1:])
