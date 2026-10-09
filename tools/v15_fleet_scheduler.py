#!/usr/bin/env python3
"""v15_fleet_scheduler — N-server SYMBOL-PAIR scheduler for the daily 30D -> 365D -> REPAIR chain (USER 2026-09-30 rework).

UNIT OF WORK = one SYMBOL = its LONG and SHORT side launched together on the same host. A symbol slot is HELD from 30D start through
the whole chain (30D -> 365D verify -> REPAIR reruns incl. better-30D sheet + pos-365D gating) and released only when both sides are
terminal (ok / UNVERIFIABLE / failing). Chain work prefers the 30D owner host; pass 2 STEALS 365D/REPAIR-a1 stages onto idle hosts
(owner had first refusal; thief fetches the small inputs tick-side via S1 relay, no new fleet keys; slot held where the work RUNS).
30D / GS / REPAIR-aN(N>=2) never steal (owner / rep-host sticky).
Per host: max_pairs concurrent symbols (fleet_hosts*.json, default 3, clamped 2..12), workers_per_side (pilot --workers).
Admission of a NEW symbol needs: free slot, MemAvailable - mem_reserve >= estimated pair memory (measured PSS of running pairs x1.2,
else default_pair_mb, + its NPZ size), cpu < cpu_target_pct, NPZ readiness on that host, both templates present, and at most
--max-launch new pairs per host per tick (no ramp, shared by pass 1 + steal pass). Symbols whose data is not ready are SKIPPED and listed in the state.

UNIVERSE (tools/v15_universe.py, written to data/daily_universe/<date>.json): STOCKS = symbols that traded in the last 30d in the real
ledger (data/history/{tra,trb,trc,inf}); CRYPTO = union of symbols_{flz,men,ang}_{long,short}.json. --extend-universe (off) adds the
legacy order-file symbols, and only once the base universe is fully terminal.
Priority: stocks first while the US market is closed until 09:30 ET, crypto otherwise; adopted chain work (already started symbols) first. USER 2026-10-07: crypto-first forced Oct 7-8 UTC (CRYPTO_FIRST_DATES, self-reverting) + V15_SCHED_VENUE_FIRST env override.
NO LIVE/backtest_v12_engine backfill jobs (removed by USER 2026-09-30).
Nothing is killed here; `--kill-out-of-universe [--apply-kill]` lists/kills pilots of the CURRENT progress dir whose symbol is not in the universe.

  --once  --dry-run  --simulate F (host-stats JSON; implies dry-run)  --now ISO  --hosts FILE  --extend-universe
"""
import argparse, datetime, fcntl, json, os, pathlib, re, shlex, subprocess, sys, time
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_universe as U  # noqa: E402

HOSTS = ROOT / "tools" / "fleet_hosts.json"
STATE = ROOT / "data" / "fleet_scheduler_state.json"
DAILY = ROOT / "data" / "daily_reports"
NY = ZoneInfo("America/New_York")
CRYPTO_SUFFIX = U.CRYPTO_SUFFIX
CATS = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
MIN_SPAN_30D, MIN_SPAN_365D = 32.0, 330.0


def _load_non_shortable():
    import re
    try:
        txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config_tradier.py")).read()
        m = re.search(r"NON_SHORTABLE\s*=\s*\{([^}]*)\}", txt)
        out = set(re.findall(r'"([A-Z0-9.\-]+)"', m.group(1))) if m else set()
        try:
            out |= set(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "non_shortable_mac.json"))))  # Mac config_tradier = source of truth
        except Exception:
            pass
        return out
    except Exception:
        return set()


NON_SHORTABLE = _load_non_shortable()


def venue_of(sym):
    return "crypto" if sym.endswith(CRYPTO_SUFFIX) else "stocks"


def us_market_open(now):
    t = now.astimezone(NY)
    return t.weekday() < 5 and datetime.time(9, 30) <= t.time() < datetime.time(16, 0)


def minutes_to_open(now):
    t = now.astimezone(NY)
    for d in range(0, 8):
        c = (t + datetime.timedelta(days=d)).replace(hour=9, minute=30, second=0, microsecond=0)
        if c.weekday() < 5 and c > t:
            return (c - t).total_seconds() / 60
    return 9999


# USER 2026-10-07: crypto-first through Oct 8 UTC so the crypto SHORT tail drains
# before the avg rerun (self-reverts Oct 9; V15_SCHED_VENUE_FIRST overrides).
CRYPTO_FIRST_DATES = ("2026-10-07", "2026-10-08")


def priority_syms():
    """V15_SCHED_PRIORITY_SYMS=EDUUSDT,... -> these symbols sort before all others (USER 2026-10-07: crypto SHORT tail first)."""
    return {s.strip().upper() for s in os.environ.get("V15_SCHED_PRIORITY_SYMS", "").split(",") if s.strip()}


def sym_rank_key(s, vrank, all_syms, pri):
    return (0 if s in pri else 1, vrank[venue_of(s)], all_syms.index(s))


def venue_order(now):
    v = os.environ.get("V15_SCHED_VENUE_FIRST")
    if v == "crypto":
        return ["crypto", "stocks"]
    if v == "stocks":
        return ["stocks", "crypto"]
    if now.strftime("%Y-%m-%d") in CRYPTO_FIRST_DATES:
        return ["crypto", "stocks"]
    return ["crypto", "stocks"] if us_market_open(now) else ["stocks", "crypto"]


def sh_ssh(host, cmd, timeout=60):
    for target in host["ssh"]:
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", target, cmd],
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                host["_via"] = target
                return r.stdout
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- host-side helper (runs on the host under .venv python)
HOST_PY = r'''
import glob, json, os, re, signal, sys, time
_VERDICT_TERMINAL_RE = re.compile(r'"verdict":\s*"(IMPOSSIBLE|NO_TRADES|BEST_EFFORT)"')
mode = sys.argv[1]
arg = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
HOME = os.path.expanduser("~")
ME = {os.getpid(), os.getppid()}
SCRIPTS = ("v15_pilot.py", "v15_365_cycle.py")


def procs():
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in ME:
            continue
        try:
            a = [x.decode("utf8", "replace") for x in open("/proc/%s/cmdline" % d, "rb").read().split(b"\0") if x]
        except Exception:
            continue
        if a:
            out.append((int(d), a))
    return out


def pss_mb(pid):
    try:
        for l in open("/proc/%d/smaps_rollup" % pid):
            if l.startswith("Pss:"):
                return int(l.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        return int(open("/proc/%d/statm" % pid).read().split()[1]) * 4096 / 1048576.0
    except Exception:
        return 0.0


def environ(pid):
    try:
        return dict(x.split("=", 1) for x in open("/proc/%d/environ" % pid, "rb").read().decode("utf8", "replace").split("\0") if "=" in x)
    except Exception:
        return {}


def pilots():
    """python processes of v15_pilot / v15_365_cycle with --sym-side: {ss: {pss_mb, pids, kind, pdir}} (workers included)"""
    r = {}
    for pid, a in procs():
        if not os.path.basename(a[0]).startswith("python") or "--sym-side" not in a:
            continue
        kind = next((s for s in SCRIPTS if any(s in x for x in a)), None)
        if not kind:
            continue
        ss = a[a.index("--sym-side") + 1]
        e = r.setdefault(ss, {"pss_mb": 0.0, "pids": [], "kind": kind, "pdir": None})
        e["pss_mb"] += pss_mb(pid)
        e["pids"].append(pid)
        if e["pdir"] is None:
            e["pdir"] = environ(pid).get("V15_PROGRESS_DIR")
    return r


if mode == "probe":
    pdir = open(HOME + "/v15_current_progress_dir.txt").read().strip() if os.path.exists(HOME + "/v15_current_progress_dir.txt") else None
    mem = 0
    swp_t, swp_f = 0, 0
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"):
            mem = int(l.split()[1]) // 1024
        elif l.startswith("SwapTotal"):
            swp_t = int(l.split()[1]) // 1024
        elif l.startswith("SwapFree"):
            swp_f = int(l.split()[1]) // 1024
    swp_u = max(0, swp_t - swp_f)
    def _cpu_t():
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(v), v[3] + v[4], v[1]
    _t0, _i0, _n0 = _cpu_t()
    time.sleep(1.0)
    _t1, _i1, _n1 = _cpu_t()
    busy = round(100.0 * (1 - (_i1 - _i0) / max(1, _t1 - _t0)), 1)
    busy_nn = round(100.0 * (1 - ((_i1 - _i0) + (_n1 - _n0)) / max(1, _t1 - _t0)), 1)
    o = {"nproc": os.cpu_count(), "load1": float(open("/proc/loadavg").read().split()[0]), "busy_pct": busy, "busy_nonnice_pct": busy_nn, "mem_avail_mb": mem, "pdir": pdir, "swap_total_mb": swp_t, "swap_used_mb": swp_u,
         "running": pilots(), "started": [], "done": [], "quarantined": [], "v365": {}, "repair": {}, "gs": {}, "gains": {}}
    if pdir and os.path.isdir(pdir):
        cache_p = "/tmp/v15_sched_done_cache.json"
        try:
            cache = json.load(open(cache_p))
        except Exception:
            cache = {}
        for f in glob.glob(os.path.join(pdir, "*_v14_progress.json")):
            ss = os.path.basename(f)[:-len("_v14_progress.json")]
            o["started"].append(ss)
            try:
                stt = os.stat(f)
                key = "%s|%s|%s" % (f, stt.st_mtime, stt.st_size)
                if key not in cache or key + "|q" not in cache or key + "|g" not in cache:
                    cache = {k: v for k, v in cache.items() if not k.startswith(f + "|")}
                    _txt = open(f).read()
                    cache[key] = bool(re.search(r'"final_gain": [-0-9]', _txt))
                    cache[key + "|q"] = bool(_VERDICT_TERMINAL_RE.search(_txt))
                    _gm = re.search(r'"final_gain":\s*(-?[0-9]+\.?[0-9]*(?:[eE][-+]?[0-9]+)?)', _txt)
                    cache[key + "|g"] = float(_gm.group(1)) if _gm else None
                if cache[key]:
                    o["done"].append(ss)
                    if cache.get(key + "|g") is not None:
                        o["gains"][ss] = {"gain": cache[key + "|g"], "mtime": stt.st_mtime}
                if cache.get(key + "|q"):
                    o["quarantined"].append(ss)
            except Exception:
                pass
        try:
            json.dump(cache, open(cache_p, "w"))
        except Exception:
            pass
        w = os.path.join(os.path.dirname(pdir.rstrip("/")), "chain")

        def verdict(f):
            try:
                d = json.load(open(f))
                return {"ok": bool(d.get("final_both_ok")), "unverifiable": bool(d.get("unverifiable")), "final_progress": d.get("final_progress"), "ts": str(d.get("ts", ""))}
            except Exception:
                return None
        for f in glob.glob(os.path.join(w, "v365", "*_365_cycle.json")):
            v = verdict(f)
            if v:
                o["v365"][os.path.basename(f)[:-len("_365_cycle.json")]] = v
        for f in glob.glob(os.path.join(w, "repair_a*", "*_365_cycle.json")):
            v = verdict(f)
            if v:
                o["repair"].setdefault(os.path.basename(f)[:-len("_365_cycle.json")], {})[int(re.search(r"repair_a(\d+)", f).group(1))] = v

        def gs_verdict(f):  # USER 2026-10-07: GS-heal stage verdict (standalone {ss}.gs.json); ok = 365D gain>0 measured
            try:
                d = json.load(open(f))
                a365 = d.get("after_365") or {}
                g = a365.get("gain")
                return {"ok": bool(g is not None and g > 0), "unverifiable": bool(not a365 and not (d.get("origin_365") or {}).get("m365"))}
            except Exception:
                return None
        for f in glob.glob(os.path.join(w, "gs_a*", "*.gs.json")):
            v = gs_verdict(f)
            if v:
                o["gs"].setdefault(os.path.basename(f)[:-len(".gs.json")], {})[int(re.search(r"gs_a(\d+)", f).group(1))] = v
    print(json.dumps(o))

elif mode == "ready":
    import numpy as np
    res = {}
    now = time.time()
    for sym in arg["symbols"]:
        p = os.path.join("backtest_v8", "indicators", sym + ".npz")
        r = {"ok": False, "reason": "npz missing"}
        if os.path.exists(p):
            r["size_mb"] = round(os.path.getsize(p) / 1048576.0, 1)
            try:
                z = np.load(p, allow_pickle=True)
                if "timestamps" not in z.files or "timestamp_15m" not in z.files:
                    r["reason"] = "no timestamps/timestamp_15m"
                else:
                    t = z["timestamps"].astype("float64")
                    t15 = z["timestamp_15m"].astype("float64")
                    if len(t15) != len(t):
                        r["reason"] = "len(timestamp_15m)=%d != len(timestamps)=%d" % (len(t15), len(t))
                    elif len(t) < 100:
                        r["reason"] = "too few bars %d" % len(t)
                    else:
                        k = 1000.0 if t[-1] > 1e11 else 1.0
                        d = np.diff(t) / k
                        d = d[d > 0]
                        bdt = float(np.median(d)) if len(d) else 0.0
                        span = float((t[-1] - t[0]) / k / 86400.0)
                        age_h = float((now - t[-1] / k) / 3600.0)
                        r.update(span_d=round(span, 1), age_h=round(age_h, 1), base_dt=bdt)
                        gap_h = 0.0
                        if arg.get("venue") == "crypto":
                            rec = t[t >= t[-1] - 30 * 86400 * k]
                            gd = np.diff(rec) / k
                            gap_h = float(gd.max() / 3600.0) if len(gd) else 0.0
                            r["max_gap_30d_h"] = round(gap_h, 2)
                        max_age = 336.0 if arg.get("venue") == "crypto" else 120.0  # AUTOPILOT 2026-10-02: crypto NPZ tail refresh is not automatic (NPZB loop only); 48h would stop every crypto launch while the user is away
                        par = t15[(t15 > 0) & np.isfinite(t15)]
                        ev = int((np.r_[True, par[1:] != par[:-1]]).sum()) if len(par) else 0
                        lag_min = float(np.nanmin(t - t15))
                        # base dt (300s = 5m-base) is recorded, NOT a blocker: the sweep loader evaluate_v12._compact_to_15m works on the
                        # timestamp_15m parent array and the old tick produced valid sheets from 5m-base stock NPZs.
                        if ev < 10 or lag_min < 0:  # mirrors evaluate_v12._compact_to_15m
                            r["reason"] = "_compact_to_15m would raise (events=%d, lag_min=%.0f)" % (ev, lag_min)
                        elif span < arg["min_span_30d"]:
                            r["reason"] = "history %.1fd < %.0fd" % (span, arg["min_span_30d"])
                        elif age_h > max_age:
                            r["reason"] = "stale: last bar %.1fh old" % age_h
                        elif gap_h > 3.0:
                            r["reason"] = "gap %.1fh inside last 30d" % gap_h
                        else:
                            r.update(ok=True, reason="", can_365=bool(span >= arg["min_span_365d"]))
            except Exception as e:
                r["reason"] = "load fail %s" % str(e)[:80]
        res[sym] = r
    print(json.dumps(res))

elif mode == "reap":
    # ported from the retired v15_local_herd (OOM guard, stuck hardcap, orphan reap). arg: {oom_mb, stall_min, hard_min, apply}.
    # Everything killed resumes from its progress JSON; the scheduler's attempt caps bound relaunches.
    now = time.time()
    _btime = next(int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime"))
    def start_ts(pid):
        try:
            return _btime + int(open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[19]) / float(os.sysconf("SC_CLK_TCK"))
        except Exception:
            return now
    groups = pilots()
    info = {}
    for ss, g in groups.items():
        sym, side = ss.rsplit("_", 1)
        age = max([now - start_ts(p) for p in g["pids"]] or [0])
        pd = g.get("pdir") or ""
        cands = glob.glob("/tmp/sweep_%s_%s_*.log" % (sym, side)) + ["/tmp/v15_%s.log" % ss]
        if pd:
            cands += [os.path.join(pd, ss + "_v14_progress.json"), os.path.join(pd, "v15_delta_log", ss + "_jump.jsonl")]
        cands += glob.glob(os.path.join(HOME, "binance-sandbox", "SPREADSHEETS", "V15_V16_CELL_BY_CELL", ss + "_30d_matrix.xlsx"))
        mt = [os.path.getmtime(c) for c in cands if os.path.exists(c)]
        ppid = {}
        for p in g["pids"]:
            try:
                ppid[p] = int(open("/proc/%d/stat" % p).read().rsplit(")", 1)[1].split()[1])
            except Exception:
                ppid[p] = 1
        roots = [p for p in g["pids"] if ppid[p] not in g["pids"]]
        kids = {r: sum(1 for p in g["pids"] if ppid[p] == r) for r in roots}
        info[ss] = {"pids": g["pids"], "age": age, "min_age": min([now - start_ts(p) for p in g["pids"]] or [0]), "idle": now - max(mt) if mt else age, "roots": roots, "kids": kids}
    acts = []
    _TERM_GRACE_S = 150  # USER 2026-10-07: THROTTLE instead of OOM — SIGTERM first (pilot checkpoints + exits <60s), SIGKILL only past grace
    for _sent in glob.glob("/tmp/v15_termed_*"):
        try:
            _spid = int(_sent.rsplit("_", 1)[1])
            os.kill(_spid, 0)
        except Exception:
            try: os.unlink(_sent)
            except Exception: pass
    def kill(pids, why, ss):
        for p in pids:
            _how = ""
            if arg.get("apply"):
                _sent = "/tmp/v15_termed_%d" % p
                try:
                    _sage = now - os.path.getmtime(_sent) if os.path.exists(_sent) else -1
                except Exception:
                    _sage = -1
                try:
                    if _sage >= 0 and _sage > _TERM_GRACE_S:
                        os.kill(p, signal.SIGKILL)
                        _how = " (KILL-after-grace)"
                        try: os.unlink(_sent)
                        except Exception: pass
                    else:
                        os.kill(p, signal.SIGTERM)
                        _how = " (TERM)"
                        try: open(_sent, "w").write(str(now))
                        except Exception: pass
                except Exception:
                    pass
            acts.append({"ss": ss, "why": why + _how, "pids": [p]})
    mem = 0
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"):
            mem = int(l.split()[1]) // 1024
    dead = set()
    for ss, i in info.items():
        if i["age"] > arg["hard_min"] * 60:
            kill(i["pids"], "hardcap %.0fmin" % (i["age"] / 60), ss); dead.add(ss)
        elif i["age"] > arg["stall_min"] * 60 and i["idle"] > arg["stall_min"] * 60:
            kill(i["pids"], "stuck: no progress/log/xlsx write for %.0fmin" % (i["idle"] / 60), ss); dead.add(ss)
        elif len(i["roots"]) > 1 and sum(1 for r in i["roots"] if i["kids"][r]) == 1:
            orph = [r for r in i["roots"] if not i["kids"][r]]
            kill(orph, "orphaned workers (parent gone)", ss)
        elif i["roots"] and all(i["kids"].get(r, 0) == 0 for r in i["roots"]) and i["idle"] > 20 * 60 and i.get("min_age", 0) > 15 * 60:
            kill(i["pids"], "wedged: no linked workers, no output for %.0fmin" % (i["idle"] / 60), ss); dead.add(ss)  # USER 2026-10-07: S1 UNI pair wedged 30+ min (0 CPU, slots held, nothing finished) — auto-clear instead of waiting for the 60-min stall trip. 2026-10-08: min_age grace — piped/starting pilots have no kids + stale log cands for minutes; TERMing a 23s-old healthy pilot churns (KSMUSDT_SHORT).
    if mem < arg["oom_mb"]:
        _rov, _pdr = None, None
        try:
            sys.path.insert(0, os.path.join(HOME, "binance-sandbox", "tools"))
            from v15_fleet_scheduler import _rank_oom_victim as _rov, _progress_done_rows as _pdr
        except Exception:
            _rov, _pdr = None, None
        _victim = None
        if _rov is not None and _pdr is not None:
            try:
                _cands = []
                for _ss, _i in info.items():
                    if _ss in dead:
                        continue
                    _pd = (groups.get(_ss) or {}).get("pdir") or ""
                    _done = _pdr(os.path.join(_pd, _ss + "_v14_progress.json")) if _pd else None
                    _cands.append((_ss, _done, _i["age"]))
                _victim = _rov(_cands)
            except Exception:
                _victim = None
        if _victim is None:
            _live = [(i["age"], ss) for ss, i in info.items() if ss not in dead]
            _victim = min(_live)[1] if _live else None
        if _victim is not None:
            kill(info[_victim]["pids"], "OOM guard: MemAvailable %dMB < %dMB, least-progress victim" % (mem, arg["oom_mb"]), _victim)
    print(json.dumps({"apply": bool(arg.get("apply")), "mem_avail_mb": mem, "actions": acts}))

elif mode == "kill":
    # arg: {symbols:[...out-of-universe...], pdir:..., apply:bool}. Only processes that belong to the CURRENT progress dir
    # (env V15_PROGRESS_DIR or cmdline V15_PROGRESS_DIR=<pdir>) — old allcells pilots in other dirs are left alone.
    pat = re.compile(r"--sym-side (%s)_(LONG|SHORT)(\s|$)" % "|".join(re.escape(s) for s in arg["symbols"])) if arg["symbols"] else None
    hit = []
    for pid, a in procs():
        line = " ".join(a)
        if not pat or not pat.search(line) or not any(s in line for s in SCRIPTS):
            continue
        if ("V15_PROGRESS_DIR=" + arg["pdir"]) not in line and environ(pid).get("V15_PROGRESS_DIR") != arg["pdir"]:
            continue
        hit.append((pid, pat.search(line).group(1) + "_" + pat.search(line).group(2), os.path.basename(a[0])))
    if arg.get("apply"):
        for pid, _, _ in hit:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    print(json.dumps({"apply": bool(arg.get("apply")), "pids": [[p, s, k] for p, s, k in hit]}))
'''


def host_py(host, mode, arg, timeout=90):
    cmd = f"cd {host['root']} && .venv/bin/python - {mode} {shlex.quote(json.dumps(arg))} <<'PYE'\n{HOST_PY}\nPYE"
    out = sh_ssh(host, cmd, timeout)
    try:
        return json.loads((out or "").strip().splitlines()[-1])
    except Exception:
        return None


_VERDICT_TERMINAL_RE = re.compile(r'"verdict":\s*"(IMPOSSIBLE|NO_TRADES|BEST_EFFORT)"')

_LAUNCH_CAPS = {"30D": 3, "365D": 2, "REPAIR": 1, "GS": 1}  # USER 2026-10-09: single source of truth (gate + pending_actions); 30D 6->3, 40-min slices + free resume make more unnecessary
_GAIN_TIER_W_FRAC = 0.4  # top 40% of measured syms = winners (recalculated every round with the latest NPZ)
_GAIN_TIER_L_FRAC = 1.0 / 3.0  # bottom third = losers/low gainers (deferred, re-admitted after _GAIN_TIER_DEFER_DAYS)
_GAIN_TIER_DEFER_DAYS = {"M": 3.0, "L": 7.0}  # USER 2026-10-09: starve losers, feed winners — mediocre re-measured 2x/week (regime turns visible), losers weekly; winners every round + slot reservation -> ~90% of compute on winners
_NONW_QUOTA_DIV = 4  # non-winner new pairs per host capped at max(1, cap//4); unknowns (discovery) exempt
_TIER_RANK = {"W": 0, "M": 1, "L": 2}


def _gain_tiers(gains):
    """Rank measured sym_sides by latest gain -> {ss: {"tier": W|M|L, "gain": g, "mtime": m}}.
    Relative ranks (never empty tiers, never a full defer): W = top 40%, L = bottom third, M = rest. Sides with no
    measured gain are absent (callers default them to M: new/unknown work is never starved)."""
    rows = []
    for ss, g in (gains or {}).items():
        try:
            gv = float((g or {}).get("gain"))
        except (TypeError, ValueError):
            continue
        try:
            mt = float((g or {}).get("mtime", 0) or 0)
        except (TypeError, ValueError):
            mt = 0.0
        rows.append((str(ss), gv, mt))
    rows.sort(key=lambda r: r[1], reverse=True)
    n = len(rows)
    nw = max(1, int(n * _GAIN_TIER_W_FRAC)) if n else 0
    nl = max(1, int(n * _GAIN_TIER_L_FRAC)) if n else 0
    out = {}
    for i, (ss, gv, mt) in enumerate(rows):
        out[ss] = {"tier": "W" if i < nw else ("L" if i >= n - nl else "M"), "gain": gv, "mtime": mt}
    return out


def _side_tier(ss, tiers):
    return ((tiers or {}).get(ss) or {}).get("tier", "M")


def _sym_tier_rank(sym, tiers):
    return min(_TIER_RANK[_side_tier(f"{sym}_{sd}", tiers)] for sd in ("LONG", "SHORT"))


def _side_deferred(ss, tiers, now_ts, windows=_GAIN_TIER_DEFER_DAYS):
    """True = M/L-tier side whose last calc is younger than its window (M 3d, L 7d): skip its fresh board (chain
    continuations still drain; staleness re-admits, so regime turns re-measure). W/unknown/missing/bad fails open."""
    e = (tiers or {}).get(ss)
    if not e:
        return False
    try:
        win = float((windows or {}).get(e.get("tier"), 0) or 0)
    except (TypeError, ValueError):
        return False
    if win <= 0:
        return False
    try:
        age_d = (float(now_ts) - float(e.get("mtime", 0) or 0)) / 86400.0
    except (TypeError, ValueError):
        return False
    return age_d < win


def _sym_quota_hit(sym, tiers):
    """True = pair consumes a non-winner slot: measured M/L present and no W side. Unknown/new pairs are discovery (exempt)."""
    ts = [((tiers or {}).get(f"{sym}_{sd}") or {}).get("tier") for sd in ("LONG", "SHORT")]
    return any(t in ("M", "L") for t in ts) and not any(t == "W" for t in ts)


def _gs_allowed_for_tier(tier, measured):
    """USER 2026-10-09 (90% on winners): GS-heal for W/M/discovery; L gets verified, not rescued."""
    return (tier in ("W", "M")) or not measured


def _repair_allowed_for_tier(tier, measured):
    """USER 2026-10-09 (90% on winners): repair rounds for W/discovery only; M got its GS shot, L gets none."""
    return tier == "W" or not measured


def _place_order(held_ordered, adopted_owned, new_syms, tiers):
    """USER 2026-10-09 (trb gainers absolute priority): W chains + W new before ALL M/L work. Stable sort
    keeps chain-first within a tier. Running pilots are never touched — this orders new launches only."""
    seq = [(s, False) for s in held_ordered] + [(s, True) for s in adopted_owned] + [(s, True) for s in new_syms]
    seq.sort(key=lambda t: _sym_tier_rank(t[0], tiers))
    return seq


def _pair_gate_ok(sym, acts, owner, chain_state, attempts):
    """USER 2026-10-03 (drain stall): fresh (unowned) syms launch when EVERY launchable side has an
    action — terminal/capped sides don't block their sibling (half-quarantined pairs used to stall
    forever: the live side could never launch alone). Owned syms always pass (stickiness). Mirrors
    pending_actions caps exactly via _LAUNCH_CAPS (30D:3, 365D:2, REPAIR:1, GS:1)."""
    if sym in owner:
        return True
    caps = {"need30": ("30D", _LAUNCH_CAPS["30D"]), "need365": ("365D", _LAUNCH_CAPS["365D"]), "needrepair": ("REPAIR", _LAUNCH_CAPS["REPAIR"]), "needgs": ("GS", _LAUNCH_CAPS["GS"])}
    needy = set()
    for side, (s, att) in (chain_state.get(sym) or {}).items():
        if s not in caps:
            continue
        w, cap = caps[s]
        key = "%s_%s|%s%s" % (sym, side, w, att if w in ("REPAIR", "GS") else "")
        if attempts.get(key, 0) < cap:
            needy.add(side)
    return bool(needy) and {a["side"] for a in acts} == needy


def _quar_terminal(ss, quar, no_chain):
    """Terminal-verdict (progress verdict IMPOSSIBLE, NO_TRADES, BEST_EFFORT) syms never relaunch:
    the pilot refuses them (IMPOSSIBLE-SKIP / SOFT-SKIP), so relaunching only burns attempts. Returns
    the terminal state or None. USER 2026-10-03 (attempt-burn stall: 229 keys at cap; best-effort: soft verdicts join)."""
    if ss not in quar:
        return None
    return ("terminal_ok", None) if no_chain else ("terminal_failing", None)


def _est_pair_mb(pair_measure, floor_mb=12000):
    """Honest pair estimate: biggest measured pair x1.25 (growth headroom) or the floor.
    USER 2026-10-08: avg x1.2 + 4000 floor admitted 5 pairs/host (~50GB) on 31GB boxes;
    the OOM guard then murdered high-progress sides (s6 RLC LONG at 3249 rows). A steady
    pair peaks ~12-15GB (2 x 5GB parents + 12 workers). Never raises."""
    try:
        vals = [float(v) for v in (pair_measure or []) if v is not None]
        mx = max([v for v in vals if v > 0] or [0.0])
    except Exception:
        mx = 0.0
    try:
        floor = float(floor_mb or 12000)
    except Exception:
        floor = 12000.0
    return max(floor, mx * 1.25)


def _growth_debt_mb(pairs_pss, est_pair):
    """Committed-but-not-yet-resident memory: young pairs grow toward est_pair.
    USER 2026-10-08 follow-up: s2 admitted a 3rd pair across ticks (young RSS
    understated growth) then collapsed 18GB -> 1GB in minutes. Only pairs under
    half est count (mature single-side stocks at ~6GB never grow a sibling).
    Never raises."""
    try:
        e = float(est_pair or 0)
    except Exception:
        return 0.0
    debt = 0.0
    try:
        vals = pairs_pss.values() if isinstance(pairs_pss, dict) else (pairs_pss or [])
        for v in vals:
            try:
                m = float(v or 0)
            except Exception:
                continue
            if 0 < m < e * 0.5:
                debt += e - m
    except Exception:
        return 0.0
    return debt


def _swap_admit_ok(stats, pct_max=40.0):
    """Refuse new launches while the host is swap-drowning (USER 2026-10-08: s5 ran
    8/8GB swap full and kept admitting). Missing swap fields fail open (old probe)."""
    try:
        tot = float((stats or {}).get("swap_total_mb") or 0)
        used = float((stats or {}).get("swap_used_mb") or 0)
    except Exception:
        return True
    if tot <= 0:
        return True
    try:
        return (100.0 * used / tot) <= float(pct_max)
    except Exception:
        return True


def _progress_done_rows(path):
    """len(done) of a progress JSON, None when unreadable (fail-open for OOM ranking)."""
    try:
        d = json.load(open(path))
        return len(d.get("done") or {})
    except Exception:
        return None


def _rank_oom_victim(cands):
    """Least-progress OOM victim. cands: [(ss, done_or_None, age_s)]. A side with no
    readable progress file and age < 30min counts as 0 rows (brand-new, least loss);
    no-file + old counts last (unknown act, probably slow 365D — don't murder it).
    Ties break youngest. Returns ss or None. Never raises."""
    best, best_key = None, None
    for c in cands or []:
        try:
            ss, done, age = c[0], c[1], float(c[2] or 0)
        except Exception:
            continue
        if done is None:
            key = (0, 0, age) if age < 1800 else (1, 0, age)
        else:
            try:
                key = (0, int(done), age)
            except Exception:
                continue
        if best_key is None or key < best_key:
            best, best_key = ss, key
    return best


def _reap_burn_weight(why, key):
    """Attempt burn per reap (USER 2026-10-08: stalled sides skip fast, OOM sides forgive).
    Stall/wedge/hardcap on 30D = side-fault -> 3 (parks after 2 trips, ~4x faster than
    before); OOM/chain stalls -> 1 (host pressure or slow act, deserves relaunch);
    orphan hygiene -> 0. Never raises."""
    try:
        w = str(why or "")
        k = str(key or "")
    except Exception:
        return 1
    if w.startswith("orphaned"):
        return 0
    if k.endswith("|30D") and (w.startswith("stuck:") or w.startswith("wedged:") or w.startswith("hardcap")):
        return 3
    return 1


def _reap_burns(actions, last_act):
    """{attempt-key: total burn} for one host's reap actions. Dedupes by side —
    the reaper emits one entry per PID, and without this a 7-proc side burns 7
    (NMR hit 16). Never raises."""
    out = {}
    seen = set()
    for a in actions or []:
        try:
            ss = a.get("ss")
        except Exception:
            continue
        if ss in seen:
            continue
        seen.add(ss)
        try:
            k = (last_act or {}).get(ss)
        except Exception:
            k = None
        if k:
            out[k] = out.get(k, 0) + _reap_burn_weight(a.get("why"), k)
    return out


def _stall_tick(prev_pending, prev_same, cur_pending, warn_ticks=60):
    """Pending-stuck counter. Returns (same_count, warn). 60 ticks ~= 2h of zero drain."""
    try:
        same = int(prev_same or 0) + 1 if prev_pending == cur_pending else 0
    except Exception:
        same = 0
    try:
        return same, bool(same >= int(warn_ticks or 60))
    except Exception:
        return same, False


def probe(host):
    return host_py(host, "probe", {})


def readiness(host, symbols, venue, cache, now):
    """{sym: info} for this host, cached (ok entries 3h, failures 30 min) in `cache[host]`."""
    c = cache.setdefault(host["name"], {})
    need = [s for s in symbols if s not in c or (now.timestamp() - c[s].get("_at", 0)) > (10800 if c[s].get("ok") else 1800)]
    if need:
        res = host_py(host, "ready", {"symbols": need, "venue": venue, "min_span_30d": MIN_SPAN_30D, "min_span_365d": MIN_SPAN_365D}, timeout=180)
        if res:
            for s, r in res.items():
                r["_at"] = now.timestamp()
                c[s] = r
    return c


def cat_of_ss(ss):
    sym, side = ss.rsplit("_", 1)
    return f"{'CRYPTO' if venue_of(sym) == 'crypto' else 'STOCKS'}_{side}"


def tmpl_path(host, venue, side):
    return f"{host['root']}/{host.get('template_dir', 'SPREADSHEETS')}/TEMPLATE_{'CRYPTO' if venue == 'crypto' else 'STOCKS'}_{side}.xlsx"


def _held_host(sym, owner, running):
    """Slot holder: the host where the symbol currently RUNS wins over the 30D owner
    (stolen chain work holds its slot where it runs). Pure helper (unit-tested)."""
    for sd in ("LONG", "SHORT"):
        r = running.get(f"{sym}_{sd}")
        if r:
            return r["host"]
    return owner.get(sym)


def _fetch_specs(sym, acts, thief_pdir, done_host, pdirs):
    """Pure: [(src_host, src_abs, dst_abs)] a thief must fetch before chain stages.
    365D + REPAIR-a1 need the finished 30D progress from its exec host; REPAIR-aN (N>=2)
    is rep-host-sticky (no fetch). Returns None when a source is unknown (fail-closed)."""
    specs = []
    for a in acts:
        if not (a["window"] == "365D" or (a["window"] == "REPAIR" and a["attempt"] == 1)):
            continue
        ss = f"{sym}_{a['side']}"
        src = done_host.get(ss)
        if not src or not pdirs.get(src):
            return None
        specs.append((src, f"{pdirs[src]}/{ss}_v14_progress.json", f"{thief_pdir}/{ss}_v14_progress.json"))
    seen, out = set(), []
    for s in specs:
        if (s[1], s[2]) not in seen:
            seen.add((s[1], s[2]))
            out.append(s)
    return out


def _repair_sticky_ok(act, rep_host, host_name):
    """REPAIR-aN (N>=2) runs only where a{N-1} ran (its output is local there). Pure."""
    if act.get("window") != "REPAIR" or int(act.get("attempt") or 1) < 2:
        return True
    ss = f"{act['sym']}_{act['side']}"
    return rep_host.get((ss, int(act["attempt"]) - 1), host_name) == host_name


def _steal_fetch(hosts, thief_host, sym, specs, log):
    """Tick-side (S1-relayed) fetch of chain inputs to the thief. No new fleet keys:
    S1 pulls each file from its exec host, pushes to the thief. Fail-closed."""
    byhost = {x["name"]: x.get("_via", x["ssh"][0]) for x in hosts}
    if thief_host["name"] not in byhost:
        return False
    tgt_thief = byhost[thief_host["name"]]
    remote = [s for s in specs if s[0] != thief_host["name"]]
    if not remote:
        return True
    try:
        mk = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", tgt_thief, "mkdir -p %s" % shlex.quote(os.path.dirname(remote[0][2]))], capture_output=True, timeout=60)
        if mk.returncode != 0:
            log.setdefault("steal_fetch_fail", {})[sym] = f"thief {thief_host['name']} mkdir rc={mk.returncode}"
            return False
        for src_host, src, dst in remote:
            if src_host not in byhost:
                return False
            tmp = f"/tmp/steal_{sym}_{os.path.basename(dst)}"
            r1 = subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes", f"{byhost[src_host]}:{src}", tmp], capture_output=True, timeout=120)
            if r1.returncode != 0 or not os.path.exists(tmp):
                log.setdefault("steal_fetch_fail", {})[sym] = f"pull {src_host}:{src} rc={r1.returncode}"
                return False
            try:
                _jd = json.load(open(tmp))
                _ov = _jd.get("cumulative_overrides") or {}
                _fg = _jd.get("final_gain")
                assert (_ov and isinstance(_ov, dict)) or isinstance(_fg, (int, float)), "no overrides and no final_gain yet"
            except Exception as _je:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                log.setdefault("steal_fetch_fail", {})[sym] = f"unstable source {src_host}:{os.path.basename(src)} ({_je}); retry next tick"
                return False
            r2 = subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes", tmp, f"{tgt_thief}:{dst}"], capture_output=True, timeout=120)
            try:
                os.unlink(tmp)
            except Exception:
                pass
            if r2.returncode != 0:
                log.setdefault("steal_fetch_fail", {})[sym] = f"push {thief_host['name']}:{dst} rc={r2.returncode}"
                return False
    except Exception as e:
        log.setdefault("steal_fetch_fail", {})[sym] = f"{e}"[:120]
        return False
    return True


def side_cmd(host, sym, side, window, pdir, attempt, workers):
    """shell body for ONE side job (30D pilot | 365D verify | REPAIR loop). Detached by pair_launch_cmd."""
    r, ss, venue = host["root"], f"{sym}_{side}", venue_of(sym)
    chain, t = f"{pdir}/../chain", tmpl_path(host, venue, side)
    if window == "30D":
        env = f"V15_START_OVERRIDES=$(test -f ~/v15_autopsy_first/{ss}_autopsy_base.json && echo ~/v15_autopsy_first/{ss}_autopsy_base.json) V15_FRESH_RUN=1 V15_TEMPLATE_DEFAULTS=1 V15_SKIP_CAT_PERSYM_BASELINE=1 V15_ADAPT_BASELINE=0 V15_SKIP_LIVE_AT_DONE=1 V15_POSSYM_SAMPLING=1 V15_UNWIRED_SKIP=0 V12_NPZ_CACHE=8 V15_PROGRESS_DIR={pdir} V15_DEFAULTS_ROUND=$(cat ~/v15_defaults_round.txt 2>/dev/null) CAT_SIDE_DEFAULTS_PATH=$(test -f ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json && echo ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json)"
        return f"{env} .venv/bin/python -u v15_pilot.py --sym-side {ss} --template {t} --seq-mode worst2best --window-days 30 --vector-only --workers {workers}"
    if window == "365D":
        return (f"mkdir -p {chain}/v365 && nice -n 10 .venv/bin/python -u tools/v15_365_cycle.py --sym-side {ss} --progress {pdir}/{ss}_v14_progress.json "
                f"--template {t} --work {chain}/v365 --rounds 0 --workers {workers}")
    if window == "GS":  # USER 2026-10-07: GS-heal adjuster stage (standalone runner, heal3+); gated by V15_GS_FLEET=1
        return (f"mkdir -p {chain}/gs_a{attempt} && nice -n 10 .venv/bin/python -u tools/v15_graph_search.py --symsides {ss} --out {chain}/gs_a{attempt} "
                f"--method gs --budget ${{V15_GS_BUDGET:-1200}} --workers {workers} --parallel 1 --progress-dirs {pdir},data/reports/lifecycle_pilot")
    prev = (f"{pdir}/{ss}_v14_progress.json" if attempt == 1 else
            f"$(.venv/bin/python -c \"import json;print(json.load(open('{chain}/repair_a{attempt-1}/{ss}_365_cycle.json'))['final_progress'])\")")
    return (f"mkdir -p {chain}/repair_a{attempt} && nice -n 10 .venv/bin/python -u tools/v15_365_cycle.py --sym-side {ss} --progress {prev} "
            f"--template {t} --work {chain}/repair_a{attempt} --rounds 3 --workers {workers}")


def pair_launch_cmd(host, actions, pdir, workers):
    """one ssh command that detaches every side job of a symbol (subshell > /dev/null so ssh returns at once)."""
    parts = []
    for a in actions:
        body = side_cmd(host, a["sym"], a["side"], a["window"], pdir, a.get("attempt", 1), workers)
        parts.append(f"(cd {host['root']} && mkdir -p logs && setsid nohup bash -c {shlex.quote(body)} >> /tmp/sweep_{a['sym']}_{a['side']}_{a['window']}.log 2>&1 < /dev/null &) > /dev/null 2>&1")
    return "; ".join(parts)


def npz_prep(host, symbol):
    """V15_SCHED_NPZ_REFRESH=1: klines gate + guarded NPZ regen (tools/v15_npz_prep.py) on the target host before a NEW stock pair launches. None = could not run (launch proceeds, gate is advisory on infra failure)."""
    out = sh_ssh(host, f"cd {host['root']} && .venv/bin/python tools/v15_npz_prep.py --symbol {shlex.quote(symbol)} 2>&1 | tail -1", 900)
    try:
        return json.loads((out or "").strip().splitlines()[-1])
    except Exception:
        return None


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(st):
    STATE.parent.mkdir(exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE)


def legacy_rank(cfg, venue):
    p = ROOT / cfg.get("order_dir", "data/reports/per_sym_recheck_20260929") / f"run_order_{venue}.txt"
    return {l.strip(): i for i, l in enumerate(open(p)) if l.strip()} if p.exists() else {}


def tick(args, cfg, now):
    hosts = cfg["hosts"]
    sim = json.load(open(args.simulate)) if args.simulate else None
    uni, how = U.load_or_build(ROOT, now, write=not (args.dry_run or sim))
    stocks, crypto = list(uni["stocks"]), list(uni["crypto"])
    rank = {v: legacy_rank(cfg, v) for v in ("stocks", "crypto")}
    for v, lst in (("stocks", stocks), ("crypto", crypto)):
        lst.sort(key=lambda s: (rank[v].get(s, 10 ** 6), s))  # worst-first per the previous order file, unknown last
    st = load_state()
    if st.get("date") != now.strftime("%Y%m%d"):
        st = {"date": now.strftime("%Y%m%d"), "held": {}, "attempts": {}, "ready": {}}
    st.setdefault("held", {}); st.setdefault("attempts", {}); st.setdefault("ready", {})
    stats = {h["name"]: (sim["hosts"].get(h["name"]) if sim else probe(h)) for h in hosts}
    reaped = {}
    if not (args.dry_run or sim or args.no_reap):
        for h_ in hosts:
            if not stats[h_["name"]]:
                continue
            r_ = host_py(h_, "reap", {"oom_mb": int(h_.get("oom_mb", 500)), "stall_min": int(h_.get("stall_min", 60)), "hard_min": int(h_.get("hard_min", 480)), "apply": True})
            if r_ and r_.get("actions"):
                reaped[h_["name"]] = r_["actions"]
                gone = {a_["ss"] for a_ in r_["actions"] if not a_["why"].startswith("orphaned")}
                for ss_ in gone:
                    stats[h_["name"]]["running"].pop(ss_, None)
                for k_, w_ in _reap_burns(r_["actions"], st.get("last_act")).items():
                    st["attempts"][k_] = st["attempts"].get(k_, 0) + w_
                for a_ in r_["actions"]:
                    print(f"[reap] {h_['name']} {a_['ss']} {a_['why']} pids={a_['pids']}", flush=True)
    cur_pdir = next((x["pdir"] for x in stats.values() if x and x.get("pdir")), None)
    if cur_pdir and st.get("attempts_pdir") != cur_pdir:
        st["attempts"] = {}
        st["attempts_pdir"] = cur_pdir  # SLOT fix 2026-10-01: day-level attempt counters carried over from the previous run's progress dir exhausted every chain action of the new run, so pairs held slots forever
    is_open, mto = us_market_open(now), minutes_to_open(now)
    vorder = venue_order(now)
    # ---- merge host views
    running, owner, started, done, quar, v365, repair, gs, gains = {}, {}, set(), set(), set(), {}, {}, {}, {}
    done_host, v365_host, rep_host = {}, {}, {}
    for n, s in stats.items():
        if not s:
            continue
        for ss, r in s["running"].items():
            running[ss] = {**r, "host": n}
            owner.setdefault(ss.rsplit("_", 1)[0], n)
        for ss in s["started"]:
            started.add(ss); owner.setdefault(ss.rsplit("_", 1)[0], n)
        for ss in s["done"]:
            done.add(ss); done_host.setdefault(ss, n)
        quar |= set(s.get("quarantined", []))
        for ss, v in s["v365"].items():
            if ss not in v365 or str(v.get("ts", "")) > str(v365[ss].get("ts", "")):
                v365[ss] = v; v365_host[ss] = n
        for ss, d in s["repair"].items():
            for k, v in d.items():
                _kk = (ss, int(k))
                if int(k) not in repair.get(ss, {}) or str(v.get("ts", "")) > str(repair[ss][int(k)].get("ts", "")):
                    repair.setdefault(ss, {})[int(k)] = v; rep_host[_kk] = n
        for ss, d in s.get("gs", {}).items():
            gs.setdefault(ss, {}).update({int(k): v for k, v in d.items()})
        for ss, g in s.get("gains", {}).items():
            try:
                _gmt = float((g or {}).get("mtime", 0) or 0)
            except (TypeError, ValueError):
                continue
            _cur = gains.get(ss)
            try:
                _curt = float((_cur or {}).get("mtime", 0) or 0)
            except (TypeError, ValueError):
                _curt = -1.0
            if _cur is None or _gmt > _curt:
                gains[ss] = {"gain": g.get("gain"), "mtime": _gmt}
    universe_syms = {"stocks": set(stocks), "crypto": set(crypto)}
    all_syms = stocks + crypto
    # ---- readiness (per host, only for symbols that could be placed there)
    ready_skip = {}
    readiness_by_host = {}
    for h in hosts:
        if not stats[h["name"]]:
            continue
        if sim:
            readiness_by_host[h["name"]] = sim.get("ready", {}).get(h["name"], {})
            continue
        cand = {}
        for v in h["venues"]:
            cand[v] = [s for s in (stocks if v == "stocks" else crypto)]
        rb = {}
        for v, syms in cand.items():
            rb.update({s: r for s, r in readiness(h, syms, v, st["ready"], now).items() if s in syms})
        readiness_by_host[h["name"]] = rb

    def is_ready(host_name, sym):
        r = readiness_by_host.get(host_name, {}).get(sym)
        if sim and r is None:
            return {"ok": True, "can_365": True, "size_mb": 300}
        return r or {"ok": False, "reason": "not probed"}

    # ---- per-symbol chain status
    def side_state(sym, side, host_name, _tiers=None):
        ss = f"{sym}_{side}"
        if ss in running:
            return "running", None
        if side == "SHORT" and venue_of(sym) == "stocks" and sym in NON_SHORTABLE:
            return "terminal_ok", None  # USER 2026-10-02: config_tradier NON_SHORTABLE: no SHORT compute, ever
        _qt = _quar_terminal(ss, quar, os.environ.get("V15_SCHED_NO_CHAIN") == "1" and not (ROOT / "data" / "autopilot" / "chain_mode.flag").exists())
        if _qt:
            return _qt
        if ss not in done:
            # USER 2026-10-08 ("flying through the test"): V15_SCHED_REQUIRE_BASE=1 -> a 30D board launches only once its autopsy ran
            # (pruned base with effective_switches = ~50x faster board, or an autopsy report = NO_RESCUE -> template start). Until then
            # the side WAITS (re-checked every tick); the s7 backfill feeds ~/v15_autopsy_first on this host.
            if os.environ.get("V15_SCHED_REQUIRE_BASE") == "1":
                _afd = pathlib.Path(os.path.expanduser("~/v15_autopsy_first"))
                _bp = _afd / f"{ss}_autopsy_base.json"
                try:
                    _ok = _bp.exists() and "effective_switches" in _bp.read_text()
                    if not _ok and not _bp.exists() and (_afd / f"{ss}_autopsy.json").exists():
                        # NO_RESCUE report: launchable from the template only if the template base actually trades — 83 STOCKS_LONG
                        # bases have 0 trades (2026-10-08 19:1xZ) and such a board is 3358 ZERO_TRADES rows for nothing (AAPL_SHORT).
                        _rep = json.load(open(_afd / f"{ss}_autopsy.json"))
                        _ok = int(((_rep.get("base") or {}).get("trades") or 0)) > 0
                except Exception:
                    _ok = False
                if not _ok:
                    return "waiting_base", None
            return "need30", 1
        if os.environ.get("V15_SCHED_NO_CHAIN") == "1" and not (ROOT / "data" / "autopilot" / "chain_mode.flag").exists():  # FINAL PHASE 2026-10-02: the autopilot creates this flag Mon 08:00Z -> 365D verify + REPAIR chains run
            return "terminal_ok", None  # USER 2026-10-01: 365D verify/REPAIR chains not needed now: release the slot after 30D
        rd = is_ready(host_name, sym) if host_name else {}
        if rd and rd.get("ok") and not rd.get("can_365", True):
            return "terminal_unverifiable", None
        v = v365.get(ss)
        if v is None:
            return "need365", 1
        if v["unverifiable"]:
            return "terminal_unverifiable", None
        if v["ok"]:
            return "terminal_ok", None
        if os.environ.get("V15_GS_FLEET") == "1":  # USER 2026-10-07: GS-heal is the primary adjuster (repair = fallback)
            g = gs.get(ss, {})
            lastg = g[max(g)] if g else None
            if lastg and lastg["ok"]:
                return "terminal_ok", None
            if not (lastg and lastg["unverifiable"]) and len(g) < 1:
                if not _gs_allowed_for_tier(_side_tier(ss, _tiers), ss in (_tiers or {})):
                    return "terminal_failing", None
                return "needgs", len(g) + 1
        reps = repair.get(ss, {})
        last = reps[max(reps)] if reps else None
        if last and last["ok"]:
            return "terminal_ok", None
        venue = venue_of(sym)
        late = venue == "stocks" and not is_open and mto < args.repair_cutoff_min
        if len(reps) >= args.max_attempts or late or (last and last["unverifiable"]):
            return "terminal_failing", None
        if not _repair_allowed_for_tier(_side_tier(ss, _tiers), ss in (_tiers or {})):
            return "terminal_failing", None
        return "needrepair", len(reps) + 1

    chain_state = {}
    _seen = st.setdefault("gain_seen", {})  # USER 2026-10-09: gain memory survives round rollover (latest mtime wins; >30d pruned)
    for ss, g in gains.items():
        _cur = _seen.get(ss)
        try:
            _curt = float((_cur or {}).get("mtime", 0) or 0)
        except (TypeError, ValueError):
            _curt = -1.0
        if _cur is None or float(g.get("mtime", 0) or 0) > _curt:
            _seen[ss] = {"gain": g.get("gain"), "mtime": g.get("mtime", 0)}
    _now_ts = now.timestamp()
    for ss in [k for k, v in _seen.items() if (_now_ts - float((v or {}).get("mtime", 0) or 0)) > 30 * 86400.0]:
        _seen.pop(ss, None)
    _gtiers = _gain_tiers(_seen)  # winners-first scheduling; loser tiers defer fresh boards (chains still drain)
    _deferred_sides = []
    for sym in all_syms:
        h = owner.get(sym)
        _allowed = set(uni.get("allowed_sym_sides") or [])  # USER 2026-10-06: only tradeable keys are calculated
        _st = {side: (("terminal_not_tradeable", None) if _allowed and f"{sym}_{side}" not in _allowed else side_state(sym, side, h, _gtiers)) for side in ("LONG", "SHORT")}
        for side, v in _st.items():
            if v[0] in ("need30", "waiting_base") and _side_deferred(f"{sym}_{side}", _gtiers, _now_ts):
                _st[side] = ("terminal_deferred", None)
                _deferred_sides.append(f"{sym}_{side}")
        chain_state[sym] = _st

    def terminal(sym):
        return all(v[0].startswith("terminal") for v in chain_state[sym].values())

    # ---- slots (held symbols per host): running + persisted-held, minus terminal
    held = {h["name"]: {} for h in hosts}
    for sym in all_syms:
        hn = _held_host(sym, owner, running)  # running host wins over 30D owner (stolen chain work)
        if not hn or hn not in held:
            continue
        if any(f"{sym}_{sd}" in running for sd in ("LONG", "SHORT")) and not terminal(sym):
            held[hn][sym] = "running"
        elif sym in st["held"].get(hn, {}) and not terminal(sym):
            held[hn][sym] = "chain"
    st["held"] = {hn: {s: st["held"].get(hn, {}).get(s, now.isoformat()) for s in d} for hn, d in held.items()}
    foreign = {ss: r for ss, r in running.items() if ss.rsplit("_", 1)[0] not in universe_syms["stocks"] | universe_syms["crypto"]}
    log = {"now": now.isoformat(), "market_open": is_open, "venue_order": vorder, "priority_syms": sorted(priority_syms()), "min_to_open": round(mto), "universe_source": how,
           "universe": uni["counts"], "reaped": reaped, "hosts": {}, "launched": [], "skipped_unready": {}, "foreign_running": {k: v["host"] for k, v in foreign.items()}}

    # ---- candidate work
    # (A) adopted chain work: owner host known, not terminal, not currently running anything for that side; (B) brand-new symbols
    def pending_actions(sym, host_name):
        acts = []
        for side, (s, att) in chain_state[sym].items():
            if s in ("need30", "need365", "needrepair", "needgs"):
                w = {"need30": "30D", "need365": "365D", "needrepair": "REPAIR", "needgs": "GS"}[s]
                key = f"{sym}_{side}|{w}{att if w in ('REPAIR', 'GS') else ''}"
                if st["attempts"].get(key, 0) >= _LAUNCH_CAPS[w]:  # USER 2026-10-09: 30D 6->3 (slices + free resume make more unnecessary); hopeless sides stop burning slots
                    continue
                acts.append({"sym": sym, "side": side, "window": w, "attempt": att or 1, "key": key})
        return acts

    released = {}
    for hn_ in list(held):
        for sym_ in list(held[hn_]):
            if held[hn_][sym_] == "chain" and not pending_actions(sym_, hn_):
                released[sym_] = hn_
                del held[hn_][sym_]
                st["held"].get(hn_, {}).pop(sym_, None)  # nothing running and every chain action exhausted: free the slot
    log["released_slots"] = released
    new_syms = [s for s in all_syms if s not in owner and not terminal(s)]
    vrank = {v: i for i, v in enumerate(vorder)}
    pri = priority_syms()
    _trk = lambda s: (_sym_tier_rank(s, _gtiers), sym_rank_key(s, vrank, all_syms, pri))  # USER 2026-10-09: winners first, losers last
    new_syms.sort(key=_trk)
    adopted = [s for s in all_syms if s in owner and not terminal(s)]
    adopted.sort(key=_trk)
    mapped_unready = {}
    HB, launched_keys = {}, set()
    for h in hosts:
        s = stats[h["name"]]
        if not s:
            log["hosts"][h["name"]] = "UNREACHABLE"
            continue
        cap = max(2, min(12, int(h.get("max_pairs", 3))))
        workers = int(h.get("workers_per_side", 3))
        cpu = float(s["busy_nonnice_pct"]) if s.get("busy_nonnice_pct") is not None else float(s["busy_pct"]) if s.get("busy_pct") is not None else 100.0 * s["load1"] / max(1, s["nproc"])  # real busy% (load1 lags and over-reads stalled threads)
        pairs = {}
        for ss, r in s["running"].items():
            pairs.setdefault(ss.rsplit("_", 1)[0], 0.0)
            pairs[ss.rsplit("_", 1)[0]] += r["pss_mb"]
        pair_measure = [v for k, v in pairs.items() if k in universe_syms["stocks"] | universe_syms["crypto"] and v > 0]
        est_pair = _est_pair_mb(pair_measure, cfg.get("pair_floor_mb", 12000))  # USER 2026-10-08 #2: AUTOPSY-INERT boards peak ~4GB/pair -> floor from cfg (measured max x1.25 still wins)
        reserve = max(h.get("mem_reserve_mb", 3000), int(h.get("oom_mb", 500)) + 2000)
        proj_mem = s["mem_avail_mb"] - reserve - _growth_debt_mb(pairs, est_pair)
        swap_ok = _swap_admit_ok(s)
        used = len(held[h["name"]])
        info = {"cpu": round(cpu), "cpu_real": round(float(s.get("busy_pct") or 0)), "mem_avail_mb": s["mem_avail_mb"], "slots": f"{used}/{cap}", "held": sorted(held[h["name"]]), "est_pair_mb": round(est_pair),
                "workers_per_side": workers, "launched_pairs": 0}
        launched = 0
        new_launched = 0
        chain_launched = 0
        running_cnt = sum(1 for v_ in held[h["name"]].values() if v_ == "running")  # concurrent pairs actually running: hard cap = cap (s1 OOM 13:48Z when 9 held pairs all ran)
        HB[h["name"]] = {"cap": cap, "workers": workers, "cpu": cpu, "est_pair": est_pair}
        # continue chains of held symbols first (no new slot); then admit adopted-but-unheld; then new
        held_ordered = sorted(held[h["name"]], key=_trk)
        nonw_used = sum(1 for s in held[h["name"]] if _sym_quota_hit(s, _gtiers))
        nonw_quota = max(1, cap // _NONW_QUOTA_DIV)  # USER 2026-10-09: winners own the host; non-winners get 1 new-pair slot (chains still drain, discovery exempt)
        order = _place_order(held_ordered, [sym for sym in adopted if owner.get(sym) == h["name"] and sym not in held[h["name"]]],
                             [sym for sym in new_syms if venue_of(sym) in h["venues"]], _gtiers)
        for sym, needs_slot in order:
            if needs_slot and new_launched >= args.max_launch:
                break  # budget counts only NEW pairs; chain continuations (365D/REPAIR) of held symbols never starve new admissions
            if needs_slot and (used >= cap or proj_mem < est_pair or cpu >= cfg.get("cpu_target_pct", 90) or not swap_ok):
                break
            if venue_of(sym) not in h["venues"]:
                continue
            if sym in owner and owner[sym] != h["name"]:
                continue
            if needs_slot and _sym_quota_hit(sym, _gtiers) and nonw_used >= nonw_quota:
                log.setdefault("skipped_quota", []).append(f"{h['name']}:{sym}")
                continue
            if not needs_slot and (proj_mem < est_pair / 2 or cpu >= 120 or not swap_ok):
                continue
            if not needs_slot and held[h["name"]].get(sym) != "running" and (running_cnt >= cap or chain_launched >= max(args.max_launch, 4)):
                continue
            rd = is_ready(h["name"], sym)
            if not rd.get("ok"):
                log["skipped_unready"][sym] = f"{h['name']}: {rd.get('reason')}"
                continue
            acts = pending_actions(sym, h["name"])
            acts = [a for a in acts if _repair_sticky_ok(a, rep_host, h["name"])]
            if not acts:
                continue
            if needs_slot and not _pair_gate_ok(sym, acts, owner, chain_state, st["attempts"]):  # fresh syms: every launchable side covered; terminal/capped sides don't block siblings
                continue
            if needs_slot and os.environ.get("V15_SCHED_NPZ_REFRESH") == "1" and venue_of(sym) == "stocks" and not (args.dry_run or sim):
                pr = npz_prep(h, sym)
                if pr is not None and not pr.get("ok"):
                    log["skipped_unready"][sym] = f"{h['name']}: npz_prep {pr.get('reason')}"
                    continue
            tag = f"{h['name']}:{sym}:" + "+".join(f"{a['side'][0]}{a['window']}" + (f"#a{a['attempt']}" if a["window"] == "REPAIR" else "") for a in acts)
            if args.dry_run or sim:
                log["launched"].append(tag + " (dry)")
                for a in acts:
                    launched_keys.add(a["key"])
            else:
                pd = s.get("pdir") or cfg.get("fallback_pdir")
                if not pd:
                    continue
                try:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", h.get("_via", h["ssh"][0]), pair_launch_cmd(h, acts, pd, workers)], timeout=40)
                except subprocess.TimeoutExpired:
                    print(f"[sched] launch ssh timeout {h['name']} {sym} (job may still have started; pgrep dedups)", flush=True)
                for a in acts:
                    st["attempts"][a["key"]] = st["attempts"].get(a["key"], 0) + 1
                    st.setdefault("last_act", {})[a["key"].split("|")[0]] = a["key"]
                    launched_keys.add(a["key"])
                log["launched"].append(tag)
            if needs_slot:
                used += 1; proj_mem -= est_pair + float(rd.get("size_mb") or 0)
                if _sym_quota_hit(sym, _gtiers):
                    nonw_used += 1
                held[h["name"]][sym] = "new"
                st["held"].setdefault(h["name"], {})[sym] = now.isoformat()
                owner[sym] = h["name"]
            launched += 1
            if needs_slot:
                new_launched += 1
            else:
                chain_launched += 1
                if held[h["name"]].get(sym) != "running":
                    running_cnt += 1
                    held[h["name"]][sym] = "running"
        info["launched_pairs"] = launched
        info["slots"] = f"{len(held[h['name']])}/{cap}"
        HB[h["name"]].update(used=len(held[h["name"]]), proj_mem=proj_mem, launched=launched, new_launched=new_launched, held_syms=sorted(held[h["name"]]))
        if cpu < cfg.get("cpu_target_pct", 90) and launched == 0:
            info["idle_reason"] = "slots full" if used >= cap else "mem guard" if proj_mem < est_pair else "no eligible ready symbol for this host"
        log["hosts"][h["name"]] = info
    # ---- pass 2 (USER 2026-10-08): steal adopted chain stages (365D, REPAIR-a1) onto hosts
    # with free capacity. Owner had first refusal in pass 1; the thief fetches the small
    # inputs tick-side (S1-relayed, no new keys) and holds the slot where the work RUNS.
    # 30D / GS / REPAIR-aN(N>=2) never steal (owner / rep-host sticky).
    pdirs = {n: (s.get("pdir") if s else None) for n, s in stats.items()}
    for h in hosts:
        s = stats[h["name"]]
        if not s or h["name"] not in HB:
            continue
        B = HB[h["name"]]
        for sym in adopted:
            if sym in owner and owner[sym] == h["name"]:
                continue
            if terminal(sym):
                continue
            acts = [a for a in pending_actions(sym, h["name"]) if a["key"] not in launched_keys and (a["window"] == "365D" or (a["window"] == "REPAIR" and a["attempt"] == 1))]
            if not acts:
                continue
            if B["new_launched"] >= args.max_launch or len(held[h["name"]]) >= B["cap"] or B["proj_mem"] < B["est_pair"] or B["cpu"] >= cfg.get("cpu_target_pct", 90):
                break
            if venue_of(sym) not in h["venues"]:
                continue
            rd = is_ready(h["name"], sym)
            if not rd.get("ok"):
                continue
            if not _pair_gate_ok(sym, acts, owner, chain_state, st["attempts"]):
                continue
            pd = s.get("pdir") or cfg.get("fallback_pdir")
            if not pd:
                continue
            specs = _fetch_specs(sym, acts, pd, done_host, pdirs)
            if specs is None:
                continue
            tag = f"{h['name']}:{sym}:" + "+".join(f"{a['side'][0]}{a['window']}" for a in acts) + "[steal]"
            if args.dry_run or sim:
                log["launched"].append(tag + " (dry)")
                for a in acts:
                    launched_keys.add(a["key"])
            else:
                if specs and not _steal_fetch(hosts, h, sym, specs, log):
                    continue
                try:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", h.get("_via", h["ssh"][0]), pair_launch_cmd(h, acts, pd, B["workers"])], timeout=40)
                except subprocess.TimeoutExpired:
                    print(f"[sched] steal launch ssh timeout {h['name']} {sym} (job may still have started; pgrep dedups)", flush=True)
                for a in acts:
                    st["attempts"][a["key"]] = st["attempts"].get(a["key"], 0) + 1
                    st.setdefault("last_act", {})[a["key"].split("|")[0]] = a["key"]
                    launched_keys.add(a["key"])
                log["launched"].append(tag)
            B["proj_mem"] -= B["est_pair"] + float(rd.get("size_mb") or 0)
            held[h["name"]][sym] = "new"
            st["held"].setdefault(h["name"], {})[sym] = now.isoformat()
            owner[sym] = h["name"]
            B["new_launched"] += 1
            B["launched"] += 1
            if isinstance(log["hosts"].get(h["name"]), dict):
                log["hosts"][h["name"]]["launched_pairs"] = log["hosts"][h["name"]].get("launched_pairs", 0) + 1
                log["hosts"][h["name"]]["slots"] = f"{len(held[h['name']])}/{B['cap']}"
                log["hosts"][h["name"]].setdefault("held", []).append(sym)
                log["hosts"][h["name"]].pop("idle_reason", None)
    # ---- summary per cat
    cats = {c: {"sym_sides": 0, "done30": 0, "running": 0, "v365_ok": 0, "v365_bad": 0, "v365_unverifiable": 0, "repair_attempts": 0, "repair_ok": 0, "gs_attempts": 0, "gs_ok": 0, "failing": []} for c in CATS}
    for sym in all_syms:
        for side in ("LONG", "SHORT"):
            ss, c = f"{sym}_{side}", f"{'CRYPTO' if venue_of(sym) == 'crypto' else 'STOCKS'}_{side}"
            x = cats[c]
            x["sym_sides"] += 1
            x["done30"] += ss in done
            x["running"] += ss in running
            s_ = chain_state[sym][side][0]
            v = v365.get(ss)
            x["v365_unverifiable"] += s_ == "terminal_unverifiable"
            if v and not v["unverifiable"]:
                x["v365_ok"] += bool(v["ok"]); x["v365_bad"] += not v["ok"]
            x["repair_attempts"] += len(repair.get(ss, {}))
            x["repair_ok"] += s_ == "terminal_ok" and bool(v) and not v["ok"]
            _gd = gs.get(ss, {})
            x["gs_attempts"] += len(_gd)
            x["gs_ok"] += bool(_gd) and bool(_gd[max(_gd)]["ok"])
            if s_ == "terminal_failing":
                x["failing"].append(ss)
    for c in cats.values():
        c["unlocked_365"] = True
    log["cats"] = cats
    _tl = {"W": 0, "M": 0, "L": 0}
    for _t in log["launched"]:
        try:
            _tl[{0: "W", 1: "M", 2: "L"}[_sym_tier_rank(str(_t).split(":")[1], _gtiers)]] += 1
        except Exception:
            pass
    log["tier_launched"] = _tl  # USER 2026-10-09: prove ~90% of launches go to winners (M 3d / L 7d defer + slot quota)
    log["gain_tiers"] = {"measured_sides": len(_gtiers), **{t: sum(1 for e in _gtiers.values() if e["tier"] == t) for t in "WML"},
                         "deferred_sides": len(_deferred_sides), "deferred_sample": _deferred_sides[:30]}
    log["pending_symbols"] = sum(1 for s in all_syms if not terminal(s))
    log["stocks_chain_left"] = sum(1 for s in stocks if not terminal(s))
    st["pend_same"], _stall_warn = _stall_tick(st.get("pend_last"), st.get("pend_same"), log["pending_symbols"])
    st["pend_last"] = log["pending_symbols"]
    if _stall_warn:
        log["stall_warning"] = "pending %d unchanged %d ticks (~%.1fh of zero drain)" % (log["pending_symbols"], st["pend_same"], st["pend_same"] / 30.0)
    if not (sim or args.dry_run):
        try:  # AUTOPILOT 2026-10-02: compact per-tick snapshot for tools/v15_autopilot.py + tools/v15_npz_keeper.py (atomic)
            snap = {"at": now.isoformat(), "pdir": cur_pdir, "stocks": stocks, "crypto": crypto, "done": sorted(done), "running": sorted(running), "quarantined": sorted(quar),
                    "pending_stocks": [s for s in stocks if not terminal(s)], "pending_crypto": [s for s in crypto if not terminal(s)],
                    "non_shortable": sorted(NON_SHORTABLE & set(stocks)), "skipped_unready": log["skipped_unready"], "launched": log["launched"], "cats": cats,
                    "hosts": log["hosts"], "released_slots": log.get("released_slots", {})}
            ap_dir = ROOT / "data" / "autopilot"
            ap_dir.mkdir(parents=True, exist_ok=True)
            tmp_ = ap_dir / "sched_snapshot.tmp"
            tmp_.write_text(json.dumps(snap))
            tmp_.replace(ap_dir / "sched_snapshot.json")
        except Exception as e_:
            print(f"[sched] snapshot write failed: {e_}", flush=True)
    log["ready_summary"] = {}
    for hn, rb in readiness_by_host.items():
        hist = {}
        for sym_, r_ in rb.items():
            k = "ok" if r_.get("ok") else re.sub(r"[\d.]+", "#", str(r_.get("reason")))
            hist[k] = hist.get(k, 0) + 1
        log["ready_summary"][hn] = hist
    if log["stocks_chain_left"] and not is_open and mto < 60:
        log["WARN"] = f"{log['stocks_chain_left']} stock symbols unfinished {mto:.0f} min before open (partial apply must be flagged)"
    if not sim:
        if not args.dry_run:
            st["ready"] = {h: {s: r for s, r in d.items()} for h, d in st["ready"].items()}
            save_state(st)
            d = DAILY / now.strftime("%Y%m%d")
            d.mkdir(parents=True, exist_ok=True)
            (d / "chain_state.json").write_text(json.dumps(log, indent=1))
            pull_chain(hosts, stats, now)
    if args.extend_universe:
        log["extend_universe"] = "on (extension stage not reached)" if log["pending_symbols"] else "base universe complete — extension symbols listed in data/daily_universe/extension_<date>.json"
        if not log["pending_symbols"] and not args.dry_run and not sim:
            ext = {"stocks": [s for s in rank["stocks"] if s not in universe_syms["stocks"]], "crypto": [s for s in rank["crypto"] if s not in universe_syms["crypto"]]}
            (ROOT / "data" / "daily_universe" / f"extension_{now.strftime('%Y%m%d')}.json").write_text(json.dumps(ext, indent=1))
    return log


def pull_chain(hosts, stats, now):
    d = DAILY / now.strftime("%Y%m%d")
    for h in hosts:
        pd = (stats.get(h["name"]) or {}).get("pdir")
        if pd:
            (d / "chain" / h["name"]).mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes", "--include=*/", "--include=*.json", "--exclude=*",
                                f"{h.get('_via', h['ssh'][0])}:{pd}/../chain/", str(d / "chain" / h["name"]) + "/"], capture_output=True, timeout=90)
            except Exception:
                pass


def kill_out_of_universe(args, cfg, now):
    uni, _ = U.load_or_build(ROOT, now, write=False)
    keep = set(uni["stocks"]) | set(uni["crypto"])
    for h in cfg["hosts"]:
        p = probe(h)
        if not p or not p.get("pdir"):
            print(h["name"], "UNREACHABLE/no pdir"); continue
        bad = sorted({ss.rsplit("_", 1)[0] for ss, r in p["running"].items() if ss.rsplit("_", 1)[0] not in keep and r.get("pdir") == p["pdir"]})
        other = sorted({ss for ss, r in p["running"].items() if r.get("pdir") != p["pdir"]})
        res = host_py(h, "kill", {"symbols": bad, "pdir": p["pdir"], "apply": bool(args.apply_kill)}) if bad else {"apply": False, "pids": []}
        print(json.dumps({"host": h["name"], "pdir": p["pdir"], "out_of_universe_symbols": bad, "kill": res, "left_alone_other_pdir": other, "in_universe_running": sorted(ss for ss in p["running"] if ss.rsplit('_', 1)[0] in keep)}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-reap", action="store_true", help="skip the OOM/stuck/orphan reaper (ported from the retired v15_local_herd)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--simulate")
    ap.add_argument("--now")
    ap.add_argument("--hosts", default=str(HOSTS))
    ap.add_argument("--windows", default="30D", help="ignored (kept for cron compatibility)")
    ap.add_argument("--max-launch", type=int, default=2, help="max NEW pair launches per host per tick (no ramp)")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--max-attempts", type=int, default=4, help="REPAIR attempts per sym_side (each = v15_365_cycle --rounds 3)")
    ap.add_argument("--repair-cutoff-min", type=int, default=20, help="no new STOCK repair attempts this close to the open")
    ap.add_argument("--extend-universe", action="store_true", help="after the base universe is fully terminal, emit the legacy-order extension list (OFF by default)")
    ap.add_argument("--kill-out-of-universe", action="store_true", help="list pilots of the current progress dir whose symbol is not in the universe")
    ap.add_argument("--apply-kill", action="store_true", help="with --kill-out-of-universe: actually SIGTERM them")
    args = ap.parse_args()
    cfg = json.load(open(args.hosts))
    now0 = datetime.datetime.fromisoformat(args.now).astimezone(datetime.timezone.utc) if args.now else datetime.datetime.now(datetime.timezone.utc)
    if args.kill_out_of_universe:
        return kill_out_of_universe(args, cfg, now0)
    lk = open("/tmp/v15_fleet_scheduler.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[fleet] another scheduler holds the lock")
        return
    while True:
        now = datetime.datetime.fromisoformat(args.now).astimezone(datetime.timezone.utc) if args.now else datetime.datetime.now(datetime.timezone.utc)
        log = tick(args, cfg, now)
        print(json.dumps(log), flush=True)
        if args.once or args.simulate:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
