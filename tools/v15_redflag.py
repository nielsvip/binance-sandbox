#!/usr/bin/env python3
"""v15_redflag — hunts non-credible sheet-fill patterns in live pilot output.

USER order 2026-09-28: "red flag any 0 or repeated delta and investigate immediately".
Flags (per active sym_side, newest delta logs + progress JSONs):
  REPEATED_DELTA  same non-zero delta for K+ consecutive DIFFERENT candidates (echo)
  REPEATED_GAIN   same gain_pct across K+ consecutive different variants (echo)
  ALL_ZERO_LOG    every recent valid eval delta==0 across multiple sheets (dead engine path?)
  ERR_SPIKE       >20% of recent evals errored/timed out
  STALE_LOG       delta log idle >15 min while its pilot process is alive
  ECHO_ROWS       done-record rows where 5+ yellow deltas are exactly equal non-zero
Exit 0 = no flags. Prints one line per flag; --json for machine output.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "reports" / "lifecycle_pilot" / "v15_delta_log"
PROG_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"


def tail_lines(path, n):
    try:
        out = subprocess.run(["tail", "-n", str(n), str(path)], capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines()
    except Exception:
        return []


def pilot_alive(symside):
    try:
        out = subprocess.run(["pgrep", "-f", f"v15.*{symside}"], capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip())
    except Exception:
        return False


def scan_log(path, lines_n=600, rep_k=6):
    flags = []
    symside = path.name.split("_jump")[0].split(".jsonl")[0]
    recs = []
    for line in tail_lines(path, lines_n):
        try:
            d = json.loads(line)
        except Exception:
            continue
        recs.append(d)
    if not recs:
        return flags
    # STALE
    age_min = (time.time() - path.stat().st_mtime) / 60
    if age_min > 15 and pilot_alive(symside):
        flags.append(f"STALE_LOG {symside}: idle {age_min:.0f} min with pilot alive")
    # CELL_RATE (USER: monitor cell by cell): cells/minute over the last 10 min,
    # from per-record ts fields; 0 cells with the log recently active and a pilot
    # alive = the pilot is running but not producing cells -> STALLED_CELLS.
    if age_min <= 15:
        now = time.time()
        recent = 0
        last_key = None
        for d in recs:
            ts = d.get("ts")
            t = None
            if isinstance(ts, (int, float)):
                t = float(ts)
            elif isinstance(ts, str):
                try:
                    import datetime as _dt
                    t = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    t = None
            if t is not None and now - t <= 600:
                recent += 1
                last_key = f"{d.get('sheet')}!{d.get('row')}:{d.get('switch')}={d.get('cand')}"
        if recent == 0 and age_min > 10 and pilot_alive(symside):
            last = recs[-1]
            flags.append(f"STALLED_CELLS {symside}: 0 cells in 10 min with pilot alive; last cell {last.get('sheet')}!{last.get('row')}:{last.get('switch')}={last.get('cand')}")
    # ERR spike
    errs = sum(1 for d in recs if (d.get("err") or "").strip())
    if len(recs) >= 30 and errs / len(recs) > 0.20:
        top = Counter((d.get("err") or "")[:40] for d in recs if (d.get("err") or "").strip()).most_common(2)
        flags.append(f"ERR_SPIKE {symside}: {errs}/{len(recs)} recent evals errored; top: {top}")
    # repeated non-zero delta across consecutive DIFFERENT candidates
    run_val, run_len, run_keys = None, 0, set()
    best = (0, None, 0)
    zero_valid = 0
    valid_n = 0
    gain_run_val, gain_run_len, gain_keys = None, 0, set()
    gain_best = (0, None)
    sheets_seen = set()
    gate_blocks = {}
    for d in recs:
        delta = d.get("delta")
        key = f"{d.get('sheet')}!{d.get('row')}:{d.get('switch')}={d.get('cand')}|{d.get('label')}"
        sheets_seen.add(d.get("sheet"))
        label = str(d.get("label") or "")
        # FINAL_RECHECK/ORANGE/JOINT re-test filters against one fixed config: identical
        # gains there are by construction, not echo (verified 2026-09-28 ETCUSDT: 240
        # recheck hits, real trades, delta 0). Exclude from run heuristics.
        if label.startswith(("FINAL_RECHECK", "ORANGE", "JOINT")):
            continue
        # candidates that zero out ALL trades share delta == -cum_before honestly
        # (harsh real gates like HTF direction); track separately as info, not echo.
        g0 = d.get("gain_pct")
        if g0 is not None and abs(float(g0)) < 1e-12 and (d.get("trades") in (0, None)):
            gate_blocks[key.split("|")[0]] = True
            continue
        if d.get("valid"):
            valid_n += 1
            if delta is not None and abs(float(delta)) < 1e-12:
                zero_valid += 1
        if delta is not None and abs(float(delta)) > 1e-9:
            v = round(float(delta), 10)
            if v == run_val:
                run_len += 1
                run_keys.add(key)
            else:
                run_val, run_len, run_keys = v, 1, {key}
            if run_len > best[0] and len(run_keys) >= rep_k:
                best = (run_len, v, len(run_keys))
        g = d.get("gain_pct")
        if g is not None:
            gv = round(float(g), 10)
            if gv == gain_run_val:
                gain_run_len += 1
                gain_keys.add(key)
            else:
                gain_run_val, gain_run_len, gain_keys = gv, 1, {key}
            if gain_run_len > gain_best[0] and len(gain_keys) >= rep_k * 4:
                gain_best = (gain_run_len, gv)
    if best[0] >= rep_k:
        flags.append(f"REPEATED_DELTA {symside}: delta {best[1]} repeated {best[0]}x across {best[2]} distinct candidates")
    if gain_best[0] >= rep_k * 4:
        flags.append(f"REPEATED_GAIN {symside}: gain {gain_best[1]} repeated {gain_best[0]}x across distinct variants (echo?)")
    if valid_n >= 100 and zero_valid == valid_n and len(sheets_seen) >= 3:
        flags.append(f"ALL_ZERO_LOG {symside}: {valid_n} recent VALID evals all delta==0 across {len(sheets_seen)} sheets")
    if len(gate_blocks) >= 20:
        flags.append(f"INFO_GATE_BLOCKS_ALL {symside}: {len(gate_blocks)} candidates zero out all trades (harsh real gates — honest, review if unexpected)")
    return flags


def scan_progress(path, echo_min=5):
    flags = []
    try:
        d = json.loads(path.read_text())
    except Exception:
        return flags
    done = d.get("done") or {}
    echo_rows = []
    for key, rec in done.items():
        yl = [v for v in (rec.get("yellows") or {}).values() if isinstance(v, (int, float)) and abs(v) > 1e-9]
        if len(yl) >= echo_min and len(set(round(v, 10) for v in yl)) == 1:
            echo_rows.append(key)
    if echo_rows:
        flags.append(f"ECHO_ROWS {path.name}: {len(echo_rows)} rows where {echo_min}+ yellows are one identical non-zero value (e.g. {echo_rows[0]})")
    return flags



def scan_coverage(active_symsides, state_path="/tmp/v15_coverage_state.json"):
    """USER 2026-09-28: 'monitor logs and empty cells'. For each active sym_side, count
    filled data cells (E/F/G numeric + non-empty allowlisted yellows) in its workbook and
    flag EMPTY_SHEET (coverage <2% with >200 expected) and NO_CELL_PROGRESS (coverage
    unchanged since last cycle while its pilot is alive)."""
    import openpyxl
    flags = []
    try:
        prev = json.loads(Path(state_path).read_text())
    except Exception:
        prev = {}
    cur = {}
    for ss in active_symsides:
        wb_path = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{ss}_30d_matrix.xlsx"
        if not wb_path.exists():
            continue
        try:
            wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=False)
        except Exception as e:
            flags.append(f"WB_UNREADABLE {ss}: {e}")
            continue
        filled = expected = 0
        for sn in wb.sheetnames:
            if sn not in ("STDEV_SLOPE_SIZING","ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"):
                continue
            ws = wb[sn]
            for row in ws.iter_rows(min_row=3, max_col=8):
                vals = [c.value for c in row] + [None] * 8
                if vals[0] in (None, ""):
                    continue
                for idx in (4, 5, 6):
                    expected += 1
                    if isinstance(vals[idx], (int, float)):
                        filled += 1
        wb.close()
        cur[ss] = filled
        if expected > 200 and filled < expected * 0.02:
            flags.append(f"EMPTY_SHEET {ss}: {filled}/{expected} E/F/G cells filled")
        if ss in prev and prev[ss] == filled and pilot_alive(ss):
            flags.append(f"NO_CELL_PROGRESS {ss}: stuck at {filled} filled cells since last cycle (pilot alive)")
    try:
        Path(state_path).write_text(json.dumps(cur))
    except Exception:
        pass
    return flags

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", type=int, default=12, help="newest N delta logs to scan")
    ap.add_argument("--lines", type=int, default=600)
    ap.add_argument("--progress-age-min", type=int, default=120, help="scan progress JSONs modified in last N min")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    flags = []
    logs = sorted(LOG_DIR.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime)[: args.logs] if LOG_DIR.exists() else []
    for p in logs:
        flags += scan_log(p, args.lines)
    active = [p.name.split("_jump")[0].split(".jsonl")[0] for p in logs if time.time() - p.stat().st_mtime < 1800]
    flags += scan_coverage(active)
    cutoff = time.time() - args.progress_age_min * 60
    for p in sorted(PROG_DIR.glob("*_progress*.json"), key=lambda q: -q.stat().st_mtime):
        if p.stat().st_mtime < cutoff:
            break
        flags += scan_progress(p)
    # FINISHED_NOT_SAVED (USER: sheets saved with bh/gain in title): the finisher's
    # state marks completion; a finished entry with no artifact for >30 min, or a
    # finisher daemon that stopped logging for >30 min while alive, is a flag.
    try:
        fstate = json.loads((PROG_DIR / "finisher_state.json").read_text())
        for ss, st in fstate.items():
            fin_at = st.get("finished_at")
            if st.get("artifact"):
                continue
            held_since = st.get("json_mtime")
            if st.get("held") and held_since and time.time() - held_since > 1800:
                flags.append(f"FINISHED_NOT_SAVED {ss}: held >30min ({st.get('held')}) — no bh/gain artifact")
    except Exception:
        pass
    if args.json:
        print(json.dumps({"host": os.uname().nodename, "ts": time.time(), "flags": flags}))
    else:
        if flags:
            for f in flags:
                print(f"[REDFLAG] {f}")
        else:
            print(f"[v15_redflag] clean: {len(logs)} logs scanned, no flags")
    sys.exit(1 if flags else 0)


if __name__ == "__main__":
    main()
