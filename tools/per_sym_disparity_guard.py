#!/usr/bin/env python3
"""Does LIVE actually hold the per-sym settings its published board dictates? Measured through the live code.

USER 2026-10-20: "Make sure it also monitors/alerts/fixes any disparity with what the per_sym settings dictate".

This does NOT reimplement the comparison — reimplementing it is how two answers get born, and it already happened
here once: the go-live applier compared the overrides COLUMN against its own diff-only key set and called 139
sym_sides already-live while the live entry gate saw them off-board (2026-10-16). So both questions are asked of
the one authority, `live_twins.vec_exact`:

  latest_board_ok(symbol, side)  -> (ok, why)   the ACTUAL gate live uses before any OPEN/AUGMENT
  board_live_diff(...)           -> [keys]      the ONE comparator shared by that gate and the applier

That makes the verdict operational rather than academic: on crypto a False from `latest_board_ok` refuses EVERY
entry for that sym_side, silently, while exits keep working — a sym_side that looks alive and simply never opens
again. That is the disparity worth alerting on, and it is why the `why` string is reported verbatim.

Known-benign classes are separated from real drift, because they are not bugs and burying them hides the rest:
  SIZING_CAPPED    the only differing keys are the live minimum-trading sizing caps (CRYPTO_MINIMUM_TRADING_MODE
                   outranks every board by design, and per_sym_live_set._apply_caps clamps them on read)
  BOARD_TOO_OLD    the board exists but is older than the live age bar — a vintage decision, not drift
  NO_LATEST_BOARD  nothing published for that sym_side yet

--fix does NOT write live settings itself. The single sanctioned writer is
`tools/v15_persym_golive.py golive --published --pin-all`; this only invokes it and re-measures afterwards, so
there is never a second writer of per-sym state.

Exit 0 = no real drift, 1 = drift found (or the fix did not converge), 2 = could not measure.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(os.environ.get("BASE_PATH") or pathlib.Path(__file__).resolve().parents[1])
os.environ.setdefault("BASE_PATH", str(ROOT))
sys.path.insert(0, str(ROOT))

# the live sizing caps: per_sym_live_set._apply_caps clamps these on every read, so a board that names a bigger
# size can never match the live set and must not be reported as drift (measured: 4 of these differ on 60/60 crypto
# sym_sides, and START_POSITION_SIZE alone refused entries on 25 of 60 when the capped set was fed to the gate)
CAP_KEYS = {"START_POSITION_SIZE", "MAX_POSITION_SIZE", "MAX_ORDER_VALUE", "MAX_ORDER_VALUE_FIN", "MAX_ORDER_VALUE_MEN"}


def live_universe() -> list:
    """The sym_sides live is actually allowed to trade (symbols_<acct>_<side>.json), crypto + stocks."""
    out = []
    for acct, venue in (("ang", "crypto"), ("trb", "stocks")):
        for side in ("long", "short"):
            p = ROOT / f"symbols_{acct}_{side}.json"
            try:
                for sym in json.loads(p.read_text()):
                    out.append((f"{str(sym).upper()}_{side.upper()}", venue))
            except Exception:
                continue
    return sorted(set(out))


def measure(symsides: list) -> dict:
    import live_twins.vec_exact as vx
    sel_path = ROOT / "data" / "published_selection.json"
    try:
        sel = json.loads(sel_path.read_text()).get("sym_sides") or {}
    except Exception as exc:
        return {"error": f"cannot read {sel_path}: {exc}"}
    rows, classes = [], collections.Counter()
    for symside, venue in symsides:
        symbol, side = symside.rsplit("_", 1)
        board = sel.get(symside) or {}
        try:
            ok, why = vx.latest_board_ok(symbol, side)
        except Exception as exc:
            ok, why = False, f"GATE_ERROR_{type(exc).__name__}"
        diff = []
        if board.get("overrides"):
            try:
                diff = vx.board_live_diff(symbol, side, board.get("overrides"), cat_side=board.get("cat_side") or None)
            except Exception as exc:
                diff = [f"<diff error {type(exc).__name__}>"]
        if ok:
            klass = "OK"
        elif str(why).startswith("NO_LATEST_BOARD"):
            klass = "NO_LATEST_BOARD"
        elif str(why).startswith("BOARD_TOO_OLD") or "VINTAGE" in str(why).upper():
            klass = "BOARD_TOO_OLD"
        elif diff and set(diff) <= CAP_KEYS:
            klass = "SIZING_CAPPED"
        elif str(why).startswith("LIVE_SET_NE_BOARD"):
            klass = "DRIFT"
        else:
            klass = "OTHER"
        classes[klass] += 1
        rows.append({"sym_side": symside, "venue": venue, "entry_allowed": bool(ok), "class": klass,
                     "why": str(why)[:160], "diff_keys": sorted(diff)[:20], "n_diff": len(diff),
                     "board_md5": str(board.get("overrides_md5") or "")[:8],
                     "n_board_overrides": board.get("n_overrides")})
    drift = [r for r in rows if r["class"] == "DRIFT"]
    key_hist = collections.Counter(k for r in drift for k in r["diff_keys"])
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n": len(rows),
            "classes": dict(classes.most_common()), "n_drift": len(drift),
            "n_entry_blocked": sum(1 for r in rows if not r["entry_allowed"]),
            "drift_keys": dict(key_hist.most_common(30)), "rows": rows}


def run_applier(timeout_s: float) -> dict:
    """The ONLY sanctioned writer of per-sym live settings."""
    cmd = [sys.executable, str(ROOT / "tools" / "v15_persym_golive.py"), "golive", "--published", "--pin-all"]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout_s)
        return {"cmd": " ".join(cmd), "rc": p.returncode, "tail": (p.stdout or "")[-2000:], "err": (p.stderr or "")[-800:]}
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(cmd), "rc": None, "error": f"timed out after {timeout_s}s"}
    except Exception as exc:
        return {"cmd": " ".join(cmd), "rc": None, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "data" / "reports" / "per_sym_disparity.json"))
    ap.add_argument("--jsonl", help="append one compact line per run")
    ap.add_argument("--fix", action="store_true", help="invoke tools/v15_persym_golive.py and re-measure")
    ap.add_argument("--fix-timeout", type=float, default=900.0)
    ap.add_argument("--max-drift", type=int, default=0, help="drifted sym_sides tolerated before exit 1")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    universe = live_universe()
    if not universe:
        print("per_sym_disparity: EMPTY live universe (symbols_*.json unreadable) — cannot measure", flush=True)
        return 2
    rep = measure(universe)
    if rep.get("error"):
        print(f"per_sym_disparity: {rep['error']}", flush=True)
        return 2
    if a.fix and rep["n_drift"] > 0:
        rep["applier"] = run_applier(a.fix_timeout)
        after = measure(universe)
        rep["after_fix"] = {k: after[k] for k in ("classes", "n_drift", "n_entry_blocked", "drift_keys")}
        rep["converged"] = after["n_drift"] <= a.max_drift
        rep["rows"] = after["rows"]
        rep["n_drift_after"] = after["n_drift"]

    pathlib.Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.json).write_text(json.dumps(rep, indent=1, default=str))
    final_drift = rep.get("n_drift_after", rep["n_drift"])
    if a.jsonl:
        with open(a.jsonl, "a") as fh:
            fh.write(json.dumps({"ts": rep["ts"], "epoch": int(time.time()), "n": rep["n"],
                                 "n_drift": final_drift, "n_entry_blocked": rep["n_entry_blocked"],
                                 "classes": rep["classes"], "fixed": bool(a.fix and rep["n_drift"] > 0)}) + "\n")

    alert = ROOT / "data" / "reports" / "ALERT_PER_SYM_DISPARITY.txt"
    if final_drift > a.max_drift:
        lines = [f"[{rep['ts']}] {final_drift} sym_sides hold per-sym settings that DIFFER from their published board.",
                 f"{rep['n_entry_blocked']} of {rep['n']} live sym_sides cannot OPEN at all right now "
                 f"(latest_board_ok False -> every entry refused; exits still work).",
                 f"classes: {json.dumps(rep['classes'])}",
                 f"top differing keys: {json.dumps(dict(list(rep['drift_keys'].items())[:12]))}"]
        for r in [x for x in rep["rows"] if x["class"] == "DRIFT"][:25]:
            lines.append(f"  {r['sym_side']:22s} n_diff={r['n_diff']:3d} {','.join(r['diff_keys'][:4])}  why={r['why'][:70]}")
        lines.append("FIX: python3 tools/per_sym_disparity_guard.py --fix   (delegates to tools/v15_persym_golive.py)")
        alert.write_text("\n".join(lines) + "\n")
    else:
        try:
            alert.unlink()
        except FileNotFoundError:
            pass

    if not a.quiet or final_drift > a.max_drift:
        print(f"PER_SYM_DISPARITY n={rep['n']} drift={final_drift} entry_blocked={rep['n_entry_blocked']} "
              f"classes={json.dumps(rep['classes'])}", flush=True)
        for r in [x for x in rep["rows"] if x["class"] == "DRIFT"][:15]:
            print(f"  DRIFT {r['sym_side']:22s} n_diff={r['n_diff']:3d} {','.join(r['diff_keys'][:4])}", flush=True)
        if "applier" in rep:
            print(f"  applier rc={rep['applier'].get('rc')} converged={rep.get('converged')}", flush=True)
    return 1 if final_drift > a.max_drift else 0


if __name__ == "__main__":
    sys.exit(main())
