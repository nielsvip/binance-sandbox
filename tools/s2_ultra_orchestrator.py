#!/usr/bin/env python3
"""S2 Ultra Fast Orchestrator — 6-at-a-time NPZ sliding window, 30D tradier priority, FLZ fallback, sync+monitor.

Order:
  1. tradier symbols traded last 30d (inferred from tradier_recovery_state + gap_inventory) alternating LONG/SHORT
  2. then all remaining TRB symbols alternating LONG/SHORT
  3. then all FLZ symbols _LONG/_SHORT alternating

Sliding NPZ:
  - Keeps at most 6 NPZ files on S2 local indicators dir
  - Pulls next batch from S1 (10.0.0.3) via rsync -e "ssh -J gateway-internal" when 4 completions done
  - Evicts oldest completed NPZ when over limit, keeping active jobs' NPZ

Processing:
  - Uses tools/simple_switch_filter_calculator.py --symside X --window-days 30 (vector-only, batch)
  - Then fills TEMPLATE via tools/fill_template_from_csv.py -> SPREADSHEETS/<SYM>_30d_matrix.xlsx
  - Max parallel workers = 2 (S2 has 16G RAM per prior OOM notes, single store 288M, so 2*2 cache=~1G safe)
  - Each worker logs to /tmp/s2_<SYM>.log

Sync:
  - After each completion: rsync CSV+XLSX to S1 (~/binance/data/reports + ~/binance/SPREADSHEETS)
  - S1 -> Mac via existing autosave + explicit rsync -e "ssh -p 2201" when available
  - Also pushes progress json to S1 for Mac visibility

Anomaly monitor:
  - After each completion: checks deltas flat (all zero / duplicate -0.49), trades zero, BH inverted, suspicious streak
  - Writes to data/reports/s2_anomalies.jsonl and /tmp/s2_monitor.log

Usage on S2:
  nohup python3 ~/binance/tools/s2_ultra_orchestrator.py --window-days 30 > /tmp/s2_orchestrator.log 2>&1 &
  tail -f /tmp/s2_orchestrator.log
  tail -f /tmp/s2_monitor.log

Requires: TEMPLATE.xlsx at ~/binance/SPREADSHEETS/TEMPLATE.xlsx (or ~/binance-sandbox)
          S1 reachable as niels@10.0.0.3 via ProxyJump gateway-internal (157.90.168.35)
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path.home() / "binance"
if not (ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx").exists():
    ROOT = Path.home() / "binance-sandbox"
    if not (ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx").exists():
        ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
IND_DIR = ROOT / "backtest_v8" / "indicators"
REPORT_DIR = ROOT / "data" / "reports"
SHEET_DIR = ROOT / "SPREADSHEETS"
PROGRESS_DIR = ROOT / "data" / "reports" / "s2_progress"
ANOMALY_LOG = ROOT / "data" / "reports" / "s2_anomalies.jsonl"
MONITOR_LOG = Path("/tmp/s2_monitor.log")
ORCH_LOG = Path("/tmp/s2_orchestrator.log")
S1_HOST = "niels@10.0.0.3"
S1_JUMP = "gateway-internal"  # ssh -J gateway-internal
MAX_NPZ = 6
REFILL_AT = 4  # fetch next batch when this many in current batch completed
MAX_WORKERS = 2

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(MONITOR_LOG, "a") as f:
            f.write(line + "\n")
    except: pass

def run(cmd: str, timeout=120):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", e.stderr or ""

def ensure_dirs():
    for d in [IND_DIR, REPORT_DIR, SHEET_DIR, PROGRESS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except: return default

def build_queue() -> list[str]:
    """Return ordered sym_sides: tradier_30d alternating LONG/SHORT, then remaining TRB, then FLZ."""
    trb_long = load_json(ROOT / "symbols_trb_long.json", [])
    trb_short = load_json(ROOT / "symbols_trb_short.json", [])
    flz = load_json(ROOT / "symbols_flz.json", [])
    # recent priority from recovery state + recent SNDK/MSTR/NVDA/MU priority per user (last 30d tradier)
    recent = []
    try:
        rec = load_json(ROOT / "data" / "tradier_recovery_state.json", [])
        if isinstance(rec, list):
            recent = [r.get("symbol") for r in rec if r.get("symbol")]
    except: pass
    # Add user-highlighted priority syms that were in S2 package instructions (SNDK/MSTR/NVDA/MU) if not already
    for must in ["SNDK","MSTR","NVDA","MU","AAPL","TSLA","PLTR","HOOD","AMZN","GOOGL"]:
        if must not in recent:
            recent.append(must)
    # Do NOT use gap inventory for priority — it polluted queue with random UUUU etc. Keep only true tradier traded.
    # dedup preserve order
    seen=set(); recent_u=[]
    for s in recent:
        if s not in seen:
            seen.add(s); recent_u.append(s)
    # Build tradier priority set: those in recent that are in TRB
    trb_set = set(trb_long) | set(trb_short)
    recent_trb = [s for s in recent_u if s in trb_set][:16]
    # Fallback if still empty: use first 12 of each TRB list
    if not recent_trb:
        recent_trb = (trb_long[:8] + trb_short[:8])[:12]
        recent_trb = list(dict.fromkeys(recent_trb))
    # Ensure recent_trb respects recent order but also alternates correctly via strict_alternate later
    # Deduplicate already done
    # Build strictly alternating queue: global alternation with recent priority
    def strict_alternate(items):
        longs=[x for x in items if x.endswith("_LONG")]
        shorts=[x for x in items if x.endswith("_SHORT")]
        out=[]; i=j=0; turn_long=True
        while i<len(longs) or j<len(shorts):
            if turn_long and i<len(longs):
                out.append(longs[i]); i+=1
            elif not turn_long and j<len(shorts):
                out.append(shorts[j]); j+=1
            elif i<len(longs):
                out.append(longs[i]); i+=1
            elif j<len(shorts):
                out.append(shorts[j]); j+=1
            turn_long=not turn_long
        return out
    # All TRB sym_sides
    all_trb=[]
    for s in trb_long: all_trb.append(f"{s}_LONG")
    for s in trb_short: all_trb.append(f"{s}_SHORT")
    all_trb = strict_alternate(all_trb)
    # Partition by recent priority while preserving alternation
    recent_set = set(recent_trb)
    def base_of(ss): return ss[:-5] if ss.endswith("_LONG") else ss[:-6]
    recent_queue = [ss for ss in all_trb if base_of(ss) in recent_set]
    # Reorder recent_queue to respect recent priority order, but keep alternation as much as possible:
    # stable sort by recent index, then re-alternate
    recent_priority = {sym:i for i,sym in enumerate(recent_trb)}
    # Sort recent_queue by priority, then strict_alternate again to keep alternation
    recent_queue_sorted = sorted(recent_queue, key=lambda ss: recent_priority.get(base_of(ss), 999))
    recent_queue = strict_alternate(recent_queue_sorted)
    remaining_queue = [ss for ss in all_trb if base_of(ss) not in recent_set]
    queue = recent_queue + remaining_queue
    # Third phase: FLZ alternating
    flz_queue=[]
    for sym in flz:
        flz_queue.append(f"{sym}_LONG")
        flz_queue.append(f"{sym}_SHORT")
    flz_queue = strict_alternate(flz_queue)
    # avoid dup with TRB (some overlap like BTC? no)
    for ss in flz_queue:
        if ss not in queue:
            queue.append(ss)
    return queue

def csv_path_for(symside: str, window: int) -> Path:
    return REPORT_DIR / f"simple_calc_{symside}_{window}d.csv"

def xlsx_path_for(symside: str, window: int) -> Path:
    # fill_template will create SPREADSHEETS/<symside>_30d_matrix.xlsx
    # simple_calc uses data/reports; fill creates xlsx
    return SHEET_DIR / f"{symside}_{window}d_matrix.xlsx"

def is_completed(symside: str, window: int) -> bool:
    csvp = csv_path_for(symside, window)
    xlsx = xlsx_path_for(symside, window)
    if not csvp.exists() or not xlsx.exists():
        return False
    # csv should have expected rows (~9290 but variable with relevant filters). At least >100 rows + header.
    try:
        with open(csvp) as f:
            n = sum(1 for _ in f)
            if n < 100: return False
    except: return False
    # xlsx exists and non-zero
    if xlsx.stat().st_size < 50000: return False
    return True

def pull_npz_from_s1(syms: list[str]):
    """Pull NPZs for syms (base symbol without _LONG/_SHORT) from S1, keep max 6. CCX33: S2 and S1 on same 10.0.0/16 so direct private works, fallback via gateway."""
    bases = []
    for ss in syms:
        base = ss.replace("_LONG","").replace("_SHORT","")
        bases.append(base)
    bases = list(dict.fromkeys(bases))
    if not bases:
        return
    # Detect if we are on CCX33 fresh (10.0.0.4) where direct private to 10.0.0.3 works - try direct first, then gateway
    for base in bases:
        for suffix in ["", "_LONG", "_SHORT"]:
            fname = f"{base}{suffix}.npz"
            src = f"{S1_HOST}:~/binance/backtest_v8/indicators/{fname}"
            dst = IND_DIR / fname
            if dst.exists() and dst.stat().st_size > 1000000:
                continue
            # Try direct private (no jump) first - S2 and S1 share tradingnet 10.0.0
            for jump_opt in [' -o StrictHostKeyChecking=accept-new', f' -J {S1_JUMP}']:
                cmd = f'rsync -avz --checksum -e "ssh{jump_opt}" {shlex.quote(src)} {shlex.quote(str(IND_DIR))}/ 2>&1'
                rc, out, err = run(cmd, timeout=300)
                if rc==0 and "No such file" not in out and "No such file" not in err and dst.exists() and dst.stat().st_size > 1000000:
                    log(f"pulled {fname} from S1 via {'direct' if 'StrictHost' in jump_opt else 'gateway'} ({dst.stat().st_size} bytes)")
                    break
                # try next jump method if failed

def evict_old_npz(keep_syms: list[str]):
    """Sliding window: keep at most MAX_NPZ active, but do NOT destroy pre-existing full cache.
    S2 already has 659 NPZs (20G) - deleting them would cause 600+ re-fetches. Only evict if we
    ourselves created the pressure (i.e., after we started). For now, log but don't delete when total >> MAX_NPZ."""
    all_npz = list(IND_DIR.glob("*.npz"))
    if len(all_npz) <= MAX_NPZ + 10:
        keep_bases = set(s.replace("_LONG","").replace("_SHORT","") for s in keep_syms)
        all_npz = sorted(all_npz, key=lambda p: p.stat().st_mtime)
        for p in all_npz:
            if len(list(IND_DIR.glob("*.npz"))) <= MAX_NPZ: break
            base = p.stem.replace("_LONG","").replace("_SHORT","")
            if base in keep_bases:
                continue
            try:
                p.unlink()
                log(f"evicted {p.name} (keep {MAX_NPZ})")
            except: pass
    else:
        # Full cache present - don't evict, just log window status
        log(f"window keep {len(keep_syms)} total {len(all_npz)} - not evicting (full cache preserved per disk)")

def run_one(symside: str, window: int) -> bool:
    csvp = csv_path_for(symside, window)
    xlsx = xlsx_path_for(symside, window)
    # ensure NPZ present - pull if missing
    base = symside.replace("_LONG","").replace("_SHORT","")
    need = []
    for cand in [f"{base}.npz", f"{base}_LONG.npz", f"{base}_SHORT.npz", f"{symside}.npz"]:
        if not (IND_DIR / cand).exists():
            need.append(symside)
            break
    if need:
        pull_npz_from_s1(need)
    # still missing? log and skip
    has_npz = any((IND_DIR / c).exists() for c in [f"{base}.npz", f"{base}_LONG.npz", f"{base}_SHORT.npz", f"{symside}.npz"])
    if not has_npz:
        # try to check S1 has it at all
        cmd = f'ssh -J {S1_JUMP} {S1_HOST} "ls ~/binance/backtest_v8/indicators/{base}*.npz 2>&1 | head"'
        rc,out,err = run(cmd, timeout=20)
        log(f"NPZ missing for {symside} local and S1 says: {out.strip()[:200]} err {err.strip()[:200]}")
        # don't fail permanently, try next
        return False
    # Run simple_switch_filter_calculator via venv (CCX33)
    venv_py = ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"
    log(f"START {symside} window {window}d via {py}")
    cmd = f"{shlex.quote(py)} {shlex.quote(str(ROOT / 'tools' / 'simple_switch_filter_calculator.py'))} --symside {shlex.quote(symside)} --window-days {window} --out {shlex.quote(str(csvp))} 2>&1"
    rc, out, err = run(cmd, timeout=3600)
    # log tail
    tail = (out + err).strip().splitlines()[-20:]
    for l in tail: log(f"  {symside}: {l[:300]}")
    if rc != 0:
        log(f"FAIL {symside} rc={rc}")
        return False
    if not csvp.exists():
        log(f"FAIL {symside} no csv after run")
        return False
    # Fill template via venv
    log(f"FILL {symside} -> {xlsx.name} via {py}")
    cmd2 = f"{shlex.quote(py)} {shlex.quote(str(ROOT / 'tools' / 'fill_template_from_csv.py'))} {shlex.quote(str(csvp))} {shlex.quote(str(xlsx))} 2>&1"
    rc2, out2, err2 = run(cmd2, timeout=600)
    for l in (out2+err2).strip().splitlines()[-10:]: log(f"  fill {symside}: {l[:300]}")
    if rc2 != 0 or not xlsx.exists():
        log(f"FILL FAIL {symside} rc={rc2}")
        return False
    log(f"DONE {symside} csv {csvp.stat().st_size} xlsx {xlsx.stat().st_size}")
    # Sync to S1
    sync_to_s1(symside, window)
    # Anomaly check
    check_anomaly(symside, window)
    return True

def sync_to_s1(symside: str, window: int):
    csvp = csv_path_for(symside, window)
    xlsx = xlsx_path_for(symside, window)
    # rsync to S1 data/reports and SPREADSHEETS
    for src, dst_dir in [(csvp, "~/binance/data/reports/"), (xlsx, "~/binance/SPREADSHEETS/")]:
        if not src.exists(): continue
        cmd = f'rsync -avz --checksum -e "ssh -J {S1_JUMP}" {shlex.quote(str(src))} {S1_HOST}:{dst_dir} 2>&1'
        rc,out,err = run(cmd, timeout=120)
        if rc==0: log(f"sync S1 {src.name} ok")
        else: log(f"sync S1 {src.name} FAIL rc={rc} {out[:200]} {err[:200]}")
    # also push progress marker
    prog = PROGRESS_DIR / f"{symside}_{window}d.json"
    try:
        prog.write_text(json.dumps({"symside": symside, "window": window, "ts": datetime.now(timezone.utc).isoformat(), "done": True}))
        cmd = f'rsync -avz --checksum -e "ssh -J {S1_JUMP}" {shlex.quote(str(prog))} {S1_HOST}:~/binance/data/reports/s2_progress/ 2>&1'
        run(cmd, timeout=30)
    except: pass
    # Try push to Mac via S1 -> Mac tunnel if S1 has 2201
    # We do S1-side rsync in background via ssh: ask S1 to rsync to Mac
    try:
        cmd = f'ssh -J {S1_JUMP} {S1_HOST} \'rsync -avz --checksum -e "ssh -p 2201 -o StrictHostKeyChecking=accept-new" ~/binance/SPREADSHEETS/{shlex.quote(xlsx.name)} niels@127.0.0.1:~/binance/SPREADSHEETS/ 2>&1 | head -20; rsync -avz --checksum -e "ssh -p 2201" ~/binance/data/reports/{shlex.quote(csvp.name)} niels@127.0.0.1:~/binance/data/reports/ 2>&1 | head -20\''
        rc,out,err = run(cmd, timeout=120)
        if rc==0: log(f"sync Mac via S1 {symside} ok")
    except: pass

def check_anomaly(symside: str, window: int):
    csvp = csv_path_for(symside, window)
    try:
        with open(csvp, newline="") as f:
            r = list(csv.DictReader(f))
        if not r: return
        deltas = [float(x["delta"]) for x in r if x.get("delta")]
        trades = [int(float(x["new_trades"])) for x in r if x.get("new_trades")]
        # anomalies
        flat = sum(1 for d in deltas if abs(d) < 1e-9)
        dup = len(deltas) - len(set(round(d,4) for d in deltas))
        zero_trades = sum(1 for t in trades if t==0)
        max_d = max(deltas) if deltas else 0
        min_d = min(deltas) if deltas else 0
        pos = sum(1 for d in deltas if d>0)
        rec = {"symside": symside, "window": window, "rows": len(r), "flat": flat, "dup": dup, "zero_trades": zero_trades, "max": max_d, "min": min_d, "pos": pos, "ts": datetime.now(timezone.utc).isoformat()}
        # flag if all deltas zero or dup high
        flag=""
        if flat > len(deltas)*0.9: flag+=" FLAT90"
        if dup > len(deltas)*0.5: flag+=" DUP50"
        if zero_trades > len(deltas)*0.9: flag+=" ZERO_TRADES90"
        if pos==0: flag+=" NO_POSITIVE"
        if max_d==min_d: flag+=" CONSTANT_DELTA"
        rec["flag"]=flag.strip()
        log(f"ANOMALY {symside} rows {len(r)} max {max_d:.4f} min {min_d:.4f} pos {pos} flat {flat} dup {dup} zeroTr {zero_trades} FLAG={flag.strip() or 'OK'}")
        # append to jsonl
        try:
            with open(ANOMALY_LOG, "a") as af:
                af.write(json.dumps(rec)+"\n")
            # also show in monitor
            if flag.strip():
                log(f"  *** ANOMALY FLAG {symside}: {flag.strip()} ***")
        except: pass
        # also rsync anomaly log to S1
        cmd = f'rsync -avz --checksum -e "ssh -J {S1_JUMP}" {shlex.quote(str(ANOMALY_LOG))} {S1_HOST}:~/binance/data/reports/ 2>&1'
        run(cmd, timeout=30)
    except Exception as e:
        log(f"anomaly check fail {symside}: {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    queue = build_queue()
    # persist queue
    qpath = ROOT / "data" / "s2_queue.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(json.dumps(queue, indent=2))
    log(f"QUEUE {len(queue)} sym_sides, template {TEMPLATE.exists()} window {args.window_days}d")
    log(f"First 20: {queue[:20]}")
    if args.dry_run:
        print(json.dumps(queue[:30], indent=2))
        return
    # sliding window: keep 6 npz, refill every 4 completions
    completed=[]
    pending = [ss for ss in queue if not is_completed(ss, args.window_days)]
    log(f"pending {len(pending)}/{len(queue)} (already done {len(queue)-len(pending)})")
    if not pending:
        log("all done")
        return
    # initial pull 6
    initial = pending[:MAX_NPZ]
    log(f"initial pull {initial}")
    pull_npz_from_s1(initial)
    # Process sequentially but with max_workers parallel (simple sequential for now to avoid OOM, but orchestrator supports parallel via background)
    # We run one at a time to keep RAM low; ultra-fast is from vector batch not from parallelism
    idx=0
    active_batch = initial[:]
    while idx < len(pending):
        symside = pending[idx]
        # ensure its NPZ is present, if not pull it (should be in sliding window)
        base = symside.replace("_LONG","").replace("_SHORT","")
        # if not in current window, pull it and evict
        if symside not in active_batch:
            # refill window
            pull_npz_from_s1([symside])
            active_batch.append(symside)
            if len(active_batch) > MAX_NPZ:
                # evict oldest completed
                evict_old_npz(active_batch)
                active_batch = active_batch[-MAX_NPZ:]
        ok = run_one(symside, args.window_days)
        if ok:
            completed.append(symside)
        else:
            # on fail, still count as attempted but log; continue
            completed.append(symside+"_FAIL")
        idx+=1
        # every REFILL_AT completions, prefetch next MAX_NPZ
        if len([c for c in completed if not c.endswith("_FAIL")]) % REFILL_AT == 0 and idx < len(pending):
            nxt = pending[idx: idx+MAX_NPZ]
            # filter those not already pulled
            need = [s for s in nxt if s not in active_batch]
            if need:
                log(f"prefetch next {need}")
                pull_npz_from_s1(need)
                active_batch.extend(need)
                active_batch = active_batch[-MAX_NPZ*2:]  # keep recent
                evict_old_npz(active_batch)
                active_batch = [s for s in active_batch if (IND_DIR / f"{s.replace('_LONG','').replace('_SHORT','')}.npz").exists() or s in pending[idx: idx+2]][-MAX_NPZ:]
        # sync queue progress to S1
        try:
            prog = {"completed": completed, "pending": len(pending)-idx, "queue_len": len(queue), "ts": datetime.now(timezone.utc).isoformat()}
            (PROGRESS_DIR / "s2_progress.json").write_text(json.dumps(prog, indent=2))
            cmd = f'rsync -avz --checksum -e "ssh -J {S1_JUMP}" {shlex.quote(str(PROGRESS_DIR / "s2_progress.json"))} {S1_HOST}:~/binance/data/reports/s2_progress/ 2>&1'
            run(cmd, timeout=30)
        except: pass
        time.sleep(1)
    log(f"ALL DONE completed {len(completed)}")

if __name__ == "__main__":
    main()
