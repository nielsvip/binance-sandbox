#!/usr/bin/env python3
"""fleet_dispatch — the ONE launcher of 30D boards across the fleet (USER 2026-10-27 rewrite of the launch path
of tools/v15_fleet_scheduler.py: "it can not dispatch 4 symbols to 4 servers reliably").

Design (every rule below is the user's 2026-10-27 order):
  * Runs on BOTH s1 and s2 (cron every minute, flock). Sticky failover by heartbeat: s1 acts unless s2 is
    acting with a fresh heartbeat; s2 acts only when s1's heartbeat is stale/unreachable or s1 stands by.
    Correctness never depends on the election: the worker-side launch (tools/fleet_launch.sh) recounts
    slots and refuses duplicates inside /tmp/v15_fleet_launch_v2.lock.
  * Servers are DISCOVERED every tick by probing the private net (FLEET_SCAN, default 10.0.0.3-10.0.0.20).
    A deleted server simply stops answering (its sym_sides fall back to pending); a new one that answers
    with ~/binance-sandbox/v15_pilot.py starts receiving work on the same tick. Nothing to register.
  * Home cat_sides: s1 CRYPTO_SHORT, s2 CRYPTO_LONG, s5 STOCKS_LONG, s6 STOCKS_SHORT. Priority when a server
    must take foreign work: CRYPTO_SHORT, CRYPTO_LONG, STOCKS_SHORT, STOCKS_LONG. A server whose home cat_side
    has no pending sym_side takes the highest-priority cat_side that still has work. A cat_side whose home
    server is gone or cannot admit is ORPHANED and gets FLEET_ORPHAN_SLOTS slot(s) on every other server.
  * NPZ goes WITH the launch: right before each start the NPZ owner (s1 crypto, s5 stocks; the target itself
    when the owner is unreachable) appends the latest bar, and the file is relayed md5-verified to the target
    (never regressing its tail). A failed refresh launches with what the target has (USER 2026-10-13).
  * Done = final_gain (FLEET_DONE_RULE=complete also requires v15_progress_board.is_complete(), the harvester's
    rule) AND the board's NPZ is within 48 bars of the current tail. Everything else tradeable is pending.
Outputs: data/fleet_dispatch/status.json (last tick) + one JSON line per tick in logs/fleet_dispatch.jsonl.
"""
import argparse
import concurrent.futures as cf
import fcntl
import hashlib
import ipaddress
import json
import os
import pathlib
import shlex
import socket
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
STATE_DIR = ROOT / "data" / "fleet_dispatch"
LOG = ROOT / "logs" / "fleet_dispatch.jsonl"
HB = pathlib.Path.home() / "fleet_dispatch" / "heartbeat.json"
# Operator hold with a mandatory expiry: data/fleet_hold_hosts.json {"s2": {"until": <epoch>, "why": "..."}}. An expired
# entry is ignored, so a forgotten hold can never idle a host for 19 h again (2026-10-27 drain incident). Running boards
# are untouched; only NEW launches on that host are withheld.
HOLD_FILE = ROOT / "data" / "fleet_hold_hosts.json"


def manual_hold(name, now):
    try:
        entry = (json.loads(HOLD_FILE.read_text()) or {}).get(name) or {}
        until = float(entry.get("until") or 0)
        if until > now:
            return f"HOLD until {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(until))}: {entry.get('why', '')}"[:200]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""
PY = "/home/niels/.conda/envs/binance_env_mac/bin/python"
CAT_PRIORITY = ("CRYPTO_SHORT", "CRYPTO_LONG", "STOCKS_SHORT", "STOCKS_LONG")
HOMES = {"s1": "CRYPTO_SHORT", "s2": "CRYPTO_LONG", "s5": "STOCKS_LONG", "s6": "STOCKS_SHORT"}
NPZ_OWNER = {"crypto": "s1", "stocks": "s5"}
LEADERS = {"s1": "10.0.0.3", "s2": "10.0.0.4"}
# Fleet names come from the private IP, not hostname(): S1's hostname is "niels". New servers keep their hostname.
IP_NAMES = {"10.0.0.3": "s1", "10.0.0.4": "s2", "10.0.0.5": "s5", "10.0.0.6": "s6"}
BAR_S = 900
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
       "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


SLOTS = env_int("FLEET_SLOTS", 4)
# Per-host board cap (operator 2026-10-28 NO-OOM order): S1 also runs the crypto NPZ refresh pool, the bar trigger and the
# forward-state writers (25-28 GB peak, 3 reboots on 10-27), so it holds ONE board; others keep FLEET_SLOTS. Override with
# FLEET_HOST_SLOTS='{"s1": 1, "s5": 3}'.
try:
    HOST_SLOTS = {str(k): int(v) for k, v in json.loads(os.environ.get("FLEET_HOST_SLOTS", '{"s1": 3}')).items()}
except (ValueError, TypeError, AttributeError):
    HOST_SLOTS = {"s1": 3}
WORKERS = env_int("FLEET_WORKERS", 7)
ORPHAN_SLOTS = env_int("FLEET_ORPHAN_SLOTS", 1)
MEM_MIN_MB = env_int("FLEET_MEM_MIN_MB", 11000)  # 2026-10-28 operator: NO OOM — a 7-worker board peaks 8-9 GB, so a launch needs that much headroom beyond the 2-3 GB reserve
DISK_MIN_MB = env_int("FLEET_DISK_MIN_MB", 3000)
RAMP_S = env_int("FLEET_RAMP_S", 600)
RAMP_MB = env_int("FLEET_RAMP_MB", 4000)
LAUNCH_GAP_S = env_int("FLEET_LAUNCH_GAP_S", 120)
FRESH_BARS = env_int("FLEET_FRESH_BARS", 48)
MAX_ATTEMPTS = env_int("FLEET_MAX_ATTEMPTS", 2)
RETRY_S = env_int("FLEET_RETRY_S", 1800)
HB_STALE_S = env_int("FLEET_HB_STALE_S", 180)
ORPHAN_AFTER_S = env_int("FLEET_ORPHAN_AFTER_S", 600)
NPZ_TIMEOUT_S = env_int("FLEET_NPZ_TIMEOUT_S", 240)
QUARANTINE_DEATHS = env_int("FLEET_QUARANTINE_DEATHS", 2)
QUARANTINE_S = env_int("FLEET_QUARANTINE_S", 900)  # 2026-10-28 operator throughput order: with the 11 GB launch floor an OOM cascade cannot repeat, so a death costs 15 min of launches, not an hour
# "final" (default): a board with final_gain is done. "complete": also require v15_progress_board.is_complete().
# 2026-10-27 measured: finished+published boards read 2775/3965 under is_complete() because V15_FILL_SLICE_S
# truncated every board (now 0 in fleet_launch.sh). Until boards launched without the slice dominate, "complete"
# would relaunch every truncated board; flip to "complete" (the harvester's rule) once they do.
DONE_RULE = os.environ.get("FLEET_DONE_RULE", "final")
TOMBSTONE_SKIP = os.environ.get("FLEET_TOMBSTONE_SKIP", "1") == "1"


def cat_of(symside):
    sym, _, side = symside.rpartition("_")
    venue = "CRYPTO" if sym.endswith(("USDT", "USDC")) else "STOCKS"
    return f"{venue}_{side}"


def log_line(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def run(cmd, timeout, stdin=None, inp=None):
    try:
        r = subprocess.run(cmd, stdin=stdin, input=inp, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout.decode("utf8", "replace"), r.stderr.decode("utf8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as error:
        return 127, "", str(error)


def self_ips():
    rc, out, _ = run(["hostname", "-I"], 5)
    return set(out.split()) if rc == 0 else set()


class Host:
    def __init__(self, ip, local):
        self.ip, self.local = ip, local

    def sh(self, script, timeout, inp=None):
        """Run a bash script on the host (locally when it is this machine)."""
        if self.local:
            return run(["bash", "-c", script], timeout, inp=inp)
        return run(SSH + [f"niels@{self.ip}", script], timeout, inp=inp)

    def feed(self, interpreter_cmd, path, timeout):
        with open(path, "rb") as fh:
            if self.local:
                return run(["bash", "-c", interpreter_cmd], timeout, stdin=fh)
            return run(SSH + [f"niels@{self.ip}", interpreter_cmd], timeout, stdin=fh)


def probe(host, syms):
    arg = shlex.quote(json.dumps({"syms": sorted(syms)}))
    rc, out, err = host.feed(f"{PY} - {arg} 2>/dev/null || python3 - {arg}", TOOLS / "fleet_probe.py", 120)
    if rc != 0 or not out.strip():
        return None
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except ValueError:
        return None
    if data.get("error") or not data.get("repo"):
        return None
    data["ip"] = host.ip
    return data


def discover(scan, syms):
    lo, _, hi = scan.partition("-")
    lo_ip, hi_ip = ipaddress.ip_address(lo), ipaddress.ip_address(hi or lo)
    mine = self_ips()
    ips = [str(ipaddress.ip_address(i)) for i in range(int(lo_ip), int(hi_ip) + 1)]
    hosts = {}
    with cf.ThreadPoolExecutor(max_workers=len(ips)) as pool:
        futs = {pool.submit(probe, Host(ip, ip in mine), syms): ip for ip in ips}
        for fut in cf.as_completed(futs):
            data = fut.result()
            if data:
                name = IP_NAMES.get(futs[fut], data["host"])
                if name in hosts:
                    continue
                hosts[name] = (Host(futs[fut], futs[fut] in mine), data)
    return hosts


# ------------------------------------------------------------------ leader election (sticky failover)
def read_peer_hb(peer_ip):
    rc, out, _ = Host(peer_ip, False).sh(f"cat {shlex.quote(str(HB))} 2>/dev/null", 15)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def write_hb(me, acting, note=""):
    HB.parent.mkdir(parents=True, exist_ok=True)
    tmp = HB.with_suffix(".tmp")
    tmp.write_text(json.dumps({"host": me, "ts": time.time(), "acting": acting, "note": note}))
    os.replace(tmp, HB)


def decide(me, peer_hb, now, load1, ncpu):
    """Pure election. s1 acts unless overloaded; s2 acts iff s1's heartbeat is stale/missing or s1 says it is not
    acting. s1's choice never depends on s2, so the pair cannot deadlock or oscillate; a one-tick overlap during a
    handover is harmless because fleet_launch.sh recounts slots under the worker's launch lock."""
    if me not in LEADERS:
        return True, "not-a-leader-host-acting-alone"
    if me == "s1":
        if load1 > 3 * ncpu:
            return False, f"s1 overloaded (load1 {load1:.0f} > 3x{ncpu}) -> standby, s2 acts"
        return True, "s1 primary"
    if not peer_hb or now - float(peer_hb.get("ts") or 0) >= HB_STALE_S:
        return True, "s1 hb stale/unreachable -> s2 acts"
    if not peer_hb.get("acting"):
        return True, f"s1 standing by ({str(peer_hb.get('note'))[:60]}) -> s2 acts"
    return False, f"s1 acting (hb {now - float(peer_hb['ts']):.0f}s) -> s2 standby"


def should_act(me, now):
    peer_hb = read_peer_hb(LEADERS["s1"]) if me == "s2" else None
    return decide(me, peer_hb, now, os.getloadavg()[0], os.cpu_count() or 1)


class Beat(threading.Thread):
    def __init__(self, me):
        super().__init__(daemon=True)
        self.me, self.stop = me, threading.Event()

    def run(self):
        while not self.stop.wait(30):
            write_hb(self.me, True, "mid-tick")


# ------------------------------------------------------------------ universe + order
def load_snapshot(fleet, me):
    """data/v15_best_first.json from S1 (publisher), else the local copy while < 6 h old."""
    local = ROOT / "data" / "v15_best_first.json"
    cache = STATE_DIR / "best_first_cache.json"
    candidates = []
    if me != "s1":
        s1 = fleet["s1"][0] if "s1" in fleet else Host(LEADERS["s1"], False)
        rc, out, _ = s1.sh("cat ~/binance-sandbox/data/v15_best_first.json", 25)
        if rc == 0:
            candidates.append(out)
    for path in (local, pathlib.Path("/tmp/v15_best_first.json")):
        try:
            candidates.append(path.read_text())
        except OSError:
            pass
    src = None
    for text in candidates:
        try:
            cand = json.loads(text)
        except ValueError:
            continue
        if isinstance(cand, dict) and (src is None or float(cand.get("generated_at") or 0) > float(src.get("generated_at") or 0)):
            src = cand
    if isinstance(src, dict) and time.time() - float(src.get("generated_at") or 0) < 21600:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(src))
        return src
    try:
        src = json.loads(cache.read_text())
        if time.time() - float(src.get("generated_at") or 0) < 21600:
            return src
    except (OSError, ValueError):
        pass
    return None


def ordered_universe(snap):
    gains = snap.get("gains") or {}

    def key(ss):
        g = gains.get(ss)
        return (0, -float(g), ss) if g is not None else (1, 0.0, ss)
    out = {c: [] for c in CAT_PRIORITY}
    for ss in sorted({str(s).upper() for s in snap.get("allowed") or []}, key=key):
        if ss.endswith(("_LONG", "_SHORT")):
            out[cat_of(ss)].append(ss)
    return out


# ------------------------------------------------------------------ pure planning (unit-tested)
def pending_by_cat(universe, fleet_data, now):
    """-> ({cat: [ss...]}, {ss: host_with_partial}, {ss: reason_skipped}) — pure function of the probes."""
    running = {ss for d in fleet_data.values() for ss in d.get("running", {})}
    tails = {}
    for d in fleet_data.values():
        for sym, t in (d.get("tails") or {}).items():
            tails[sym] = max(tails.get(sym, 0), int(t or 0))
    pend, partial, skipped = {}, {}, {}
    for cat, sides in universe.items():
        pend[cat] = []
        for ss in sides:
            if ss in running:
                continue
            sym = ss.rsplit("_", 1)[0]
            tail = tails.get(sym, 0)
            done, part_host, part_rows = False, None, 0
            for name, d in fleet_data.items():
                p = (d.get("progress") or {}).get(ss)
                if not p:
                    continue
                finished = (p.get("fg") or p.get("zero")) and (DONE_RULE != "complete" or p.get("complete") is not False)
                fresh = tail == 0 or p.get("ts", 0) == 0 or (tail - int(p.get("ts") or 0)) / BAR_S < FRESH_BARS
                if finished and fresh:
                    done = True
                    break
                if not finished and int(p.get("rows") or 0) > part_rows:
                    part_host, part_rows = name, int(p.get("rows") or 0)
            if done:
                continue
            last = None
            for d in fleet_data.values():
                rec = (d.get("launches") or {}).get(ss)
                if rec and (last is None or rec.get("ts", 0) > last.get("ts", 0)):
                    last = rec
            if last:
                age = now - float(last.get("ts") or 0)
                rtail = int(last.get("tail") or 0)
                advanced = tail and rtail and (tail - rtail) / BAR_S >= FRESH_BARS
                if age < RETRY_S and not advanced:
                    skipped[ss] = f"cooldown {int(age)}s after launch"
                    continue
                if int(last.get("n") or 0) >= MAX_ATTEMPTS and not advanced:
                    skipped[ss] = f"{last.get('n')} attempts on tail {rtail}; waits {FRESH_BARS} new bars"
                    continue
            # 2026-10-28: v15_pilot refuses a progress verdict IMPOSSIBLE forever unless FORCE_DC_RERUN=1 (stocks only, fleet_launch.sh):
            # 69 of 149 launches in 5 h were crypto tombstones exiting at start, re-picked every RETRY_S because the tail check below
            # never holds for crypto (a new bar lands every 15 min)
            if TOMBSTONE_SKIP and cat.startswith("CRYPTO") and any(((d.get("progress") or {}).get(ss) or {}).get("verdict") == "IMPOSSIBLE" for d in fleet_data.values()):
                skipped[ss] = "IMPOSSIBLE tombstone (pilot refuses; manual revision)"
                continue
            imp = max((int((d.get("impossible") or {}).get(ss) or 0) for d in fleet_data.values()), default=0)
            if imp and now - imp < 86400 and (tail == 0 or tail <= imp):
                skipped[ss] = "IMPOSSIBLE <24h, no new bar"
                continue
            pend[cat].append(ss)
            if part_host:
                partial[ss] = part_host
    return pend, partial, skipped


def unfinished_deaths(d, now):
    """-> [(launch_ts, ss)] of boards that RAN and vanished unfinished. A start refusal (the pilot exited at start on a
    guard such as BASELINE-PERSYM-SNAPSHOT-INVALID, reported by the probe as start_refused >= the launch ts) is not a death."""
    died = []
    refused = d.get("start_refused") or {}
    for ss, r in (d.get("launches") or {}).items():
        age = now - float(r.get("ts") or 0)
        p = (d.get("progress") or {}).get(ss) or {}
        if 180 < age < 7200 and ss not in (d.get("running") or {}) and not (p.get("fg") or p.get("zero")):
            if float(refused.get(ss) or 0) >= float(r.get("ts") or 0):
                continue
            # 2026-10-28: a board that ended with a verdict (IMPOSSIBLE quarantine, NO_TRADES, BEST_EFFORT) finished; it is not a
            # death (S1 sat in quarantine 10:15-15:2xZ on such boards with 16 GB free)
            if p.get("verdict"):
                continue
            # A deliberate operator restart (SIGTERM for a code deploy) is booked in the launch record as
            # "restarted": <epoch> by whoever restarts it; it is not a death and must not quarantine the host.
            if float(r.get("restarted") or 0) >= float(r.get("ts") or 0):
                continue
            died.append((float(r["ts"]), ss))
    return died


def admission(d, now, name=""):
    """-> (free_slots, reason). One launch per host per tick; memory/disk/spacing gates."""
    nrun = len(d.get("running") or {})
    if d.get("_quarantine"):
        return 0, d["_quarantine"]
    hold = manual_hold(name, now) if name else ""
    if hold:
        return 0, hold
    cap = HOST_SLOTS.get(name, SLOTS) if name else SLOTS
    if nrun >= cap:
        return 0, f"full {nrun}/{cap}"
    if int(d.get("disk_free_mb") or 0) < DISK_MIN_MB:
        return 0, f"disk {d.get('disk_free_mb')}MB < {DISK_MIN_MB}"
    if int(d.get("mem_avail_mb") or 0) < MEM_MIN_MB:
        return 0, f"mem {d.get('mem_avail_mb')}MB < {MEM_MIN_MB}"
    # 2026-10-28 fleet throughput order: a board reaches its peak minutes after launch, so MemAvailable still looks free
    # when the next launch is admitted; 73 of 125 launches (19:30-04:40Z) died silently, mostly global OOM kills.
    # Every board launched here < RAMP_S ago and still running reserves RAMP_MB on top of MEM_MIN_MB.
    ramping = sum(1 for ss, r in (d.get("launches") or {}).items() if now - float(r.get("ts") or 0) < RAMP_S and ss in (d.get("running") or {}))
    if ramping and int(d.get("mem_avail_mb") or 0) < MEM_MIN_MB + ramping * RAMP_MB:
        return 0, f"mem {d.get('mem_avail_mb')}MB < {MEM_MIN_MB}+{ramping}x{RAMP_MB} ramping"
    last = max((float(r.get("ts") or 0) for r in (d.get("launches") or {}).values()), default=0)
    if now - last < LAUNCH_GAP_S:
        return 0, f"spacing {int(now - last)}s < {LAUNCH_GAP_S}"
    if not str(d.get("pdir") or "").startswith("/home/niels/v15_run"):
        return 0, "no round pointer"
    return 1, "ok"


def plan(pend, partial, fleet_data, now, orphan_since):
    """-> ([(host, ss, why)], {host: reason}, orphans). Pure."""
    homes_present = {HOMES[n]: n for n in fleet_data if n in HOMES}
    adm = {n: admission(d, now, n) for n, d in fleet_data.items()}
    orphans = set()
    for cat in CAT_PRIORITY:
        home = homes_present.get(cat)
        blocked = home is None or adm[home][0] == 0 and not adm[home][1].startswith(("full", "spacing"))
        if blocked and now - orphan_since.get(cat, now) >= ORPHAN_AFTER_S:
            orphans.add(cat)
    claimed, out, why_not = set(), [], {}

    def take(host, cat):
        sides = [s for s in pend.get(cat, []) if s not in claimed]
        # resume-first: finish boards this host already started, then untouched ones; a board started elsewhere
        # comes last (its progress lives on that host, so here it would be a fresh re-run)
        mine = [s for s in sides if partial.get(s) == host]
        fresh = [s for s in sides if s not in partial]
        pick = (mine or fresh or sides or [None])[0]
        if pick:
            claimed.add(pick)
        return pick

    order = sorted(fleet_data, key=lambda n: (CAT_PRIORITY.index(HOMES[n]) if n in HOMES else 9, n))
    for name in order:
        free, reason = adm[name]
        if not free:
            why_not[name] = reason
            continue
        home = HOMES.get(name)
        running = list((fleet_data[name].get("running") or {}).keys())
        foreign = sum(1 for s in running if cat_of(s) != home)
        pick, why = None, ""
        live_orphans = [c for c in CAT_PRIORITY if c in orphans and c != home and any(s not in claimed for s in pend.get(c, []))]
        if live_orphans and foreign < ORPHAN_SLOTS:
            pick, why = take(name, live_orphans[0]), f"orphan {live_orphans[0]}"
        if not pick and home and any(s not in claimed for s in pend.get(home, [])):
            pick, why = take(name, home), f"home {home}"
        if not pick:
            for cat in CAT_PRIORITY:
                if cat != home and any(s not in claimed for s in pend.get(cat, [])):
                    pick, why = take(name, cat), f"overflow {cat} (home {home or 'none'} exhausted)"
                    break
        if pick:
            out.append((name, pick, why))
        else:
            why_not[name] = "no pending work anywhere"
    return out, why_not, orphans


# ------------------------------------------------------------------ execution
def crypto_fresh_floor(now):
    """Oldest acceptable crypto NPZ tail (bar-open stamp): one bar behind the latest closed 15m bar."""
    return int(now // BAR_S) * BAR_S - 2 * BAR_S


def npz_refresh_on(host, sym, venue):
    """-> (md5, tail) after appending the latest bar on `host`; ('', 0) on failure."""
    rel = f"backtest_v8/indicators/{sym}.npz"
    script = (f"cd ~/binance-sandbox && timeout {NPZ_TIMEOUT_S - 10} {PY} -u tools/npz_bar_refresh.py "
              f"--symbols {sym} --mode {venue} --workers 1 >> /tmp/fleet_npz_refresh.log 2>&1; "
              f"{PY} -c \"import numpy as np,sys,hashlib;t=np.load(sys.argv[1],mmap_mode='r')['timestamps'];"
              f"v=float(t[-1]);print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest(),int(v/1000 if v>1e11 else v))\" {rel}")
    rc, out, _ = host.sh(script, NPZ_TIMEOUT_S)
    parts = out.strip().splitlines()[-1].split() if out.strip() else []
    if len(parts) == 2 and len(parts[0]) == 32:
        return parts[0], int(parts[1])
    return "", 0


def npz_ship(fleet, target, ss):
    """Refresh the symbol's NPZ to the latest bar and relay it md5-verified to the target. Sources in order:
    the venue's owners (crypto s1,s2; stocks s5,s6), skipping overloaded ones, then the target itself; the first
    crypto source whose tail is fresh wins, otherwise the freshest result is used."""
    sym = ss.rsplit("_", 1)[0]
    venue = "crypto" if sym.endswith(("USDT", "USDC")) else "stocks"
    prefs = ("s1", "s2") if venue == "crypto" else ("s5", "s6")
    sources = [n for n in prefs if n in fleet and float(fleet[n][1].get("load1") or 0) <= 3 * max(1, int(fleet[n][1].get("ncpu") or 1))]
    sources = list(dict.fromkeys(sources + [target]))
    floor = crypto_fresh_floor(time.time())
    tried, best = [], ("", "", 0)
    for name in sources:
        md5, tail = npz_refresh_on(fleet[name][0], sym, venue)
        tried.append(f"{name}:{tail}")
        if md5 and tail > best[2]:
            best = (name, md5, tail)
        if md5 and (venue == "stocks" or tail >= floor):
            break
    owner, md5, tail = best
    fresh = venue == "stocks" or tail >= floor
    if not md5:
        return {"owner": None, "ok": False, "fresh": False, "tried": tried, "why": "no source could refresh"}
    oh, th = fleet[owner][0], fleet[target][0]
    rel = f"backtest_v8/indicators/{sym}.npz"
    if owner == target:
        return {"owner": owner, "ok": True, "fresh": fresh, "tail": tail, "md5": md5, "pushed": False, "tried": tried}
    try:
        if oh.local:
            blob, rc = (pathlib.Path.home() / "binance-sandbox" / rel).read_bytes(), 0
        else:
            r = subprocess.run(SSH + [f"niels@{oh.ip}", f"cat ~/binance-sandbox/{rel}"], capture_output=True, timeout=120)
            blob, rc = r.stdout, r.returncode
    except (OSError, subprocess.TimeoutExpired):
        blob, rc = b"", 1
    if rc != 0 or hashlib.md5(blob).hexdigest() != md5:
        return {"owner": owner, "ok": False, "fresh": False, "tried": tried, "why": "relay read/md5 mismatch"}
    install = (f"cd ~/binance-sandbox && T={rel}.fleet_tmp && cat > $T && [ \"$(md5sum $T | cut -c1-32)\" = {md5} ] && "
               f"{PY} -c \"import numpy as np,sys,os;a=np.load(sys.argv[1],mmap_mode='r')['timestamps'];"
               f"b=np.load(sys.argv[2],mmap_mode='r')['timestamps'] if os.path.exists(sys.argv[2]) else [0];"
               f"sys.exit(0 if float(a[-1])>=float(b[-1]) else 3)\" $T {rel} && mv -f $T {rel} && echo INSTALLED "
               f"|| {{ rc=$?; rm -f $T; echo REFUSED-$rc; }}")
    rc, out, err = th.sh(install, 120, inp=blob)
    # REFUSED-3 = the target's own NPZ already has a newer tail than the owner's: keep it (never regress)
    ok = "INSTALLED" in out or "REFUSED-3" in out
    return {"owner": owner, "ok": ok, "fresh": fresh and ok, "tail": tail, "md5": md5, "pushed": True, "tried": tried,
            "install": "TARGET_NEWER_KEPT" if "REFUSED-3" in out else out.strip()[-60:]}


def launch(fleet, target, ss, tail, me):
    t0 = time.time()
    npz = npz_ship(fleet, target, ss)
    if not npz.get("fresh") and cat_of(ss).startswith("CRYPTO"):
        # the pilot refuses a crypto NPZ >20 min behind (STALE_NPZ_SKIPPED) and would burn an attempt; hold instead
        return {"host": target, "ss": ss, "npz": npz, "result": "NPZ_STALE_HOLD", "s": round(time.time() - t0, 1)}
    tail = max(int(tail or 0), int(npz.get("tail") or 0))
    args = " ".join(shlex.quote(str(a)) for a in (ss, WORKERS, SLOTS, tail, me))
    rc, out, err = fleet[target][0].feed(f"bash -s -- {args}", TOOLS / "fleet_launch.sh", 60)
    result = (out.strip().splitlines() or [f"ERROR-rc{rc}-{err[-120:]}"])[-1]
    return {"host": target, "ss": ss, "npz": npz, "result": result, "s": round(time.time() - t0, 1)}


def settings_history(fleet, state):
    """USER 2026-10-13: record every freshly published board's settings to per_sym_history (detached, flocked)."""
    seen = state.setdefault("hist_seen", {})
    fresh = []
    for name, (_, d) in fleet.items():
        for ss, p in (d.get("progress") or {}).items():
            if p.get("fg") and p.get("complete") and p.get("mtime", 0) > seen.get(ss, 0):
                seen[ss] = p["mtime"]
                fresh.append(ss)
    tool = TOOLS / "v15_settings_history.py"
    if fresh and tool.exists():
        hosts = ",".join("127.0.0.1" if h.local else h.ip for h, _ in fleet.values())
        subprocess.Popen(["setsid", "flock", "-n", "/tmp/v15_settings_history.lock", sys.executable, str(tool),
                          "--window-h", "6", "--hosts", hosts], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return fresh


def my_name():
    for ip in self_ips():
        if ip in IP_NAMES:
            return IP_NAMES[ip]
    return socket.gethostname().split(".")[0]


def tick(args):
    now = time.time()
    me = my_name()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "me": me, "dry_run": args.dry_run}
    act, why = (True, "forced") if args.force_act else should_act(me, now)
    rec["leader"] = why
    if not act:
        write_hb(me, False, why)
        rec["acting"] = False
        log_line(rec)
        return rec
    write_hb(me, True, why)
    beat = Beat(me)
    beat.start()
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_path = STATE_DIR / f"state_{me}.json"
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
        prev_snap = state.get("universe_syms") or []
        fleet = discover(args.scan, set(prev_snap))
        snap = load_snapshot(fleet, me)
        if not snap:
            rec["error"] = "no tradeable snapshot (S1 publisher dead and local cache > 6 h)"
            log_line(rec)
            return rec
        universe = ordered_universe(snap)
        syms = sorted({ss.rsplit("_", 1)[0] for v in universe.values() for ss in v})
        if set(syms) - set(prev_snap):
            state["universe_syms"] = syms
            fleet = discover(args.scan, set(syms))
        state["universe_syms"] = syms
        data = {n: d for n, (_, d) in fleet.items()}
        rec["hosts"] = {n: {"ip": d["ip"], "home": HOMES.get(n), "running": sorted(d.get("running") or {}),
                            "mem_avail_mb": d.get("mem_avail_mb"), "disk_free_mb": d.get("disk_free_mb"),
                            "load1": round(float(d.get("load1") or 0), 1)} for n, d in sorted(data.items())}
        known = set(state.get("known_hosts") or [])
        rec["hosts_added"] = sorted(set(data) - known)
        rec["hosts_gone"] = sorted(known - set(data))
        state["known_hosts"] = sorted(data)
        # a host that misses one probe (S1 under load) must not make its finished boards look pending:
        # its last-seen finished boards + launch records stay in the done/retry view for 6 h (never in the slot plan)
        seen = state.setdefault("seen", {})
        for n, d in data.items():
            seen[n] = {"ts": now, "progress": {k: v for k, v in (d.get("progress") or {}).items() if v.get("fg") or v.get("zero")},
                       "launches": d.get("launches") or {}}
        view = dict(data)
        for n, c in list(seen.items()):
            if n not in data and now - float(c.get("ts") or 0) < 21600:
                view[n] = {"running": {}, "progress": c.get("progress") or {}, "launches": c.get("launches") or {}}
            elif n not in data:
                seen.pop(n)
        rec["ghost_hosts"] = sorted(set(view) - set(data))
        pend, partial, skipped = pending_by_cat(universe, view, now)
        # host quarantine: a board launched here that vanished unfinished (OOM-killed: S1 runs tests at
        # oom_score_adj +800 under its live stack) counts as a death; QUARANTINE_DEATHS within 2 h -> no launches
        # on that host for QUARANTINE_S (its home cat_side is then orphaned to the other servers)
        running_all = {ss for d in data.values() for ss in (d.get("running") or {})}
        for h, d in data.items():
            died = unfinished_deaths(d, now)
            if len(died) >= QUARANTINE_DEATHS:
                last = max(t for t, _ in died)
                if now - last < QUARANTINE_S:
                    d["_quarantine"] = f"quarantine: {len(died)} boards died unfinished <2h {sorted(s for _, s in died)[:4]}"
        holds = {k: v for k, v in (state.get("npz_hold") or {}).items() if now - v < 900}
        state["npz_hold"] = holds
        for cat in pend:
            for ss in [s for s in pend[cat] if s in holds]:
                pend[cat].remove(ss)
                skipped[ss] = f"NPZ stale hold {int(now - holds[ss])}s/900s"
        orphan_since = state.setdefault("orphan_since", {})
        homes_present = {HOMES[n]: n for n in data if n in HOMES}
        for cat in CAT_PRIORITY:
            home = homes_present.get(cat)
            ok = home is not None and (admission(data[home], now, home)[0] or admission(data[home], now, home)[1].startswith(("full", "spacing")))
            if ok:
                orphan_since.pop(cat, None)
            else:
                orphan_since.setdefault(cat, now)
        picks, why_not, orphans = plan(pend, partial, data, now, orphan_since)
        rec["pending"] = {c: len(v) for c, v in pend.items()}
        rec["universe"] = {c: len(v) for c, v in universe.items()}
        rec["running"] = sum(len(d.get("running") or {}) for d in data.values())
        rec["orphans"] = sorted(orphans)
        rec["idle_why"] = why_not
        rec["skipped_n"] = len(skipped)
        rec["plan"] = [{"host": h, "ss": s, "why": w} for h, s, w in picks]
        tails = {}
        for d in data.values():
            for sym, t in (d.get("tails") or {}).items():
                tails[sym] = max(tails.get(sym, 0), int(t or 0))
        if picks and not args.dry_run:
            with cf.ThreadPoolExecutor(max_workers=len(picks)) as pool:
                futs = [pool.submit(launch, fleet, h, s, tails.get(s.rsplit("_", 1)[0], 0), me) for h, s, _ in picks]
                rec["launches"] = [f.result() for f in futs]
            for x in rec["launches"]:
                if x["result"] == "NPZ_STALE_HOLD":
                    state.setdefault("npz_hold", {})[x["ss"]] = now
            if any(str(x["result"]).startswith("LAUNCHED") for x in rec["launches"]):
                state["last_launch"] = now
        if not args.dry_run:
            rec["settings_history"] = settings_history(fleet, state)[:12]
        free_cap = sum(1 for n in data if admission(data[n], now, n)[0])
        work = sum(rec["pending"].values())
        idle_s = now - float(state.get("last_launch") or now)
        if free_cap and work and idle_s > 900:
            rec["ALARM"] = f"{free_cap} host(s) admit and {work} sym_sides pending but no launch for {int(idle_s)}s"
        if not work and not rec["running"]:
            rec["round_state"] = "COMPLETE: no pending, nothing running"
        state.setdefault("last_launch", now)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, state_path)
        (STATE_DIR / "status.json").write_text(json.dumps(dict(rec, skipped=skipped, pending_list=pend), indent=1, default=str))
        log_line(rec)
        return rec
    finally:
        beat.stop.set()
        write_hb(me, True, "tick done")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="probe + plan, launch nothing")
    ap.add_argument("--force-act", action="store_true", help="skip the leader election (manual runs)")
    ap.add_argument("--scan", default=os.environ.get("FLEET_SCAN", "10.0.0.3-10.0.0.20"))
    args = ap.parse_args()
    lock = open("/tmp/fleet_dispatch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another tick running")
        return
    rec = tick(args)
    print(json.dumps({k: rec.get(k) for k in ("ts", "me", "leader", "pending", "running", "plan", "launches", "idle_why", "orphans", "hosts_added", "hosts_gone", "ALARM", "error")}, default=str))


if __name__ == "__main__":
    main()
