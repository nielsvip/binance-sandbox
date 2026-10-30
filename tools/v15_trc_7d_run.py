#!/usr/bin/env python3
"""trc 7D lane: one timed 7D catch-up pass over every tradeable stock key, staged to trc (paper) as each board finishes.

USER 2026-10-29: "7D uses the catch-up settings so it can finish quickly. Run it now to time how long the entire run
will take and start trading it on trc as soon as it finishes (it is paper)". Morning schedule (same tool, --mode
premarket): klines from 08:45 ET, 3 temporary NPZ bars 08:45/09:00/09:15 per board (isolated overlay), the lower half
of the ranking starts at 09:00, best-first at 09:15, runs until finished; 30D backtests paused meanwhile and resumed as
soon as every key has its 7D set.

Runs on S5 (stock NPZ owner + trc trader host). Per tick:
  pause   fleet_dispatch hold (data/fleet_hold_hosts.json on s1+s2, with expiry) + SIGSTOP of every 30D v15_pilot on
          each host (pids recorded, SIGCONT on resume). Running 30D work is suspended, never killed.
  launch  tools/v15_preopen_7d_launch.py per key with V18_CATCHUP=1, nice 15, NPZ shipped md5-verified from S5;
          per-host slot cap + MemAvailable floor + launch spacing keep the live traders (S1 crypto, S5 stocks) fed.
  stage   finished board -> its 7D cumulative_overrides on top of the 30D row's defaults -> per_sym_store_trc7d.db
          (per_sym_active) + ~/fwd_trc7d_syms.txt; the trc 7D state watcher and the trc trader pick it up. A board that
          failed the pilot's qualification gate is still staged (trc is paper) with quality=DIAGNOSTIC_ONLY + reasons.
  resume  every key terminal -> SIGCONT + hold removed; timing report written.
State + report: data/preopen_7d/trc_run/<day>/{state.json,timing.json}.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PY = "/home/niels/.conda/envs/binance_env_mac/bin/python"
BOOKS_ROOT = Path.home() / "binance"
SRC_DB = ROOT / "data/hourly_reconfig/per_sym_store.db"
TRC_DB = ROOT / "data/hourly_reconfig/per_sym_store_trc7d.db"
SIDES_FILE = Path.home() / "fwd_trc7d_syms.txt"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]
# name -> (ip, slots). S1 trades crypto and runs the crypto NPZ/state chain, S5 trades stocks: fewer 7D slots there.
# 2026-10-29 18:4xZ: S5 at 3 boards + its live trader/state chain took ~9 kernel OOM kills (board workers mid-sweep) -> 2.
HOSTS = {"s5": ("local", 2), "s6": ("10.0.0.6", 4), "s2": ("10.0.0.4", 2), "s1": ("10.0.0.3", 1)}
# overnight (USER 2026-10-29 "the bottom half ... starting at 20:00 and do it slowly overnight while all tests keep running"):
# nothing paused, one 7D board per stock/crypto worker beside the 30D boards, none on S1 (crypto live + NPZ/state chain)
OVERNIGHT_SLOTS = {"s5": 1, "s6": 1, "s2": 1, "s1": 0}
LEADERS = ("10.0.0.3", "10.0.0.4")
MEM_FLOOR_MB = int(os.environ.get("TRC7D_MEM_FLOOR_MB", "5000"))
LAUNCH_GAP_S = int(os.environ.get("TRC7D_LAUNCH_GAP_S", "45"))
BOARD_TIMEOUT_S = int(os.environ.get("TRC7D_BOARD_TIMEOUT_S", "2400"))
DEADLINE_S = int(os.environ.get("TRC7D_DEADLINE_S", str(3 * 3600)))
HOLD_WHY ="TRC7D pass: 30D paused until every tradeable stock key has its 7D set"
STOP_PIDS = "/tmp/v15_trc7d_stopped.pids"
# USER 2026-10-29 "suspend the 30D boards to disk": no swap anywhere and every disk is under the 40 GB floor, so the disk image
# is the board's own per-row progress checkpoint. Same preemption fleet_mem_valve uses: SIGTERM the board PARENT (the pilot
# saves at the next cell boundary and exits; its fork pool goes with it), book "restarted" in the dispatcher's launch record
# (no death, no host quarantine), and on resume the dispatcher relaunches it first (data/fleet_priority.json) from its progress.
PAUSE_SCRIPT = r"""
import json, os, signal, time
ps = {}
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        args = open(f'/proc/{pid}/cmdline', 'rb').read().split(b'\0')
        if not any(a.endswith(b'v15_pilot.py') for a in args[:3]) or b'--sym-side' not in args:
            continue
        if b'--window-days' in args and args[args.index(b'--window-days') + 1] == b'7':
            continue
        ppid = int(open(f'/proc/{pid}/stat').read().rsplit(')', 1)[1].split()[1])
        ps[int(pid)] = (ppid, args[args.index(b'--sym-side') + 1].decode())
    except (OSError, ValueError, IndexError):
        continue
tops = {pid: ss for pid, (ppid, ss) in ps.items() if ppid not in ps}
for pid, ss in tops.items():
    path = os.path.expanduser(f'~/fleet_dispatch/launches/{ss}.json')
    try:
        rec = json.load(open(path))
        rec['restarted'] = int(time.time())
        rec['restarted_by'] = 'trc7d_preempt'
        json.dump(rec, open(path + '.trc7d_tmp', 'w'))
        os.replace(path + '.trc7d_tmp', path)
    except (OSError, ValueError):
        pass
    for sig in (signal.SIGTERM, signal.SIGCONT):  # SIGCONT: a board still frozen by the earlier SIGSTOP pause
        try:
            os.kill(pid, sig)
        except OSError:
            pass
for pid, (ppid, ss) in ps.items():
    if pid not in tops:
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass
open('/tmp/v15_trc7d_preempted.txt', 'a').write(''.join(ss + '\n' for ss in tops.values()))
open('/tmp/v15_preopen_7d.pause', 'a').close()
print(json.dumps(sorted(tops.values())))
"""
RESUME_SCRIPT = r"""
import os
try:
    sides = sorted(set(open('/tmp/v15_trc7d_preempted.txt').read().split()))
except OSError:
    sides = []
for path in ('/tmp/v15_trc7d_preempted.txt', '/tmp/v15_trc7d_stopped.pids', '/tmp/v15_preopen_7d.pause'):
    try:
        os.remove(path)
    except OSError:
        pass
print(' '.join(sides))
"""
PROBE_SCRIPT = r"""
import json, os, re
mem = int(re.search(r'^MemAvailable:\s+(\d+)', open('/proc/meminfo').read(), re.M).group(1)) // 1024
run = set()
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        args = open(f'/proc/{pid}/cmdline', 'rb').read().split(b'\0')
    except OSError:
        continue
    if any(a.endswith(b'v15_pilot.py') for a in args) and b'--window-days' in args and b'--sym-side' in args:
        if args[args.index(b'--window-days') + 1] == b'7':
            run.add(args[args.index(b'--sym-side') + 1].decode())
print(json.dumps({"mem": mem, "running7d": sorted(run), "load1": os.getloadavg()[0]}))
"""
EXTRACT_SCRIPT = r"""
import json, sys
p = json.load(open(sys.argv[1]))
keep = ("window_days", "result_done_utc", "result_start_utc", "cumulative_gain", "baseline_gain", "initial_baseline_gain",
        "not_compliant", "diagnostic_only", "impossible_reasons", "impossible_metrics", "verdict", "done", "result_cpu_s")
out = {k: p.get(k) for k in keep}
out["cumulative_overrides"] = p.get("cumulative_overrides")
print(json.dumps(out, default=str))
"""


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic(path: Path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n")
    os.replace(tmp, path)


def sh(ip, script, timeout=60, inp=None):
    cmd = ["bash", "-c", script] if ip == "local" else SSH + [f"niels@{ip}", script]
    try:
        r = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def pyrun(ip, script, args="", timeout=60):
    return sh(ip, f"python3 - {args}", timeout, inp=script)


def build_queue(day_dir: Path):
    from tools.v15_preopen_7d_stock_select import build
    doc = build(BOOKS_ROOT, dt.datetime.now(dt.timezone.utc), SRC_DB)
    atomic(day_dir / "queue.json", doc)
    return doc


def hold(on: bool):
    """fleet_dispatch's own expiring host hold on both leaders; only TRC7D entries are added/removed."""
    until = time.time() + 4 * 3600
    code = (
        "import json,sys,pathlib\n"
        "p=pathlib.Path.home()/'binance-sandbox/data/fleet_hold_hosts.json'\n"
        "try: d=json.loads(p.read_text()) or {}\n"
        "except Exception: d={}\n"
        f"on={on!r}\n"
        "for h in ('s1','s2','s5','s6'):\n"
        f"    if on: d[h]={{'until':{until},'why':{HOLD_WHY!r}}}\n"
        "    elif str((d.get(h) or {}).get('why','')).startswith('TRC7D'): d.pop(h)\n"
        "t=p.with_suffix('.trc7d.tmp'); t.write_text(json.dumps(d)); t.replace(p); print(sorted(d))\n")
    return {ip: pyrun(ip, code, timeout=30)[1].strip() for ip in LEADERS}


def prioritize(sides):
    """The preempted 30D boards go first when the dispatcher resumes (its own expiring priority file, merged)."""
    until = time.time() + 6 * 3600
    code = (
        "import json,time,pathlib\n"
        "p=pathlib.Path.home()/'binance-sandbox/data/fleet_priority.json'\n"
        "try: d=json.loads(p.read_text()) or {}\n"
        "except Exception: d={}\n"
        "old=d.get('sym_sides') or [] if float(d.get('until') or 0)>time.time() else []\n"
        f"d={{'sym_sides':sorted(set(old)|set({sides!r})),'until':max(float(d.get('until') or 0),{until}),"
        "'why':'TRC7D: 30D boards preempted for the 7D pass resume first'}\n"
        "t=p.with_suffix('.trc7d.tmp'); t.write_text(json.dumps(d)); t.replace(p); print(len(d['sym_sides']))\n")
    return {ip: pyrun(ip, code, timeout=30)[1].strip() for ip in LEADERS}


def ship_npz(ip, symbol):
    """S5's NPZ to the target, md5-verified, never regressing the target's tail."""
    if ip == "local":
        return "local"
    rel = f"backtest_v8/indicators/{symbol}.npz"
    src = ROOT / rel
    md5 = hashlib.md5(src.read_bytes()).hexdigest()
    tmp = f"binance-sandbox/backtest_v8/indicators/.{symbol}.npz.trc7d"
    r = subprocess.run(["rsync", "-a", "--timeout=60", "-e", " ".join(SSH), str(src), f"niels@{ip}:{tmp}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        return f"RSYNC_FAIL {r.stderr[-120:]}"
    install = (f"cd ~ && [ \"$(md5sum {tmp} | cut -c1-32)\" = {md5} ] && {PY} -c \"import numpy as np,sys,os;"
               f"a=np.load(sys.argv[1],mmap_mode='r')['timestamps'];b=np.load(sys.argv[2],mmap_mode='r')['timestamps'] "
               f"if os.path.exists(sys.argv[2]) else [0];sys.exit(0 if float(a[-1])>=float(b[-1]) else 3)\" {tmp} "
               f"binance-sandbox/{rel} && mv -f {tmp} binance-sandbox/{rel} && echo INSTALLED || {{ rc=$?; rm -f {tmp}; echo REFUSED-$rc; }}")
    return sh(ip, install, 90)[1].strip()[-40:]


def launch(name, ip, ss, day, mode, no_overlay=False):
    rdir = f"binance-sandbox/data/preopen_7d/trc_run/{day}/results"
    q = f"binance-sandbox/data/preopen_7d/trc_run/{day}/queue.json"
    if ip != "local":
        sh(ip, f"mkdir -p {rdir}/{ss}", 20)
        r = subprocess.run(["rsync", "-a", "--timeout=60", "-e", " ".join(SSH), str(ROOT / f"data/preopen_7d/trc_run/{day}/queue.json"),
                            f"niels@{ip}:{q}"], capture_output=True, text=True, timeout=120)
        if r.returncode:
            return f"QUEUE_SYNC_FAIL {r.stderr[-120:]}"
    # premarket boards carry the temporary overlay of the three 15m bars ending at the open; a premarket board launched at or
    # after the open (the canonical NPZ then holds the session) and every intraday/overnight board run on the canonical NPZ.
    # UTC only (USER 2026-10-30): the open is the exchange calendar's UTC epoch.
    late = mode == "premarket" and time.time() >= (session_open_utc() or float("inf"))
    late = late or no_overlay  # a premarket-thin side has no premarket bars: it runs on the canonical NPZ like an overnight board
    env = ("V18_CATCHUP=1 V15_7D_PILOT_FILE=v15_pilot.py V15_7D_ALREADY_PAUSED=1 V15_7D_MIN_MEM_MB=3000 "
           + ("V15_7D_ANYTIME=1 V15_7D_OVERLAY=0 " if mode != "premarket" or late else ""))
    # setsid -f + every fd redirected: the board must not hold the ssh session (or the local pipe) open
    cmd = (f"cd ~/binance-sandbox && mkdir -p data/preopen_7d/trc_run/{day}/results/{ss} && "
           f"test ! -e data/preopen_7d/trc_run/{day}/results/{ss}/launcher.json && "
           f"{env} setsid -f nice -n 15 {PY} -u tools/v15_preopen_7d_launch.py --queue ~/{q} --sym-side {ss} "
           f"--result-dir ~/{rdir} --workers 7 --timeout-sec {BOARD_TIMEOUT_S} --python {PY} "
           f"> ~/{rdir}/{ss}/launcher.json 2> ~/{rdir}/{ss}/launcher.stderr < /dev/null && echo LAUNCHED")
    rc, out, err = sh(ip, cmd, 30)
    return out.strip() or f"rc={rc} {err[-120:]}"


def result_of(ip, ss, day, folder=None):
    base = f"~/binance-sandbox/data/preopen_7d/trc_run/{day}/results/{folder or ss}"
    rc, out, _ = sh(ip, f"test -s {base}/launcher.json && tail -n 1 {base}/launcher.json", 20)
    if rc or not out.strip():
        return None
    try:
        launcher = json.loads(out.strip().splitlines()[-1])
    except ValueError:
        return None
    if "valid" not in launcher:  # the launcher's REHEARSAL_WINDOW_OVERRIDE event line, not its result
        return None
    rc, out, err = sh(ip, f"test -s {base}/progress/{ss}_7d_progress.json && python3 - {base}/progress/{ss}_7d_progress.json",
                      90, inp=EXTRACT_SCRIPT)
    progress = None
    if not rc and out.strip():
        try:
            progress = json.loads(out.strip().splitlines()[-1])
        except ValueError:
            progress = None
    rc, out, _ = sh(ip, f"grep -c 'REDO scheduled' {base}/pilot.log", 20)
    redo = out.strip().isdigit() and int(out.strip()) > 0
    rc, out, _ = sh(ip, f"grep -c '^\\[PUBLISH\\] ' {base}/pilot.log", 20)
    # the pilot passed its publish gates and wrote the 7D workbook + manifest; result_done_utc is stamped only after the
    # live-verify step, which the 7D launcher skips (XOM_LONG 2026-10-29: published 31 trades +4.48 %, refused as "not completed")
    published = out.strip().isdigit() and int(out.strip()) > 0
    rc, out, _ = sh(ip, f"grep -c -E 'NO-TRADES-SKIP|0-TRADES-FAST-FAIL' {base}/pilot.log", 20)
    # the board's honest 7D answer is "no trade" (0 trades, no flip restores any): trc stays flat on it
    no_trades = out.strip().isdigit() and int(out.strip()) > 0 and not (progress or {}).get("cumulative_overrides")
    return {"launcher": launcher, "progress": progress, "redo": redo, "no_trades": no_trades, "published": published}


def stage(ss, res, day):
    """-> (staged, why). Writes the trc 7D row; never touches the 30D store."""
    prog = res.get("progress") or {}
    cum = prog.get("cumulative_overrides")
    # USER 2026-10-29 (option 1): a board that ended in the pilot's [REDO] (its compliance repair changed the set, rows not
    # refilled) is staged with its pre-repair set — the greedy chain it measured — instead of being relaunched
    published = bool(res.get("published"))
    redo = bool(res.get("redo")) and not prog.get("result_done_utc") and not published
    if prog.get("window_days") != 7 or not (prog.get("result_done_utc") or redo or published) or not isinstance(cum, dict) or not cum:
        return False, f"no finished 7D set ({(res.get('launcher') or {}).get('why')})"
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True, timeout=10)
    src.row_factory = sqlite3.Row
    row = src.execute("SELECT * FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
    cols = [d[1] for d in src.execute("PRAGMA table_info(per_sym_active)").fetchall()]
    src.close()
    if row is None:
        return False, "30D source row vanished"
    defaults = json.loads(row["defaults_snapshot_json"] or "{}")
    full = {**defaults, **cum}
    launcher = res.get("launcher") or {}
    quality = "QUALIFIED" if launcher.get("valid") else ("PUBLISHED" if published else "DIAGNOSTIC_ONLY")
    why = None if launcher.get("valid") or published else (launcher.get("why") or "pilot qualification gate")
    if redo:
        why = "REDO: pre-repair set (pilot compliance repair changed it; rows not refilled)"
    con = sqlite3.connect(TRC_DB, timeout=10)
    con.execute(f"CREATE TABLE IF NOT EXISTS per_sym_active ({', '.join(cols)})")
    con.execute("CREATE TABLE IF NOT EXISTS trc7d_provenance (sym_side TEXT, run_day TEXT, staged_utc TEXT, quality TEXT, why TEXT,"
                " result_done_utc TEXT, cumulative_gain REAL, baseline_gain REAL, impossible_reasons TEXT, source_30d_updated_at TEXT,"
                " full_config_sha256 TEXT)")
    values = dict(row)
    values.update(overrides_json=json.dumps(cum, sort_keys=True, default=str), full_config_json=json.dumps(full, sort_keys=True, default=str),
                  updated_at=now_iso())
    con.execute("DELETE FROM per_sym_active WHERE sym_side=?", (ss,))
    con.execute(f"INSERT INTO per_sym_active ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [values[c] for c in cols])
    con.execute("INSERT INTO trc7d_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ss, day, now_iso(), quality, why, prog.get("result_done_utc"), prog.get("cumulative_gain"), prog.get("baseline_gain"),
                 json.dumps(prog.get("impossible_reasons"), default=str), row["updated_at"],
                 hashlib.sha256(json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()))
    con.commit()
    con.close()
    sides = set(SIDES_FILE.read_text().split()) if SIDES_FILE.exists() else set()
    sides.add(ss)
    tmp = SIDES_FILE.with_name(SIDES_FILE.name + ".tmp")
    tmp.write_text("\n".join(sorted(sides)) + "\n")
    os.replace(tmp, SIDES_FILE)
    return True, quality


def timing(state):
    rows = [p for p in state["placements"].values() if p.get("started") and p.get("finished")]
    walls = sorted(p["finished"] - p["started"] for p in rows)
    t0 = state["started_epoch"]
    end = max([p["finished"] for p in rows] or [time.time()])
    return {"day": state["day"], "mode": state["mode"], "started_utc": state["started_utc"], "keys": len(state["placements"]),
            "finished": len(rows), "staged": sum(1 for p in state["placements"].values() if p.get("staged")),
            "qualified": sum(1 for p in state["placements"].values() if p.get("quality") == "QUALIFIED"),
            "total_wall_min": round((end - t0) / 60, 1), "board_wall_min_p50": round(walls[len(walls) // 2] / 60, 1) if walls else None,
            "board_wall_min_max": round(walls[-1] / 60, 1) if walls else None,
            "by_host": {h: sum(1 for p in state["placements"].values() if p.get("host") == h) for h in HOSTS},
            "first_staged_utc": state.get("first_staged_utc"), "complete_utc": state.get("complete_utc")}


def session_open_utc(day=None):
    """UTC epoch of the exchange open on this UTC date (None when there is no session)."""
    from tools.npz_cutoffs import stock_session_bounds

    bounds = stock_session_bounds(day or dt.datetime.now(dt.timezone.utc).date())
    return int(bounds[0]) if bounds else None


def premarket_thin(symbols, sessions=3):
    """USER 2026-10-30: symbols without premarket trades get their 7D overnight, not in the morning pass. A symbol is thin when
    Tradier served none of the three 15m bars ending at the open on ANY of the last `sessions` sessions (one timesales call
    per symbol, matched on Tradier's UTC epoch). Returns {"sessions": [...], "thin": [...], "bars": {symbol: [n per session]}}."""
    import requests
    from tools.npz_premarket_temp_bars import temp_epochs, tradier_keys

    now = time.time()
    days, day = [], dt.datetime.now(dt.timezone.utc).date()
    while len(days) < sessions:
        epochs = temp_epochs(day)
        if epochs and epochs[-1] + 900 <= now:
            days.append(day)
        day -= dt.timedelta(days=1)
    days.reverse()
    keys = tradier_keys(ROOT)
    out = {"sessions": [d.isoformat() for d in days], "thin": [], "bars": {}, "errors": {}}
    for i, symbol in enumerate(sorted(set(symbols))):
        r = None
        try:
            r = requests.get("https://api.tradier.com/v1/markets/timesales", timeout=15,
                             params={"symbol": symbol, "interval": "15min", "start": f"{days[0].isoformat()} 00:00",
                                     "end": f"{days[-1].isoformat()} 23:59", "session_filter": "all"},
                             headers={"Authorization": f"Bearer {keys[i % len(keys)]}", "Accept": "application/json"})
            data = ((r.json() or {}).get("series") or {}).get("data") or [] if r.status_code == 200 else None
        except Exception as exc:
            data, out["errors"][symbol] = None, f"{type(exc).__name__}: {str(exc)[:80]}"
        if data is None:
            out["errors"].setdefault(symbol, f"HTTP {getattr(r, 'status_code', '?')}")
            continue  # unknown is never classified thin: it stays in its normal half
        stamps = {int(d.get("timestamp") or 0) for d in (data if isinstance(data, list) else [data])}
        counts = [len(stamps & set(temp_epochs(d))) for d in days]
        out["bars"][symbol] = counts
        if any(c == 0 for c in counts):
            out["thin"].append(symbol)
    return out


def wave_open(state, ss, now):
    """premarket: the less important half of this pass starts 30 min before the open (that first bar's price is not very
    reliable), the best half best-first 15 min before the open. UTC epochs only."""
    if state["mode"] != "premarket":
        return True
    if state["placements"][ss].get("no_overlay"):
        # a premarket-thin side never takes a slot from a best-first side: it starts once every other side has launched
        return not any(p.get("status") == "pending" and not p.get("no_overlay") for p in state["placements"].values())
    pos = state["placements"][ss].get("pos", state["placements"][ss]["rank"])
    upper = pos <= (len(state["placements"]) + 1) // 2
    opened = session_open_utc()
    return opened is not None and now >= opened - (900 if upper else 1800)


def tick(state, day_dir):
    now = time.time()
    day = state["day"]
    if not state.get("paused") and state["mode"] != "overnight":
        state["hold"] = hold(True)
        state["paused"] = {n: pyrun(ip, PAUSE_SCRIPT, timeout=60)[1].strip() for n, (ip, _) in HOSTS.items()}
        state["paused_utc"] = now_iso()
    probes = {}
    for name, (ip, slots) in HOSTS.items():
        rc, out, _ = pyrun(ip, PROBE_SCRIPT, timeout=40)
        try:
            probes[name] = json.loads(out.strip().splitlines()[-1]) if not rc else None
        except (ValueError, IndexError):
            probes[name] = None
    state["probes"] = probes
    for name, pr in probes.items():
        for ss in (pr or {}).get("running7d") or []:
            p = state["placements"].get(ss)
            if p and p["status"] == "pending":
                # a board already running there (a launch whose reply was lost): adopt it, never launch a duplicate
                p.update(status="running", host=name, started=p.get("started") or now, adopted=True)
    for ss, p in state["placements"].items():
        if p["status"] != "running":
            continue
        ip = HOSTS[p["host"]][0]
        res = result_of(ip, ss, day)
        if res is None:
            if probes.get(p["host"]) and ss not in probes[p["host"]]["running7d"] and now - p["started"] > 300:
                p["misses"] = p.get("misses", 0) + 1
                if p["misses"] >= 3:
                    p.update(status="failed", why="launcher vanished without a result", finished=now)
            continue
        p.update(status="done", finished=now, launcher=res["launcher"],
                 summary={k: v for k, v in (res.get("progress") or {}).items() if k != "cumulative_overrides"})
        ok, why = stage(ss, res, day)
        p.update(staged=ok, quality=why if ok else None, stage_why=None if ok else why)
        if not ok and res.get("no_trades"):
            p.update(quality="FLAT_NO_TRADES_7D", stage_why=None)
        elif not ok and p.get("attempt", 1) < 2:
            # neither a result, a REDO nor a 0-trade verdict: the board was killed (OOM) mid-sweep -> one relaunch
            base = f"~/binance-sandbox/data/preopen_7d/trc_run/{day}/results"
            sh(ip, f"rm -rf {base}/{ss}.attempt1 && mv {base}/{ss} {base}/{ss}.attempt1", 30)
            state["placements"][ss] = {"rank": p["rank"], "status": "pending", "attempt": 2, "a1_tried": True, **({"no_overlay": True} if p.get("no_overlay") else {}), "attempt1": {
                "host": p["host"], "wall_min": round((now - p["started"]) / 60, 1), "why": why}}
            print(f"[{now_iso()}] {ss} killed mid-sweep -> relaunch (attempt 2)", flush=True)
        if ok and not state.get("first_staged_utc"):
            state["first_staged_utc"] = now_iso()
        print(f"[{now_iso()}] {ss} on {p['host']} {round((now - p['started']) / 60, 1)} min staged={ok} {why}", flush=True)
    for ss, p in state["placements"].items():
        retry_a1 = p["status"] == "done" and not p.get("staged") and not p.get("a1_tried")
        if p.get("attempt") == 2 and p.get("attempt1") and (p["status"] == "pending" or retry_a1):
            p["a1_tried"] = True
            ip = HOSTS[p["attempt1"]["host"]][0]
            res = result_of(ip, ss, day, folder=f"{ss}.attempt1")
            if res is None or (retry_a1 and not res.get("redo")):
                continue
            ok, why = stage(ss, res, day)
            if not ok and res.get("no_trades"):
                p.update(status="done", host=p["attempt1"]["host"], quality="FLAT_NO_TRADES_7D", started=now, finished=now)
                continue
            if not ok and not retry_a1:
                continue  # attempt 1 left nothing usable (killed): attempt 2 runs
            p.update(status="done", host=p["attempt1"]["host"], staged=ok, quality=why if ok else None, stage_why=None if ok else why,
                     started=now - 60 * float(p["attempt1"].get("wall_min") or 0), finished=now, from_attempt1=True)
            print(f"[{now_iso()}] {ss} staged from attempt 1 (REDO pre-repair) staged={ok} {why}", flush=True)
    order = sorted((ss for ss, p in state["placements"].items() if p["status"] == "pending"), key=lambda s: state["placements"][s]["rank"])
    for name, (ip, slots) in HOSTS.items():
        pr = probes.get(name)
        if state["mode"] == "overnight":
            slots = OVERNIGHT_SLOTS.get(name, 0)
        if not pr or not slots:
            continue
        busy = set(pr["running7d"]) | {ss for ss, p in state["placements"].items() if p.get("host") == name and p["status"] == "running"}
        if len(busy) >= slots or pr["mem"] < MEM_FLOOR_MB or now - state["last_launch"].get(name, 0) < LAUNCH_GAP_S:
            continue
        pick = next((ss for ss in order if wave_open(state, ss, now)), None)
        if not pick:
            break
        order.remove(pick)
        npz = ship_npz(ip, pick.rsplit("_", 1)[0])
        out = launch(name, ip, pick, day, state["mode"], bool(state["placements"][pick].get("no_overlay")))
        p = state["placements"][pick]
        p.update(host=name, npz=npz, launch=out[-160:])
        if out.endswith("LAUNCHED"):
            p.update(status="running", started=time.time())
            state["last_launch"][name] = time.time()
            print(f"[{now_iso()}] launch {pick} rank {p['rank']} -> {name} npz={npz}", flush=True)
        else:
            p["launch_errors"] = p.get("launch_errors", 0) + 1
            if p["launch_errors"] >= 3:
                p.update(status="failed", why=f"launch failed: {out[-120:]}", finished=time.time())
    deadline_s = 12 * 3600 if state["mode"] == "overnight" else DEADLINE_S
    if now - state["started_epoch"] > deadline_s:
        # the paused 30D fleet is never stranded: past the deadline nothing new launches and the fleet resumes
        for p in state["placements"].values():
            if p["status"] == "pending":
                p.update(status="failed", why=f"pass deadline {deadline_s // 60} min", finished=now)
            elif p["status"] == "running" and now - p["started"] > BOARD_TIMEOUT_S + 600:
                p.update(status="failed", why="board exceeded launcher timeout", finished=now)
    terminal = all(p["status"] in ("done", "failed") for p in state["placements"].values())
    if terminal and not state.get("complete_utc"):
        state["complete_utc"] = now_iso()
        if state.get("paused"):
            state["resumed"] = {n: pyrun(ip, RESUME_SCRIPT, timeout=60)[1].strip() for n, (ip, _) in HOSTS.items()}
            preempted = sorted({x for v in state["resumed"].values() for x in v.split()})
            state["priority"] = prioritize(preempted)
            state["unhold"] = hold(False)
    atomic(day_dir / "timing.json", timing(state))
    atomic(day_dir / "state.json", state)
    return terminal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("intraday", "premarket", "overnight"), default="intraday")
    ap.add_argument("--half", choices=("all", "top", "bottom"), default="all", help="top = best half of the ranking, bottom = the rest")
    ap.add_argument("--day", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--limit", type=int, default=0, help="first N keys of the ranking only (smoke test)")
    a = ap.parse_args()
    day_dir = ROOT / "data/preopen_7d/trc_run" / (a.day + {"overnight": "_overnight", "premarket": "_premarket"}.get(a.mode, ""))
    state_path = day_dir / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        queue = build_queue(day_dir)
        full = queue["ranking"]
        cut = (len(full) + 1) // 2
        chosen = {"all": list(enumerate(full)), "top": list(enumerate(full))[:cut], "bottom": list(enumerate(full))[cut:]}[a.half]
        # USER 2026-10-30: "12 of 121 symbols had no premarket trade at all <- add them to the sym_sides that get calculated after
        # 20:00utc they have all night to complete we ONLY need the best_first to have pre market numbers and 7D calculations RIGHT
        # AT market open". The thin top-half sides move to the overnight pass (best-first there too); the morning pass keeps only
        # sides with premarket bars. A thin side the overnight pass of this trade day did not hold runs last in the morning pass,
        # without the overlay, after every best-first side has launched.
        thin, late_thin = {}, set()
        if a.half in ("top", "bottom") and a.mode in ("premarket", "overnight"):
            thin = premarket_thin({ss.rsplit("_", 1)[0] for ss in full})
            atomic(day_dir / "premarket_thin.json", thin)
            top = list(enumerate(full))[:cut]
            thin_top = [(i, ss) for i, ss in top if ss.rsplit("_", 1)[0] in set(thin["thin"])]
            if a.half == "bottom":
                chosen = sorted(chosen + thin_top)
            else:
                night = ROOT / "data/preopen_7d/trc_run" / f"{a.day}_overnight" / "state.json"
                done = set((json.loads(night.read_text()).get("placements") or {}) if night.exists() else ())
                chosen = [(i, ss) for i, ss in chosen if (i, ss) not in thin_top]
                late_thin = {ss for _, ss in thin_top if ss not in done}
                chosen += [(i, ss) for i, ss in thin_top if ss in late_thin]
        chosen = chosen[: a.limit] if a.limit else chosen
        state = {"day": day_dir.name, "trade_day": a.day, "mode": a.mode, "half": a.half, "started_utc": now_iso(), "started_epoch": time.time(), "last_launch": {},
                 "queue_meta": queue["_meta"], "premarket_thin": thin.get("thin", []), "premarket_thin_sessions": thin.get("sessions", []),
                 "placements": {ss: dict({"rank": i + 1, "pos": j + 1, "status": "pending"}, **({"no_overlay": True} if ss in late_thin else {}))
                                for j, (i, ss) in enumerate(chosen)}}
        ranking = [ss for _, ss in chosen]
        atomic(state_path, state)
        print(f"[{now_iso()}] trc 7D {a.mode}/{a.half} pass: {len(ranking)} keys (excluded {queue['_meta']['excluded_counts']})", flush=True)
    while True:
        try:
            if tick(state, day_dir):
                print(f"[{now_iso()}] COMPLETE {json.dumps(timing(state))}", flush=True)
                return 0
        except Exception as exc:  # one bad tick must not strand the paused fleet; the next tick retries
            print(f"[{now_iso()}] TICK_ERROR {type(exc).__name__}: {exc}", flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
