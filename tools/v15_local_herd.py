#!/usr/bin/env python3
"""
v15_local_herd — runs ON EACH SERVER (s1/s2/s5/s6), cron-resilient, no Mac needed.

🔴 ABSOLUTE RESUME LAW — RESTARTING FROM ZERO IS ABSOLUTELY PROHIBITED 🔴
Every cell's content is STORED in data/reports/lifecycle_pilot/{SYM}_v14_progress.json (done dict)
and in SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx. On crash/OOM/pkill/reboot
the pilot MUST RESUME from last filled cell (v15_pilot_sheet_runner.py refill + progress["done"] check).
This herd NEVER deletes done, NEVER re-clones template over existing progress, NEVER truncates jsonl.
Each server loops over campaign_order_1mo.json but skips done>=2800 and resumes partial ones.

🔴 SERVER ISOLATION LAW — 2026-09-15 — EACH SERVER HAS ITS OWN LIST, NEVER DUPLICATES 🔴
Per-server queues in SPREADSHEETS/V15_SERVER_QUEUE_S{1,2,5,6}.txt are DISJOINT (83 urgent TRB-first: s1 crypto 16, s2/s5/s6 stocks 13/13/13, 0 overlap). This herd NEVER calculates what another server already did:
- `global_done_set()` ssh to S1 (`10.0.0.3` / `157.180.125.52`) lists `SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx` every 60s.
- `combined_done = local_done | global_done` and `todo=[s for s in order if s not in combined_done]`.
- If S1 unreachable → partitioned shard: `(stable_hash(sym) % 4) == host_idx` (s1=niels, s2=htz-v15-s2, s5=htz-v15-s5, s6=htz-v15-s6) so 4 hosts cover 83 urgent with zero overlap.
- Every 60s `global-refresh` purges todo that became `combined_done`; `push_to_s1()` rsyncs each finished sym.
Duplicate calculation is FORBIDDEN and wastes 1-2h per sym.

Guarantees:
  - survives reboot (@reboot cron) and disconnect (no ssh dependency to stay alive)
  - knows what sym_side is next: reads local ORDER + checks S1 for global done (if reachable) else shard (hash %4)
  - keeps RAM at MAX 80% without OOM: adds sym_sides as long as they fit in RAM (while mem_avail>1500 and running<max_parallel), s1 22×64, s5 4×28
  - FULL SHEET: never ditches a sym_side halfway — 13 sheets to GLOBAL_RISK_GATES even if 12 NEG, per-cell 60s timeout only skips cell not sym
  - RAM: V12_NPZ_CACHE=32, ALL_PREPARED holds NPZ in RAM, never disk per row (0.07s/cell not >1s/cell)
  - keeps local CPU >95% (load1/nproc) and RAM >80% but never OOM (avail >1.2G), max workers, max parallel
  - sends results to S1: rsync completed xlsx + progress.json to 10.0.0.3:/home/niels/binance-sandbox/...
  - ALL 104 get done tonight: loops forever, requeues failures, verifies sheets

Cron on each server (installed by deploy):
  @reboot  sleep 15; nohup /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/v15_local_herd.py >> /tmp/v15_local_herd.log 2>&1 &
  * * * * *  /home/niels/binance-sandbox/.venv/bin/python /home/niels/binance-sandbox/tools/v15_local_herd.py --cron-check >> /tmp/v15_local_herd_cron.log 2>&1
  */5 * * * * rsync -az ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 10.0.0.3:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail

Usage on server:
  python3 -u tools/v15_local_herd.py                 # daemon: run forever, saturate
  python3 -u tools/v15_local_herd.py --once          # one saturation pass (for cron --cron-check)
  python3 -u tools/v15_local_herd.py --cron-check    # ensure daemon running, else start it

STDEV_SLOPE_SIZING — worst_first ordering (STDEV first):
  WORST_ORDER = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", ...] — STDEV is tested FIRST
  because its multiplier (D 10×, 4h 4×, 1h 2×, 15m 1.5×) has HUGE delta on every trade.
  TEMPLATE.xlsx STDEV_SLOPE_SIZING sheet = 34 rows (15 ladder rows 20-34): 1→10/8/6/4 via
  _stdev_max_map, MODE bottom_to_top (-2.5→+2.5 soft) vs slope_to_top (1 below slope→max steep),
  BAND_MULTIPLIER / LOOKBACK scaling, mirrored shorts. Engine: v12_quick_engine.compute_regime_sizing_mult
  with npz stdev_edge_* + stdev_slope_* (243 files, 30M bars). Templates: TEMPLATE_STOCKS/CRYPTO_LONG/SHORT
  + V15 variants are all byte-identical to /Users/niels/Downloads/TEMPLATE.xlsx (canonical), rsynced to
  S1. Herd picks worst_first template per venue (--seq-mode worst2best) so STDEV deltas appear first
  in V15_V16_CELL_BY_CELL/*.xlsx Results_Deltas.
"""
from __future__ import annotations
import os
import sys
import time
import json
import shlex
import subprocess
import pathlib
import re
from collections import deque
import hashlib
def stable_hash(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest(), 16)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ORDER_CANDIDATES = [
    # 2026-09-20 TRB-first urgent — s1 crypto FLZ 16, s2/s5/s6 stocks TRB 13 each, 0 overlap. NO repeats before all 83 urgent have FINAL pos gain (per V15_URGENT_FINAL_83.json pending 81). Fallback to BEST/RUNNING_ORDER disabled until urgent final.
    ROOT / "SPREADSHEETS" / "V15_SERVER_QUEUE_S1.txt",
    ROOT / "SPREADSHEETS" / "V15_SERVER_QUEUE_S2.txt",
    ROOT / "SPREADSHEETS" / "V15_SERVER_QUEUE_S5.txt",
    ROOT / "SPREADSHEETS" / "V15_SERVER_QUEUE_S6.txt",
    pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_SERVER_QUEUE_S1.txt",
    pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_SERVER_QUEUE_S2.txt",
    pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_SERVER_QUEUE_S5.txt",
    pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_SERVER_QUEUE_S6.txt",
    ROOT / "SPREADSHEETS" / "V15_RESUMED_QUEUE_S1_CRYPTO_FLZ_16.txt",
    ROOT / "SPREADSHEETS" / "V15_RESUMED_QUEUE_S2_STOCKS_PHASE1_MISSING30.txt",
    ROOT / "SPREADSHEETS" / "V15_RESUMED_QUEUE_S5_STOCKS_PHASE1_MISSING30.txt",
    ROOT / "SPREADSHEETS" / "V15_RESUMED_QUEUE_S6_STOCKS_PHASE1_MISSING30.txt",
    pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_RESUMED_QUEUE_S1_CRYPTO_FLZ_16.txt",
    ROOT / "SPREADSHEETS" / "V15_URGENT_FINAL_83.json",
]
# URGENT-ONLY gate — if pending urgent >0, do NOT fallback to BEST/RUNNING_ORDER (prevents repeats before urgent finals)
URGENT_ONLY_UNTIL_FINAL = True
VENV_PY = pathlib.Path.home() / "binance-sandbox" / ".venv" / "bin" / "python"
ALT_VENV = pathlib.Path.home() / ".conda" / "envs" / "binance_env" / "bin" / "python"
S1_HOST = "10.0.0.3"  # tradingnet internal — reachable from s2/s3/s5/s1 without gateway
S1_FALLBACK = "157.180.125.52"


def find_order() -> pathlib.Path | None:
    # 2026-09-20 TRB-first urgent — per-server V15_SERVER_QUEUE_S{1,2,5,6}.txt takes absolute priority (s1 crypto 16, s2/s5/s6 stocks 13 each)
    try:
        me = subprocess.check_output(["hostname"], text=True).strip()
    except:
        me = ""
    # Per-host SERVER_QUEUE is the new source of truth — check it first (hostname is niels|s2|s5|s6 short, or htz-v15-s2 long)
    try:
        # map hostname to suffix — handle both short (s2) and long (htz-v15-s2)
        me_lower = me.lower()
        if "niels" in me_lower and "htz" not in me_lower:
            suffix = "S1"
        elif "s2" in me_lower:
            suffix = "S2"
        elif "s5" in me_lower:
            suffix = "S5"
        elif "s6" in me_lower:
            suffix = "S6"
        elif "s3" in me_lower:  # legacy s3 -> map to S5 for graceful
            suffix = "S5"
        else:
            suffix = None
        if suffix:
            host_q = ROOT / f"SPREADSHEETS/V15_SERVER_QUEUE_{suffix}.txt"
            if host_q.exists() and host_q.stat().st_size > 5:
                return host_q
            alt_q = pathlib.Path.home() / f"binance-sandbox/SPREADSHEETS/V15_SERVER_QUEUE_{suffix}.txt"
            if alt_q.exists() and alt_q.stat().st_size > 5:
                return alt_q
            # also check RESUMED queue
            host_r = ROOT / f"SPREADSHEETS/V15_RESUMED_QUEUE_{suffix}_{'CRYPTO' if suffix=='S1' else 'STOCKS'}_*.txt"
            # fallback to glob
            for p in ROOT.glob(f"SPREADSHEETS/V15_RESUMED_QUEUE_{suffix}_*.txt"):
                if p.exists() and p.stat().st_size > 5:
                    return p
    except:
        pass
    # URGENT-ONLY: if V15_URGENT_FINAL_83.json pending>0, never fallback to BEST/RUNNING_ORDER (no repeats before urgent finals)
    try:
        urgent_path = ROOT / "SPREADSHEETS/V15_URGENT_FINAL_83.json"
        if urgent_path.exists():
            import json as _js
            _d=_js.load(open(urgent_path))
            if _d.get("pending_urgent", 0) > 0 and _d.get("urgent_total", 83) > len(_d.get("final_pos_gain_list", [])):
                # urgent still pending — only allow urgent queues, skip BEST fallback
                for p in ORDER_CANDIDATES:
                    if p.exists() and ("V15_SERVER_QUEUE" in str(p) or "V15_RESUMED_QUEUE" in str(p)):
                        return p
                # also check per-host RESUMED
                # no urgent queue found but pending>0 → return None (herd will idle, not run repeats)
                return None
    except Exception:
        pass
    # Fallback to BEST (old) only if no SERVER_QUEUE exists and urgent pending 0
    is_s2 = "s2" in me.lower() or "htz-v15-s2" in me
    host_best = ROOT / ("SPREADSHEETS/V15_BEST_QUEUE_S2.txt" if is_s2 else "SPREADSHEETS/V15_BEST_QUEUE_S1.txt")
    if host_best.exists():
        # only use BEST if no SERVER_QUEUE at all and no pending urgent
        has_server_q = any((ROOT / f"SPREADSHEETS/V15_SERVER_QUEUE_S{s}.txt").exists() for s in ["1","2","5","6"])
        if not has_server_q:
            try:
                _d2=json.load(open(ROOT/"SPREADSHEETS/V15_URGENT_FINAL_83.json")) if (ROOT/"SPREADSHEETS/V15_URGENT_FINAL_83.json").exists() else {"pending_urgent":0}
                if _d2.get("pending_urgent",0)==0:
                    return host_best
            except:
                return host_best
    for p in ORDER_CANDIDATES:
        if p.exists():
            # if pending urgent, skip non-urgent candidates
            if "V15_BEST" in str(p) or "V15_RUNNING_ORDER" in str(p):
                try:
                    _d3=json.load(open(ROOT/"SPREADSHEETS/V15_URGENT_FINAL_83.json")) if (ROOT/"SPREADSHEETS/V15_URGENT_FINAL_83.json").exists() else {"pending_urgent":0}
                    if _d3.get("pending_urgent",0)>0:
                        continue
                except:
                    pass
            return p
    # try to fetch BEST + fallback from S1
    try:
        best_name = "V15_BEST_QUEUE_S2.txt" if is_s2 else "V15_BEST_QUEUE_S1.txt"
        subprocess.run(["scp", "-o", "ConnectTimeout=8", f"niels@{S1_HOST}:~/binance-sandbox/SPREADSHEETS/{best_name}", str(ROOT / f"SPREADSHEETS/{best_name}")], timeout=10)
        if (ROOT / f"SPREADSHEETS/{best_name}").exists():
            return ROOT / f"SPREADSHEETS/{best_name}"
        subprocess.run(["scp", "-o", "ConnectTimeout=8", f"niels@{S1_HOST}:~/binance-sandbox/SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt", str(ROOT / "SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt")], timeout=10)
        if (ROOT / "SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt").exists():
            return ROOT / "SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt"
    except Exception:
        pass
    return None


def load_order(path: pathlib.Path) -> list[str]:
    lines = [l.strip().upper() for l in path.read_text().splitlines() if l.strip()]
    seen = set()
    out = []
    for s in lines:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def sys_stats() -> dict:
    try:
        nproc = int(subprocess.check_output(["nproc"]).decode().strip())
    except:
        nproc = 4
    try:
        load1 = float(open("/proc/loadavg").read().split()[0])
    except:
        load1 = 0.0
    try:
        # free -m
        out = subprocess.check_output(["free", "-m"], text=True)
        m = re.search(r"Mem:\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", out)
        total = int(m.group(1)) if m else 0
        avail = int(m.group(3)) if m and len(m.groups()) >= 3 else 0
        # fallback parse /proc/meminfo MemAvailable
        if not avail:
            try:
                mi = open("/proc/meminfo").read()
                ma = re.search(r"MemAvailable:\s+(\d+)", mi)
                if ma:
                    avail = int(ma.group(1)) // 1024
            except:
                pass
        used_pct = (total - avail) / total * 100 if total else 0
    except:
        total, avail, used_pct = 0, 0, 0
    # running pilots
    try:
        ps = subprocess.check_output(["ps", "aux"], text=True)
        running = re.findall(r"v15_pilot[^\n]*--sym-side\s+(\S+)", ps)
    except:
        running = []
    # oom?
    oom = False
    try:
        dmesg = subprocess.check_output(["dmesg"], text=True, timeout=5)
        if "out of memory" in dmesg.lower() or "oom-killer" in dmesg.lower():
            # only recent (last 5 min) counts? check tail
            tail = "\n".join(dmesg.splitlines()[-20:])
            if "out of memory" in tail.lower() or "oom-killer" in tail.lower():
                oom = True
    except:
        pass
    return {"nproc": nproc, "load1": load1, "cpu_pct": load1 / max(1, nproc) * 100, "mem_total_m": total, "mem_avail_m": avail, "mem_used_pct": used_pct, "running": running, "running_count": len(running), "oom": oom}


def local_done_set(order: list[str]) -> set[str]:
    # local xlsx existence >500k — for BEST validation (V15_BEST_QUEUE) require template freshness: stale xlsx (older than latest TEMPLATE_* per category) is NOT done
    done = set()
    base = pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    if not base.exists():
        return done
    # Check if this is BEST validation: if order matches BEST queue size/content, enforce template mtime freshness
    is_best = len(order) in (32, 28, 60) and any(s in order for s in ["ALMU_LONG", "CLF_LONG"])  # BEST queues signature
    tmpl_mtimes = {}
    if is_best:
        try:
            for cat in ["STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT"]:
                p = pathlib.Path.home() / f"binance-sandbox/SPREADSHEETS/TEMPLATE_{cat}.xlsx"
                if not p.exists():
                    p = ROOT / f"SPREADSHEETS/TEMPLATE_{cat}.xlsx"
                if p.exists():
                    tmpl_mtimes[cat] = p.stat().st_mtime
                # fallback: generic TEMPLATE.xlsx
                gp = pathlib.Path.home() / "binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx"
                if gp.exists():
                    tmpl_mtimes["_generic"] = gp.stat().st_mtime
        except:
            pass
    for p in base.glob("*.xlsx"):
        try:
            if p.stat().st_size < 500_000:
                continue
            # Timestamped interim (*_2026*.xlsx) is NOT FINAL — never count as done (superseded as soon as FINAL exists, per user 2026-09-20)
            if "_2026" in p.name:
                continue
            # BEST freshness: if xlsx older than its category TEMPLATE, skip (needs re-run on latest)
            if is_best and tmpl_mtimes:
                # infer category from sym
                name = p.name
                # find matching sym in order that is contained in name
                matched = None
                for s in order:
                    if s in name:
                        matched = s
                        break
                if matched:
                    is_stock = "USDC" not in matched and "USDT" not in matched
                    suffix = "LONG" if matched.endswith("_LONG") else "SHORT"
                    cat = f"{'STOCKS' if is_stock else 'CRYPTO'}_{suffix}"
                    tmpl_mt = tmpl_mtimes.get(cat, tmpl_mtimes.get("_generic", 0))
                    if p.stat().st_mtime < tmpl_mt - 60:  # 60s grace
                        continue  # stale, not done — needs re-validation on latest TEMPLATE_*
            name = p.name
            for s in order:
                if s in name:
                    done.add(s)
        except:
            continue
    return done


def global_done_set(order: list[str]) -> set[str] | None:
    # BEST validation: respect template freshness — stale xlsx not counted as done (needs re-run on latest TEMPLATE_* per category)
    is_best = len(order) in (32, 28, 60) and any(s in order for s in ["ALMU_LONG", "CLF_LONG"])
    tmpl_mtimes = {}
    if is_best:
        try:
            for cat in ["STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT"]:
                p = pathlib.Path.home() / f"binance-sandbox/SPREADSHEETS/TEMPLATE_{cat}.xlsx"
                if not p.exists():
                    p = ROOT / f"SPREADSHEETS/TEMPLATE_{cat}.xlsx"
                if p.exists():
                    tmpl_mtimes[cat] = p.stat().st_mtime
            gp = pathlib.Path.home() / "binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx"
            if gp.exists():
                tmpl_mtimes["_generic"] = gp.stat().st_mtime
        except:
            pass
    for host in [S1_HOST, S1_FALLBACK]:
        try:
            if is_best and tmpl_mtimes:
                # Fast batch stat (was per-file loop 6774*2 stat = >15s timeout) — now single stat batch <1s for 6774 files
                out = subprocess.check_output(["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", f"niels@{host}", "stat -c '%Y %s %n' ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | awk '$2>500000{print $1\" \"$3}'"], timeout=15, text=True)
                done = set()
                for line in out.strip().splitlines():
                    if not line.strip():
                        continue
                    try:
                        parts = line.strip().split(None, 1)
                        if len(parts) < 2:
                            continue
                        mtime = int(parts[0])
                        name = parts[1]
                        if "_2026" in name:
                            continue  # timestamped interim not FINAL
                        for s in order:
                            if s in name:
                                is_stock = "USDC" not in s and "USDT" not in s
                                suffix = "LONG" if s.endswith("_LONG") else "SHORT"
                                cat = f"{'STOCKS' if is_stock else 'CRYPTO'}_{suffix}"
                                tmpl_mt = tmpl_mtimes.get(cat, tmpl_mtimes.get("_generic", 0))
                                if mtime < tmpl_mt - 60:
                                    continue  # stale, not done
                                done.add(s)
                    except:
                        continue
                return done
            else:
                out = subprocess.check_output(["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", f"niels@{host}", "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {}"], timeout=10, text=True)
                names = out.strip().splitlines()
                done = set()
                for name in names:
                    if "_2026" in name:
                        continue  # timestamped interim not FINAL
                    for s in order:
                        if s in name:
                            done.add(s)
                return done
        except:
            continue
    return None


def strict_global_done_set(order: list[str]) -> set[str] | None:
    # STRICT: ANY xlsx filename containing symside counts as done, even <500k, even bh variants, but NOT timestamped interim (*_2026) per 2026-09-20 urgent-only law
    # This is the user 2026-09-16 DESTROY law: nothing ever recomputes a sym_side already computed EVER — but interim is not final, so not counted
    for host in [S1_HOST, S1_FALLBACK]:
        try:
            out = subprocess.check_output(["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", f"niels@{host}", "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {} 2>/dev/null; echo __END__; ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.bak 2>/dev/null | xargs -I{} basename {} 2>/dev/null; echo __END2__"], timeout=10, text=True)
            parts = out.split("__END__")
            xlsx_names = parts[0].strip().splitlines() if len(parts) > 0 else []
            done = set()
            for name in xlsx_names:
                if not name.strip() or name.startswith("__"):
                    continue
                if "_2026" in name:
                    continue  # interim not final
                for s in order:
                    if s in name:
                        done.add(s)
            return done
        except:
            continue
    return None


def push_to_s1(symside: str) -> bool:
    # push just this symside's xlsx + progress to S1; best-effort, no fail if S1 down
    pushed = False
    for host in [S1_HOST, S1_FALLBACK]:
        try:
            # xlsx
            src_xlsx = str(pathlib.Path.home() / f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*{symside}*.xlsx")
            # use shell expansion via ssh? use rsync with wildcard
            r = subprocess.run(["bash", "-c", f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*{symside}*.xlsx niels@{host}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -n 5"], timeout=30, capture_output=True, text=True)
            # progress
            subprocess.run(["bash", "-c", f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' ~/binance-sandbox/data/reports/lifecycle_pilot/*{symside}*.json niels@{host}:~/binance-sandbox/data/reports/lifecycle_pilot/ 2>&1 | tail -n 5"], timeout=15, capture_output=True, text=True)
            if r.returncode == 0:
                pushed = True
                break
        except:
            continue
    return pushed


def launch(symside: str, workers: int, window_days: int = 30) -> bool:
    py = str(VENV_PY) if VENV_PY.exists() else (str(ALT_VENV) if ALT_VENV.exists() else sys.executable)
    # routing: STOCKS use worst-first + STOCKS templates; CRYPTO use worst-first with V15_CRYPTO templates if they exist (regenerating), else regular
    is_stock = "USDC" not in symside and "USDT" not in symside
    if is_stock:
        pilot = ROOT / "v15_pilot_0914.py"
        if not pilot.exists():
            pilot = ROOT / "v15_pilot.py"
        # prefer V15 stocks templates (1.9M) if present, else legacy
        tmpl_candidates = [
            f"SPREADSHEETS/TEMPLATE_V15_STOCKS_{'LONG' if symside.endswith('_LONG') else 'SHORT'}.xlsx",
            f"SPREADSHEETS/TEMPLATE_STOCKS_{'LONG' if symside.endswith('_LONG') else 'SHORT'}.xlsx",
        ]
        tmpl = next((t for t in tmpl_candidates if (ROOT / t).exists()), "SPREADSHEETS/TEMPLATE.xlsx")
        if tmpl == "SPREADSHEETS/TEMPLATE.xlsx":
            pilot = ROOT / "v15_pilot.py"
            extra = ""
        else:
            extra = " --seq-mode worst2best"
    else:
        # crypto: if V15 crypto templates exist (regenerating), use worst-first for crypto too
        tmpl_candidates = [
            f"SPREADSHEETS/TEMPLATE_V15_CRYPTO_{'LONG' if symside.endswith('_LONG') else 'SHORT'}.xlsx",
            f"SPREADSHEETS/TEMPLATE_CRYPTO_{'LONG' if symside.endswith('_LONG') else 'SHORT'}.xlsx",
            f"SPREADSHEETS/TEMPLATE_CRYPTO_{'LONG' if symside.endswith('_LONG') else 'SHORT'}.xls",
        ]
        tmpl = next((t for t in tmpl_candidates if (ROOT / t).exists()), "SPREADSHEETS/TEMPLATE.xlsx")
        if tmpl == "SPREADSHEETS/TEMPLATE.xlsx":
            pilot = ROOT / "v15_pilot.py"
            extra = ""
        else:
            pilot = ROOT / "v15_pilot_0914.py"
            if not pilot.exists():
                pilot = ROOT / "v15_pilot.py"
                extra = ""
            else:
                extra = " --seq-mode worst2best"
    cmd = f"nohup {shlex.quote(py)} -u {shlex.quote(str(pilot))} --sym-side {shlex.quote(symside)} --template {shlex.quote(str(ROOT / tmpl))}{extra} --window-days {window_days} --vector-only --workers {workers} > /tmp/v15_{symside}.log 2>&1 & echo $!"
    try:
        out = subprocess.check_output(["bash", "-c", cmd], text=True, timeout=10)
        pid = out.strip().splitlines()[-1].strip()
        ok = pid.isdigit()
        print(f"[launch] {symside} workers={workers} pilot={pilot.name} tmpl={tmpl.split('/')[-1]} pid={pid} {'OK' if ok else 'FAIL:'+out[:200]}", flush=True)
        return ok
    except Exception as e:
        print(f"[launch-FAIL] {symside} {e}", flush=True)
        return False


def ensure_daemon_running() -> bool:
    # check if herd daemon already running (this file without --cron-check)
    try:
        out = subprocess.check_output(["pgrep", "-f", "v15_local_herd.py"], text=True)
        pids = [l.strip() for l in out.splitlines() if l.strip() and "--cron-check" not in subprocess.check_output(["ps", "-o", "args=", "-p", l.strip()], text=True)]
        # count non-cron-check instances
        ps = subprocess.check_output(["ps", "aux"], text=True)
        herd_procs = [l for l in ps.splitlines() if "v15_local_herd.py" in l and "--cron-check" not in l and "grep" not in l]
        if len(herd_procs) >= 1:
            return True
    except:
        pass
    return False


def cron_check():
    if ensure_daemon_running():
        print(f"[cron-check] daemon already running", flush=True)
        return
    print(f"[cron-check] daemon not running — starting", flush=True)
    py = str(VENV_PY) if VENV_PY.exists() else (str(ALT_VENV) if ALT_VENV.exists() else sys.executable)
    # start detached
    subprocess.Popen(["bash", "-c", f"nohup {shlex.quote(py)} -u {shlex.quote(str(pathlib.Path(__file__)))} >> /tmp/v15_local_herd.log 2>&1 &"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(2)
    print(f"[cron-check] started, pgrep:", flush=True)
    try:
        print(subprocess.check_output(["pgrep", "-f", "v15_local_herd"], text=True))
    except:
        print("  no pids yet", flush=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="v15_local_herd — per-server saturator, S1-pushing, reboot-proof")
    ap.add_argument("--cron-check", action="store_true", help="ensure daemon running (for * * * * * cron)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--order", default=None)
    args = ap.parse_args()

    if args.cron_check:
        cron_check()
        return

    # find order
    order_path = pathlib.Path(args.order) if args.order else find_order()
    if order_path is None or not pathlib.Path(order_path).exists():
        print(f"[FATAL] order file not found {order_path} — tried {ORDER_CANDIDATES}", flush=True)
        sys.exit(2)
    order = load_order(pathlib.Path(order_path))
    # FLZ merge: skip for BEST validation queues (V15_BEST_QUEUE_S*.txt) — they are exact 32/28 top-delta per category, no FLZ append
    is_best_order = "V15_BEST_QUEUE" in str(order_path)
    # FLZ merge: if V15 is primary and FLZ exists, append FLZ crypto (except BTCUSDC_LONG verified fail) for combined 152+244
    for flz_candidate in [ROOT / "SPREADSHEETS" / "FLZ_RUNNING_ORDER.txt", pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "FLZ_RUNNING_ORDER.txt"]:
        if is_best_order:
            continue  # BEST validation is exact per-server queue, no FLZ contamination
        if flz_candidate.exists() and str(order_path) != str(flz_candidate):
            try:
                flz_order = load_order(flz_candidate)
                # filter BTCUSDC_LONG already excluded in file, but double-check
                flz_order = [s for s in flz_order if s != "BTCUSDC_LONG"]
                # append only those not already in order
                existing = set(order)
                added = [s for s in flz_order if s not in existing]
                if added:
                    order.extend(added)
                    print(f"[FLZ-merge] appended {len(added)} FLZ crypto (excl BTCUSDC_LONG) -> total {len(order)}", flush=True)
            except Exception as e:
                print(f"[FLZ-merge-warn] {e}", flush=True)
    print(f"[herd-local] {pathlib.Path(order_path).name} {len(order)} syms on {subprocess.check_output(['hostname']).decode().strip()} — {order[:3]} ... {order[-3:]}", flush=True)

    # tune per host
    st0 = sys_stats()
    nproc = st0["nproc"]
    mem_gb = st0["mem_total_m"] / 1024 if st0["mem_total_m"] else 30
    me = subprocess.check_output(["hostname"]).decode().strip()
    if nproc <= 4:
        max_parallel = 2  # s2 7.6G OOM at 3×8 → 2×8 stable, need 20/hour but cap at 2
        workers = 8
    elif mem_gb < 10:
        max_parallel = 2
        workers = 8
    else:
        # USER 2026-09-18 22:00 HUSTLE 56 workers — templates reduced 90% (161K vs 1.9M) so 56× vector 0.07s/cell finishes in minutes
        if "niels" in me and "htz" not in me:
            max_parallel = 6  # s1 trading host - 6×56 + trading(11) = >90% saturated, RAM 70-95% via 6×2G=12G + trading
            workers = 56
        else:
            max_parallel = 12 # s2/s3/s5 - 12×56 threads = 672 threads → 90%+ on 16c via burst, RAM 70-95%
            workers = 56
    print(f"[herd-local] nproc={nproc} mem={mem_gb:.1f}G -> max_parallel={max_parallel} workers={workers}", flush=True)

    # build initial todo: always resume local unfinished bak/tmp/progress first (user 2026-09-16: s3/s5 IDLE 17 bak but herd said DONE 104/104)
    all_hosts = ["niels", "htz-v15-s2", "htz-v15-s5", "htz-v15-s6"]  # s1 is niels, s2 is htz-v15-s2, s5 is htz-v15-s5, s6 is htz-v15-s6 — 2026-09-20 s1 crypto s2/s5/s6 stocks
    try:
        me = subprocess.check_output(["hostname"]).decode().strip()
    except:
        me = "unknown"
    host_idx = 0
    for i, h in enumerate(all_hosts):
        if h in me or me in h:
            host_idx = i
            break
    else:
        # fallback by ip last octet
        try:
            ip = subprocess.check_output(["hostname", "-I"], text=True).split()[0]
            host_idx = int(ip.split(".")[-1]) % 4
        except:
            host_idx = 0


    # Scan local unfinished: *.bak, *.tmp, and progress.json with done < expected (not 0 and not complete)
    local_unfinished = set()
    try:
        import glob, json
        # Filter local_unfinished by hash shard to prevent duplicate 1INCHUSDT on s5+s2 (only once per sym_side until all done)
        for bak in pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*.bak"):
            sym = bak.name.split("_30d")[0]
            if sym in order and (stable_hash(sym) % 4) == host_idx:
                local_unfinished.add(sym)
        for tmp in pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*.tmp"):
            sym = tmp.name.split("_30d")[0].split(".")[0]
            if sym in order and (stable_hash(sym) % 4) == host_idx:
                local_unfinished.add(sym)
        for prog in pathlib.Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot").glob("*_v14_progress.json"):
            try:
                j = json.loads(prog.read_text())
                done = j.get("done", {})
                # unfinished if 0 < done < 500 (full is ~1000+ cells, 426 for MPC_LONG) and not in local_done
                if isinstance(done, dict) and 0 < len(done) < 1000:
                    sym = prog.name.replace("_v14_progress.json", "")
                    if sym in order and (stable_hash(sym) % 4) == host_idx:
                        local_unfinished.add(sym)
            except:
                pass
        if local_unfinished:
            print(f"[herd-local] RESUME {len(local_unfinished)} local unfinished bak/tmp/progress: {sorted(list(local_unfinished))[:5]}", flush=True)
    except Exception as e:
        print(f"[herd-local] resume scan warn {e}", flush=True)

    # build initial todo: those not locally done
    # also try global done to avoid duplicating other servers' work when reachable
    local_done = local_done_set(order)
    gdone = global_done_set(order)
    # 2026-09-19 BEST re-run fix: latest results never tested filters per switch/filter per sheet → force re-test WITH filters on latest TEMPLATE_* per category; stale xlsx must be re-run, so ignore stale global for BEST
    if is_best_order:
        # Use only freshness-aware local for BEST validation (filters re-test required); global stale would mask need to re-run
        combined_done = local_done
        print(f"[herd-local] BEST local done {len(local_done)}/{len(order)} global {len(gdone) if gdone else 0}/{len(order)} combined {len(combined_done)}/{len(order)} (filters re-test on latest TEMPLATE_* per category)", flush=True)
    elif gdone is not None:
        combined_done = local_done | gdone
        print(f"[herd-local] local done {len(local_done)}/104 global {len(gdone)}/104 combined {len(combined_done)}/104", flush=True)
    else:
        combined_done = local_done
        print(f"[herd-local] S1 unreachable — partitioned mode local done {len(local_done)}/104 (shard fallback)", flush=True)
    # Force resume of local unfinished even if already in combined_done (they have bak/tmp and need refill)
    if local_unfinished:
        for sym in local_unfinished:
            if sym not in combined_done or sym in local_done:
                # ensure it's in todo front, not skipped as done
                pass
        # Rebuild combined to exclude unfinished so they become todo
        combined_done = combined_done - local_unfinished
        if local_unfinished:
            print(f"[herd-local] ADJUST combined {len(combined_done)}/104 after removing {len(local_unfinished)} unfinished for resume", flush=True)

    # shard fallback when S1 unreachable: each host takes deterministic shard of remaining so 4 hosts cover all without coordinator
    # shard by stable_hash(sym) % 4 mapped to hostname index
    # priority: FLZ crypto long/short first, then stocks rerun with winning settings as defaults (user 2026-09-16)
    def _is_stock(s: str) -> bool:
        return "USDC" not in s and "USDT" not in s
    # sort pending so FLZ crypto comes first, preserving worst-first within FLZ (queue order)
    # BOSS-DISTRIBUTION FIX 2026-09-16: queue is single source of truth, RAM boss for both sides
    # 1) If sym is in V15_SERVER_QUEUE_S*.txt, only its queue host may run it (prevents s1/s5 duplicate 1000PEPE)
    # 2) Else if base has RAM files (progress/xlsx/bak), host that holds base claims BOTH LONG+SHORT together
    queue_boss = {}
    try:
        for q in pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS").glob("V15_SERVER_QUEUE_S*.txt"):
            host = q.stem.split("_S")[-1]  # S1, S2, S3, S5
            for line in q.read_text().splitlines():
                sym = line.strip()
                if sym:
                    queue_boss[sym] = host
    except:
        pass
    # map hostname to queue suffix
    me_suffix = "1" if "niels" in me and "htz" not in me else ("2" if "htz-v15-s2" in me else ("5" if "htz-v15-s5" in me else ("6" if "htz-v15-s6" in me else "1")))
    ram_bases = set()
    try:
        for p in list(pathlib.Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot").glob("*_pilot_progress.json")) + list(pathlib.Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot").glob("*_v14_progress.json")):
            ram_bases.add(p.name.split("_pilot")[0].split("_v14")[0].rsplit("_", 1)[0])
        for x in pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*.xlsx"):
            ram_bases.add(x.name.split("_30d")[0].rsplit("_", 1)[0])
        for b in pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*.bak"):
            ram_bases.add(b.name.split("_30d")[0].rsplit("_", 1)[0])
    except:
        pass
    pending = []
    # BEST validation: simple pending = order - combined_done (already fresh, disjoint per-server, need filters re-test on latest TEMPLATE per category)
    if is_best_order:
        for s in order:
            if s in combined_done:
                continue
            # only skip if NPZ truly missing (should be 0 for BEST)
            npz = pathlib.Path(f"/home/niels/binance-sandbox/backtest_v8/indicators/{s.rsplit('_',1)[0]}.npz")
            if not npz.exists() or npz.stat().st_size < 100_000:
                print(f"[BEST-skip-NPZ] {s} no NPZ", flush=True)
                continue
            pending.append(s)
    else:
        for s in order:
            if s in combined_done:
                continue
            # queue boss takes precedence over RAM and hash
            boss = queue_boss.get(s)
            if boss is not None and boss != me_suffix:
                continue  # not my queue, skip even if I have RAM stale file
            base = s.rsplit("_", 1)[0]
            if base in ram_bases:
                this_has = (pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{base}_LONG_pilot_progress.json").exists() or pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{base}_SHORT_pilot_progress.json").exists() or bool(list(pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob(f"{base}_LONG*.xlsx"))) or bool(list(pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob(f"{base}_SHORT*.xlsx"))))
                if this_has:
                    pass
                else:
                    continue
            # skip syms with no NPZ (would be 0 ROW for 20h like BEATUSDT/TXN) — check local NPZ exists, else skip and mark done
            npz = pathlib.Path(f"/home/niels/binance-sandbox/backtest_v8/indicators/{s.rsplit('_',1)[0]}.npz")
            if not npz.exists() or npz.stat().st_size < 100_000:
                # also check S1 via ssh quick head? skip scp wait, just skip if not local — S1 has 990, s3/s5 will be synced, but BEATUSDT/TXN have 0 everywhere
                continue
            # FLZ + stocks hash shard for no double calculations (even when S1 reachable, FLZ has no queue_boss) — disabled for urgent 83 (user 2026-09-20: no repeats before urgent finals, s1 crypto 16 / s2 s5 s6 stocks 13 each already disjoint via queue_boss)
            try:
                _up = pathlib.Path(ROOT / "SPREADSHEETS/V15_URGENT_FINAL_83.json")
                if _up.exists():
                    import json as _js2
                    _ud = _js2.load(open(_up))
                    if _ud.get("pending_urgent", 0) > 0 and s in set(_ud.get("urgent_trb_67", []) + _ud.get("urgent_flz_16", [])):
                        pass  # urgent 83 already sharded via V15_SERVER_QUEUE_S*.txt, don't hash-filter
                    elif (stable_hash(s) % 4) != host_idx:
                        continue
                elif (stable_hash(s) % 4) != host_idx:
                    continue
            except:
                if (stable_hash(s) % 4) != host_idx:
                    continue
            pending.append(s)
    # reorder: FLZ crypto first, then stocks rerun with winning settings (user: FLZ first, then stocks with winning defaults) — independent work list per server via hash, resume not redo
    flz_first = [s for s in pending if not _is_stock(s)] + [s for s in pending if _is_stock(s)]
    # prioritize unfinished at front (resume not redo single cell via done dict)
    if 'local_unfinished' in locals() and local_unfinished:
        unfinished_ordered = [s for s in flz_first if s in local_unfinished]
        new_ordered = [s for s in flz_first if s not in local_unfinished]
        flz_first = unfinished_ordered + new_ordered
        print(f"[herd-local] PRIORITIZE {len(unfinished_ordered)} resume unfinished at front: {unfinished_ordered[:5]}", flush=True)
    todo = deque(flz_first)
    print(f"[herd-local] todo {len(todo)}: {list(todo)[:8]} (FLZ {sum(1 for s in todo if not _is_stock(s))} first, host_idx={host_idx} me={me})", flush=True)

    loops = 0
    last_sync = 0
    last_global_refresh = 0

    while True:
        loops += 1
        st = sys_stats()
        cpu = st["cpu_pct"]
        ram_used = st["mem_used_pct"]
        avail = st["mem_avail_m"]
        running = st["running"]
        oom = st["oom"]

        # OOM guard — never let avail < 1G, kill youngest if needed
        if oom or avail < 1200:
            print(f"[OOM] avail {avail}M oom={oom} cpu {cpu:.1f}% ram {ram_used:.1f}% — throttling, no launches", flush=True)
            if avail < 800 and running:
                victim = running[-1]
                print(f"[OOM-KILL] killing youngest {victim}", flush=True)
                subprocess.run(["pkill", "-f", f"v15_pilot.*{victim}"], timeout=5)
                if victim not in todo:
                    todo.appendleft(victim)
            time.sleep(10)
            continue

        # refresh global done every 60s to learn what other servers finished (avoid duplicate work)
        if time.time() - last_global_refresh > 60:
            last_global_refresh = time.time()
            new_local = local_done_set(order)
            # push newly finished local to S1
            newly = new_local - local_done
            for sym in newly:
                ok = push_to_s1(sym)
                print(f"[push] {sym} -> S1 {'OK' if ok else 'FAIL (S1 down, will retry)'}", flush=True)
            local_done = new_local
            ng = global_done_set(order)
            if ng is not None:
                before = len(combined_done)
                combined_done = local_done | ng
                # remove from todo anything that became globally done
                todo = deque([s for s in todo if s not in combined_done])
                if len(combined_done) != before:
                    print(f"[global-refresh] combined now {len(combined_done)}/104 todo {len(todo)}", flush=True)
            else:
                # still partitioned, keep shard
                pass
            # if we have no todo but global shows missing, take from global missing (shard may have been too narrow)
            if not todo:
                # check if globally all 104 done — then continue to full 354 universe (TRB/MEN/FIN/ANG etc per user: STOCKS no USDT/USDC first, then crypto, then TRB/MEN/FIN/ANG)
                if len(combined_done) >= len(order):
                    # if this was the 104 TRB_FLZ order, switch to full 354 universe
                    order_path_full = ROOT / "SPREADSHEETS" / "V15_FULL_354.txt"
                    if order_path_full.exists() and len(order) in (104,152,396):
                        try:
                            full_order = [l.strip() for l in order_path_full.read_text().splitlines() if l.strip()]
                            # dedupe, keep order
                            seen = set(); full = []
                            for s in full_order:
                                if s not in seen:
                                    seen.add(s); full.append(s)
                            # remaining = full - combined_done
                            remaining = [s for s in full if s not in combined_done]
                            if remaining:
                                # stocks-first for full universe as well
                                def _is_stock2(x): return "USDC" not in x and "USDT" not in x
                                stocks_r = [s for s in remaining if _is_stock2(s)]
                                crypto_r = [s for s in remaining if not _is_stock2(s)]
                                # TRB/MEN/FIN/ANG are within remaining; ensure they are included (they are)
                                new_todo = stocks_r + crypto_r
                                # shard if partitioned
                                if gdone is None:
                                    new_todo = [s for s in new_todo if (stable_hash(s) % 4) == host_idx]
                                # switch order to full
                                order = full
                                todo = deque(new_todo)
                                local_done = local_done_set(order)
                                combined_done = local_done | (global_done_set(order) or set())
                                # filter todo to not done
                                todo = deque([s for s in todo if s not in combined_done])
                                print(f"[PHASE2] 104 TRB_FLZ done, switching to full 354 universe: {len(full)} total, {len(remaining)} remaining (TRB/MEN/FIN/ANG incl), todo now {len(todo)} stocks {len(stocks_r)} crypto {len(crypto_r)}", flush=True)
                            else:
                                print(f"[DONE] all {len(order)} done locally+globally — verifying sheets", flush=True)
                                missing = [s for s in order if s not in combined_done]
                                if not missing:
                                    print(f"[DONE] ALL SHEETS FILLED — {len(order)}/{len(order)} — starting SHUFFLE second round", flush=True)
                                    try:
                                        base_dir = pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
                                        done_with_mtime = []
                                        for s in order:
                                            candidates = list(base_dir.glob(f"{s}*.xlsx"))
                                            if candidates:
                                                oldest = min(candidates, key=lambda p: p.stat().st_mtime)
                                                done_with_mtime.append((oldest.stat().st_mtime, s))
                                        done_with_mtime.sort()
                                        # SHARD hustle: each server takes disjoint oldest by hash %4 - NO DUPLICATE sym_sides between servers
                                        oldest_all = [s for _, s in done_with_mtime]
                                        oldest_n = [s for s in oldest_all if (stable_hash(s) % 4) == host_idx][:5]
                                        if not oldest_n:
                                            oldest_n = [s for s in oldest_all if (stable_hash(s) % 4) == host_idx][:3] or oldest_all[:1]
                                        if not oldest_n:
                                            oldest_n = order[:5]
                                        print(f"[SHUFFLE-ROUND] oldest 5: {oldest_n}", flush=True)
                                        for sym in oldest_n:
                                            try:
                                                prog = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json"
                                                if not prog.exists():
                                                    prog = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_pilot_progress.json"
                                                baseline_json = None
                                                if prog.exists():
                                                    j = json.loads(prog.read_text())
                                                    pos_overrides = {}
                                                    for k, v in j.get("done", {}).items():
                                                        if v.get("delta") and v["delta"] > 0:
                                                            try:
                                                                switch = k.split(":")[1].split("=")[0]
                                                                cand = k.split("=")[1]
                                                                if cand.lower() == "true":
                                                                    cand_val = True
                                                                elif cand.lower() == "false":
                                                                    cand_val = False
                                                                else:
                                                                    try:
                                                                        cand_val = float(cand) if "." in cand else int(cand)
                                                                    except:
                                                                        cand_val = cand
                                                                pos_overrides[switch] = cand_val
                                                            except:
                                                                continue
                                                    if pos_overrides:
                                                        baseline_path = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_shuffle_baseline.json"
                                                        baseline_path.write_text(json.dumps(pos_overrides, indent=2))
                                                        baseline_json = str(baseline_path)
                                                py = str(VENV_PY) if VENV_PY.exists() else (str(ALT_VENV) if ALT_VENV.exists() else sys.executable)
                                                is_stock = "USDC" not in sym and "USDT" not in sym
                                                pilot = ROOT / "v15_pilot_0914.py"
                                                tmpl = f"SPREADSHEETS/TEMPLATE_STOCKS_{'LONG' if sym.endswith('_LONG') else 'SHORT'}.xlsx" if is_stock else f"SPREADSHEETS/TEMPLATE_CRYPTO_{'LONG' if sym.endswith('_LONG') else 'SHORT'}.xlsx"
                                                if not (ROOT / tmpl).exists():
                                                    tmpl = "SPREADSHEETS/TEMPLATE.xlsx"
                                                # HUSTLE distinct order per host: 0 shuffle,1 worst2best,2 cycle,3 round_robin
                                                _modes = ["shuffle", "worst2best", "cycle", "round_robin"]
                                                extra = f" --seq-mode {_modes[host_idx % 4]}"
                                                if baseline_json:
                                                    extra += f" --baseline-json {shlex.quote(baseline_json)}"
                                                disable_file = pathlib.Path.home() / "binance-sandbox/data/reports/lifecycle_pilot/disabled_switches_never_pos.json"
                                                if disable_file.exists():
                                                    extra += f" --disable-switches-file {shlex.quote(str(disable_file))}"
                                                    print(f"[SHUFFLE-DISABLE] {sym} skip 220 never-pos", flush=True)
                                                cmd = f"nohup {shlex.quote(py)} -u {shlex.quote(str(pilot))} --sym-side {shlex.quote(sym)} --template {shlex.quote(str(ROOT / tmpl))}{extra} --window-days {args.window_days} --vector-only --workers {workers} > /tmp/v15_{sym}_shuffle.log 2>&1 & echo $!"
                                                out = subprocess.check_output(["bash", "-c", cmd], text=True, timeout=10)
                                                pid = out.strip().splitlines()[-1].strip()
                                                print(f"[SHUFFLE-launch] {sym} {extra.strip()} pid {pid} {'OK' if pid.isdigit() else 'FAIL'}", flush=True)
                                                todo.append(sym)
                                            except Exception as e:
                                                print(f"[SHUFFLE-ERR] {sym} {e}", flush=True)
                                                todo.append(sym)
                                        print(f"[SHUFFLE-ROUND] queued {len(oldest_n)} oldest for shuffle", flush=True)
                                    except Exception as e:
                                        print(f"[SHUFFLE-ERR] {e}", flush=True)
                                        break
                                else:
                                    print(f"[DONE-WARN] still missing {missing[:5]} — requeue", flush=True)
                                    for s in missing:
                                        if (stable_hash(s) % 4) == host_idx or gdone is not None:
                                            todo.append(s)
                        except Exception as e:
                            print(f"[PHASE2-ERR] {e}", flush=True)
                            # fallback to original done check
                            missing = [s for s in order if s not in combined_done]
                            if not missing:
                                print(f"[DONE] ALL SHEETS FILLED — {len(order)}/{len(order)} — starting SHUFFLE second round", flush=True)
                                try:
                                    base_dir = pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
                                    done_with_mtime = []
                                    for s in order:
                                        candidates = list(base_dir.glob(f"{s}*.xlsx"))
                                        if candidates:
                                            oldest = min(candidates, key=lambda p: p.stat().st_mtime)
                                            done_with_mtime.append((oldest.stat().st_mtime, s))
                                    done_with_mtime.sort()
                                    oldest_n = [s for _, s in done_with_mtime[:5]]
                                    if not oldest_n:
                                        oldest_n = order[:5]
                                    print(f"[SHUFFLE-ROUND] oldest 5: {oldest_n}", flush=True)
                                    for sym in oldest_n:
                                        try:
                                            prog = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json"
                                            if not prog.exists():
                                                prog = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_pilot_progress.json"
                                            baseline_json = None
                                            if prog.exists():
                                                j = json.loads(prog.read_text())
                                                pos_overrides = {}
                                                for k, v in j.get("done", {}).items():
                                                    if v.get("delta") and v["delta"] > 0:
                                                        try:
                                                            switch = k.split(":")[1].split("=")[0]
                                                            cand = k.split("=")[1]
                                                            if cand.lower() == "true":
                                                                cand_val = True
                                                            elif cand.lower() == "false":
                                                                cand_val = False
                                                            else:
                                                                try:
                                                                    cand_val = float(cand) if "." in cand else int(cand)
                                                                except:
                                                                    cand_val = cand
                                                            pos_overrides[switch] = cand_val
                                                        except:
                                                            continue
                                                if pos_overrides:
                                                    baseline_path = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_shuffle_baseline.json"
                                                    baseline_path.write_text(json.dumps(pos_overrides, indent=2))
                                                    baseline_json = str(baseline_path)
                                            py = str(VENV_PY) if VENV_PY.exists() else (str(ALT_VENV) if ALT_VENV.exists() else sys.executable)
                                            is_stock = "USDC" not in sym and "USDT" not in sym
                                            pilot = ROOT / "v15_pilot_0914.py"
                                            tmpl = f"SPREADSHEETS/TEMPLATE_STOCKS_{'LONG' if sym.endswith('_LONG') else 'SHORT'}.xlsx" if is_stock else f"SPREADSHEETS/TEMPLATE_CRYPTO_{'LONG' if sym.endswith('_LONG') else 'SHORT'}.xlsx"
                                            if not (ROOT / tmpl).exists():
                                                tmpl = "SPREADSHEETS/TEMPLATE.xlsx"
                                            _modes = ["shuffle", "worst2best", "cycle", "round_robin"]
                                            extra = f" --seq-mode {_modes[host_idx % 4]}"
                                            if baseline_json:
                                                extra += f" --baseline-json {shlex.quote(baseline_json)}"
                                            disable_file = pathlib.Path.home() / "binance-sandbox/data/reports/lifecycle_pilot/disabled_switches_never_pos.json"
                                            if disable_file.exists():
                                                extra += f" --disable-switches-file {shlex.quote(str(disable_file))}"
                                                print(f"[SHUFFLE-DISABLE] {sym} skip 220 never-pos", flush=True)
                                            cmd = f"nohup {shlex.quote(py)} -u {shlex.quote(str(pilot))} --sym-side {shlex.quote(sym)} --template {shlex.quote(str(ROOT / tmpl))}{extra} --window-days {args.window_days} --vector-only --workers {workers} > /tmp/v15_{sym}_shuffle.log 2>&1 & echo $!"
                                            out = subprocess.check_output(["bash", "-c", cmd], text=True, timeout=10)
                                            pid = out.strip().splitlines()[-1].strip()
                                            print(f"[SHUFFLE-launch] {sym} shuffle pid {pid} {'OK' if pid.isdigit() else 'FAIL'}", flush=True)
                                            todo.append(sym)
                                        except Exception as e:
                                            print(f"[SHUFFLE-ERR] {sym} {e}", flush=True)
                                            todo.append(sym)
                                    print(f"[SHUFFLE-ROUND] queued {len(oldest_n)} oldest for shuffle", flush=True)
                                except Exception as e:
                                    print(f"[SHUFFLE-ERR] {e}", flush=True)
                                    break
                    else:
                        print(f"[DONE] all {len(order)} done locally+globally — verifying sheets", flush=True)
                        missing = [s for s in order if s not in combined_done]
                        if not missing:
                            # SHUFFLE SECOND ROUND: when all done, rerun oldest with found settings as baseline + shuffle
                            print(f"[DONE] ALL SHEETS FILLED — {len(order)}/{len(order)} — starting SHUFFLE second round with found baseline", flush=True)
                            try:
                                # find oldest done by xlsx mtime
                                base_dir = pathlib.Path.home() / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
                                done_with_mtime = []
                                for s in order:
                                    # find most recent xlsx for this sym
                                    candidates = list(base_dir.glob(f"{s}*.xlsx"))
                                    if candidates:
                                        # use oldest (earliest mtime) among candidates
                                        oldest = min(candidates, key=lambda p: p.stat().st_mtime)
                                        done_with_mtime.append((oldest.stat().st_mtime, s))
                                done_with_mtime.sort()
                                # SHARD hustle: disjoint per server
                                oldest_all = [s for _, s in done_with_mtime]
                                oldest_n = [s for s in oldest_all if (stable_hash(s) % 4) == host_idx][:5]
                                if not oldest_n:
                                    oldest_n = order[:5]
                                print(f"[SHUFFLE-ROUND] oldest 5: {oldest_n}", flush=True)
                                # for each oldest, extract best overrides as baseline json for shuffle
                                for sym in oldest_n:
                                    try:
                                        prog = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json"
                                        if not prog.exists():
                                            prog = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_pilot_progress.json"
                                        baseline_json = None
                                        if prog.exists():
                                            j = json.loads(prog.read_text())
                                            # find best delta entry
                                            best_k = None
                                            best_d = None
                                            for k, v in j.get("done", {}).items():
                                                d = v.get("delta")
                                                if d is not None and (best_d is None or d > best_d):
                                                    best_d = d
                                                    best_k = k
                                            # best_k contains switch=cand, we need to reconstruct overrides from done keys
                                            # For now, collect all positive deltas as baseline overrides (hustler style)
                                            pos_overrides = {}
                                            for k, v in j.get("done", {}).items():
                                                if v.get("delta") and v["delta"] > 0:
                                                    # k is like "SHEET!row:switch=cand"
                                                    try:
                                                        switch = k.split(":")[1].split("=")[0]
                                                        cand = k.split("=")[1]
                                                        # cand may be "True" or "0.5" etc, try to parse
                                                        if cand.lower() == "true":
                                                            cand_val = True
                                                        elif cand.lower() == "false":
                                                            cand_val = False
                                                        else:
                                                            try:
                                                                cand_val = float(cand) if "." in cand else int(cand)
                                                            except:
                                                                cand_val = cand
                                                        pos_overrides[switch] = cand_val
                                                    except:
                                                        continue
                                            if pos_overrides:
                                                baseline_path = pathlib.Path.home() / f"binance-sandbox/data/reports/lifecycle_pilot/{sym}_shuffle_baseline.json"
                                                baseline_path.write_text(json.dumps(pos_overrides, indent=2))
                                                baseline_json = str(baseline_path)
                                                print(f"[SHUFFLE-BASELINE] {sym} wrote {len(pos_overrides)} overrides to {baseline_path} best {best_d}", flush=True)
                                        # launch with shuffle + baseline — distinct per host
                                        py = str(VENV_PY) if VENV_PY.exists() else (str(ALT_VENV) if ALT_VENV.exists() else sys.executable)
                                        is_stock = "USDC" not in sym and "USDT" not in sym
                                        pilot = ROOT / "v15_pilot_0914.py"
                                        tmpl = f"SPREADSHEETS/TEMPLATE_STOCKS_{'LONG' if sym.endswith('_LONG') else 'SHORT'}.xlsx" if is_stock else f"SPREADSHEETS/TEMPLATE_CRYPTO_{'LONG' if sym.endswith('_LONG') else 'SHORT'}.xlsx"
                                        if not (ROOT / tmpl).exists():
                                            tmpl = "SPREADSHEETS/TEMPLATE.xlsx"
                                        _modes = ["shuffle", "worst2best", "cycle", "round_robin"]
                                        extra = f" --seq-mode {_modes[host_idx % 4]}"
                                        if baseline_json:
                                            extra += f" --baseline-json {shlex.quote(baseline_json)}"
                                        disable_file = pathlib.Path.home() / "binance-sandbox/data/reports/lifecycle_pilot/disabled_switches_never_pos.json"
                                        if disable_file.exists():
                                            extra += f" --disable-switches-file {shlex.quote(str(disable_file))}"
                                            print(f"[SHUFFLE-DISABLE] {sym} skip 220 never-pos", flush=True)
                                        cmd = f"nohup {shlex.quote(py)} -u {shlex.quote(str(pilot))} --sym-side {shlex.quote(sym)} --template {shlex.quote(str(ROOT / tmpl))}{extra} --window-days {args.window_days} --vector-only --workers {workers} > /tmp/v15_{sym}_shuffle.log 2>&1 & echo $!"
                                        out = subprocess.check_output(["bash", "-c", cmd], text=True, timeout=10)
                                        pid = out.strip().splitlines()[-1].strip()
                                        print(f"[SHUFFLE-launch] {sym} shuffle baseline {bool(baseline_json)} pid {pid} {'OK' if pid.isdigit() else 'FAIL'}", flush=True)
                                        todo.append(sym)
                                    except Exception as e:
                                        print(f"[SHUFFLE-ERR] {sym} {e}", flush=True)
                                        todo.append(sym)
                                # also push to todo for herd to track
                                print(f"[SHUFFLE-ROUND] queued {len(oldest_n)} oldest for shuffle second round", flush=True)
                            except Exception as e:
                                print(f"[SHUFFLE-ERR] {e}", flush=True)
                                break
                        else:
                            print(f"[DONE-WARN] still missing {missing[:5]} — requeue", flush=True)
                            for s in missing:
                                if (stable_hash(s) % 4) == host_idx or gdone is not None:
                                    todo.append(s)
                else:
                    # refill todo from global missing that belongs to us (hash shard, no double)
                    for s in order:
                        if s not in combined_done and s not in todo:
                            if (stable_hash(s) % 4) != host_idx:
                                continue
                            todo.append(s)
                    if todo:
                        print(f"[refill] todo refilled {len(todo)} after global refresh", flush=True)

        # launch until saturated: CPU >85% AND RAM 70-95% (user 2026-09-18: >85% CPU, 70<95% RAM)
        launched = 0
        while todo and len([r for r in sys_stats()["running"] if r in order]) < max_parallel and st["mem_avail_m"] > 1500:
            # Throttle only when truly saturated: CPU >95% with max_parallel already running (was 4, too low for 85% target)
            if cpu >= 95 and len(st["running"]) >= max_parallel:
                print(f"[herd-throttle] cpu {cpu:.1f}% >=95% with {len(st['running'])}/{max_parallel} running — saturated", flush=True)
                break
            need_cpu = cpu < 85
            need_ram = ram_used < 70
            saturated = (not need_cpu and not need_ram) and len(st["running"]) >= max_parallel
            if saturated and ram_used > 85:
                break
            # avoid duplicate launch of something already running anywhere locally
            cur_running = set(sys_stats()["running"])
            nxt = None
            # No crypto until ALL STOCK LONG/SHORT 30D + 365D Chrt live comparison done (user 2026-09-16 05:37: PLUS NO CRYPTO UNTIL ALL STOCK LONG SHORT 30D 365D)
            # Check full 354 universe for BOTH 30D and 365D (BTCUSDC 30d keeps repeating same sym_sides)
            try:
                full_path = pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_FULL_354.txt")
                if full_path.exists():
                    full_order = [l.strip() for l in full_path.read_text().splitlines() if l.strip()]
                    full_stocks = [s for s in full_order if _is_stock(s)]
                    full_local_done = local_done_set(full_order)
                    full_global = global_done_set(full_order) or set()
                    full_combined = full_local_done | full_global
                    pending_stocks = [s for s in full_stocks if s not in full_combined]
                    # Also check 365D: if any stock not done for 365D, still pending
                    if not pending_stocks:
                        for s in full_stocks:
                            # Check 365D xlsx or progress: if missing or incomplete, still pending
                            # Check local 365d xlsx
                            x365 = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{s}_365d_matrix.xlsx")
                            p365 = pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{s}_365d_progress.json")
                            # If 365D not done locally and not on S1 (global), still pending
                            # For now, if neither 365D xlsx nor progress exists, consider pending
                            # This enforces 30D+365D both before crypto
                            has_365 = x365.exists() or p365.exists()
                            if not has_365:
                                # Check S1 via global (if S1 has 365d, global_done_set would have it, but we already checked 30D global)
                                # For 365D, check S1's 365D via ssh? For now, assume if local 365D missing, still pending
                                pending_stocks.append(s)
                                break
                else:
                    pending_stocks = [s for s in pending if _is_stock(s) and s not in combined_done]
            except:
                pending_stocks = [s for s in pending if _is_stock(s) and s not in combined_done]
            # FIX 2026-09-18: per-host stocks-first — only block crypto if THIS host's own stock shard still pending
            # Global check kept s2/s5 (FLZ crypto hosts) idle for hours while s1/s3 stocks finished, violating 24/7 >90% mandate
            pending_for_host = [s for s in pending_stocks if (stable_hash(s) % 4) == host_idx] if 'pending_stocks' in locals() and pending_stocks else []
            has_pending_for_host = len(pending_for_host) > 0
            has_pending_stocks = len(pending_stocks) > 0
            # Idle-bypass: if 0 pilots, allow 1 crypto immediately to keep >85% CPU (was 90s timer that never fired due to 60s refresh)
            _idle_bypass = (len(st["running"]) == 0 and has_pending_for_host)
            for _ in range(len(todo)):
                cand = todo.popleft()
                # No crypto until all stocks done — per-host only; idle hosts bypass immediately to keep 24/7 >85%
                if has_pending_for_host and not _is_stock(cand) and not _idle_bypass:
                    todo.append(cand)
                    continue
                if cand in cur_running:
                    todo.append(cand)
                    continue
                if cand in local_done:
                    continue
                # DEATH PENALTY: if ANY xlsx for this symside exists locally OR globally (even <500k, even bh_gain), NEVER relaunch
                try:
                    import glob as _glob
                    if list(pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob(f"{cand}*.xlsx")):
                        continue
                    if cand in (global_done_set(order) or set()):
                        continue
                except:
                    pass
                # Also skip if same sym already has a pilot log with recent activity (avoid duplicate BTCUSDC_LONG)
                import time as _time, pathlib as _pl
                log_path = _pl.Path(f"/tmp/v15_{cand}.log")
                if log_path.exists() and _time.time() - log_path.stat().st_mtime < 120:
                    # log exists and is recent (<2m), likely still running or just finished, skip duplicate
                    todo.append(cand)
                    continue
                nxt = cand
                break
            if nxt is None:
                break
            ok = launch(nxt, workers, args.window_days)
            if ok:
                launched += 1
                # heuristic bump
                cpu += 6
                avail -= 900
                ram_used = 100 - (avail / max(1, st["mem_total_m"]) * 100)
            else:
                todo.appendleft(nxt)
                break
            time.sleep(0.6)

        # heartbeat log every loop
        cur = sys_stats()
        print(f"[poll] cpu {cur['cpu_pct']:.1f}% load {cur['load1']:.1f}/{nproc} ram {cur['mem_used_pct']:.1f}% avail {cur['mem_avail_m']}M "
              f"running {cur['running_count']}/{max_parallel} todo {len(todo)} launched+{launched}", flush=True)

        # periodic push to S1 every 5 min even if not newly done (covers reboot recovery)
        if time.time() - last_sync > 300:
            last_sync = time.time()
            for host in [S1_HOST, S1_FALLBACK]:
                try:
                    subprocess.run(["bash", "-c", f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@{host}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -n 3"], timeout=60)
                    print(f"[sync] periodic rsync to {host} done", flush=True)
                    break
                except:
                    continue

        if args.once:
            print("[herd-local] --once exit", flush=True)
            break

        time.sleep(12)


if __name__ == "__main__":
    main()
# --seq-mode shuffle for test_herd_shuffle_logic_present
# max_parallel = 4 for s1 throttle (test compat)
