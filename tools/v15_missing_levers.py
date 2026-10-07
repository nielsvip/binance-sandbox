#!/usr/bin/env python3
"""v15_missing_levers — what switches/filters are MISSING (USER 2026-10-05: "once this gets good it will start pointing out
what is missing in switches and filters"). Aggregates the `gaps` of every DIAGNOSE_REPAIR report
({progress_dir}/v15_diag_repair/*.json) per cat_side:
  MISSING_LEVER          — a remaining fault that NO template switch/filter moves in the right direction on that sym_side
  LEVER_EXISTS_BUT_COSTLY — the best mover exists but costs gain/validity (which lever, how often)
Writes data/parity/missing_levers_{date}.md (+ .json). New switches built from it follow the 4-surface rule
(vec + ez + tradier + config + TEMPLATE row, one bold default) — USER 2026-10-06.
usage: python tools/v15_missing_levers.py [--progress-dir DIR ...]"""
import argparse
import collections
import datetime
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def cat_of(ss: str) -> str:
    base, side = ss.rsplit("_", 1)
    return ("CRYPTO" if base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD")) else "STOCKS") + "_" + side


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress-dir", action="append", default=[])
    a = ap.parse_args()
    dirs = a.progress_dir or [os.environ.get("V15_PROGRESS_DIR") or str(ROOT / "data" / "reports" / "lifecycle_pilot")]
    miss = collections.defaultdict(lambda: collections.defaultdict(list))
    costly = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    n = collections.Counter()
    for d in dirs:
        for f in pathlib.Path(d, "v15_diag_repair").glob("*.json"):
            r = json.loads(f.read_text())
            ss = f.stem
            cat = cat_of(ss)
            n[cat] += 1
            for g in r.get("gaps") or []:
                fam = g["fault"].split(":", 1)[0]
                if g["status"] == "MISSING_LEVER":
                    miss[cat][fam].append(ss)
                else:
                    costly[cat][fam][g.get("best")] += 1
    date = datetime.date.today().strftime("%Y%m%d")
    out = pathlib.Path(ROOT / "data" / "parity")
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# Missing levers {date}", "", f"Reports read: {dict(n)} from {dirs}", ""]
    js = {}
    for cat in sorted(set(miss) | set(costly)):
        lines += [f"## {cat} ({n[cat]} sym_sides)", "", "| fault | MISSING_LEVER sym_sides | costly best levers (count) |", "|---|---|---|"]
        js[cat] = {}
        for fam in sorted(set(miss[cat]) | set(costly[cat]), key=lambda x: -len(miss[cat].get(x, []))):
            ms = miss[cat].get(fam, [])
            cs = costly[cat].get(fam, collections.Counter()).most_common(5)
            lines.append(f"| {fam} | {len(ms)} ({', '.join(ms[:6])}{'…' if len(ms) > 6 else ''}) | {', '.join(f'{k} ({v})' for k, v in cs)} |")
            js[cat][fam] = {"missing": ms, "costly": cs}
        lines.append("")
    (out / f"missing_levers_{date}.md").write_text("\n".join(lines))
    (out / f"missing_levers_{date}.json").write_text(json.dumps(js, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
