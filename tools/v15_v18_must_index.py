#!/usr/bin/env python3
"""V18 MUST index (USER LAW 2026-10-20: "if a certain switch/filter combination worked on another sym_side for the same
cat_side it HAS to be tried on any other sym_side that has not tried it yet").

Builds, per cat_side, the set of every template-row item that was POSITIVE (delta > 0, valid, trades >= floor) on at least one
sym_side, from the host's own delta logs (real evals only, BIBLE §20), incrementally (byte watermark per file):

  data/learn/must_pos_<CAT_SIDE>.json = {"meta": {...}, "items": {"SW=cand": {"syms": [...], "best": d},
                                                                  "SW=cand@F=opt": {...}}}

Item keys: a naked row "SWITCH=cand"; a yellow cell "SWITCH=cand@FILTER=opt" (the cell label of that row). The pilot
(V18_MUST=1) subtracts what THIS sym_side already evaluated (its own delta logs + progress) and what its own MUST ledger
settled on the same start set, and evaluates the rest at p = 1. Strictly per cat_side (principle 13). Stdlib only.

  python3 tools/v15_v18_must_index.py --logs '~/v15_run3*/progress/v15_delta_log/*.jsonl' [--out-dir data/learn] [--max-s 1800]
"""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
NON_CELL = ("naked", "hustle", "ROW_DONE", "ROW_SETTLED", "SETTLE", "BOARD_CTX", "HOLDOUT", "FORWARD", "REF_BASELINE", "E3_DEFAULT_BASE", "BEST_START_SET", "RECALC_CUMULATIVE")
SWITCH_SHEETS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")
FLOOR_TRADES = 10


def cat_side_of(ss: str):
    s = str(ss or "").upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT" if s.endswith("_SHORT") else None
    if side is None:
        return None
    base = s.rsplit("_", 1)[0]
    return ("CRYPTO_" if base.endswith(CRYPTO_SUFFIX) else "STOCKS_") + side


def item_of(rec: dict):
    """-> item key of one delta-log eval record, or None (non-row / phase / settle lines)."""
    if rec.get("sheet") not in SWITCH_SHEETS:
        return None
    sw, c, lab = str(rec.get("switch") or "").strip(), str(rec.get("cand") or "").strip(), str(rec.get("label") or "")
    if not sw or not lab:
        return None
    if lab == "naked":
        return f"{sw}={c}"
    if lab in NON_CELL or lab.startswith(("JOINT:", "RECALC", "RED_RETRY:", "STANDALONE:", "INERT", "V17", "ABL", "WINNERS", "EXITREP", "XPOLL", "FOCUS", "SURGEON", "COMBO")) or "=" not in lab:
        return None
    return f"{sw}={c}@{lab.strip()}"


def positive(rec: dict) -> bool:
    try:
        return bool(rec.get("valid")) and not rec.get("err") and float(rec.get("delta")) > 1e-9 and int(rec.get("trades") or 0) >= FLOOR_TRADES
    except Exception:
        return False


def scan_line(line: str):
    """Cheap prefilter before json: only eval lines that can be positive."""
    if '"delta": ' not in line or '"valid": true' not in line or '"delta": 0.0,' in line or '"delta": -' in line or '"delta": null' in line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def tried_items(paths, symside: str) -> set:
    """Every item THIS sym_side evaluated (any verdict) in the given delta logs of its own."""
    out = set()
    for p in paths:
        try:
            with open(p, errors="replace") as fh:
                for line in fh:
                    if '"sheet": ' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("sym_side") != symside:
                        continue
                    k = item_of(r)
                    if k:
                        out.add(k)
        except Exception:
            continue
    return out


def build(log_glob: str, out_dir: Path, max_s: float = 1800.0) -> dict:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    wm_path = out_dir / "must_pos_watermarks.json"
    try:
        wm = json.loads(wm_path.read_text())
    except Exception:
        wm = {}
    idx = {}
    for cs in CAT_SIDES:
        try:
            idx[cs] = json.loads((out_dir / f"must_pos_{cs}.json").read_text()).get("items") or {}
        except Exception:
            idx[cs] = {}
    files = sorted({str(Path(p)) for g in log_glob.split(",") for p in glob.glob(os.path.expanduser(g))}, key=lambda p: -os.path.getmtime(p))
    stats = {"files": 0, "bytes": 0, "positives": 0, "timed_out": False}
    for p in files:
        if time.time() - t0 > max_s:
            stats["timed_out"] = True
            break
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        off = int((wm.get(p) or {}).get("off") or 0)
        if off > size:
            off = 0
        if off == size:
            continue
        with open(p, "rb") as fh:
            fh.seek(off)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                off += len(raw)
                r = scan_line(raw.decode("utf-8", "replace"))
                if r is None or not positive(r):
                    continue
                ss = str(r.get("sym_side") or "")
                cs = cat_side_of(ss)
                k = item_of(r)
                if cs is None or k is None:
                    continue
                e = idx[cs].setdefault(k, {"syms": [], "best": None})
                if ss not in e["syms"]:
                    e["syms"].append(ss)
                d = float(r["delta"])
                if e["best"] is None or d > e["best"]:
                    e["best"] = round(d, 6)
                stats["positives"] += 1
        wm[p] = {"off": off, "size": size}
        stats["files"] += 1
        stats["bytes"] += size
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for cs in CAT_SIDES:
        tmp = out_dir / f"must_pos_{cs}.json.tmp"
        tmp.write_text(json.dumps({"meta": {"cat_side": cs, "built_at": at, "n_items": len(idx[cs]), "source": "host delta logs (real evals, valid, delta > 0, trades >= 10)"}, "items": idx[cs]}))
        os.replace(tmp, out_dir / f"must_pos_{cs}.json")
    tmp = wm_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(wm))
    os.replace(tmp, wm_path)
    stats["wall_s"] = round(time.time() - t0, 1)
    stats["items"] = {cs: len(idx[cs]) for cs in CAT_SIDES}
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="~/v15_run3*/progress/v15_delta_log/*.jsonl")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "learn"))
    ap.add_argument("--max-s", type=float, default=1800.0)
    a = ap.parse_args()
    print(json.dumps(build(a.logs, Path(a.out_dir), a.max_s)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
