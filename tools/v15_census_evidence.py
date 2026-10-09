#!/usr/bin/env python3
"""v15_census_evidence — refresh the ENCYCLOPEDIA evidence layer from CURRENT fleet artifacts (USER 2026-10-09).

The Oct 6 census evidence (data/encyclopedia/lever_evidence.csv + fleet_diagnosis.csv, 523 sheets, ~17M evals
over P_naked/P_yellow/F/L sources) has no builder — it was a one-off mining job. This tool re-runs the
reproducible core on TODAY's mirror: P_naked (progress-JSON done rows: one naked switch=cand eval vs the
running set) + published manifests (fleet table). Same class rule + table shape as chapter 09; narrower
source scope (P only — F/L archaeology not redone), stamped in every output. Never overwrites the Oct 6
files: writes *_20261009.csv + chapter 09b.

NO-LIES: every number comes from committed progress/manifest JSONs; baselines use the Oct 6 rule (mode of
trade counts among EXACT-zero-gain-delta evals at the same cumulative_before); rows without a baseline
contribute gain stats only, never fabricated trade deltas.
"""

import argparse
import csv
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now(timezone.utc).strftime("%Y%m%d")


def cat_side_of(symside):
    s = symside.upper()
    venue = "CRYPTO" if ("USDT" in s or "USDC" in s) else "STOCKS"
    side = "LONG" if s.endswith("_LONG") else "SHORT"
    return f"{venue}_{side}"


def parse_done_key(key):
    """'TAB!row:SWITCH=value' -> (TAB, 'SWITCH=value')."""
    try:
        tab = key.split("!", 1)[0]
        lever = key.split(":", 1)[1]
        return tab, lever
    except Exception:
        return "", key


def collect_naked(progress_dir):
    """[(symside, cat, tab, lever, trades, tim, dd, delta, cum_before)] + n_files."""
    rows = []
    fs = sorted(glob.glob(os.path.join(progress_dir, "*_v14_progress.json")))
    for f in fs:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ss = d.get("symside") or os.path.basename(f).replace("_v14_progress.json", "")
        cat = cat_side_of(ss)
        for key, v in (d.get("done") or {}).items():
            if not isinstance(v, dict):
                continue
            vec = v.get("vec") or {}
            delta = v.get("naked_delta")
            if delta is None:
                delta = v.get("delta")
            trades = v.get("trades")
            if trades is None:
                trades = vec.get("trades")
            if delta is None and trades is None:
                continue  # never calculated (NOT_WIRED/ZERO_FORMULA/SKIPPED) — not an eval
            try:
                rows.append(
                    {
                        "symside": ss,
                        "cat": cat,
                        "tab": parse_done_key(key)[0],
                        "lever": parse_done_key(key)[1],
                        "trades": trades,
                        "tim": vec.get("tim_pct"),
                        "dd": vec.get("max_dd_pct"),
                        "delta": delta,
                        "cum": v.get("cumulative_before"),
                    }
                )
            except Exception:
                continue
    return rows, len(fs)


def _mode(vals):
    vals = [x for x in vals if x is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def attach_baselines(rows):
    """Oct 6 rule: baseline trades/tim/dd at (file,cum) = mode among EXACT-zero-delta evals."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["symside"], r["cum"])].append(r)
    n_hit = n_miss = 0
    for rs in groups.values():
        zero = [r for r in rs if r["delta"] == 0]
        bt, btim, bdd = (
            _mode([r["trades"] for r in zero]),
            _mode([r["tim"] for r in zero]),
            _mode([r["dd"] for r in zero]),
        )
        for r in rs:
            r["b_trades"], r["b_tim"], r["b_dd"] = bt, btim, bdd
            if bt is None:
                n_miss += 1
            else:
                n_hit += 1
    return n_hit, n_miss


def classify(dtrades):
    """Chapter 09 class rule on a lever's Δtrades list."""
    nz = [x for x in dtrades if x != 0]
    if len(nz) < 2 or len(nz) < 0.05 * len(dtrades):
        return "NEUTRAL"
    up = sum(1 for x in nz if x > 0) / len(nz)
    if up >= 0.67:
        return "ADDS_TRADES"
    if up <= 0.33:
        return "REMOVES_TRADES"
    return "MIXED"


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def aggregate(rows):
    """Per (cat, lever) stats in the lever_evidence.csv shape."""
    by = defaultdict(list)
    for r in rows:
        by[(r["cat"], r["lever"])].append(r)
    out = []
    for (cat, lever), rs in by.items():
        syms = sorted({r["symside"] for r in rs})
        dts = [
            r["trades"] - r["b_trades"]
            for r in rs
            if r["trades"] is not None and r["b_trades"] is not None
        ]
        dg = [r["delta"] for r in rs if r["delta"] is not None]
        dtim = [
            r["tim"] - r["b_tim"]
            for r in rs
            if r["tim"] is not None and r["b_tim"] is not None
        ]
        ddd = [
            r["dd"] - r["b_dd"]
            for r in rs
            if r["dd"] is not None and r["b_dd"] is not None
        ]
        rel = [
            100.0 * (r["trades"] - r["b_trades"]) / r["b_trades"]
            for r in rs
            if r["trades"] is not None
            and r["b_trades"] not in (None, 0)
            and r["trades"] != r["b_trades"]
        ]
        net_t, net_g = defaultdict(float), defaultdict(float)
        for r in rs:
            if r["trades"] is not None and r["b_trades"] is not None:
                net_t[r["symside"]] += r["trades"] - r["b_trades"]
            if r["delta"] is not None:
                net_g[r["symside"]] += r["delta"]
        nz = [x for x in dts if x != 0]
        tabs = sorted({r["tab"] for r in rs if r["tab"]})
        out.append(
            {
                "lever": lever,
                "lever_type": "naked",
                "source": "P_naked_refresh",
                "era": "current",
                "cat_side": cat,
                "tabs": ";".join(tabs),
                "n_evals": len(rs),
                "n_symsides": len(syms),
                "n_trades_evals": len(dts),
                "median_dtrades_all": _med(dts),
                "median_dtrades_nonzero": _med(nz),
                "mean_dtrades_all": _mean(dts),
                "pct_trades_up": (
                    round(100.0 * sum(1 for x in nz if x > 0) / len(nz), 2)
                    if nz
                    else 0.0
                ),
                "pct_trades_down": (
                    round(100.0 * sum(1 for x in nz if x < 0) / len(nz), 2)
                    if nz
                    else 0.0
                ),
                "median_rel_dtrades_pct": _med(rel),
                "median_base_trades": _med([r["b_trades"] for r in rs]),
                "symsides_trades_up": sum(1 for v in net_t.values() if v > 0),
                "symsides_trades_down": sum(1 for v in net_t.values() if v < 0),
                "n_gain_evals": len(dg),
                "median_dgain_all": _med(dg),
                "mean_dgain_all": _mean(dg),
                "pct_gain_pos": (
                    round(100.0 * sum(1 for x in dg if x > 0) / len(dg), 2)
                    if dg
                    else 0.0
                ),
                "pct_gain_neg": (
                    round(100.0 * sum(1 for x in dg if x < 0) / len(dg), 2)
                    if dg
                    else 0.0
                ),
                "symsides_gain_pos": sum(1 for v in net_g.values() if v > 0),
                "symsides_gain_neg": sum(1 for v in net_g.values() if v < 0),
                "n_tim": len(dtim),
                "median_dtim": _med(dtim),
                "n_dd": len(ddd),
                "median_ddd": _med(ddd),
                "class_trades": classify(dts),
            }
        )
    return out


LEVER_COLS = [
    "lever",
    "lever_type",
    "source",
    "era",
    "cat_side",
    "tabs",
    "n_evals",
    "n_symsides",
    "n_trades_evals",
    "median_dtrades_all",
    "median_dtrades_nonzero",
    "mean_dtrades_all",
    "pct_trades_up",
    "pct_trades_down",
    "median_rel_dtrades_pct",
    "median_base_trades",
    "symsides_trades_up",
    "symsides_trades_down",
    "n_gain_evals",
    "median_dgain_all",
    "mean_dgain_all",
    "pct_gain_pos",
    "pct_gain_neg",
    "symsides_gain_pos",
    "symsides_gain_neg",
    "n_tim",
    "median_dtim",
    "n_dd",
    "median_ddd",
    "class_trades",
]


def top_tables(agg, cls, n=40):
    """Chapter 09 ranking: class + n_symsides>=3, by symsides net-added desc, then median rel Δtrades desc."""
    rows = [a for a in agg if a["class_trades"] == cls and a["n_symsides"] >= 3]
    key = "symsides_trades_up" if cls == "ADDS_TRADES" else "symsides_trades_down"
    rows.sort(
        key=lambda a: (
            a[key],
            (
                a["median_rel_dtrades_pct"]
                if a["median_rel_dtrades_pct"] is not None
                else -1e18
            ),
        ),
        reverse=True,
    )
    return rows[:n]


def _f(x, nd=2):
    return "" if x is None else round(float(x), nd)


def render_chapter(agg, n_files, n_hit, n_miss, stamp):
    L = [
        f"# Lever evidence refresh (P_naked only) — generated on the Mac, {stamp[:4]}-{stamp[4:6]}-{stamp[6:]}",
        "",
        f"Pooled evidence = progress-JSON done rows on the Mac mirror ({n_files} files): naked switch=cand eval vs the running set. "
        f"Baselines = Oct 6 rule (mode among EXACT-zero-delta evals at the same cumulative_before): {n_hit} evals with baseline, {n_miss} without (gain stats only). "
        "Class rule identical to chapter 09. DOES NOT include the Oct 6 F/L sources — compare within-refresh, not across.",
        "",
    ]
    for cat in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        sub = [a for a in agg if a["cat_side"] == cat and a["n_symsides"] >= 3]
        cnt = Counter(a["class_trades"] for a in sub)
        L.append(f"## {cat}")
        L.append("")
        L.append(
            f"levers with >= 3 sym_sides: {len(sub)}; "
            + ", ".join(
                f"{k} {cnt.get(k, 0)}"
                for k in ("ADDS_TRADES", "REMOVES_TRADES", "MIXED", "NEUTRAL")
            )
        )
        L.append("")
        for cls, title in (
            ("ADDS_TRADES", "top 40 TRADE ADDERS"),
            ("REMOVES_TRADES", "top 40 TRADE REMOVERS"),
        ):
            L.append(f"#### {cat} — {title}")
            L.append("")
            L.append(
                "| # | lever (switch=value) | tab | n_symsides (up/down) | evals | med Δtrades (nonzero) | med rel Δtrades % | med Δgain pp | % evals gain>0 | symsides gain +/- | ΔTIM med (n) |"
            )
            L.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for i, a in enumerate(
                top_tables([x for x in agg if x["cat_side"] == cat], cls), 1
            ):
                L.append(
                    f"| {i} | `{a['lever']}` | {a['tabs'].split(';')[0]} | {a['n_symsides']} ({a['symsides_trades_up']}/{a['symsides_trades_down']}) | {a['n_evals']} | {_f(a['median_dtrades_nonzero'])} | {_f(a['median_rel_dtrades_pct'])} | {_f(a['median_dgain_all'])} | {_f(a['pct_gain_pos'])} | {a['symsides_gain_pos']}/{a['symsides_gain_neg']} | {_f(a['median_dtim'])} ({a['n_tim']}) |"
                )
            L.append("")
    return "\n".join(L)


def fleet_table(cell_dir, progress_dir=None):
    """Per-manifest finished-sheet metrics. Manifest-only on purpose: the Mac progress mirror is a
    different era than the published finals, so joining verdicts would mix eras (proven: QUALIFIED
    manifest paired with a stale IMPOSSIBLE verdict)."""
    rows = []
    for f in sorted(glob.glob(os.path.join(cell_dir, "*_manifest.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        m = d.get("metrics") or {}
        c = d.get("counts") or {}
        ss = d.get("symside", "")
        rows.append(
            {
                "sym_side": ss,
                "cat_side": cat_side_of(ss),
                "gain": m.get("gain_pct"),
                "bh": m.get("bh"),
                "gain_minus_bh": (
                    (m.get("gain_pct") - m.get("bh"))
                    if m.get("gain_pct") is not None and m.get("bh") is not None
                    else None
                ),
                "trades": m.get("trades"),
                "tim": m.get("tim_pct"),
                "dd": m.get("max_dd_pct"),
                "valid": m.get("valid"),
                "publish_class": d.get("publish_class"),
                "host": d.get("host"),
                "published_utc": d.get("published_utc"),
                "template": d.get("template"),
                "npz_name": d.get("npz_name"),
                "done_n": c.get("done_n"),
                "F": c.get("F"),
                "C": c.get("C"),
                "E": c.get("E"),
                "file": os.path.basename(d.get("final_name") or ""),
            }
        )
    return rows


FLEET_COLS = [
    "sym_side",
    "cat_side",
    "gain",
    "bh",
    "gain_minus_bh",
    "trades",
    "tim",
    "dd",
    "valid",
    "publish_class",
    "host",
    "published_utc",
    "template",
    "npz_name",
    "done_n",
    "F",
    "C",
    "E",
    "file",
]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="refresh encyclopedia evidence from current progress + manifests"
    )
    ap.add_argument("--progress-dir", default="data/reports/lifecycle_pilot")
    ap.add_argument("--cell-dir", default="SPREADSHEETS/V15_V16_CELL_BY_CELL")
    ap.add_argument("--out-dir", default="data/encyclopedia")
    ap.add_argument(
        "--chapter-out", default="docs/encyclopedia/09b_lever_evidence_refresh.md"
    )
    ap.add_argument("--stamp", default=STAMP)
    a = ap.parse_args(argv)
    rows, n_files = collect_naked(a.progress_dir)
    n_hit, n_miss = attach_baselines(rows)
    agg = aggregate(rows)
    lever_path = os.path.join(a.out_dir, f"lever_evidence_{a.stamp}.csv")
    with open(lever_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEVER_COLS)
        w.writeheader()
        for r in sorted(agg, key=lambda x: (x["cat_side"], x["lever"])):
            w.writerow({k: ("" if r[k] is None else r[k]) for k in LEVER_COLS})
    fleet = fleet_table(a.cell_dir, a.progress_dir)
    fleet_path = os.path.join(a.out_dir, f"fleet_diagnosis_{a.stamp}.csv")
    with open(fleet_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FLEET_COLS)
        w.writeheader()
        for r in sorted(fleet, key=lambda x: x["sym_side"]):
            w.writerow({k: ("" if r[k] is None else r[k]) for k in FLEET_COLS})
    ch = render_chapter(agg, n_files, n_hit, n_miss, a.stamp)
    Path(a.chapter_out).write_text(ch)
    cc = Counter(x["class_trades"] for x in agg)
    print(
        f"[census] files={n_files} evals={len(rows)} baseline_hit={n_hit} miss={n_miss} levers={len(agg)} {dict(cc)} fleet={len(fleet)}"
    )
    print(f"[census] wrote {lever_path} {fleet_path} {a.chapter_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
