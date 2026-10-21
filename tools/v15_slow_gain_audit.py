#!/usr/bin/env python3
"""v15_slow_gain_audit — slow-round vs quick-round gain record (USER 2026-10-21).

For every sym_side with a quick (catchup-mode) board, pair it with its slow-round
board and record the delta. Sym_sides with no quick board are tracked separately
(first board comes from slow). Report-only: reads manifests, writes JSON + Markdown.

Honesty rules (no cherry-picking, no forged comparability):
  * EVERY sym_side with a quick board gets a row, improvements and regressions alike.
  * A pair is comparable only on the same approximate data window + host; mismatches
    are flagged, never silently differenced away (NPZ/harness skew note per row).
  * Slow-missing rows stay PENDING; quarantined/slow-absent rows stay visible, never
    dropped (no survivorship filtering).
  * A 30D window moves with time, so quick-vs-slow delta is directional evidence of
    the round's value, not proof either mode is better. Both window ends are recorded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
from pathlib import Path

CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def cat_side_of(symside: str) -> str:
    sym, _, side = str(symside).upper().rpartition("_")
    venue = "CRYPTO" if sym.endswith(CRYPTO_SUFFIX) else "STOCKS"
    return f"{venue}_{side or '?'}"


def load_manifests(board_dir: str) -> list:
    out = []
    for f in glob.glob(os.path.join(board_dir, "*_manifest.json")):
        if ".superseded" in f:
            continue
        try:
            m = json.load(open(f))
        except Exception:
            continue
        mt = m.get("metrics") or {}
        out.append({
            "symside": m.get("symside"), "file": os.path.basename(f),
            "gain": mt.get("gain_pct"), "trades": mt.get("trades"),
            "tim": mt.get("tim_pct"), "dd": mt.get("max_dd_pct"),
            "bh": mt.get("bh"), "mode": m.get("mode"),
            "published_utc": m.get("published_utc"), "host": m.get("host"),
            "pilot_md5": m.get("pilot_md5"),
            "npz_last_bar_ts": m.get("npz_last_bar_ts"),
            "publish_class": m.get("publish_class"),
        })
    return out


def classify(m: dict, slow_since: str) -> str:
    mode, pub = m.get("mode"), str(m.get("published_utc") or "")
    if mode == "catchup":
        return "QUICK"
    if pub >= slow_since and mode == "slow":
        return "SLOW_ROUND"
    if pub >= slow_since:
        return "AMBIGUOUS"
    return "SLOW_LEGACY"


def _num(v):
    return v if isinstance(v, (int, float)) else None


def pair_manifests(manifests: list, slow_since: str) -> list:
    by_ss: dict = {}
    for m in manifests:
        if not m.get("symside"):
            continue
        by_ss.setdefault(m["symside"], []).append(m)
    rows = []
    for ss in sorted(by_ss):
        ms = by_ss[ss]
        quicks = sorted(
            (m for m in ms if classify(m, slow_since) == "QUICK"),
            key=lambda d: str(d.get("published_utc") or ""),
        )
        slows = sorted(
            (m for m in ms if classify(m, slow_since) == "SLOW_ROUND"),
            key=lambda d: str(d.get("published_utc") or ""),
        )
        amb = sum(1 for m in ms if classify(m, slow_since) == "AMBIGUOUS")
        q, s = (quicks[-1] if quicks else None), (slows[-1] if slows else None)
        row = {"symside": ss, "cat_side": cat_side_of(ss),
               "had_quick": q is not None, "ambiguous": amb, "status": "PENDING_SLOW",
               "quick": q, "slow": s, "d_gain": None, "d_trades": None,
               "d_tim": None, "notes": []}
        if q is None:
            row["status"] = "NO_QUICK_SLOW_ONLY" if s else "NO_BOARDS"
            if s and _num(s.get("gain")) is not None:
                row["notes"].append("first board from slow; no delta")
            rows.append(row)
            continue
        if s is None:
            rows.append(row)
            continue
        qg, sg = _num(q.get("gain")), _num(s.get("gain"))
        if qg is None or sg is None:
            row["status"] = "UNPAIRABLE_METRICS"
            rows.append(row)
            continue
        row["d_gain"] = round(sg - qg, 4)
        qt, st = _num(q.get("trades")), _num(s.get("trades"))
        if qt is not None and st is not None:
            row["d_trades"] = st - qt
        qi, si = _num(q.get("tim")), _num(s.get("tim"))
        if qi is not None and si is not None:
            row["d_tim"] = round(si - qi, 2)
        if q.get("host") != s.get("host"):
            row["notes"].append(f"host skew {q.get('host')}->{s.get('host')}")
        if q.get("npz_last_bar_ts") != s.get("npz_last_bar_ts"):
            row["notes"].append("NPZ window moved between rounds; delta is directional, not proof")
        if q.get("pilot_md5") != s.get("pilot_md5"):
            row["notes"].append("pilot version changed between rounds")
        for t in (qt, st):
            if t is not None and t < 30:
                row["notes"].append("thin sample (<30 trades on a leg)")
                break
        row["status"] = "IMPROVED" if row["d_gain"] > 0 else ("REGRESSED" if row["d_gain"] < 0 else "FLAT")
        rows.append(row)
    return rows


def summarize(rows: list) -> dict:
    cats: dict = {}
    for r in rows:
        c = cats.setdefault(r["cat_side"], {"n": 0, "paired": 0, "improved": 0,
                                            "regressed": 0, "flat": 0, "pending": 0,
                                            "no_quick": 0, "d_gains": []})
        c["n"] += 1
        if not r["had_quick"]:
            c["no_quick"] += 1
            continue
        if r["d_gain"] is None:
            c["pending"] += 1
            continue
        c["paired"] += 1
        c["d_gains"].append(r["d_gain"])
        c[{"IMPROVED": "improved", "REGRESSED": "regressed"}.get(r["status"], "flat")] += 1
    for c in cats.values():
        ds = sorted(c.pop("d_gains"))
        c["mean_d_gain"] = round(sum(ds) / len(ds), 3) if ds else None
        c["median_d_gain"] = round(ds[len(ds) // 2], 3) if ds else None
    return cats


def render_markdown(rec: dict) -> str:
    L = [f"# Slow vs quick gain record — {rec['generated_utc']}",
         "",
         f"slow-since {rec['slow_since']} · manifests {rec['n_manifests']} · symsides {rec['n_symsides']}",
         "",
         "Pair = newest catchup board vs newest slow-round board per sym_side. "
         "Windows move with time: deltas are directional evidence, not proof.",
         "",
         "## By cat_side",
         "",
         "| cat_side | n | paired | improved | regressed | flat | pending | no_quick | mean Δgain | median Δgain |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for cs in sorted(rec["by_cat_side"]):
        c = rec["by_cat_side"][cs]
        L.append(f"| {cs} | {c['n']} | {c['paired']} | {c['improved']} | {c['regressed']} | "
                 f"{c['flat']} | {c['pending']} | {c['no_quick']} | {c['mean_d_gain']} | {c['median_d_gain']} |")
    L += ["", "## Worst regressions (slow under quick)", ""]
    regs = sorted((r for r in rec["rows"] if r["status"] == "REGRESSED"),
                  key=lambda r: r["d_gain"])[:20]
    for r in regs:
        L.append(f"- {r['symside']}: {r['d_gain']:+.2f}pp "
                 f"(quick {r['quick']['gain']:.2f} -> slow {r['slow']['gain']:.2f}) {'; '.join(r['notes'])}")
    L += ["", "## Still pending a slow board (had quick)", ""]
    pend = [r["symside"] for r in rec["rows"] if r["status"] == "PENDING_SLOW"]
    L.append(f"{len(pend)} symsides: " + (", ".join(pend[:40]) + (" ..." if len(pend) > 40 else "") if pend else "none"))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parents[1] / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"))
    ap.add_argument("--out", default="")
    ap.add_argument("--slow-since", default="2026-10-21T18:00:00Z",
                    help="slow round publishes at/after this UTC stamp count as SLOW_ROUND")
    a = ap.parse_args()
    manifests = load_manifests(a.dir)
    rows = pair_manifests(manifests, a.slow_since)
    rec = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "slow_since": a.slow_since, "board_dir": a.dir,
           "n_manifests": len(manifests),
           "n_symsides": len({r["symside"] for r in rows}),
           "by_cat_side": summarize(rows), "rows": rows}
    if a.out:
        Path(a.out + ".json").parent.mkdir(parents=True, exist_ok=True)
        Path(a.out + ".json").write_text(json.dumps(rec, indent=1))
        Path(a.out + ".md").write_text(render_markdown(rec))
        print(f"[slow-audit] {len(rows)} symsides -> {a.out}.json/.md")
    else:
        print(json.dumps({k: v for k, v in rec.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
