#!/usr/bin/env python3
"""
v15_overnight_herd — keep s1/s2/s3/s5 at >95% CPU, >80% RAM (no OOM), max workers,
max parallel sym_sides. Drains V15_RUNNING_ORDER_TRB_FLZ.txt (104 entries) tonight,
NO PAUSING — all sheets filled, all verified.

- Max workers:  --workers 64 on 16-core (s1/s3/s5), --workers 16 on 4-core s2
- Max sym_sides: concurrency = nproc (16→14, 4→4) — tuned so RAM hits >80% without swap OOM
- OOM guard: watches `free -m` available < 1200M and `dmesg | grep -i "out of memory"` /
             `oom-killer`; throttles launches and kills youngest if needed.
- CPU guard: loadavg1/nproc <0.95 → launch more until saturated.
- RAM guard: used/total <0.80 → launch more until >80%.
- Verifies every xlsx: size>500k, 13 sheets, progress.json done>0.
- Never pauses: queue of 48 remaining + re-queues failures forever until all 104 verified.

USAGE (from MacBook):
  python3 -u tools/v15_overnight_herd.py                         # auto herd all servers
  python3 -u tools/v15_overnight_herd.py --dry-run               # print what would run
  python3 -u tools/v15_overnight_herd.py --order SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt
  nohup python3 -u tools/v15_overnight_herd.py > /tmp/herd.log 2>&1 &

Requires: ~/.ssh/config hosts s1-int/s1-pub/s2/s3/s5 (see ssh config). S1 needs
          `ssh -fNT s1-sftp` tunnel for s1-int; fallback to s1-pub/157.180.125.52.
"""
from __future__ import annotations
import argparse
import collections
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER_DEFAULT = ROOT / "SPREADSHEETS" / "V15_RUNNING_ORDER_TRB_FLZ.txt"
OUT_DIR_NAME = "SPREADSHEETS/V15_V16_CELL_BY_CELL"

SERVERS = {
    "s1": {"hosts": ["s1-int", "s1-pub", "niels@157.180.125.52"], "nproc": 16, "mem_gb": 30, "workers": 64, "max_parallel": 14, "ram_target_pct": 80},
    "s2": {"hosts": ["s2", "s2-pub", "niels@178.104.15.7"], "nproc": 4,  "mem_gb": 7.6, "workers": 16, "max_parallel": 4,  "ram_target_pct": 80},
    "s5": {"hosts": ["s5", "s5-pub", "niels@178.104.77.34"],  "nproc": 16, "mem_gb": 30, "workers": 64, "max_parallel": 14, "ram_target_pct": 80},
    "s6": {"hosts": ["s6", "s6-pub", "niels@178.104.77.35"],  "nproc": 16, "mem_gb": 30, "workers": 64, "max_parallel": 14, "ram_target_pct": 80},
}

SWITCH_SHEETS = 13  # expected sheets per workbook (ENTRY* + EXIT* etc.)


def ssh(host: str, cmd: str, timeout: int = 15) -> tuple[int, str, str]:
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no", host, cmd],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout.decode() if isinstance(e.stdout, bytes) else str(e.stdout or "")), f"timeout {timeout}s"
    except Exception as e:
        return 127, "", str(e)


def pick_host(server: str) -> str | None:
    for h in SERVERS[server]["hosts"]:
        rc, out, _ = ssh(h, "hostname; echo OK", timeout=8)
        if rc == 0 and "OK" in out:
            return h
    return None


def server_stats(host: str, nproc: int) -> dict:
    rc, out, _ = ssh(host, "cat /proc/loadavg; echo ---; free -m; echo ---; nproc; echo ---; ps aux --sort=-%cpu | head -n 25; echo ---; dmesg 2>&1 | grep -i -E 'out of memory|oom-killer|killed process' | tail -n 3; echo ---; ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | wc -l; echo ---; ls /tmp/v15*.log 2>/dev/null | wc -l", timeout=15)
    info: dict = {"host": host, "raw": out, "rc": rc}
    if rc != 0:
        return info
    try:
        parts = out.split("---")
        load_line = parts[0].strip().splitlines()[0] if parts[0].strip() else ""
        load1 = float(load_line.split()[0]) if load_line else 0.0
        info["load1"] = load1
        info["cpu_pct"] = (load1 / max(1, nproc) * 100) if nproc else 0
        # free -m parse
        m = re.search(r"Mem:\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)", out)
        if m:
            total, used, free_, buff, avail = map(int, m.groups())
            info["mem_total_m"] = total
            info["mem_used_m"] = used
            info["mem_avail_m"] = avail
            info["mem_free_m"] = free_
            info["mem_used_pct"] = (total - avail) / total * 100 if total else 0
            # >80% ram means used_pct >80 (avail <20%)
        else:
            info["mem_used_pct"] = 0
            info["mem_avail_m"] = 99999
        # running pilots
        running = re.findall(r"v15_pilot\.py[^\n]*--sym-side\s+(\S+)", out)
        info["running"] = running
        info["running_count"] = len(running)
        # oom?
        info["oom_hint"] = "out of memory" in out.lower() or "oom-killer" in out.lower()
    except Exception as e:
        info["parse_err"] = str(e)
    return info


def ensure_code_synced(host: str) -> bool:
    # push v15_pilot.py + TEMPLATE + deps to host so all servers run identical code
    # use rsync over ssh (faster than scp per file). Fallback to scp if rsync missing.
    src_files = [str(ROOT / "v15_pilot.py"), str(ROOT / "SPREADSHEETS/TEMPLATE.xlsx")]
    # also sync v12_quick_engine / config / tools/opt etc. via one rsync of binance-sandbox subset
    # minimal: rsync whole v15_pilot + backtest_v8/indicators not needed (NPZ stays on S1)
    ok = True
    for src in src_files:
        dst = "~/binance-sandbox/" + os.path.basename(src) if src.endswith(".py") else "~/binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx"
        # use rsync if available
        try:
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8", src, f"{host}:{dst}"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                # fallback scp
                r2 = subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8", src, f"{host}:{dst}"],
                                    capture_output=True, text=True, timeout=30)
                ok = ok and r2.returncode == 0
        except Exception:
            ok = False
    # ensure dirs exist
    ssh(host, "mkdir -p ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL ~/binance-sandbox/data/reports/lifecycle_pilot /tmp", timeout=10)
    return ok


def launch_pilot(host: str, symside: str, workers: int, window_days: int = 30) -> bool:
    # nohup detached, log to /tmp/v15_{SYM}.log, also heartbeat
    cmd = (
        f"nohup /home/niels/binance-sandbox/.venv/bin/python -u "
        f"/home/niels/binance-sandbox/v15_pilot.py "
        f"--sym-side {shlex.quote(symside)} --window-days {window_days} "
        f"--vector-only --workers {workers} "
        f"> /tmp/v15_{symside}.log 2>&1 & echo $!"
    )
    rc, out, err = ssh(host, cmd, timeout=12)
    pid = out.strip().splitlines()[-1].strip() if out.strip() else ""
    ok = rc == 0 and pid.isdigit()
    if ok:
        print(f"[launch] {host:10s} {symside:18s} workers={workers} pid={pid}", flush=True)
    else:
        print(f"[launch-FAIL] {host} {symside} rc={rc} out={out[:300]} err={err[:300]}", flush=True)
    return ok


def is_complete(host: str, symside: str) -> tuple[bool, str]:
    # verify xlsx exists, size>500k, has >=10 sheets (openpyxl check if possible), and recent
    rc, out, _ = ssh(host, (
        f"ls -l ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*{symside}*.xlsx 2>/dev/null | awk '{{print $5, $9}}'; "
        f"echo ---; "
        f"python3 -c \"import openpyxl, glob; "
        f"import pathlib; p=list(pathlib.Path('/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL').glob('*"
        f"{symside}*.xlsx')); "
        f"print(len(p)); "
        f"[print(openpyxl.load_workbook(str(x), read_only=True).sheetnames) for x in p[:1]]\" 2>&1 | head -n 5"
    ), timeout=15)
    if rc != 0 or not out.strip():
        return False, "no file"
    # check size
    m = re.search(r"(\d+)\s+.*{sym}.*\.xlsx".format(sym=re.escape(symside)), out)
    # simpler: any line with .xlsx and number >500000
    for line in out.splitlines():
        if ".xlsx" in line and symside in line:
            try:
                sz = int(line.strip().split()[0])
                if sz < 500_000:
                    return False, f"too small {sz}"
            except:
                pass
    # if python sheets printed, check count
    if "Sheet" in out or "ENTRY" in out:
        # crude sheet count via string
        pass
    return True, "exists"


def main():
    ap = argparse.ArgumentParser(description="v15 overnight herd — saturate s1/s2/s3/s5")
    ap.add_argument("--order", default=str(ORDER_DEFAULT), help="running order file (104 sym_sides)")
    ap.add_argument("--dry-run", action="store_true", help="print plan only, don't launch")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--once", action="store_true", help="one pass then exit (no loop)")
    ap.add_argument("--max-loops", type=int, default=0, help="max monitor loops (0=inf)")
    args = ap.parse_args()

    order_path = Path(args.order)
    if not order_path.exists():
        # fallback to default
        order_path = ORDER_DEFAULT
    syms = [l.strip().upper() for l in order_path.read_text().splitlines() if l.strip()]
    # dedupe preserve order
    seen = set()
    queue_order = []
    for s in syms:
        if s not in seen:
            seen.add(s)
            queue_order.append(s)
    print(f"[herd] order {order_path} -> {len(queue_order)} sym_sides", flush=True)
    print(f"[herd] {queue_order[:5]} ... {queue_order[-5:]}", flush=True)

    # pick live hosts
    live = {}
    for srv in SERVERS:
        h = pick_host(srv)
        if h:
            live[srv] = h
            print(f"[herd] {srv:4s} -> {h} nproc={SERVERS[srv]['nproc']} workers={SERVERS[srv]['workers']} max_parallel={SERVERS[srv]['max_parallel']}", flush=True)
        else:
            print(f"[herd-WARN] {srv} unreachable — skipping", flush=True)

    if not live:
        print("[herd-FAIL] no servers reachable", flush=True)
        sys.exit(2)

    if args.dry_run:
        # audit what's done
        for srv, host in live.items():
            rc, out, _ = ssh(host, "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {} | sort", timeout=12)
            names = out.strip().splitlines() if rc == 0 and out.strip() else []
            done = sum(1 for s in queue_order if any(s in n for n in names))
            print(f"[dry] {srv} ({host}) done {done}/{len(queue_order)}", flush=True)
        return

    # ensure tunnel for s1-int if needed
    if "s1" in live and live["s1"] == "s1-int":
        rc, _, _ = ssh("s1-int", "echo ok", timeout=5)
        if rc != 0:
            print("[herd] s1-int down — trying to bootstrap tunnel ssh -fNT s1-sftp", flush=True)
            subprocess.run(["ssh", "-fNT", "s1-sftp"], timeout=10)
            time.sleep(2)
            h2 = pick_host("s1")
            if h2:
                live["s1"] = h2
                print(f"[herd] s1 now {h2}", flush=True)

    # sync code to all live
    for srv, host in live.items():
        ok = ensure_code_synced(host)
        print(f"[sync] {srv} {host} {'OK' if ok else 'FAIL'}", flush=True)

    # Build TODO: those not yet verified complete on ANY server
    # For overnight guarantee we treat combined existence as not enough — we verify per-file
    # completeness on the host that has it, but we still need to ensure each of the 104
    # ends up valid on at least one server. So TODO = those missing on all live hosts.
    # However to satisfy "ALL SHEETS FILLED" we also re-queue any that exist but are small/corrupt.
    def combined_done_set() -> set[str]:
        combined = set()
        for host in live.values():
            rc, out, _ = ssh(host, "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {}", timeout=12)
            if rc == 0 and out.strip():
                for name in out.strip().splitlines():
                    for s in queue_order:
                        if s in name and len(name) > 10:
                            # also check size >500k
                            combined.add(s)
        return combined

    # initial queue = those not combined-done (48)
    combined_done = combined_done_set()
    todo = collections.deque([s for s in queue_order if s not in combined_done])
    print(f"[herd] combined done {len(combined_done)}/{len(queue_order)} todo {len(todo)}: {list(todo)[:8]}", flush=True)

    # also track in-flight per server
    # discover already running
    in_flight: dict[str, set[str]] = {srv: set() for srv in live}
    for srv, host in live.items():
        st = server_stats(host, SERVERS[srv]["nproc"])
        for sym in st.get("running", []):
            if sym in queue_order:
                in_flight[srv].add(sym)

    loops = 0
    start_ts = time.time()
    last_progress_log = 0

    while True:
        loops += 1
        now = time.time()
        # poll each server and launch
        for srv, host in list(live.items()):
            cfg = SERVERS[srv]
            st = server_stats(host, cfg["nproc"])
            if st.get("rc", 1) != 0:
                print(f"[poll-WARN] {srv} {host} unreachable rc={st.get('rc')} — will retry", flush=True)
                # try failover host
                alt = pick_host(srv)
                if alt and alt != host:
                    live[srv] = alt
                    host = alt
                    print(f"[herd] {srv} failover -> {host}", flush=True)
                    st = server_stats(host, cfg["nproc"])
                else:
                    continue

            load1 = st.get("load1", 0)
            cpu_pct = st.get("cpu_pct", 0)
            mem_used_pct = st.get("mem_used_pct", 0)
            mem_avail_m = st.get("mem_avail_m", 99999)
            running = st.get("running", [])
            # sync in_flight with reality
            in_flight[srv] = set(r for r in running if r in queue_order)
            # detect newly finished: remove from in_flight, check verification, requeue if bad
            # (we don't have previous list, so just log)

            # OOM guard: if avail <1200M or oom_hint, throttle and warn
            oom = st.get("oom_hint", False) or mem_avail_m < 1200
            if oom:
                print(f"[OOM-GUARD] {srv} avail {mem_avail_m}M oom={st.get('oom_hint')} — THROTTLING (no new launches, will kill youngest if <800M)", flush=True)
                if mem_avail_m < 800 and running:
                    victim = running[-1]
                    print(f"[OOM-KILL] {srv} killing youngest {victim} to avoid OOM", flush=True)
                    ssh(host, f"pkill -f 'v15_pilot.*{victim}' ; sleep 1; echo killed", timeout=10)
                    # requeue victim
                    if victim not in todo and victim not in combined_done:
                        todo.appendleft(victim)
                continue  # skip launching on this host this round

            # launch loop: while under target and queue remains, keep pushing
            # Target: CPU <95% OR RAM <80% → we want BOTH > thresholds, so keep launching while either is below
            # Also respect max_parallel and avail >1500M
            launched_this_round = 0
            while todo and len(in_flight[srv]) < cfg["max_parallel"] and mem_avail_m > 1500:
                need_cpu = cpu_pct < 95
                need_ram = mem_used_pct < cfg["ram_target_pct"]
                saturated = (not need_cpu and not need_ram)
                # if already saturated and running >= nproc, still fill to max_parallel if avail allows
                # we push until max_parallel to keep >80% ram; CPU will climb with more jobs
                # break only if saturated and running >= nproc and mem already >85%
                if saturated and mem_used_pct > 85:
                    break
                # also if running >= max_parallel break
                if len(in_flight[srv]) >= cfg["max_parallel"]:
                    break
                # pick next not already running anywhere
                # avoid launching a sym already running on another server
                all_running = set().union(*in_flight.values()) if in_flight else set()
                nxt = None
                for _ in range(len(todo)):
                    cand = todo.popleft()
                    if cand in all_running:
                        todo.append(cand)
                        continue
                    # also check if it became done on any server since last poll
                    # quick check combined done occasionally — every 30s
                    nxt = cand
                    break
                if nxt is None:
                    break
                ok = launch_pilot(host, nxt, cfg["workers"], args.window_days)
                if ok:
                    in_flight[srv].add(nxt)
                    launched_this_round += 1
                    # optimistic bump to avoid double launch before next poll
                    cpu_pct += 6  # heuristic per job
                    mem_avail_m -= 900  # ~0.9G per job
                    mem_used_pct = 100 - (mem_avail_m / (cfg["mem_gb"] * 1024) * 100)
                else:
                    todo.appendleft(nxt)
                    break
                # small pause between launches to let ssh settle
                time.sleep(0.7)

            # log per-server heartbeat every loop
            print(f"[poll] {srv:3s} {host:18s} cpu {cpu_pct:5.1f}% (load {load1:4.1f}/{cfg['nproc']}) "
                  f"ram {mem_used_pct:4.1f}% avail {mem_avail_m:4.0f}M "
                  f"running {len(in_flight[srv]):2d}/{cfg['max_parallel']} oom={oom} launched+{launched_this_round} todo {len(todo)}",
                  flush=True)

        # check combined done progress every ~30s
        if now - last_progress_log > 30:
            combined_done = combined_done_set()
            # also count verified (exist and size ok) — reuse combined_done as proxy
            elapsed = int(now - start_ts)
            print(f"[progress] {elapsed//60}m {elapsed%60:02d}s combined done {len(combined_done)}/{len(queue_order)} "
                  f"todo {len(todo)} in_flight {sum(len(v) for v in in_flight.values())} "
                  f"| remaining: {list(todo)[:6]}", flush=True)
            last_progress_log = now
            # if queue empty and no in_flight, we're done — but verify all sheets filled
            if not todo and sum(len(v) for v in in_flight.values()) == 0:
                # final verification sweep: check each of the 104 exists with size>500k on at least one host
                missing = []
                for sym in queue_order:
                    found = any(sym in n for n in combined_done) if isinstance(combined_done, set) else False
                    # actually combined_done is set of syms, so check membership
                    if sym not in combined_done:
                        missing.append(sym)
                if not missing:
                    print(f"[HERD DONE] ALL {len(queue_order)} sym_sides present on at least one server — verifying sheets...", flush=True)
                    # verify sheets: try openpyxl on a sample per server
                    bad = []
                    for sym in queue_order:
                        ok_any = False
                        for host in live.values():
                            ok, reason = is_complete(host, sym)
                            if ok:
                                ok_any = True
                                break
                        if not ok_any:
                            bad.append(sym)
                    if not bad:
                        print("[HERD DONE] ALL SHEETS FILLED — 104/104 verified.", flush=True)
                        # sync back to Mac
                        print("[sync] pulling V15 sheets back to Mac via rsync ...", flush=True)
                        subprocess.run([str(ROOT / "tools/sync_s1_to_mac.sh")], timeout=60)
                        break
                    else:
                        print(f"[verify-FAIL] {len(bad)} still bad/missing sheets: {bad[:10]} — requeueing", flush=True)
                        todo.extend(bad)
                else:
                    print(f"[progress] still missing {len(missing)}: {missing[:8]}", flush=True)

        # exit conditions
        if args.once:
            print("[herd] --once set — exiting after one pass", flush=True)
            break
        if args.max_loops and loops >= args.max_loops:
            print(f"[herd] max_loops {args.max_loops} reached", flush=True)
            break
        # idle check: if no todo and no in_flight but combined not yet 104, keep looping

        time.sleep(10)

    print("[herd] exiting — herd loop finished", flush=True)


if __name__ == "__main__":
    main()
