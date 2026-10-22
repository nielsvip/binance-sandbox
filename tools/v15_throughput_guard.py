#!/usr/bin/env python3
"""V15 THROUGHPUT GUARD (USER 2026-10-15): "make sure both s1 and s2 are producing at least 12 sym_side improved results
per hour each". Counts, per worker host, the sym_sides PUBLISHED in the window whose board actually improved
(cumulative/final gain > the baseline it started from — a republished board that found nothing is not a result), and
names the binding constraint when a host is under target, so the gap is never guessed:

  concurrency / board_latency = results per hour

Read-only: it ssh-es to each host, reads published sheet mtimes + the matching progress JSON, and the newest
/tmp/v15_fleet_sched.log tick for slots / mem / idle_reason. Nothing is launched, killed or written on the workers.
Report: data/reports/v15_throughput_guard.json (+ stdout one-liner per host). Exit 1 when a host is under target.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reports" / "v15_throughput_guard.json"
HOSTS = {"s1": "s1-int", "s2": "s2"}
SHEETS = "~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"

# One remote python per host: published finals in the window, each one's improvement from its progress JSON.
PROBE = r'''
import json, glob, os, time, re
WIN = float(%(win)s) * 3600.0
NOW = time.time()
sheets = glob.glob(os.path.expanduser("%(sheets)s/*_bh*gain*_30d_matrix.xlsx"))
rows = []
for s in sheets:
    try:
        if NOW - os.path.getmtime(s) > WIN:
            continue
    except Exception:
        continue
    m = re.match(r"([A-Z0-9.\-]+_(?:LONG|SHORT))_bh", os.path.basename(s))
    if m:
        rows.append((m.group(1), os.path.getmtime(s)))
prog = {}
for p in glob.glob(os.path.expanduser("~/v15_run*/progress/*_v14_progress.json")) + glob.glob(os.path.expanduser("~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json")):
    ss = os.path.basename(p)[: -len("_v14_progress.json")]
    if ss not in dict(rows):
        continue
    try:
        if prog.get(ss) and os.path.getmtime(p) <= prog[ss][0]:
            continue
        prog[ss] = (os.path.getmtime(p), p)
    except Exception:
        pass
out = {"host": os.uname().nodename, "published": [], "improved": 0, "flat_or_worse": 0, "unknown": 0}
for ss, mt in sorted(set(rows)):
    base = fin = None
    pp = (prog.get(ss) or (None, None))[1]
    if pp:
        try:
            d = json.load(open(pp))
            base = d.get("initial_baseline_gain")
            if base is None:
                base = d.get("baseline_gain")
            fin = d.get("final_gain")
            if fin is None:
                fin = d.get("cumulative_gain")
        except Exception:
            pass
    st = "unknown"
    try:
        st = "improved" if (fin is not None and base is not None and float(fin) > float(base) + 1e-9) else "flat_or_worse"
    except Exception:
        st = "unknown"
    out[st if st != "improved" else "improved"] = out.get(st, 0) + (1 if st != "improved" else 1)
    out["published"].append({"symside": ss, "at": time.strftime("%%Y-%%m-%%dT%%H:%%M:%%SZ", time.gmtime(mt)), "baseline": base, "final": fin, "state": st})
out["published_n"] = len(out["published"])
try:
    mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemTotal", "MemAvailable")}
    out["mem_avail_mb"] = mem["MemAvailable"] // 1024
    out["mem_total_mb"] = mem["MemTotal"] // 1024
except Exception:
    pass
out["load1"] = float(open("/proc/loadavg").read().split()[0])
out["nproc"] = os.cpu_count()
live = set()
for p in glob.glob("/proc/[0-9]*"):
    try:
        cl = open(p + "/cmdline", "rb").read().decode(errors="ignore").split("\x00")
    except Exception:
        continue
    if any("v15_pilot.py" in c for c in cl) and "--sym-side" in cl:
        live.add(cl[cl.index("--sym-side") + 1])
out["boards_running"] = sorted(live)

# USER 2026-10-15 "one improved sym_side every 5 minutes per server": the hourly count hides a host that
# produced 12 in one burst and nothing for 40 min. Rolling cadence = minutes since the newest IMPROVED
# finish, read from the pilots' own result_done_utc (never a file mtime: a re-stamped sheet is not a result).
import datetime
# A progress JSON is 5-12 MB and there are 100+ of them per host: json.load on all of them costs minutes of
# worker CPU and GBs of RAM, which is absurd for a THROUGHPUT guard. Only 6 top-level scalars are needed, so
# they are pulled out with a regex over the raw text — no object graph is built.
_SCAL = re.compile(r'"(initial_baseline_gain|baseline_gain|final_gain|cumulative_gain|result_cpu_s)":\s*(-?[0-9.eE+]+)')
_STAMP = re.compile(r'"(result_done_utc|result_start_utc)":\s*"([^"]+)"')
fins = []
for p in glob.glob(os.path.expanduser("~/v15_run*/progress/*_v14_progress.json")):
    try:
        txt = open(p, errors="ignore").read()
    except Exception:
        continue
    sc, stamps = {}, {}
    for k, v in _SCAL.findall(txt):
        if k not in sc:
            try:
                sc[k] = float(v)
            except Exception:
                pass
    for k, v in _STAMP.findall(txt):
        stamps.setdefault(k, v)
    b = sc.get("initial_baseline_gain", sc.get("baseline_gain"))
    f = sc.get("final_gain", sc.get("cumulative_gain"))
    dn = stamps.get("result_done_utc")
    if not dn or b is None or f is None:
        continue
    try:
        ts = datetime.datetime.fromisoformat(dn).timestamp()
    except Exception:
        continue
    if f > b + 1e-9:
        wall = None
        if stamps.get("result_start_utc"):
            try:
                wall = ts - datetime.datetime.fromisoformat(stamps["result_start_utc"]).timestamp()
            except Exception:
                wall = None
        fins.append((ts, os.path.basename(p)[: -len("_v14_progress.json")], sc.get("result_cpu_s") or 0.0, wall))
fins.sort()
out["improved_finishes_24h"] = sum(1 for t, _, _, _ in fins if NOW - t < 86400)
out["minutes_since_last_improved"] = round((NOW - fins[-1][0]) / 60.0, 1) if fins else None
out["last_improved_symside"] = fins[-1][1] if fins else None
gaps = [round((fins[i][0] - fins[i - 1][0]) / 60.0, 1) for i in range(1, len(fins)) if NOW - fins[i][0] < 86400]
out["improved_gap_min_median_24h"] = sorted(gaps)[len(gaps) // 2] if gaps else None
out["improved_gap_min_max_24h"] = max(gaps) if gaps else None
rec = [(c, w) for t, _, c, w in fins if NOW - t < 86400 and c]
out["board_cpu_s_median_24h"] = sorted(c for c, _ in rec)[len(rec) // 2] if rec else None
out["board_wall_s_median_24h"] = sorted(w for _, w in rec if w)[len([w for _, w in rec if w]) // 2] if any(w for _, w in rec) else None

# CPU budget: boards/hour <= board_cores * 3600 / cpu_s_per_board. Cores NOT spent on boards are the
# measurable theft (npz precompute, klines, live helpers) — named, never guessed.
def _cpu_snap():
    s = {}
    for p in glob.glob("/proc/[0-9]*"):
        try:
            st = open(p + "/stat").read()
            tail = st[st.rfind(")") + 2:].split()
            cl = open(p + "/cmdline", "rb").read().decode(errors="ignore").replace("\x00", " ")
        except Exception:
            continue
        if len(tail) < 13:
            continue
        # tail[16] is `nice`: nice>0 work is opportunistic filler that YIELDS to the nice-0 boards,
        # so it is never counted as stolen capacity (measured 2026-10-15: s1's npz precompute /
        # klines_vault run at nice 19 — the boards simply did not demand the cores).
        try:
            ni = int(tail[16])
        except Exception:
            ni = 0
        s[p] = ((int(tail[11]) + int(tail[12])) / os.sysconf("SC_CLK_TCK"), cl, ni)
    return s
def _bucket(cl):
    if "v15_pilot.py" in cl:
        return "boards"
    for k, n in (("backtest_v8_precompute", "npz_precompute"), ("klines_append", "klines_append"), ("klines_vault", "klines_vault"), ("backtest_v12", "backtest_v12")):
        if k in cl:
            return n
    if "ez_" in cl or "tradier" in cl:
        return "live_helpers"
    return "other"
a0, t0 = _cpu_snap(), time.time()
time.sleep(float(os.environ.get("V15_TG_CPU_SAMPLE_S", "6")))
a1, t1 = _cpu_snap(), time.time()
dt = max(t1 - t0, 0.001)
cpu, niced = {}, {}
for p, (c, cl, ni) in a1.items():
    d = c - a0[p][0] if p in a0 else c
    if d <= 0.02:
        continue
    b = _bucket(cl)
    tgt = niced if (ni > 0 and b != "boards") else cpu
    tgt[b] = round(tgt.get(b, 0.0) + d / dt, 2)
out["cores_by_bucket"] = cpu
out["cores_by_bucket_niced"] = niced
out["board_cores"] = cpu.get("boards", 0.0)
out["nonboard_cores_competing"] = round(sum(v for k, v in cpu.items() if k != "boards"), 2)
out["nonboard_cores_niced"] = round(sum(niced.values()), 2)
try:
    idl = [l for l in open("/proc/stat") if l.startswith("cpu ")][0].split()[1:]
    out["cpu_total_jiffies"] = sum(int(x) for x in idl)
except Exception:
    pass
print(json.dumps(out))
'''


def probe(alias, win):
    code = PROBE % {"win": win, "sheets": SHEETS}
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", alias, "python3 -"], input=code, capture_output=True, text=True, timeout=240)
    for line in reversed((r.stdout or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"error": (r.stderr or "no output")[-300:]}


def sched_tick():
    """Newest scheduler tick from S1 — slots, idle_reason, est_pair_mb per host (why a host is not launching)."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOSTS["s1"], "grep -a '^{' /tmp/v15_fleet_sched.log | tail -1"], capture_output=True, text=True, timeout=60)
        return (json.loads(r.stdout.strip()) or {}).get("hosts") or {}
    except Exception:
        return {}


def binding_reason(h, tick, target, improved, win, d=None):
    """The honest limiter, in the host's own numbers: dispatch guard first, then the CPU budget (measured
    2026-10-15: >85% of a board's CPU is real engine eval in the fork pool, so the ceiling is
    board_cores*3600/cpu_s_per_board), then the concurrency/latency arithmetic."""
    d = d or {}
    t = tick.get(h) if isinstance(tick, dict) else None
    if t == "UNREACHABLE":
        return "host UNREACHABLE to the scheduler — no dispatch at all"
    if isinstance(t, dict) and t.get("idle_reason") in ("disk guard", "mem guard"):
        return f"scheduler {t['idle_reason']} (disk {t.get('disk_avail_mb')} MB, mem {t.get('mem_avail_mb')} MB, est_pair {t.get('est_pair_mb')} MB) — slots {t.get('slots')}"
    if improved >= target:
        return None
    cpu_s = d.get("board_cpu_s_median_24h")
    nproc = d.get("nproc") or 0
    competing = float(d.get("nonboard_cores_competing") or 0)
    board_cores = float(d.get("board_cores") or 0)
    running = len(d.get("boards_running") or [])
    if cpu_s and nproc:
        # cores the boards could still take (niced work yields); the ceiling the host COULD serve
        avail = max(nproc - competing, 0.0)
        ceil_host = avail * 3600.0 / float(cpu_s)
        ceil_demand = board_cores * 3600.0 / float(cpu_s)
        if ceil_host < target:
            return (f"CPU budget: median board {float(cpu_s):.0f} core-s, {nproc} cores minus "
                    f"{competing:.1f} competing non-board cores ({d.get('cores_by_bucket')}) = {avail:.1f} usable "
                    f"-> host ceiling {ceil_host:.1f} boards/h. Move nice-0 non-board work off the host "
                    f"and/or cut core-s per board.")
        if ceil_demand < target:
            per = (board_cores / running) if running else 0.0
            need = target * float(cpu_s) / 3600.0
            if per <= 0:
                # USER 2026-10-22: `need / per` raised ZeroDivisionError on EVERY hourly run, so the guard that exists to
                # report an idle host crashed exactly when a host went idle (running=0 -> per=0). Measured 2026-10-22 03:10Z:
                # s2/s5/s6 starved at 0/6 while this guard died at line 252 each hour and alerted nobody. Report, never divide.
                return (f"host IDLE, not CPU-bound: {running} running sides pull {board_cores:.1f} cores of {avail:.1f} "
                        f"usable -> {ceil_demand:.1f} boards/h against target {target}/h. Nothing is dispatched here: "
                        f"{target}/h needs {need:.1f} board-cores. Check the scheduler's idle_reason for this host "
                        f"(slots {t.get('slots') if isinstance(t, dict) else '?'}) and whether the round is exhausted "
                        f"(tools/v15_round_watchdog.sh quorum rule rolls it).")
            return (f"board CPU DEMAND, not host capacity: {running} running sides pull only {board_cores:.1f} "
                    f"cores ({per:.2f} core/side) of {avail:.1f} usable -> {ceil_demand:.1f} boards/h. "
                    f"{target}/h needs {need:.1f} board-cores = {need / per:.0f} sides at this per-side draw "
                    f"(niced filler {d.get('nonboard_cores_niced')} cores yields). Lever: more concurrent sides "
                    f"(RAM-bound) or deeper eval pipelining per side (V15_PREFETCH_TASKCAP on an idle host).")
    slots = 0
    if isinstance(t, dict):
        try:
            slots = int(str(t.get("slots", "0/0")).split("/")[0])
        except Exception:
            slots = 0
    wall = d.get("board_wall_s_median_24h")
    need_latency_min = (slots * 60.0 / target) if (slots and target) else 0.0
    return (f"concurrency {slots} boards: {target}/h needs <= {need_latency_min:.0f} min per sym_side"
            + (f" (measured median {float(wall) / 60.0:.0f} min)" if wall else "")
            + " — raise concurrent sides or cut evals per board")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=float, default=1.0)
    ap.add_argument("--target", type=int, default=12, help="improved sym_sides per hour per host")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    tick = sched_tick()
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "window_hours": a.window_hours, "target_per_host_per_hour": a.target, "hosts": {}}
    under = []
    for h, alias in HOSTS.items():
        d = probe(alias, a.window_hours)
        imp = int(d.get("improved") or 0)
        rate = imp / a.window_hours if a.window_hours else 0.0
        reason = binding_reason(h, tick, a.target, rate, a.window_hours, d)
        cadence_min = 60.0 / a.target if a.target else None
        since = d.get("minutes_since_last_improved")
        rep["hosts"][h] = {"improved": imp, "improved_per_hour": round(rate, 2), "published_n": d.get("published_n"), "flat_or_worse": d.get("flat_or_worse"), "unknown": d.get("unknown"), "boards_running": d.get("boards_running"), "mem_avail_mb": d.get("mem_avail_mb"), "load1": d.get("load1"), "nproc": d.get("nproc"), "slots": (tick.get(h) or {}).get("slots") if isinstance(tick.get(h), dict) else tick.get(h), "binding_reason": reason, "error": d.get("error"), "published": d.get("published"),
                           "cadence_target_min": cadence_min, "minutes_since_last_improved": since, "last_improved_symside": d.get("last_improved_symside"),
                           "cadence_ok": (since is not None and cadence_min is not None and since <= cadence_min),
                           "improved_gap_min_median_24h": d.get("improved_gap_min_median_24h"), "improved_gap_min_max_24h": d.get("improved_gap_min_max_24h"),
                           "improved_finishes_24h": d.get("improved_finishes_24h"),
                           "board_cpu_s_median_24h": d.get("board_cpu_s_median_24h"), "board_wall_s_median_24h": d.get("board_wall_s_median_24h"),
                           "board_cores": d.get("board_cores"), "nonboard_cores_competing": d.get("nonboard_cores_competing"),
                           "nonboard_cores_niced": d.get("nonboard_cores_niced"), "cores_by_bucket": d.get("cores_by_bucket"),
                           "cores_by_bucket_niced": d.get("cores_by_bucket_niced")}
        if rate < a.target:
            under.append(h)
        if not a.quiet:
            print(f"[throughput] {h}: {rate:.1f} improved sym_sides/h (target {a.target}) | last improved {since if since is not None else '?'} min ago (cadence target {cadence_min:.0f} min) | gap median/max 24h {d.get('improved_gap_min_median_24h')}/{d.get('improved_gap_min_max_24h')} min | board cores {d.get('board_cores')} of {d.get('nproc')} (competing {d.get('nonboard_cores_competing')}, niced {d.get('nonboard_cores_niced')}) | {'OK' if rate >= a.target else 'UNDER: ' + str(reason)}", flush=True)
    rep["under_target"] = under
    rep["cadence_breach"] = [h for h, v in rep["hosts"].items() if not v.get("cadence_ok")]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1))
    tmp.replace(OUT)
    return 1 if under else 0


if __name__ == "__main__":
    sys.exit(main())
