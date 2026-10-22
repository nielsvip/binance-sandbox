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


def naked_key(rec: dict) -> str:
    return "|".join(str(rec.get(k)) for k in ("sym_side", "sheet", "row", "switch", "cand"))


def is_naked_line(line: str) -> bool:
    return '"label": "naked"' in line and '"delta": ' in line


def split_item(item: str):
    """'SW=c' -> (SW, c, None); 'SW=c@F=o' -> (SW, c, 'F=o')."""
    sw, rest = item.split("=", 1)
    if "@" in rest:
        c, hdr = rest.split("@", 1)
        return sw, c, hdr
    return sw, rest, None


def load_bmap(cs: str, bmap_dir: Path):
    """-> (binding map or None, why). Fail-open: absent/mismatched map = no drop."""
    p = Path(bmap_dir) / f"binding_map_{cs}.json"
    if not p.exists():
        return None, f"absent {p}"
    try:
        from tools import v18_binding_map as BM
        return BM.load_binding_map(p, cs), f"ok {p.name}"
    except Exception as e:
        return None, f"refused {p.name}: {e}"[:200]


def hygiene(cs: str, items: dict, bm) -> tuple:
    """-> (published items, dropped {reason: n}, dropped sample, demoted_to_row n, beats_unknown n). Spec 22 §4.1/§4.2."""
    from tools import v18_binding_map as BM
    out, dropped, sample, demoted, unknown = {}, {}, [], 0, 0
    for k, e in items.items():
        try:
            sw, c, hdr = split_item(k)
        except ValueError:
            out[k] = dict(e)
            continue
        if bm is not None:
            if hdr is None:
                v = {"verdict": "NEVER", "reason": "DEAD_ROW"} if ((bm.get("switches") or {}).get(sw) or {}).get("dead") else {"verdict": "BINDS", "reason": ""}
            else:
                v = BM.pair_verdict(bm, sw, c, hdr, e.get("sheets") or None)
            if v.get("verdict") == "NEVER" and v.get("reason") in BM.DROP_REASONS:
                dropped[v["reason"]] = dropped.get(v["reason"], 0) + 1
                if len(sample) < 40:
                    sample.append({"item": k, "reason": v["reason"]})
                continue
        pub = {kk: vv for kk, vv in e.items() if kk not in ("beats_syms", "nk_unknown")}
        if hdr is None:
            pub["positive_class"] = "row"
        else:
            beats = e.get("beats_syms")
            if beats is None or (not beats and e.get("nk_unknown")):
                pub.update(positive_class="pair", beats_naked=None)
                unknown += 1
            elif beats:
                pub.update(positive_class="pair", beats_naked=True, beats_syms=list(beats))
            else:
                demoted += 1
                continue
        out[k] = pub
    return out, dropped, sample, demoted, unknown


def build(log_glob: str, out_dir: Path, max_s: float = 1800.0, bmap_dir=None) -> dict:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    bmap_dir = Path(bmap_dir) if bmap_dir else out_dir
    wm_path = out_dir / "must_pos_watermarks.json"
    try:
        wm = json.loads(wm_path.read_text())
    except Exception:
        wm = {}
    idx = {}
    migrated = False
    for cs in CAT_SIDES:
        try:
            idx[cs] = json.loads((out_dir / f"must_pos_state_{cs}.json").read_text()).get("items") or {}
        except Exception:
            try:
                idx[cs] = {k: {**v, "beats_syms": None} if "@" in k else v for k, v in (json.loads((out_dir / f"must_pos_{cs}.json").read_text()).get("items") or {}).items()}
            except Exception:
                idx[cs] = {}
            migrated = True
    if migrated:
        wm = {}  # first build with pair classes: rescan every log from 0 so legacy pairs learn beats_naked (idempotent: syms dedup, best max)
    files = sorted({str(Path(p)) for g in log_glob.split(",") for p in glob.glob(os.path.expanduser(g))}, key=lambda p: -os.path.getmtime(p))
    stats = {"files": 0, "bytes": 0, "positives": 0, "timed_out": False, "rescan_from_zero": migrated}
    for p in files:
        if time.time() - t0 > max_s:
            stats["timed_out"] = True
            break
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        off = int((wm.get(p) or {}).get("off") or 0)
        nk = dict((wm.get(p) or {}).get("naked") or {})
        if off > size:
            off, nk = 0, {}
        if off == size:
            continue
        with open(p, "rb") as fh:
            fh.seek(off)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                off += len(raw)
                line = raw.decode("utf-8", "replace")
                if is_naked_line(line):
                    try:
                        rn = json.loads(line)
                        nkk = naked_key(rn)
                        nk.pop(nkk, None)
                        nk[nkk] = float(rn["delta"]) if rn.get("valid") and not rn.get("err") and rn.get("delta") is not None else None
                        if len(nk) > 64:
                            nk.pop(next(iter(nk)))
                    except Exception:
                        pass
                r = scan_line(line)
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
                sh = r.get("sheet")
                if sh and sh not in e.setdefault("sheets", []):
                    e["sheets"].append(sh)
                if "@" in k:
                    nd = nk.get(naked_key(r), "missing")
                    if e.get("beats_syms") is None:
                        e["beats_syms"] = []
                    if nd == "missing" or nd is None:
                        e["nk_unknown"] = int(e.get("nk_unknown") or 0) + 1
                    elif d > nd + 1e-9 and ss not in e["beats_syms"]:
                        e["beats_syms"].append(ss)
                stats["positives"] += 1
        wm[p] = {"off": off, "size": size, "naked": nk}
        stats["files"] += 1
        stats["bytes"] += size
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stats["hygiene"] = {}
    for cs in CAT_SIDES:
        tmp = out_dir / f"must_pos_state_{cs}.json.tmp"
        tmp.write_text(json.dumps({"meta": {"cat_side": cs, "built_at": at, "n_items": len(idx[cs]), "note": "full index state (pre-hygiene); the pilot reads must_pos_<CS>.json"}, "items": idx[cs]}))
        os.replace(tmp, out_dir / f"must_pos_state_{cs}.json")
        bm, why = load_bmap(cs, bmap_dir)
        if bm is None:
            print(f"[MUST-INDEX] {cs} binding map {why} -> no hygiene drop (fail-open)", file=sys.stderr)
        pub, dropped, sample, demoted, unknown = hygiene(cs, idx[cs], bm)
        stats["hygiene"][cs] = {"binding_map": why, "dropped": dropped, "demoted_to_row": demoted, "beats_unknown": unknown, "published": len(pub)}
        tmp = out_dir / f"must_pos_{cs}.json.tmp"
        tmp.write_text(json.dumps({"meta": {"cat_side": cs, "built_at": at, "n_items": len(pub), "n_state_items": len(idx[cs]), "binding_map": why, "source": "host delta logs (real evals, valid, delta > 0, trades >= 10); pairs only when the cell beat its row's naked on >= 1 sym_side (spec 22 §4.1)"}, "_dropped": dropped, "_dropped_sample": sample, "_demoted_to_row": demoted, "_beats_unknown": unknown, "items": pub}))
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
