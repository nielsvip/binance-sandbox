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
import v15_ranking_priority as _RANKPRI  # noqa: E402  # USER 2026-10-14: ranking top-N queue priority (order+frequency only)

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
    # USER 2026-10-15 (corrected): stocks keep their guaranteed DAILY pass, and a post-close NPZ rebuilt to the
    # session's final bar IS current (no newer bars exist), so the night is not a reason to deprioritise stocks.
    # Ordering therefore stays as it was; what changed is (a) a real crypto PHASE for after the stock pass is
    # done (_venue_phase) and (b) a session-relative freshness bar (npz_tail_limit_h).
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


# ---------------------------------------------------------------- host-side helper (runs on the host under the conda
# live env /home/niels/.conda/envs/binance_env_mac — USER 2026-10-17 "we use the conda venv for live trading always ...
# make all backtests on s1 and s2 use conda venv as well ... backtest scripts also need to run the live trading scripts
# so an incomplete venv screws that up as well". Proven numerically free first: 16 sym_sides identical under .venv
# (py3.12/numpy 2.5.3) and binance_env_mac (py3.11.14/numpy 2.4.6) on BOTH S1 and S2, 0 NaN.)
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


_PSS_FALLBACKS = [0]  # 2026-10-20: counts RSS fallbacks (smaps_rollup unreadable) so the tick log can say which measure was used


def pss_mb(pid):
    try:
        for l in open("/proc/%d/smaps_rollup" % pid):
            if l.startswith("Pss:"):
                return int(l.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        _PSS_FALLBACKS[0] += 1
        return int(open("/proc/%d/statm" % pid).read().split()[1]) * 4096 / 1048576.0
    except Exception:
        return 0.0


def _progress_done_epoch(path):
    """Epoch of the board's result_done_utc (stamped on publish AND on refusal), else None. Never raises."""
    try:
        import datetime as _dtm
        _t = json.loads(open(path).read()).get("result_done_utc")
        return _dtm.datetime.fromisoformat(str(_t).replace("Z", "+00:00")).timestamp() if _t else None
    except Exception:
        return None


def _progress_marks(path):
    """(done, quarantined, gain, redo) from a progress JSON. TOP-LEVEL ONLY (USER 2026-10-10,
    1-result-per-2h stall): the old flat "final_gain" regex matched endgame.final_gain (a
    mid-run STAGE gain), marking every endgame-reaching board done so its RULE#3 REDO refill
    never relaunched. needs_redo boards are never done: they must resume."""
    try:
        _jd = json.loads(open(path).read())
    except Exception:
        return False, False, None, False
    if not isinstance(_jd, dict):
        return False, False, None, False
    _fg = _jd.get("final_gain")
    _fg_ok = isinstance(_fg, (int, float)) and not isinstance(_fg, bool)
    # 2026-10-14 SOFT-SKIP loop (42% of 24h launches): a board with a terminal verdict AND a final gain whose needs_redo is
    # still set was relaunched every ~4 min on its frozen NPZ, and the pilot soft-skipped it every time (same NPZ). Terminal
    # verdict + final gain = done; the daily refresh re-measures it on a fresh NPZ. V15_SCHED_SOFTSKIP_DONE=0 restores the old rule.
    _terminal = _jd.get("verdict") in ("IMPOSSIBLE", "NO_TRADES", "BEST_EFFORT") and __import__("os").environ.get("V15_SCHED_SOFTSKIP_DONE", "1") == "1"
    _done = bool(_fg_ok and (not _jd.get("needs_redo") or _terminal))
    _q = _jd.get("verdict") in ("IMPOSSIBLE", "NO_TRADES", "BEST_EFFORT")
    # 2026-10-16: the 10-14 fix marked these done but still reported redo=True, so _cont_sides kept them as "unfinished
    # continuations": measured 10-16 on S2, ENAUSDC/JASMY/FILUSDC/DUSK/AMAT/QBTS/ZCSH/LRCX launched 40x each (the side
    # soft-skipped instantly, its finished sibling re-ran every time — 415 sibling re-boards in one day).
    return _done, _q, (float(_fg) if _fg_ok else None), bool(_jd.get("needs_redo")) and not (_fg_ok and _terminal)


def environ(pid):
    try:
        return dict(x.split("=", 1) for x in open("/proc/%d/environ" % pid, "rb").read().decode("utf8", "replace").split("\0") if "=" in x)
    except Exception:
        return {}


def _pilot_is_foreign(env, prod_pdir):
    """Pure: True = this pilot process belongs to a run the fleet does not own, so the reaper must leave it alone.
    Foreign = an isolated/bench run (V15_ISOLATED_PROOF=1 or its own V15_OUT_DIR) or a V15_PROGRESS_DIR other than
    the host's current round dir. A pilot with no V15_PROGRESS_DIR and no isolation marker is this round's."""
    env = env or {}
    if str(env.get("V15_ISOLATED_PROOF") or "") == "1" or env.get("V15_OUT_DIR"):
        return True
    pd = env.get("V15_PROGRESS_DIR")
    if not pd or not prod_pdir:
        return False
    return os.path.normpath(str(pd)).rstrip("/") != os.path.normpath(str(prod_pdir)).rstrip("/")


def pilots():
    """python processes of v15_pilot / v15_365_cycle with --sym-side: {ss: {pss_mb, pids, prod_pids, kind, pdir}}
    (workers included). `pids` is every process of that sym_side — its RAM counts toward the host either way.
    `prod_pids` is only the processes of THIS round's progress dir; everything else is a foreign run.

    2026-10-15 REAP SAFETY (measured): the group was keyed on --sym-side alone, so a foreign run of the same
    sym_side (S2 version-gate bench champion+candidate, tools/v15_throughput_proof arms, an older round's
    resume) landed in the same group. The reaper then saw several "roots" and TERMed the ones that momentarily
    had no linked worker as "orphaned workers (parent gone)" — it killed the gate bench's CHRUSDT_LONG champ and
    cand (gate verdict came back gain=None / 0.0 fit+OOS) and both arms of a DOTUSDT_LONG A/B on s2. The fleet
    must never kill a run it does not own; `mode == "kill"` already scopes itself to the current pdir this way."""
    r = {}
    prod_pdir = None
    try:
        prod_pdir = (open(HOME + "/v15_current_progress_dir.txt").read().strip() or None) if os.path.exists(HOME + "/v15_current_progress_dir.txt") else None
    except Exception:
        prod_pdir = None
    for pid, a in procs():
        if not os.path.basename(a[0]).startswith("python") or "--sym-side" not in a:
            continue
        kind = next((s for s in SCRIPTS if any(s in x for x in a)), None)
        if not kind:
            continue
        ss = a[a.index("--sym-side") + 1]
        e = r.setdefault(ss, {"pss_mb": 0.0, "pids": [], "prod_pids": [], "kind": kind, "pdir": None})
        _fb0 = _PSS_FALLBACKS[0]
        e["pss_mb"] += pss_mb(pid)
        if _PSS_FALLBACKS[0] != _fb0:
            e["mem_source"] = "rss-fallback"
        e["pids"].append(pid)
        env = environ(pid)
        pd = env.get("V15_PROGRESS_DIR")
        if _pilot_is_foreign(env, prod_pdir):
            continue
        e["prod_pids"].append(pid)
        if e["pdir"] is None:
            e["pdir"] = pd
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
    try:
        _sv = os.statvfs(HOME)
        dsk = int(_sv.f_bavail * _sv.f_frsize / 1048576)
    except Exception:
        dsk = None
    def _cpu_t():
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(v), v[3] + v[4], v[1]
    _t0, _i0, _n0 = _cpu_t()
    time.sleep(1.0)
    _t1, _i1, _n1 = _cpu_t()
    busy = round(100.0 * (1 - (_i1 - _i0) / max(1, _t1 - _t0)), 1)
    busy_nn = round(100.0 * (1 - ((_i1 - _i0) + (_n1 - _n0)) / max(1, _t1 - _t0)), 1)
    o = {"nproc": os.cpu_count(), "load1": float(open("/proc/loadavg").read().split()[0]), "busy_pct": busy, "busy_nonnice_pct": busy_nn, "mem_avail_mb": mem, "pdir": pdir, "swap_total_mb": swp_t, "swap_used_mb": swp_u, "disk_avail_mb": dsk,
         "running": pilots(), "started": [], "done": [], "quarantined": [], "redo": [], "v365": {}, "repair": {}, "gs": {}, "gains": {}}
    if pdir and os.path.isdir(pdir):
        cache_p = "/tmp/v15_sched_done_cache_v3.json"  # v3 (2026-10-16): redo flag semantics changed (terminal soft-skip != redo); v2 (2026-10-10): v1 cached flat-regex done verdicts poisoned by nested endgame.final_gain
        try:
            cache = json.load(open(cache_p))
        except Exception:
            cache = {}
        for f in glob.glob(os.path.join(pdir, "*_v14_progress.json")):
            ss = os.path.basename(f)[:-len("_v14_progress.json")]
            o["started"].append(ss)
            try:
                stt = os.stat(f)
                o.setdefault("started_mt", {})[ss] = stt.st_mtime
                key = "%s|%s|%s" % (f, stt.st_mtime, stt.st_size)
                if key not in cache or key + "|q" not in cache or key + "|g" not in cache or key + "|r" not in cache or key + "|t" not in cache:
                    cache = {k: v for k, v in cache.items() if not k.startswith(f + "|")}
                    _done, _q, _g, _r = _progress_marks(f)
                    cache[key] = _done
                    cache[key + "|q"] = _q
                    cache[key + "|g"] = _g
                    cache[key + "|r"] = _r
                    cache[key + "|t"] = _progress_done_epoch(f)
                if cache.get(key + "|t"):
                    o.setdefault("boarded_at", {})[ss] = cache[key + "|t"]
                if cache[key]:
                    o["done"].append(ss)
                    if cache.get(key + "|g") is not None:
                        o["gains"][ss] = {"gain": cache[key + "|g"], "mtime": stt.st_mtime}
                if cache.get(key + "|q"):
                    o["quarantined"].append(ss)
                if cache.get(key + "|r"):
                    o["redo"].append(ss)
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
                        # USER LAW 2026-10-20 crypto backstop 336 h -> 1.0 h (a bar one behind peaks at 0.75 h; tail_currency above is the exact rule). AUTOPILOT 2026-10-02: crypto NPZ tail refresh is not automatic (NPZB loop only); 48h would stop every crypto launch while the user is away.
                        # Stocks 120 h -> 26 h (USER 2026-10-20 "no more backtests can run on old npz's"): this backstop is the ONLY bar left
                        # when the tail_currency import below fails, and five days of staleness is not a backstop — one session plus a weekend
                        # margin is. 26 h still clears a normal overnight and a post-close rebuild; a longer gap is a rebuild, not a tail append.
                        max_age = 1.0 if arg.get("venue") == "crypto" else float(os.environ.get("V15_SCHED_STOCK_AGE_BACKSTOP_H", "26"))
                        par = t15[(t15 > 0) & np.isfinite(t15)]
                        ev = int((np.r_[True, par[1:] != par[:-1]]).sum()) if len(par) else 0
                        lag_min = float(np.nanmin(t - t15))
                        # base dt (300s = 5m-base) is recorded, NOT a blocker: the sweep loader evaluate_v12._compact_to_15m works on the
                        # timestamp_15m parent array and the old tick produced valid sheets from 5m-base stock NPZs.
                        # USER 2026-10-12 dispatch gate: tail must be complete to the latest bar (crypto: last closed 15m;
                        # stocks: last RTH close) BEFORE dispatch. Single source tools/npz_cutoffs.tail_currency. Import
                        # failure falls back to the age_h backstop below (never worse than today) and is reported loudly.
                        _tail_ok, _tail_why, _tail_enforced = True, "cutoffs-unavailable", False
                        try:
                            from tools.npz_cutoffs import tail_currency as _tail_currency
                            _tail_ok, _tail_why = _tail_currency(float(t[-1]) / k, arg.get("venue"), now)
                            _tail_enforced = True
                        except Exception as _tail_e:
                            _tail_why = "cutoffs-import-fail %s" % str(_tail_e)[:60]
                        r["tail_check"] = ("enforced " if _tail_enforced else "fallback ") + _tail_why
                        if ev < 10 or lag_min < 0:  # mirrors evaluate_v12._compact_to_15m
                            r["reason"] = "_compact_to_15m would raise (events=%d, lag_min=%.0f)" % (ev, lag_min)
                        elif span < arg["min_span_30d"]:
                            r["reason"] = "history %.1fd < %.0fd" % (span, arg["min_span_30d"])
                        elif _tail_enforced and not _tail_ok:
                            r["reason"] = "tail-stale: %s" % _tail_why
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
        # 2026-10-15: only the pids of THIS round's progress dir are reapable (see pilots()). A group that is
        # entirely foreign (gate bench / throughput proof / older round) is skipped — its RAM still counts in the
        # probe's pss_mb, the fleet just never kills it.
        g = dict(g, pids=list(g.get("prod_pids") or []))
        if not g["pids"]:
            continue
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
        if i["age"] > arg["hard_min"] * 60 and i["idle"] > 20 * 60:  # 2026-10-13 RESUME (d): a pilot still writing rows/logs is never hard-capped
            kill(i["pids"], "hardcap %.0fmin (idle %.0fmin)" % (i["age"] / 60, i["idle"] / 60), ss); dead.add(ss)
        elif i["age"] > arg["stall_min"] * 60 and i["idle"] > arg["stall_min"] * 60:
            kill(i["pids"], "stuck: no progress/log/xlsx write for %.0fmin" % (i["idle"] / 60), ss); dead.add(ss)
        elif len(i["roots"]) > 1 and sum(1 for r in i["roots"] if i["kids"][r]) == 1:
            orph = [r for r in i["roots"] if not i["kids"][r]]
            kill(orph, "orphaned workers (parent gone)", ss)
        elif i["roots"] and all(i["kids"].get(r, 0) == 0 for r in i["roots"]) and i["idle"] > 20 * 60 and i.get("min_age", 0) > 15 * 60:
            kill(i["pids"], "wedged: no linked workers, no output for %.0fmin" % (i["idle"] / 60), ss); dead.add(ss)  # USER 2026-10-07: S1 UNI pair wedged 30+ min (0 CPU, slots held, nothing finished) — auto-clear instead of waiting for the 60-min stall trip. 2026-10-08: min_age grace — piped/starting pilots have no kids + stale log cands for minutes; TERMing a 23s-old healthy pilot churns (KSMUSDT_SHORT).
    if mem < arg["oom_mb"]:
        _rov, _pdr, _pir = None, None, None
        try:
            sys.path.insert(0, os.path.join(HOME, "binance-sandbox", "tools"))
            from v15_fleet_scheduler import _rank_oom_victim as _rov, _progress_done_rows as _pdr, _progress_is_refill as _pir
        except Exception:
            _rov, _pdr, _pir = None, None, None
        try:
            from v15_fleet_scheduler import _progress_pinpoint_lifting as _ppl
        except Exception:
            _ppl = None
        _victim = None
        if _rov is not None and _pdr is not None:
            try:
                _cands = []
                for _ss, _i in info.items():
                    if _ss in dead:
                        continue
                    _pd = (groups.get(_ss) or {}).get("pdir") or ""
                    _pp = os.path.join(_pd, _ss + "_v14_progress.json") if _pd else ""
                    _done = _pdr(_pp) if _pp else None
                    _cands.append((_ss, _done, _i["age"], bool(_pir and _pp and _pir(_pp)), bool(_ppl and _pp and _ppl(_pp))))
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
    # arg: {symbols:[...out-of-universe...], pdir:..., apply:bool, sym_sides:[...exact...] (optional)}. Only processes that belong to the CURRENT progress dir
    # (env V15_PROGRESS_DIR or cmdline V15_PROGRESS_DIR=<pdir>) — old allcells pilots in other dirs are left alone.
    # USER 2026-10-10 ZERO WASTE: sym_sides allows sym_side-precise kills (never kills the good side of a half-allowed symbol). Old callers send symbols only — unchanged behavior.
    _syms = arg.get("symbols") or []
    _sss = arg.get("sym_sides") or []
    pat = re.compile(r"--sym-side (%s)_(LONG|SHORT)(\s|$)" % "|".join(re.escape(s) for s in _syms)) if _syms else None
    pat2 = re.compile(r"--sym-side (%s)(\s|$)" % "|".join(re.escape(s) for s in _sss)) if _sss else None
    hit = []
    for pid, a in procs():
        line = " ".join(a)
        m1 = pat.search(line) if pat else None
        m2 = pat2.search(line) if pat2 else None
        if (not m1 and not m2) or not any(s in line for s in SCRIPTS):
            continue
        if ("V15_PROGRESS_DIR=" + arg["pdir"]) not in line and environ(pid).get("V15_PROGRESS_DIR") != arg["pdir"]:
            continue
        _hit_ss = m2.group(1) if m2 else (m1.group(1) + "_" + m1.group(2))
        hit.append((pid, _hit_ss, os.path.basename(a[0])))
    if arg.get("apply"):
        for pid, _, _ in hit:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    print(json.dumps({"apply": bool(arg.get("apply")), "pids": [[p, s, k] for p, s, k in hit]}))
'''


def host_py(host, mode, arg, timeout=90):
    cmd = f"cd {host['root']} && /home/niels/.conda/envs/binance_env_mac/bin/python - {mode} {shlex.quote(json.dumps(arg))} <<'PYE'\n{HOST_PY}\nPYE"
    out = sh_ssh(host, cmd, timeout)
    try:
        return json.loads((out or "").strip().splitlines()[-1])
    except Exception:
        return None


_VERDICT_TERMINAL_RE = re.compile(r'"verdict":\s*"(IMPOSSIBLE|NO_TRADES|BEST_EFFORT)"')

_LAUNCH_CAPS = {"30D": 3, "365D": 2, "REPAIR": 1, "GS": 1}  # USER 2026-10-09: single source of truth (gate + pending_actions); 30D 6->3, 40-min slices + free resume make more unnecessary
_SLICE_RESUME_CAP = 20  # USER 2026-10-12: sliced-board resume budget (BG_SHORT stalled 23+ min at 3/3 attempts with 1769/3383 rows done — slices are resumes, not fresh attempts; 20x40min bounds any board, crash-loops still die at 20 quick spawns)


def _launch_exhausted(key, window, ss, st, started, redo, pinned=None):
    """True = no launch budget left. Redo resumes free; started (sliced) 30D sides draw the slice budget; everything else draws attempts.
    Pinned 30D sides (operator queue) draw slices+attempts against _PIN_SLICE_CAP only (crash loops still stop)."""
    if window == "30D" and ss in (pinned or set()):
        return (st.get("slices", {}) or {}).get(key, 0) + (st.get("attempts", {}) or {}).get(key, 0) >= _PIN_SLICE_CAP
    if window == "30D" and ss in (redo or set()):
        return False
    if window == "30D" and ss in (started or set()):
        return (st.get("slices", {}) or {}).get(key, 0) >= _SLICE_RESUME_CAP
    return (st.get("attempts", {}) or {}).get(key, 0) >= _LAUNCH_CAPS[window]


def _burn_launch(key, window, ss, st, started, n=1):
    """Burn the right budget: slice counter for started-30D resumes (attempts frozen — rotation keeps near-done boards early), attempts otherwise."""
    if window == "30D" and ss in (started or set()):
        st.setdefault("slices", {})[key] = st.get("slices", {}).get(key, 0) + n
    else:
        st.setdefault("attempts", {})[key] = st.get("attempts", {}).get(key, 0) + n
_VENUE_ONLY = os.environ.get("V15_SCHED_VENUE_ONLY", "").strip().lower()  # TEMPORARY USER 2026-10-10: stocks-only weekend push (all stocks rereun before Monday) — gates NEW pairs only; held chains drain, running pilots untouched. Unset/unknown = no gate.
def _deny_tokenized(syms):
    """USER 2026-10-13: drop symbols in data/excluded_tokenized_stocks.json (Binance TRADIFI_PERPETUAL). Missing module -> no-op."""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from excluded_tokenized_stocks import filter_syms
    except ImportError:
        return list(syms)
    return filter_syms(syms, ROOT)


_TRADEABLE_UNI = {"at": 0.0, "uni": None, "src": []}


def _jload(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return default


def _tradeable_universe():
    """USER 2026-10-15: "anything not in symbols_trb_long/short.json or mandatory long/short (stocks) or not in the
    latest tradeable_keys should not be traded nor calculated or use any cpu in any way." Same universe the director
    builds (tools/v15_director.py:199-242) — symbol-level, tradeable on EITHER side keeps the symbol, side-exactness
    stays this scheduler's job. Cached 120 s (the --once tick runs every 2 min). FAIL-OPEN: a missing or unreadable
    file returns None and the caller skips the filter — an unreadable file must never empty the dispatch queue."""
    if _TRADEABLE_UNI["uni"] is not None and time.time() - _TRADEABLE_UNI["at"] < 120:
        return _TRADEABLE_UNI["uni"], _TRADEABLE_UNI["src"]
    uni, src = set(), []
    try:
        for f, lab in (("symbols_trb_long.json", "trb_long"), ("symbols_trb_short.json", "trb_short")):
            v = _jload(ROOT / f, None)
            if isinstance(v, list):
                uni |= {str(x).upper() for x in v}
                src.append(f"{lab}:{len(v)}")
        try:
            sys.path.insert(0, str(ROOT))
            from config_tradier import TradierConfig as _TC
            c = _TC()
            for a in ("TRADIER_MANDATORY_LONG_TRB", "TRADIER_MANDATORY_SHORT_TRB"):
                m = getattr(c, a, None) or []
                uni |= {str(x).upper() for x in m}
                src.append(f"{a[18:].lower()}:{len(m)}")
        except Exception as me:
            print(f"[sched] mandatory stock lists unavailable: {me}", flush=True)
        tk = _jload(ROOT / "tradeable_keys.json", None)
        if isinstance(tk, list):
            for k in tk:
                uni.add(str(k).split(":", 1)[-1].rsplit("_", 1)[0].upper())
            src.append(f"tradeable_keys:{len(tk)}")
    except Exception as ue:
        print(f"[sched] tradeable-universe filter unavailable: {ue}", flush=True)
        return None, []
    if not uni:
        print("[sched] tradeable universe EMPTY — filter skipped (fail-open), check symbols_trb_*/tradeable_keys", flush=True)
        return None, []
    _TRADEABLE_UNI.update(at=time.time(), uni=uni, src=src)
    return uni, src


_SIDE_UNI = {"at": 0.0, "uni": None, "src": []}
_SIDE_UNI_ON = os.environ.get("V15_SCHED_SIDE_UNIVERSE", "1") == "1"


def _reachable_sym_sides():
    """({SYM_SIDE}, src) — THE law for what may be computed at all. USER 2026-10-20, verbatim:

        "the crypto system can only ever calculate npz files and do backtests on tradeable_keys.json
         sym/sides, stocks can only have npz's for the symbols_trb_long/short of that moment plus
         [TRADIER_MANDATORY_LONG_TRB / TRADIER_MANDATORY_SHORT_TRB]"

    So, per SIDE and never per symbol:
        crypto  {SYM}_{SIDE} present in tradeable_keys.json (keys are "acct:SYM_SIDE")
        stocks  LONG  in symbols_trb_long.json  | TRADIER_MANDATORY_LONG_TRB
                SHORT in symbols_trb_short.json | TRADIER_MANDATORY_SHORT_TRB

    Measured 2026-10-20 when this replaced the old symbol-level, trb-only _tradeable_universe() (which let a
    symbol tradeable LONG drag its SHORT board along — AAPL_SHORT and AU_SHORT were computed and published
    although no account can open them):

        ALLOWED sym_sides      262   (crypto 132, stocks 130 = 71 long + 59 short)
        scheduler work list    346 -> 171 allowed, 175 dropped (51%)
        published_selection    474 -> 252 allowed, 222 dropped (47%)

    "of that moment" is why this re-reads every 120 s instead of caching for the process: trb is edited while
    the fleet runs, and a symbol promoted into it must become computable without a restart.

    Fail-open by design: an unreadable or empty set returns None and nothing is filtered, because idling the
    fleet is worse than computing a few boards too many. V15_SCHED_SIDE_UNIVERSE=0 turns it off."""
    if _SIDE_UNI["uni"] is not None and time.time() - _SIDE_UNI["at"] < 120:
        return _SIDE_UNI["uni"], _SIDE_UNI["src"]
    uni, src = set(), []
    keys = _jload(ROOT / "tradeable_keys.json", None)
    if isinstance(keys, list):
        crypto = {str(k).split(":", 1)[-1].strip().upper() for k in keys}
        crypto = {ss for ss in crypto if ss.endswith(("_LONG", "_SHORT"))}
        uni |= crypto
        src.append(f"tradeable_keys:{len(crypto)}")
    for side, mand in (("long", "TRADIER_MANDATORY_LONG_TRB"), ("short", "TRADIER_MANDATORY_SHORT_TRB")):
        syms = set()
        v = _jload(ROOT / f"symbols_trb_{side}.json", None)
        if isinstance(v, dict):
            v = v.get("symbols") or v.get("pairs")
        if isinstance(v, list):
            syms |= {str(x).strip().upper() for x in v}
            src.append(f"trb_{side}:{len(v)}")
        try:
            sys.path.insert(0, str(ROOT))
            from config_tradier import TradierConfig as _TC

            m = getattr(_TC(), mand, None) or []
            syms |= {str(x).strip().upper() for x in m}
            src.append(f"mandatory_{side}:{len(m)}")
        except Exception as exc:
            print(f"[sched] {mand} unavailable: {exc}", flush=True)
        uni |= {f"{s}_{side.upper()}" for s in syms}
    if not uni:
        print("[sched] side universe EMPTY — filter skipped (fail-open), check tradeable_keys.json / symbols_trb_*", flush=True)
        return None, []
    _SIDE_UNI.update(at=time.time(), uni=uni, src=src)
    return uni, src


def _deny_unreachable_sides(acts, sym, label):
    """Drop the sym_sides no account can open. Returns the kept actions; logs what it cut, every tick."""
    if not _SIDE_UNI_ON or not acts:
        return acts, []
    uni, _src = _reachable_sym_sides()
    if not uni:
        return acts, []
    keep = [a for a in acts if f"{sym}_{a['side']}".upper() in uni]
    cut = [f"{sym}_{a['side']}" for a in acts if f"{sym}_{a['side']}".upper() not in uni]
    return keep, cut


def _deny_untradeable(syms, label):
    """Drop symbols outside the tradeable universe BEFORE dispatch, so an untradeable symbol gets no NPZ build and no
    board. _deny_tokenized only ever covered TOKENIZED stocks (AAPLUSDT…); 65 of the 266 live sym_sides in the
    2026-10-15 recalc triage sat outside the universe and were still being fully computed. V15_SCHED_TRADEABLE_FILTER=0
    restores the old behaviour."""
    if os.environ.get("V15_SCHED_TRADEABLE_FILTER", "1") == "0":
        return list(syms)
    # USER 2026-10-20: "can only ever calculate NPZ FILES and do backtests on ..." — so the symbol-level gate
    # that guards NPZ builds must be the SAME law as the sym_side gate, not a second opinion. A symbol with no
    # allowed side gets no NPZ and no board. Projection of _reachable_sym_sides; the legacy
    # _tradeable_universe() stays as the fallback when that set is unavailable (fail-open).
    uni, src = None, []
    sides, ssrc = _reachable_sym_sides()
    if sides:
        uni = {ss.rsplit("_", 1)[0] for ss in sides}
        src = ssrc
    if not uni:
        uni, src = _tradeable_universe()
    if not uni:
        return list(syms)
    keep = [s for s in syms if str(s).upper() in uni]
    cut = [s for s in syms if str(s).upper() not in uni]
    if cut:
        print(f"[sched] tradeable universe {len(uni)} syms ({', '.join(src)}): {label} {len(syms)} -> {len(keep)}, dropped {cut[:12]}{' …' if len(cut) > 12 else ''}", flush=True)
    return keep


def _venue_allowed(sym, native_syms=None, phase=None):
    """Venue gate. V15_SCHED_VENUE_ONLY=stocks (USER 2026-10-10): only native-stocks pairs launch. USER 2026-10-12
    `auto`: the gate is the current VENUE PHASE (`_venue_phase`): "stocks" while any tradeable stock sym_side is still
    launchable (need30, not deferred/fresh/waiting_base), else "any" (crypto flows; W-tier stocks come back daily through
    the 24h refresh and flip the phase for that tick). USDT stock equivalents (AAPLUSDT) are NEVER stocks.
    USER 2026-10-15: there is now also a real "crypto" phase — outside US RTH a stock NPZ cannot be current, so the
    fleet spends those hours on crypto, which has 24/7 bars. Pure (tested)."""
    mode = phase if phase is not None else ("stocks" if _VENUE_ONLY == "stocks" else "crypto" if _VENUE_ONLY == "crypto" else "any")
    if mode == "stocks":
        return venue_of(sym) == "stocks"
    if mode == "crypto":
        return venue_of(sym) == "crypto"
    return True


def _venue_phase(chain_state, mode=None, launchable=None, now=None, market_open=None):
    """-> (phase, launchable_stock_sides). mode = V15_SCHED_VENUE_ONLY: "auto" derives the phase, "stocks"/"crypto"
    force one, anything else = "any". Pure (tested); the clock is injected (`now`, `market_open`).

    PROVENANCE, do not delete — USER 2026-10-12: "stocks only until all tradeable stocks have passed, then crypto
    (+ best stock performers daily)". `auto` therefore returned "stocks" while any tradeable stock side was still
    launchable, regardless of the clock.

    EXTENDED BY USER 2026-10-15: "as stocks are where the real money goes make sure they actually get a rerun per
    day even after market close (understanding no new klines are produced after 20:00utc) and dedicate the rest of
    the night until market open to all cryptos that can fit."

    So stocks keep absolute priority until their daily pass is DONE — a post-close NPZ rebuilt to the session's
    final bar is current, because no newer bars exist, so "after the close" is a legitimate stock window, not
    stale data. What was missing is the second half: once no stock side is launchable, the remainder of the night
    must go to crypto. `auto` previously fell through to "any", which let a stock that became launchable mid-night
    preempt nothing and gave crypto no window of its own. Now: stocks while launchable -> else crypto while
    launchable -> else "any" (never idle a drained venue; that would trade one waste for another).

    An earlier draft of this made the phase clock-based (crypto all night). The USER corrected it: the clock
    governs FRESHNESS (see npz_tail_limit_h / the session-relative stock tail rule), never the stock pass itself.
    `now`/`market_open` stay injectable for that reason and for tests.
    V15_SCHED_VENUE_CLOCK=0 restores the 2026-10-12 behaviour exactly (stocks -> any, no crypto phase)."""
    mode = (mode if mode is not None else _VENUE_ONLY) or ""
    pending, pending_crypto = [], []
    for sym, sides in (chain_state or {}).items():
        _v = venue_of(sym)
        if _v not in ("stocks", "crypto"):
            continue
        for side, v in (sides or {}).items():
            try:
                if v[0] == "need30" and (launchable is None or launchable(sym, side)):  # USER 2026-10-13: an exhausted/crashing stock side must not hold crypto hostage
                    (pending if _v == "stocks" else pending_crypto).append(f"{sym}_{side}")
            except Exception:
                continue
    if mode == "stocks":
        return "stocks", pending
    if mode == "crypto":
        return "crypto", pending
    if mode == "auto":
        if pending:
            return "stocks", pending
        if os.environ.get("V15_SCHED_VENUE_CLOCK", "1") != "1":
            return "any", pending
        return ("crypto" if pending_crypto else "any"), pending
    return "any", pending


_NPZ_DEAD_PAT = re.compile(r"load fail|no timestamps/timestamp_15m|_compact_to_15m would raise|too few bars|len\(timestamp_15m\)")
def _npz_dead_syms(readiness_by_host):
    """USER 2026-10-16 (both hosts idle while the market was open): a STRUCTURALLY broken NPZ (unpicklable
    object timestamps, 1 bar, length mismatch) is not "unready" — no tail append or dispatch-side build fixes
    it, so the side can never launch. One such symbol (CDW: timestamp_15m = ['2026-10-13T17:30:00.000000Z'],
    1 bar) was the ONLY launchable stock side, so the RTH stocks phase held and 7 ready crypto sides on s2
    were venue-gated for hours. Dead symbols are excluded from the phase (never from the rebuild worklist).
    Staleness reasons (tail-stale/stale/gap/history) are NOT dead — the dispatch job refreshes those. Pure."""
    dead = set()
    for sym in {s for rb in (readiness_by_host or {}).values() for s in rb}:
        seen = [rb[sym] for rb in readiness_by_host.values() if sym in rb]
        if seen and all(not (r or {}).get("ok") and _NPZ_DEAD_PAT.search(str((r or {}).get("reason") or "")) for r in seen):
            dead.add(sym)
    return dead


def _phase_launchable(ss, st, started, redo, quar=None, pinned=None):
    """USER 2026-10-15 (06.3): venue-phase launch predicate — the phase counts ONLY sides that can really
    launch a 30D board now. Skipped: (a) exhausted sides (attempt/slice caps, INCLUDING the operator-pin cap —
    the old callback never passed `pinned`, so a pinned-redo side over its cap still held crypto hostage);
    (b) soft-skipped sides (quarantined terminal verdicts the pilot refuses with IMPOSSIBLE-SKIP/SOFT-SKIP —
    the pin override forces them to need30, but relaunching only burns a dispatch). Mirrors pending_actions
    budgets exactly (same _launch_exhausted call with the pin set). V15_SCHED_PHASE_STRICT=0 restores the
    legacy callback (no pin set, no quarantine filter). Pure (tested)."""
    if os.environ.get("V15_SCHED_PHASE_STRICT", "1") != "1":
        return not _launch_exhausted(f"{ss}|30D", "30D", ss, st, started, redo)
    if ss in (quar or set()):
        return False
    return not _launch_exhausted(f"{ss}|30D", "30D", ss, st, started, redo, pinned)


def _host_order(hosts, held):
    """USER 2026-10-12 "divide the load between s1 s2 s5 s6 while available": admit new pairs on the host with the most
    free capacity first (free-slot ratio, then free slots, then name) instead of config order (s1 — the live host — filled
    first). Pure (tested)."""
    def _key(h):
        cap = max(2, min(12, int(h.get("max_pairs", 3))))
        used = len((held or {}).get(h.get("name"), {}) or {})
        free = max(0, cap - used)
        return (-(free / float(cap)), -free, str(h.get("name")))
    return sorted(list(hosts or []), key=_key)
_GAIN_TIER_W_FRAC = 0.4  # top 40% of measured syms = winners (recalculated every round with the latest NPZ)
_GAIN_TIER_L_FRAC = 1.0 / 3.0  # bottom third = losers/low gainers (deferred, re-admitted after _GAIN_TIER_DEFER_DAYS)
def _defer_windows():
    """Defer windows with env override (USER 2026-10-10 weekend push: V15_TIER_DEFER_M_D=1 V15_TIER_DEFER_L_D=2
    recalculates everything before Monday; defaults restore 3d/7d steady state). Bad values fail to defaults."""
    # USER 2026-10-12 (2-server cadence): USER 2026-10-13 (2-server regime): EVERY symbol daily (1d for every tier) — the tier gates per-board BUDGET and ORDER, not frequency.
    try:
        m = float(os.environ.get("V15_TIER_DEFER_M_D", 1.0))
    except (TypeError, ValueError):
        m = 1.0
    try:
        dl = float(os.environ.get("V15_TIER_DEFER_L_D", 1.0))
    except (TypeError, ValueError):
        dl = 1.0
    return {"M": m, "L": dl}


_GAIN_TIER_DEFER_DAYS = _defer_windows()  # USER 2026-10-09: starve losers, feed winners — mediocre 2x/week, losers weekly; winners every round + slot reservation -> ~90% on winners
_FRESH_SKIP_DAYS = 4.0  # USER 2026-10-12: never re-calculate a sym_side with a completed 30d matrix newer than this (V15_FRESH_SKIP_DAYS overrides; 0 disables)


def _chain_stamp_ts(chain_dir, now=None):
    """Epoch of the newest DONE daily-chain stamp ('ended'), else 0.0 (fail-open: no stamp -> every recent matrix counts as fresh). Pure function of the dir."""
    import glob as _glob

    best = 0.0
    try:
        files = _glob.glob(os.path.join(chain_dir, "2026*.json"))
    except Exception:
        return 0.0
    for p in files:
        if "tradeable" in os.path.basename(p):
            continue
        try:
            d = json.load(open(p))
            if d.get("status") != "DONE":
                continue
            e = str(d.get("ended") or "")
            ts = datetime.datetime.fromisoformat(e.replace("Z", "+00:00")).timestamp() if e else 0.0
            best = max(best, float(ts or 0.0))
        except Exception:
            continue
    return best


def _fresh_computed_sides(matrix_dir, days=_FRESH_SKIP_DAYS, now=None, min_ts=0.0):
    """{SYM_SIDE: newest_mtime} for sides with a completed 30d matrix newer than `days` AND newer than min_ts (the chain stamp: pre-chain boards ran on stale inputs, so re-running them is new work, not a recalc). Pure function of the dir (testable); missing dir fails open ({})."""
    import glob as _glob
    import re as _re

    try:
        win = float(os.environ.get("V15_FRESH_SKIP_DAYS", days))
    except (TypeError, ValueError):
        win = float(days)
    if win <= 0:
        return {}
    try:
        names = _glob.glob(os.path.join(matrix_dir, "*_30d_matrix.xlsx"))
    except Exception:
        return {}
    now = float(now if now is not None else __import__("time").time())
    pat = _re.compile(r"^(.+)_(LONG|SHORT)_(.+_)?30d_matrix\.xlsx$")
    out = {}
    for p in names:
        m = pat.match(os.path.basename(p))
        if not m:
            continue
        try:
            mt = float(os.path.getmtime(p))
        except OSError:
            continue
        if mt >= min_ts and (now - mt) / 86400.0 < win:
            ss = f"{m.group(1)}_{m.group(2)}"
            out[ss] = max(out.get(ss, 0.0), mt)
    return out
def _pair_stale_sibling(sym, st, matrix_dir, current_npz_md5):
    """USER 2026-10-13 pair currency: side to re-queue, or None. Exactly one side wants a fresh 30D
    (need30/waiting_base) while the other sits in a CALCULATED terminal (terminal_ok/terminal_fresh)
    whose newest publish manifest ran on a DIFFERENT NPZ than the current one -> that side was never
    calculated on this data (ONEUSDT_LONG stayed on the Oct-10 NPZ while SHORT re-ran). Missing
    manifest / missing current md5 fails open (never blocks a launch, never invents work)."""
    if not current_npz_md5:
        return None
    import glob as _glob

    needy = [sd for sd, v in (st or {}).items() if v and v[0] in ("need30", "waiting_base")]
    if len(needy) != 1:
        return None
    for sd, v in (st or {}).items():
        if sd in needy or not v or v[0] not in ("terminal_ok", "terminal_fresh"):
            continue
        mans = sorted(_glob.glob(os.path.join(str(matrix_dir), f"{sym}_{sd}_*_manifest.json")), key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0)
        if not mans:
            return None
        try:
            md5 = str((json.loads(pathlib.Path(mans[-1]).read_text()) or {}).get("npz_md5") or "")
        except Exception:
            return None
        return sd if md5 and md5 != str(current_npz_md5) else None
    return None


def _npz_md5_cached(sym, cache):
    """md5 of the S1-source NPZ for sym (the file JIT prep pushes to workers), cached per tick. Missing -> "" (fail open)."""
    if sym in cache:
        return cache[sym]
    import hashlib as _hl

    out = ""
    try:
        p = ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"
        if p.exists():
            h = _hl.md5()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            out = h.hexdigest()
    except Exception:
        out = ""
    cache[sym] = out
    return out


_RECENT_BOARD_H = 24.0  # USER 2026-10-17 new-information KPI: a side boarded < this many hours ago is not re-boarded ...
_MIN_NEW_BARS_H = 24.0  # 2026-10-17 USER: 4 h let every 15-min NPZ top-up re-qualify a side after 4 h (SNXUSDT_LONG re-boarded 13:18Z + 15:40Z) ... unless its NPZ now ends at least this many hours after that board was published (V15_SCHED_RECENT_H / V15_SCHED_MIN_NEW_BARS_H; 0 disables)


def _manifest_index(matrix_dir):
    """{SYM_SIDE: (published_epoch, npz_md5)} from the newest *_30d_matrix_manifest.json per side. One glob per tick
    (the per-side glob in _same_npz_as_last_board ran ~1100 times a tick). Unreadable dir/file fails open (missing)."""
    import glob as _glob

    out = {}
    try:
        names = _glob.glob(os.path.join(str(matrix_dir), "*_30d_matrix_manifest.json"))
    except Exception:
        return out
    for p in names:
        try:
            j = json.loads(pathlib.Path(p).read_text()) or {}
            ss = str(j.get("symside") or "")
            ts = datetime.datetime.fromisoformat(str(j.get("published_utc") or "").replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ss and ts > (out.get(ss) or (0.0, ""))[0]:
            out[ss] = (ts, str(j.get("npz_md5") or ""))
    return out


def _npz_end_ts(path):
    """Last bar epoch of an NPZ's timestamps array, read from the zip member header + tail (no numpy: cron runs
    /usr/bin/python3). None on any problem (fail open)."""
    import ast as _ast
    import struct as _struct
    import zipfile as _zip

    try:
        with _zip.ZipFile(str(path)) as z:
            with z.open("timestamps.npy") as f:
                if f.read(6) != b"\x93NUMPY":
                    return None
                ver = f.read(2)
                hl = _struct.unpack("<H", f.read(2))[0] if ver[0] == 1 else _struct.unpack("<I", f.read(4))[0]
                hdr = _ast.literal_eval(f.read(hl).decode("latin1"))
                fmt = {"<f8": "<d", "<i8": "<q", "<u8": "<Q", "<f4": "<f", "<i4": "<i"}.get(hdr.get("descr"))
                if not fmt or hdr.get("fortran_order") or len(hdr.get("shape") or ()) != 1 or not hdr["shape"][0]:
                    return None
                sz = _struct.calcsize(fmt)
                data = f.read()
                return float(_struct.unpack(fmt, data[-sz:])[0]) if len(data) >= sz else None
    except Exception:
        return None


_HARD24 = {"at": {}, "pins": set(), "now": 0.0, "blocked": []}


def _boarded_within(ss, boarded_at, now_ts, hours=None, pins=None):
    """USER 2026-10-17 HARD RULE (misleading names + double scheduling must never happen again): a SYM_SIDE boarded —
    published OR refused (progress result_done_utc, newest *_matrix_manifest publish) — less than V15_SCHED_HARD_RECENT_H
    (24) hours ago is NEVER re-dispatched. Only an explicit operator pin (data/pinned sides) overrides. Pure (testable)."""
    try:
        h = float(os.environ.get("V15_SCHED_HARD_RECENT_H", "24") if hours is None else hours)
    except (TypeError, ValueError):
        h = 24.0
    if h <= 0 or (pins and ss in pins):
        return False
    t = (boarded_at or {}).get(ss)
    try:
        return t is not None and (float(now_ts) - float(t)) < h * 3600.0
    except (TypeError, ValueError):
        return False


def _recent_unmoved(ss, mindex, npz_end, now_ts, recent_h=None, min_new_h=None):
    """USER 2026-10-17 (zero new information since 03:16Z): True = SYM_SIDE has a board published < recent_h ago AND its
    NPZ does not end >= min_new_h after that publish -> a re-board re-measures (nearly) the same window and republishes
    the same result; the slot goes to a never-boarded / older side instead. A frozen/stale NPZ (GRTUSDT frozen-keep loop,
    last bar 6 h old) therefore re-boards at most once until its NPZ really advances. Missing manifest / NPZ end -> False
    (fail open: never blocks a side we cannot prove is current). Pure function (testable)."""
    try:
        rh = float(os.environ.get("V15_SCHED_RECENT_H", _RECENT_BOARD_H) if recent_h is None else recent_h)
        mh = float(os.environ.get("V15_SCHED_MIN_NEW_BARS_H", _MIN_NEW_BARS_H) if min_new_h is None else min_new_h)
    except (TypeError, ValueError):
        rh, mh = _RECENT_BOARD_H, _MIN_NEW_BARS_H
    if rh <= 0:
        return False
    rec = (mindex or {}).get(ss)
    if not rec or not rec[0]:
        return False
    if (float(now_ts) - float(rec[0])) >= rh * 3600.0:
        return False
    if npz_end is None:
        return False
    return (float(npz_end) - float(rec[0])) < mh * 3600.0


def _live_position_syms(now_ts, open_path=None):
    """Symbols with an open live position (data/open_position_sym_sides.json pushed by the Mac, ignored when > 2 h old)."""
    try:
        op = pathlib.Path(open_path) if open_path else ROOT / "data" / "open_position_sym_sides.json"
        j = json.loads(op.read_text()) or {}
        at = datetime.datetime.fromisoformat(str(j.get("at") or "").replace("Z", "+00:00")).timestamp()
        if float(now_ts) - at < 7200.0:
            return {str(x).rsplit("_", 1)[0] for x in (j.get("sym_sides") or [])}
    except Exception:
        pass
    return set()


def _recent_board_syms(syms, mindex, allowed, now_ts, recent_h=None, open_path=None):
    """Symbols whose every tradeable side has a board published < recent_h ago (sort-last set for _place_order). A symbol
    with an open live position on any side is live-relevant and never demoted (open_position_sym_sides.json, pushed by
    the Mac; ignored when older than 2 h). Pure apart from the optional file read; failures -> no exemption."""
    try:
        rh = float(os.environ.get("V15_SCHED_RECENT_H", _RECENT_BOARD_H) if recent_h is None else recent_h)
    except (TypeError, ValueError):
        rh = _RECENT_BOARD_H
    if rh <= 0:
        return frozenset()
    live = set()
    try:
        op = pathlib.Path(open_path) if open_path else ROOT / "data" / "open_position_sym_sides.json"
        j = json.loads(op.read_text()) or {}
        at = datetime.datetime.fromisoformat(str(j.get("at") or "").replace("Z", "+00:00")).timestamp()
        if float(now_ts) - at < 7200.0:
            live = {str(x).rsplit("_", 1)[0] for x in (j.get("sym_sides") or [])}
    except Exception:
        live = set()
    out = set()
    for sym in syms or ():
        if sym in live:
            continue
        sides = [f"{sym}_{sd}" for sd in ("LONG", "SHORT") if not allowed or f"{sym}_{sd}" in allowed]
        if sides and all(ss in (mindex or {}) and (float(now_ts) - float(mindex[ss][0])) < rh * 3600.0 for ss in sides):
            out.add(sym)
    return frozenset(out)


# USER 2026-10-13 throughput: the quota must never idle a paid host. V15_NONW_QUOTA_DIV=1 = no quota (every free
# slot admits work, winners still FIRST via _place_order tier rank); 4 = the 2026-10-09 "90% on winners" default.
try:
    _NONW_QUOTA_DIV = max(1, int(os.environ.get("V15_NONW_QUOTA_DIV", "4")))
except (TypeError, ValueError):
    _NONW_QUOTA_DIV = 4
_TIER_RANK = {"W": 0, "M": 1, "L": 2}


_LEDGER_PATH = ROOT / "data" / "v15_symside_ledger.json"
_LEDGER_MAX_AGE_S = float(os.environ.get("V15_LEDGER_MAX_AGE_S", 3 * 86400.0))
_TIER_W_MAX_POOL = int(os.environ.get("V15_TIER_W_MAX_POOL", "12"))  # USER 2026-10-12: 2 hosts -> at most 4 pools x 12 = 48 DAILY boards


def _ledger_load(path=None, now_ts=None):
    """{ss: {"tier": W|M|L, "mtime": float, "potential": float}} from tools/v15_symside_ledger.py output (USER 2026-10-12:
    gain + bh + trades + verdict decide the cadence, not gain alone). Stale (> V15_LEDGER_MAX_AGE_S) or missing -> {} (fail-open
    to the gain-only ranking). Pure given the file."""
    p = pathlib.Path(path) if path else _LEDGER_PATH
    try:
        if not p.exists():
            return {}
        now_ts = float(now_ts if now_ts is not None else time.time())
        if (now_ts - p.stat().st_mtime) > _LEDGER_MAX_AGE_S:
            return {}
        j = json.loads(p.read_text())
        out = {}
        for ss, e in ((j or {}).get("sym_sides") or {}).items():
            t = (e or {}).get("sched_tier")
            if t in _TIER_RANK:
                out[str(ss)] = {"tier": t, "mtime": float((e or {}).get("mtime") or 0.0), "potential": (e or {}).get("potential"), "why": (e or {}).get("tier_why")}
        return out
    except Exception:
        return {}


def _gain_tiers(gains, ledger=None, w_max=None):
    """Rank measured sym_sides by latest gain, separately per (venue, side) pool -> {ss: {"tier": W|M|L, ...}}.
    USER 2026-10-09: pooled ranks buried stock winners under crypto (PLTR_LONG rank 201/255); each pool (stocks-long,
    stocks-short, crypto-long, crypto-short) gets its own W = top 40%, L = bottom third, M = rest. Sides with no
    measured gain are absent (callers default them to M: new/unknown work is never starved).
    USER 2026-10-12: `ledger` (tools/v15_symside_ledger.py: gain + bh + trades + verdict) WINS over the gain-only rank for
    every side it carries (W = DAILY recalc, M = WEEKLY, L = BIWEEKLY); sides only in the ledger are added with the
    ledger mtime. `w_max` caps W per pool (2-host capacity); overflow W become M, highest gain kept."""
    pools = {}
    for ss, g in (gains or {}).items():
        try:
            gv = float((g or {}).get("gain"))
        except (TypeError, ValueError):
            continue
        try:
            mt = float((g or {}).get("mtime", 0) or 0)
        except (TypeError, ValueError):
            mt = 0.0
        ss = str(ss)
        sym, _, side = ss.rpartition("_")
        pools.setdefault((venue_of(sym), side), []).append((ss, gv, mt))
    out = {}
    for rows in pools.values():
        rows.sort(key=lambda r: r[1], reverse=True)
        n = len(rows)
        nw = max(1, int(n * _GAIN_TIER_W_FRAC)) if n else 0
        nl = max(1, int(n * _GAIN_TIER_L_FRAC)) if n else 0
        for i, (ss, gv, mt) in enumerate(rows):
            out[ss] = {"tier": "W" if i < nw else ("L" if i >= n - nl else "M"), "gain": gv, "mtime": mt}
    for ss, le in (ledger or {}).items():
        t = (le or {}).get("tier")
        if t not in _TIER_RANK:
            continue
        if ss in out:
            out[ss]["tier"] = t
            out[ss]["ledger"] = True
            try:
                out[ss]["mtime"] = max(float(out[ss].get("mtime") or 0.0), float((le or {}).get("mtime") or 0.0))
            except (TypeError, ValueError):
                pass
        else:
            try:
                out[ss] = {"tier": t, "gain": (le or {}).get("potential"), "mtime": float((le or {}).get("mtime") or 0.0), "ledger": True}
            except (TypeError, ValueError):
                continue
    cap = _TIER_W_MAX_POOL if w_max is None else w_max
    if cap and cap > 0:
        by_pool = {}
        for ss, e in out.items():
            if e.get("tier") != "W":
                continue
            sym, _, side = str(ss).rpartition("_")
            by_pool.setdefault((venue_of(sym), side), []).append(ss)
        for rows in by_pool.values():
            if len(rows) <= cap:
                continue
            rows.sort(key=lambda s: -float(out[s].get("gain") or 0.0))
            for s in rows[cap:]:
                out[s]["tier"] = "M"
                out[s]["w_capped"] = True
    return out


def _side_tier(ss, tiers):
    return ((tiers or {}).get(ss) or {}).get("tier", "M")


_REFRESH_W_STALE_SEC = float(os.environ.get("V15_REFRESH_W_STALE_H", "24") or 24) * 3600.0  # USER 2026-10-16 weekend: "run the entire universe several times" -> cron sets 8
_REFRESH_MAX_RM_PER_TICK = 3


def _refresh_due(ss, tiers, now_ts, windows=None):
    """USER 2026-10-09: True = W-tier (big gainer) side whose measured 30D gain is older than 24h.
    The 30D window lacks longer-TF info so it goes stale fast — due sides get a FRESH 30D board.
    USER 2026-10-13 "EVERY symbol daily (1d for every tier) — the tier gates per-board BUDGET and ORDER, not frequency" +
    2026-10-14 "run all sym_sides every 24 hours": M/L sides refresh too once older than their defer window (V15_TIER_DEFER_M_D /
    _L_D). Before this, a finished M/L side was terminal_ok for the whole round (171/278 sides idle on 10-14, S2 starved).
    V15_SCHED_REFRESH_ALL=0 restores W-only."""
    e = (tiers or {}).get(ss)
    if not e:
        return False
    tier = e.get("tier")
    # USER 2026-10-17: "ang and trb ... are the most rapidly changing according to markets and need new settings
    # for each sym_side every day". A ranking member is due on the ranking window (V15_RANKING_REFRESH_H, 24h)
    # whatever its tier, and never waits out an M/L defer window. Non-members keep the tier behaviour below.
    try:
        if _RANKPRI.is_ranking_side(ss):
            rank_win_s = _RANKPRI.refresh_window_s()
            if rank_win_s > 0:
                try:
                    return (float(now_ts) - float(e.get("mtime", 0) or 0)) >= rank_win_s
                except (TypeError, ValueError):
                    return False
    except Exception:
        pass
    if tier == "W":
        win_s = _REFRESH_W_STALE_SEC
    elif tier in ("M", "L") and os.environ.get("V15_SCHED_REFRESH_ALL", "1") == "1":
        try:
            win_s = float((windows if windows is not None else _GAIN_TIER_DEFER_DAYS).get(tier) or 0) * 86400.0
        except (TypeError, ValueError, AttributeError):
            return False
        if win_s <= 0:
            return False
    else:
        return False
    try:
        return (float(now_ts) - float(e.get("mtime", 0) or 0)) > win_s
    except (TypeError, ValueError):
        return False


def _sym_tier_rank(sym, tiers):
    return min(_TIER_RANK[_side_tier(f"{sym}_{sd}", tiers)] for sd in ("LONG", "SHORT"))


def _side_deferred(ss, tiers, now_ts, windows=_GAIN_TIER_DEFER_DAYS):
    """True = M/L-tier side whose last calc is younger than its window (M 3d, L 7d): skip its fresh board (chain
    continuations still drain; staleness re-admits, so regime turns re-measure). W/unknown/missing/bad fails open."""
    e = (tiers or {}).get(ss)
    if not e:
        return False
    # USER 2026-10-14 (ranking top-25 frequency boost): ranking members recalculate every round like W.
    # Terminal/365D-failure handling lives in chain_state/quarantine (untouched) — this only skips the M/L wait.
    try:
        if _RANKPRI.is_ranking_side(ss):
            return False
    except Exception:
        pass
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


def _sym_quota_hit(sym, tiers, failed=()):
    """True = pair consumes a non-winner slot: measured M/L present and no W side. Unknown/new pairs are discovery (exempt).
    USER 2026-10-14 (sibling independence, both directions): a side proven impossible (terminal_failing:
    IMPOSSIBLE/NO_TRADES quarantine or negative-30D failing) never counts against its sibling — pass such
    sides as failed={"SHORT"} etc. Each side stands on its own evidence; both can be productive at once."""
    bad = set(failed or ())
    ts = [((tiers or {}).get(f"{sym}_{sd}") or {}).get("tier") for sd in ("LONG", "SHORT") if sd not in bad]
    return any(t in ("M", "L") for t in ts) and not any(t == "W" for t in ts)


_FAILED_CHAIN_STATES = ("terminal_failing",)


def _quota_blocks(sym, gtiers, chain_state):
    """True = winner-quota blocks a NEW slot: non-winner pair that still wants a FRESH 30D board. Pure-chain syms (GS/repair/365D continuations) always drain — quota throttles new boards, never verification/heals (USER 2026-10-12: quota was starving 10/12 GS-heals on idle s5/s6).
    USER 2026-10-14: a terminal-failed side never blocks its sibling's fresh board (failed SHORT => LONG
    still launches, and vice versa). Measured-mediocre siblings keep normal throttling."""
    st = chain_state.get(sym) or {}
    try:
        failed = {sd for sd in ("LONG", "SHORT") if (st.get(sd) or ("",))[0] in _FAILED_CHAIN_STATES}
    except Exception:
        failed = set()
    if not _sym_quota_hit(sym, gtiers, failed):
        return False
    return any(v[0] == "need30" for v in st.values())


def _fresh_daily_state(st, datestr):
    """Midnight rollover: fresh attempts/holds, but tier memory survives (own 30d prune). Without the carry-over,
    W/M/L + deferral evaporate daily (USER 2026-10-10: observed 255 -> 71 measured at 00:00Z)."""
    if (st or {}).get("date") != datestr:
        return {"date": datestr, "held": {}, "attempts": {}, "ready": {}, "gain_seen": (st or {}).get("gain_seen", {}), "launched_at": (st or {}).get("launched_at", {})}
    return st


def _gs_allowed_for_tier(tier, measured):
    """USER 2026-10-09 (90% on winners): GS-heal for W/M/discovery; L gets verified, not rescued. USER 2026-10-12: V15_REPAIR_ALL_TIERS=1 opens all tiers (every sym_side over BH push)."""
    return os.environ.get("V15_REPAIR_ALL_TIERS") == "1" or (tier in ("W", "M")) or not measured


def _repair_allowed_for_tier(tier, measured):
    """USER 2026-10-09 (90% on winners): repair rounds for W/discovery only; M got its GS shot, L gets none. USER 2026-10-12: V15_REPAIR_ALL_TIERS=1 opens all tiers (every sym_side over BH push)."""
    return os.environ.get("V15_REPAIR_ALL_TIERS") == "1" or tier == "W" or not measured


def _place_order(held_ordered, adopted_owned, new_syms, tiers, redo=None, attempts=None, launched_at=None, pri=frozenset(), cont=None, pinned=None, recent=None, age_key=None):
    """USER 2026-10-09 (trb gainers absolute priority): W chains + W new before ALL M/L work. Stable sort
    keeps chain-first within a tier. Running pilots are never touched — this orders new launches only.
    USER 2026-10-10 (82 redo backlog): needs_redo RESUMES outrank everything (paid work finalises in
    minutes). Key = (redo, tier): the order is the NEW-pair budget priority (first 4 win); held
    chains are budget-free and the launch loop never BREAKS (continue skips over-cap/new-budget/dead
    pairs), so every held chain is evaluated every tick — a break-before-held froze the fleet 03:00-03:35Z.
    USER 2026-10-10 22:30Z: temporary native-deprioritize REVERTED (GAP_MOC parity repaired, natives run).
    USER 2026-10-11 (every sym gets its turn): within (redo, tier) the key extends to (chains-first,
    director-priority, fewest-attempts, oldest-launch) — never-launched sorts first, so every sym gets
    attempt N before any sym gets attempt N+1. Attempts reset at the midnight rollover (daily rotation)."""
    seq = [(s, False) for s in held_ordered] + [(s, True) for s in adopted_owned] + [(s, True) for s in new_syms]
    _rs = redo or set()
    _cs, _ps = cont or set(), pinned or set()
    # 2026-10-13 RESUME: operator-pinned sides first, then needs_redo AND every other unfinished board (started, no
    # final) before any fresh pair — an unfinished board resumes from its done dict before new work takes the slot.
    _rk = lambda b: 0 if f"{b}_LONG" in _ps or f"{b}_SHORT" in _ps else (1 if any(x in _rs or x in _cs for x in (f"{b}_LONG", f"{b}_SHORT")) else 2)
    _min_att, _old_ts = {}, {}
    for _k, _v in (attempts or {}).items():
        try: _sym, _vv = _k.split("|")[0].rsplit("_", 1)[0], float(_v)
        except (TypeError, ValueError, IndexError): continue
        if _sym not in _min_att or _vv < _min_att[_sym]: _min_att[_sym] = _vv
    for _k, _v in (launched_at or {}).items():
        try: _sym, _vv = _k.split("|")[0].rsplit("_", 1)[0], float(_v)
        except (TypeError, ValueError, IndexError): continue
        if _sym not in _old_ts or _vv < _old_ts[_sym]: _old_ts[_sym] = _vv
    _pri = pri or frozenset()
    # USER 2026-10-14 (ranking top-25 priority): ranking members sort right AFTER the gain tier (BEST_WORST
    # stays primary; neg-365D L-tier exceptions keep sorting last) and before director-priority/attempts.
    # The boost weighs less than a tier step by construction: it only breaks ties within a tier.
    _rkpairs = _RANKPRI.ranking_sets()["pairs"]
    # USER 2026-10-17 (new-information KPI): a pair whose every side was boarded < 24 h ago sorts after all other work of
    # the same resume class — never-boarded / older boards first; it still runs when nothing else is launchable.
    _rec = recent or frozenset()
    _ak = age_key or (lambda b: (2, 0.0, 0.0))  # 2026-10-20 USER: catch-up violators, then due live gainers, then oldest-first; default = no effect
    seq.sort(key=lambda t: (_rk(t[0]), _ak(t[0]), 1 if t[0] in _rec else 0, _sym_tier_rank(t[0], tiers), 0 if t[0] in _rkpairs else 1, 1 if t[1] else 0, 0 if t[0] in _pri else 1, _min_att.get(t[0], -1.0), _old_ts.get(t[0], -1.0)))
    return seq


def _pair_gate_ok(sym, acts, owner, chain_state, attempts, pend_sides=None):
    """USER 2026-10-03 (drain stall): fresh (unowned) syms launch when EVERY launchable side has an
    action — terminal/capped sides don't block their sibling (half-quarantined pairs used to stall
    forever: the live side could never launch alone). Owned syms always pass (stickiness). Mirrors
    pending_actions caps exactly via _LAUNCH_CAPS (30D:3, 365D:2, REPAIR:1, GS:1)."""
    if sym in owner:
        return True
    if pend_sides is not None:
        # 2026-10-14: compare with the sides that actually HAVE a pending action (same budget rules as pending_actions:
        # slices for started boards, pin cap, redo-free). The attempts-only view below declared pinned/sliced sides
        # "not needy" (attempts >= 3) -> needy empty -> 39 stock symbols gated forever on s2 (QBTS, SHOP, ABBV, MDT ...).
        return bool(acts) and {a["side"] for a in acts} >= set(pend_sides)
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


def _host_peak_pair_mb(st, host, pairs_pss, now_ts, keep_s=86400.0, workers=None):
    """Expected peak PSS of ONE new side on this host, from the book of sizes seen in the last 24 h (state-carried
    across --once ticks; young boards understate their own future). Updates st in place. Never raises.

    V15_SCHED_PEAK_Q (default 1.0 = the old behaviour, the MAX) picks a quantile of the book instead. Measured
    2026-10-18 with 132 of 234 stock sym_sides still unrecounted 13.8 h before the open, both hosts idle on "mem
    guard" and S2's CPU at 10%: the book's top-20 for `s2|stocks|side` was max 18,821 MB against a median of 7,442
    and a min of 6,196, so ONE pathological side budgeted every new stocks board at 20,703 MB (1.1x peak) and
    reserved the same amount as growth debt for the single board already running -- on a 30 GB host that is one board
    at a time, forever, from one outlier. The book only ever keeps the 20 LARGEST values, so a quantile over it is
    still high-biased, and the per-venue floor (`V15_SCHED_PAIR_FLOOR_MB{,_STOCKS}`) and the OOM reaper stay in force.
    """
    try:
        book = st.setdefault("pair_peak_mb", {})
        # 2026-10-20 USER: a sample is only evidence for the worker count it was measured at (6-worker sides peak 6-21 GB, 3-worker
        # sides about half). Entries are [ts, mb, workers]; untagged legacy entries are the 6-worker era. workers=None = old behaviour.
        hist = [x for x in (book.get(host) or []) if isinstance(x, list) and len(x) in (2, 3) and float(now_ts) - float(x[0]) < keep_s and (workers is None or int(x[2] if len(x) == 3 else 6) == int(workers))]
        vals = pairs_pss.values() if isinstance(pairs_pss, dict) else (pairs_pss or [])
        cur = max([float(v or 0) for v in vals] or [0.0])
        if cur > 0:
            hist.append([float(now_ts), round(cur, 1)] + ([int(workers)] if workers is not None else []))
        hist = sorted(hist, key=lambda x: -x[1])[:20]
        book[host] = hist
        sizes = sorted(float(x[1]) for x in hist)
        if not sizes:
            return 0.0
        q = float(os.environ.get("V15_SCHED_PEAK_Q", "1.0") or 1.0)
        q = min(1.0, max(0.0, q))
        return sizes[min(len(sizes) - 1, int(round(q * (len(sizes) - 1))))]
    except Exception:
        return 0.0


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


def _disk_admit_ok(stats, min_mb=5000.0):
    """Refuse launches while the host disk is nearly full (USER 2026-10-10: s5+s6 hit
    100% and pilots died on Errno 28 mid-save, burning attempts in a relaunch loop).
    Missing disk fields fail open (old probe)."""
    try:
        avail = (stats or {}).get("disk_avail_mb")
        if avail is None:
            return True
        return float(avail) >= float(min_mb)
    except Exception:
        return True


def _launched_recently(key, launched_at, now_ts, grace_s=180.0):
    """True when key launched within the grace window (USER 2026-10-10: fresh pilots are
    probe-invisible for minutes under load; relaunching them spawns cross-host duplicates that
    burn attempts to cap at birth). Missing/junk fails open (launch)."""
    try:
        ts = (launched_at or {}).get(key)
        if ts is None:
            return False
        return (float(now_ts) - float(ts)) < float(grace_s)
    except Exception:
        return False


def _progress_done_rows(path):
    """len(done) of a progress JSON, None when unreadable (fail-open for OOM ranking)."""
    try:
        d = json.load(open(path))
        return len(d.get("done") or {})
    except Exception:
        return None


def _progress_is_refill(path):
    """True when the progress JSON is a REDO refill / heal resume (needs_redo set, heal
    marker present, or redo_depth >= 1): closest to a publishable result, must survive
    OOM triage."""
    try:
        d = json.load(open(path))
        return bool(d.get("needs_redo") or d.get("_redo_heal_run") or (d.get("redo_depth") or 0) >= 1)
    except Exception:
        return False


def _progress_pinpoint_lifting(path, fresh_s=1200):
    """True when a V17 board is in its pinpoint round (v17.p3_budget_s set, no final yet) and its progress was written
    within fresh_s — i.e. still lifting (USER 2026-10-13: never kill a backtest while it keeps lifting >=0.3pp/min).
    Never raises."""
    try:
        if time.time() - os.path.getmtime(path) > fresh_s:
            return False
        with open(path) as f:
            d = json.load(f)
        return bool((d.get("v17") or {}).get("p3_budget_s")) and d.get("final_gain") is None
    except Exception:
        return False


def _rank_oom_victim(cands):
    """Least-progress OOM victim. cands: [(ss, done_or_None, age_s)] or 4-tuples with a
    refill flag appended. A side with no readable progress file and age < 30min counts
    as 0 rows (brand-new, least loss); no-file + old counts last (unknown act, probably
    slow 365D — don't murder it). REFILLS (REDO/heal resumes) sort after everything
    killable: they are closest to a publishable result (USER 2026-10-10: the guard kept
    murdering ENAUSDC_SHORT's refill at 186 rows while fresh boards survived). Ties break
    youngest. Returns ss or None. Never raises."""
    best, best_key = None, None
    for c in cands or []:
        try:
            ss, done, age = c[0], c[1], float(c[2] or 0)
            refill = bool(len(c) > 3 and c[3])
            lifting = bool(len(c) > 4 and c[4])
        except Exception:
            continue
        if lifting:  # USER 2026-10-13 0.3 rule: a board still lifting in pinpoint is the LAST possible victim
            key = (3, 0, age)
        elif refill:
            key = (2, 0, age)
        elif done is None:
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
    """{sym: info} for this host, cached (ok entries 30min, failures 30 min) in `cache[host]`."""
    c = cache.setdefault(host["name"], {})
    # USER 2026-10-12 dispatch gate: ok-cache was 3h — a 3h-old "ok" could dispatch onto a 12-bar-stale NPZ.
    # Tail currency decays every 15m bar, so re-probe ok entries every 30min. Probes are batched (1 ssh/host/tick
    # worst case), so this costs nothing.
    need = [s for s in symbols if s not in c or (now.timestamp() - c[s].get("_at", 0)) > 1800]
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


def _spawn_guard(ss, window, body):
    """USER 2026-10-11 (5x dup stacks + burned attempt caps): every remote launch is guarded by a real pgrep — the old 'pgrep dedups' comments had no implementation. FIX 21:50Z: patterns are ^-ANCHORED to a python argv[0] — the first version matched its own bash -c wrapper (whose cmdline contains the literal pilot invocation) and self-skipped every launch fleet-wide. Skip logs [spawnguard] to the sweep log. 2026-10-13 (2x5 live dup stacks, TERM-immune): the pgrep-or-launch was not atomic — two wrappers racing inside the same minute both saw an empty ps and both launched. FIX: serialize guard+launch per side+window under a non-blocking flock; the launcher holds the lock 3s after forking so the child python is visible in ps before the next contender's pgrep runs. Lock contention skips silently (scheduler retries next tick)."""
    pat = {"30D": f"^[^ ]*python[^ ]* -u [^ ]*v15_pilot.py --sym-side {ss}( |$)", "365D": f"^[^ ]*python[^ ]* -u [^ ]*tools/v15_365_cycle.py --sym-side {ss}( |$)", "REPAIR": f"^[^ ]*python[^ ]* -u [^ ]*tools/v15_365_cycle.py --sym-side {ss}( |$)", "GS": f"^[^ ]*python[^ ]* -u [^ ]*tools/v15_graph_search.py --symsides {ss}( |$)"}[window]
    inner = f"pgrep -f \"{pat}\" >/dev/null 2>&1 && echo \"[spawnguard] {ss} {window} already running on $(hostname)\" || {{ {body}; sleep 3; }}"
    # flock(1) ships on the Linux workers (atomic guard+launch); macOS dev/test has no flock -> plain guard there.
    return (f"if command -v flock >/dev/null 2>&1; then flock -n /tmp/v15_launch_{ss}_{window}.lock bash -c {shlex.quote(inner)} "
            f"|| echo \"[launchlock] {ss} {window} launcher race, retry next tick\"; else bash -c {shlex.quote(inner)}; fi")


GATE_ENV_KEY_RE = re.compile(r"^V1[25]_[A-Z0-9_]+$")


def gate_env_inline(path=None):
    """USER 2026-10-16 "make sure any version gets tested side by side against previous version so corner cutting is exposed":
    data/v15_gate_env.json (published ONLY by tools/v15_version_gate.py on a PASS / champion publish) is the launch env every
    30D board runs. Returned as inline ` K=V` pairs appended after the scheduler's own env, so the benched value wins over the
    hard-coded pace values and over the V17 autotune exports (inline assignment beats `export`). Missing/unreadable file or
    V15_SCHED_GATE_ENV=0 -> '' (pre-2026-10-16 behaviour)."""
    if os.environ.get("V15_SCHED_GATE_ENV", "1") == "0":
        return ""
    try:
        doc = json.loads(pathlib.Path(path or (ROOT / "data" / "v15_gate_env.json")).read_text())
        env = doc.get("env") or {}
    except (OSError, ValueError):
        return ""
    return "".join(f" {k}={shlex.quote(str(v))}" for k, v in sorted(env.items()) if GATE_ENV_KEY_RE.match(str(k)))


def _round_id_of(pdir):
    """'run33' from '/home/niels/v15_run33_20261019/progress' — the round a launch belongs to. '' when unrecognisable."""
    m = re.search(r"v15_(run\d+)", str(pdir or ""))
    return m.group(1) if m else ""


def round_env_inline(round_id, path=None):
    """USER 2026-10-19: "START SKIPPING MORE ROWS FOR THE CURRENT RUN ONLY TO COMPLY WITH WHAT WE NEED BY MARKET OPEN".

    data/v15_round_env.json = {"round": "run33", "note": "...", "env": {K: V}} and applies ONLY while the fleet is on
    that exact round, so it EXPIRES BY ITSELF the moment the round rolls — that is what makes it "this run only" rather
    than a permanent corner cut. Appended after gate_env_inline(), so it deliberately overrides the benched launch env:
    the gate exists to expose corner cutting, so anything set here is an explicit, dated, self-expiring operator
    decision and must be benched (`v15_version_gate submit --env K=V`) before it is ever made permanent.
    Delete the file (or roll the round) to revert instantly. V15_SCHED_ROUND_ENV=0 ignores it.
    """
    if os.environ.get("V15_SCHED_ROUND_ENV", "1") == "0":
        return ""
    try:
        doc = json.loads(pathlib.Path(path or (ROOT / "data" / "v15_round_env.json")).read_text())
    except (OSError, ValueError):
        return ""
    if str(doc.get("round") or "") != str(round_id or ""):
        return ""
    env = doc.get("env") or {}
    return "".join(f" {k}={shlex.quote(str(v))}" for k, v in sorted(env.items()) if GATE_ENV_KEY_RE.match(str(k)))


def _workers_for(sym, workers):
    """2026-10-20 USER: crypto sides are 5-10 GB PSS at 3 workers; V15_SCHED_WORKERS_PER_SIDE_CRYPTO overrides the worker count for
    crypto symbols only (stocks and anything unset keep the global value). Never raises."""
    try:
        _w = os.environ.get("V15_SCHED_WORKERS_PER_SIDE_CRYPTO")
        if _w and venue_of(sym) == "crypto":
            return max(1, int(_w))
    except Exception:
        pass
    return workers


_PLABEL, _PAGE = {}, {}  # per tick: sym -> rule label / board age h of its best side
_AGE_KEY = [None]  # per-tick sym -> (class, sub, key) closure built from the age rules / live gain cadence
_CATCHUP_MODE = [False]  # 2026-10-20 USER LAW: set per tick by the coverage hysteresis; side_cmd adds V18_CATCHUP=1 to 30D launches while True

_REMOTE_MANIFEST_PY = (
    "import json,glob,os,sys\n"
    "d=os.path.expanduser(sys.argv[1]);o=[]\n"
    "for f in glob.glob(d+'/*_30d_matrix_manifest.json'):\n"
    "    try:\n"
    "        j=json.load(open(f));w=(j.get('provenance') or {}).get('npz_window') or {}\n"
    "        o.append([j.get('symside'),j.get('published_utc'),j.get('host'),w.get('ts_last'),w.get('mtime_ns'),j.get('npz_age_min')])\n"
    "    except Exception: pass\n"
    "print(json.dumps(o))\n")


def _manifest_rows(matrix_dir):
    """[[symside, published_utc, host, npz_ts_last, npz_mtime_ns, npz_age_min|None]] from the publish manifests in matrix_dir. Never raises."""
    import glob as _glob

    out = []
    try:
        for p in _glob.glob(os.path.join(str(matrix_dir), "*_30d_matrix_manifest.json")):
            try:
                j = json.loads(pathlib.Path(p).read_text()) or {}
                w = (j.get("provenance") or {}).get("npz_window") or {}
                out.append([j.get("symside"), j.get("published_utc"), j.get("host"), w.get("ts_last"), w.get("mtime_ns"), j.get("npz_age_min")])
            except Exception:
                continue
    except Exception:
        pass
    return out


def _stock_session_final_bar(ts_last):
    """True when an NPZ's last bar is the final bar of a US regular session (NY 15:30-16:00 weekday): after the close no newer bar
    exists, so such an NPZ is current by construction (same rule as the stock tail gate). Never raises."""
    try:
        t = datetime.datetime.fromtimestamp(float(ts_last), datetime.timezone.utc).astimezone(NY)
        return t.weekday() < 5 and datetime.time(15, 30) <= t.time() <= datetime.time(16, 0)
    except Exception:
        return False


def _board_npz_age_min(row, venue):
    """Minutes the NPZ's last bar lagged the NPZ file's build time (the best 'NPZ age at prepare' the manifest carries): the manifest's own
    npz_age_min when present, else (npz file mtime - last bar)/60. Stocks whose last bar is a session final bar count as 0. None = unknown."""
    try:
        if row[5] is not None:
            return float(row[5])
        if venue == "stocks" and row[3] is not None and _stock_session_final_bar(row[3]):
            return 0.0
        if row[3] is None or row[4] is None:
            return None
        return (float(row[4]) / 1e9 - float(row[3])) / 60.0
    except Exception:
        return None


def _coverage(allowed, rows, now_ts, max_age_h=24.0, npz_min=20.0):
    """COVERAGE = share of tradeable sym_sides whose newest PUBLISHED board is < max_age_h old AND was built on an NPZ <= npz_min old.
    Pure (tested). rows = _manifest_rows output (any hosts merged); the newest manifest per sym_side wins."""
    newest = {}
    for r in rows or []:
        try:
            ts = datetime.datetime.fromisoformat(str(r[1]).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if r[0] and ts > (newest.get(r[0]) or (0.0, None))[0]:
            newest[r[0]] = (ts, r)
    cats, ages_all = {}, []
    for ss in sorted(allowed or []):
        sym = ss.rsplit("_", 1)[0]
        venue = venue_of(sym)
        c = cats.setdefault(cat_of_ss(ss), {"n": 0, "published_24h": 0, "covered": 0, "ages_h": [], "npz_stale": 0, "never": 0})
        c["n"] += 1
        rec = newest.get(ss)
        if not rec:
            c["never"] += 1
            continue
        age_h = (now_ts - rec[0]) / 3600.0
        c["ages_h"].append(age_h)
        ages_all.append(age_h)
        if age_h < max_age_h:
            c["published_24h"] += 1
            lag = _board_npz_age_min(rec[1], venue)
            if lag is not None and lag <= npz_min:
                c["covered"] += 1
            else:
                c["npz_stale"] += 1

    def _med(v):
        v = sorted(v)
        return round(v[len(v) // 2], 2) if v else None

    n = sum(c["n"] for c in cats.values())
    cov = sum(c["covered"] for c in cats.values())
    out = {"n": n, "covered": cov, "coverage": round(cov / n, 4) if n else 1.0, "published_24h": sum(c["published_24h"] for c in cats.values()),
           "median_board_age_h": _med(ages_all), "per_cat": {}}
    for k, c in cats.items():
        out["per_cat"][k] = {"n": c["n"], "covered": c["covered"], "coverage": round(c["covered"] / c["n"], 4) if c["n"] else 1.0, "published_24h": c["published_24h"],
                             "published_but_npz_stale": c["npz_stale"], "never_or_old": c["n"] - c["published_24h"], "median_board_age_h": _med(c["ages_h"])}
    return out


def _select_catchup_mode(prev_catchup, coverage, below=0.80, exit_=0.95):
    """Hysteresis: enter catch-up when coverage < below, leave it only when coverage >= exit_. Pure (tested)."""
    if prev_catchup:
        return not (coverage >= exit_)
    return coverage < below


def _throughput(rows, now_ts, window_h=1.0, target=12):
    """Published boards per host over the trailing window + distinct symbols with BOTH sides published in it. Pure (tested)."""
    per_host, sides = {}, {}
    for r in rows or []:
        try:
            ts = datetime.datetime.fromisoformat(str(r[1]).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if now_ts - ts > window_h * 3600.0 or not r[0]:
            continue
        hn = {"niels": "s1"}.get(str(r[2]), str(r[2]))
        per_host.setdefault(hn, set()).add(r[0])
        sym, side = r[0].rsplit("_", 1)
        sides.setdefault(sym, set()).add(side)
    return {"window_h": window_h, "target_per_host": target, "published_per_host": {h: len(v) for h, v in sorted(per_host.items())},
            "symbols_both_sides": sum(1 for v in sides.values() if len(v) == 2), "sym_sides": sum(len(v) for v in per_host.values())}


def _qualifying_ages(allowed, rows, now_ts, npz_min=20.0):
    """{sym_side: age_h of its newest PUBLISHED board computed on an NPZ <= npz_min old, or None when no such board exists}. Pure (tested)."""
    best = {}
    for r in rows or []:
        try:
            ts = datetime.datetime.fromisoformat(str(r[1]).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if not r[0] or ts <= best.get(r[0], 0.0):
            continue
        lag = _board_npz_age_min(r, venue_of(r[0].rsplit("_", 1)[0]))
        if lag is not None and lag <= npz_min:
            best[r[0]] = ts
    return {ss: ((now_ts - best[ss]) / 3600.0 if ss in best else None) for ss in (allowed or [])}


def _catchup_rules(ages, trading, any_h=216.0, trading_h=96.0):
    """USER 2026-10-20 age rules. Catch-up is ON when ANY tradeable sym_side's qualifying board is older than any_h (9 d; none = infinitely
    old) OR any actively trading sym_side's is older than trading_h (4 d). It turns OFF only when neither holds. Pure (tested)."""
    inf = float("inf")
    a = {ss: (inf if v is None else float(v)) for ss, v in (ages or {}).items()}
    tr = set(trading or ()) & set(a)
    over_any = sorted((ss for ss, v in a.items() if v > any_h), key=lambda x: -a[x])
    over_tr = sorted((ss for ss in tr if a[ss] > trading_h), key=lambda x: -a[x])
    fin = lambda v: None if v == inf else round(v, 1)
    return {"mode": bool(over_any or over_tr), "n_tradeable": len(a), "n_never_qualifying": sum(1 for v in a.values() if v == inf),
            "n_over_any": len(over_any), "n_trading": len(tr), "n_trading_over": len(over_tr),
            "max_age_any_h": ("never" if any(v == inf for v in a.values()) else fin(max(a.values()))) if a else None,
            "max_age_trading_h": ("never" if any(a[ss] == inf for ss in tr) else fin(max((a[ss] for ss in tr), default=0.0))) if tr else None,
            "over_any": over_any, "over_trading": over_tr, "any_h": any_h, "trading_h": trading_h}


def _refresh_priority(ages, live, trading, any_h=216.0, trading_h=96.0, board_gain=None):
    """-> ({sym_side: (class, sub, key)}, top-10 gainer table, {gainer: cadence_h}). Sort key (lower = launch first). class 0 = catch-up violators (trading > trading_h first, then the
    oldest), class 1 = LIVE-realised gainers whose cadence is DUE (top decile of gainers 6 h, next quartile 12 h, rest 24 h; ordered by 7-day
    realised P&L, the board gain only breaks ties), class 2 = everyone else, oldest first (never-boarded oldest). Losers/flat sit in class 2
    so they are never deferred behind a gainer beyond the 24 h cadence. Also returns the per-gainer cadence table. Pure (tested)."""
    inf = float("inf")
    live = live or {}
    bg = board_gain or {}
    gainers = sorted((ss for ss in ages if (live.get(ss) or {}).get("pnl_usd", 0.0) > 0), key=lambda x: (-live[x]["pnl_usd"], -(bg.get(x) or 0.0)))
    n = len(gainers)
    top, nxt = (max(1, -(-n // 10)) if n else 0), (max(1, -(-n // 4)) if n else 0)
    cad = {ss: (6.0 if i < top else 12.0 if i < top + nxt else 24.0) for i, ss in enumerate(gainers)}
    trset = set(trading or ())
    out = {}
    for ss, age in ages.items():
        a = inf if age is None else float(age)
        if (ss in trset and a > trading_h) or a > any_h:
            out[ss] = (0, 0.0 if (ss in trset and a > trading_h) else 1.0, -a)
        elif ss in cad and a >= cad[ss]:
            out[ss] = (1, float(gainers.index(ss)), -(bg.get(ss) or 0.0))
        else:
            out[ss] = (2, 0.0, -a)
    table = [{"sym_side": ss, "pnl_usd_7d": live[ss]["pnl_usd"], "board_age_h": (None if ages[ss] is None else round(ages[ss], 1)), "cadence_h": cad[ss],
              "next_due_in_h": (0.0 if ages[ss] is None else round(max(0.0, cad[ss] - ages[ss]), 1))} for ss in gainers[:10]]
    return out, table, cad


def _cont_overage(cont_sides, running, end_of, now_ts, cap_h):
    """USER 2026-10-20 (15-minute law): {sym: npz_age_h} for symbols with an UNFINISHED board, nothing running, whose NPZ last bar is older than
    cap_h. A running board finishes on its frozen NPZ (RULE#4); a RESUME past the cap must restart on a fresh NPZ instead. Pure (tested)."""
    out = {}
    for ss in sorted(cont_sides or ()):
        sy = ss.rsplit("_", 1)[0]
        if sy in out or f"{sy}_LONG" in running or f"{sy}_SHORT" in running:
            continue
        e = end_of(sy)
        if e and (now_ts - float(e)) / 3600.0 > cap_h:
            out[sy] = round((now_ts - float(e)) / 3600.0, 1)
    return out


def _rule_label(prio, cad_h=None):
    """Human rule name for a _refresh_priority tuple (the rule that placed it in the launch order). Pure (tested)."""
    c = prio[0]
    if c == 0:
        return "violator-trading-4d" if prio[1] == 0.0 else "violator-9d"
    if c == 1:
        return f"gainer-due-{int(cad_h) if cad_h else '?'}h"
    return "oldest"


def _trading_file_sides(home=None):
    """The 2026-10-18 V15_TRADING_CATCHUP cron's definition of 'trading', folded into the scheduler: every sym_side in ~/v15_trading_symsides.txt
    (all tra+trb+trc long/short) plus both sides of every symbol in ~/v15_priority_front.txt. Never raises."""
    out = set()
    home = home or os.path.expanduser("~")
    try:
        for ln in open(os.path.join(home, "v15_trading_symsides.txt")):
            ln = ln.strip()
            if ln:
                out.add(ln)
    except Exception:
        pass
    try:
        for sym in open(os.path.join(home, "v15_priority_front.txt")).read().replace(",", "\n").split():
            out.add(f"{sym}_LONG")
            out.add(f"{sym}_SHORT")
    except Exception:
        pass
    return out


def _order_audit(order_by_host, launched, explain, rule_of, age_of, top_n=10):
    """ORDER AUDIT (USER 2026-10-20 'pushes the correct ones in the right order always'). order_by_host {host: [sym, ...]} = the computed new-launch
    order; launched [(host, sym)] in launch sequence; explain(host, sym) -> reason string or None for a higher-ranked sym that was not launched.
    Returns {top, checks, violations}: a launch with an UNEXPLAINED higher-ranked, unlaunched sym ahead of it is an ORDER_VIOLATION. Pure (tested)."""
    top, checks, viol = {}, [], []
    by_host = {}
    for hn, sym in launched:
        by_host.setdefault(hn, []).append(sym)
    for hn, order in order_by_host.items():
        top[hn] = [{"sym": sy, "rule": rule_of(sy), "board_age_h": age_of(sy)} for sy in order[:top_n]]
        got = set(by_host.get(hn, []))
        for sym in by_host.get(hn, []):
            if sym not in order:
                continue
            ahead = [x for x in order[:order.index(sym)] if x not in got]
            named = {x: explain(hn, x) for x in ahead}
            bad = [x for x, r in named.items() if not r]
            checks.append({"host": hn, "launched": sym, "rank": order.index(sym) + 1, "skipped_ahead": {x: r for x, r in named.items() if r}})
            if bad:
                viol.append({"host": hn, "launched": sym, "rank": order.index(sym) + 1, "unlaunched_ahead": bad[:10], "cause": "higher-ranked sym skipped with no logged reason"})
    return {"top": top, "checks": checks, "violations": viol}


_ORDER_BY_HOST = {}  # per tick: host -> [sym] of the NEW-launch order (needs_slot entries only)


def side_cmd(host, sym, side, window, pdir, attempt, workers):
    """shell body for ONE side job (30D pilot | 365D verify | REPAIR loop). Detached by pair_launch_cmd. USER 2026-10-11: spawn-guarded (see _spawn_guard)."""
    r, ss, venue = host["root"], f"{sym}_{side}", venue_of(sym)
    chain, t = f"{pdir}/../chain", tmpl_path(host, venue, side)
    if window == "30D":
        env = f"V15_START_OVERRIDES=$(test -f ~/v15_autopsy_first/{ss}_autopsy_base.json && echo ~/v15_autopsy_first/{ss}_autopsy_base.json) V15_FRESH_RUN=1 V15_TEMPLATE_DEFAULTS=1 V15_ADAPT_BASELINE=0 V15_SKIP_LIVE_AT_DONE=1 V15_POSSYM_SAMPLING=0 V15_INERT_SAMPLE_EVERY=100 V15_POSSYM_MIN_N=1 V15_FILL_SLICE_S=4200 V15_DIAG_REPAIR_S=150 V15_ENDGAME_S=120 V15_FOCUS_S_WINNER=600 V15_FOCUS_S_MEDIOCRE=150 V15_FOCUS_S_LOSER=60 V15_TIER_BIG_MOVER=8 V15_FOCUS_S_BHCHASE=600 V15_FOCUS_TOP_K_BHCHASE=48 V15_POOL_BATCH_N=8 V15_UNWIRED_SKIP=0 V12_NPZ_CACHE=4 MALLOC_ARENA_MAX=2 V15_PROGRESS_DIR={pdir} V12_ALLOW_STALE_NPZ=$(test -s {pdir}/{ss}_v14_progress.json && echo 1 || echo 0) V15_DEFAULTS_ROUND=$(cat ~/v15_defaults_round.txt 2>/dev/null) CAT_SIDE_DEFAULTS_PATH=$(test -f ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json && echo ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json)"  # USER 2026-10-09: V15_SKIP_CAT_PERSYM_BASELINE REMOVED — worst2best boards run defaults+best simultaneously and START from the best-gain set (Oct-2 winner-select). Boards with autopsy/REDO base keep it (Oct-9 REDO ruling); TEMPLATE_DEFAULTS still never ingests mid-run (§64), only the E3 start may be per_sym best. USER 2026-10-10 throughput: sampling stays ON (reverted 0->1 within the hour — sample-free fills run ~9 rows/min vs ~80 sampled; the 10-min board comes from a per-row yellow cap + prior-ranked selection in the pilot, not from disabling sampling). USER 2026-10-10 pace emergency (measured <6/hr strict over 03:00-05:00Z vs 10/hr demand; user ordered lower low-n test frequency): INERT yellows 1/100, n_sym 1-2 join pos sampling; naked evals untouched, running pilots unaffected (env read at pilot startup).
        if os.environ.get("V15_SCHED_V17", "1") == "1":
            env += " V15_V17_ROUNDS=1"  # USER 2026-10-13: >=12 finalized V17 boards per hour per server (V15_SCHED_V17=0 = old pilot)
        if _CATCHUP_MODE[0]:
            env += " V18_CATCHUP=1"  # USER LAW 2026-10-20: coverage below V15_SCHED_CATCHUP_BELOW -> every new launch is a catch-up board (unknown env = harmless to an older pilot)
        env += gate_env_inline()  # USER 2026-10-16: the gate-benched launch env is authoritative (appended last = it wins)
        env += round_env_inline(_round_id_of(pdir))  # USER 2026-10-19: self-expiring, round-scoped override (see round_env_inline)
        body = f"{env} /home/niels/.conda/envs/binance_env_mac/bin/python -u v15_pilot.py --sym-side {ss} --template {t} --seq-mode worst2best --window-days 30 --vector-only --workers {workers}"
    elif window == "365D":
        body = (f"mkdir -p {chain}/v365 && nice -n 10 /home/niels/.conda/envs/binance_env_mac/bin/python -u tools/v15_365_cycle.py --sym-side {ss} --progress {pdir}/{ss}_v14_progress.json "
                f"--template {t} --work {chain}/v365 --rounds 0 --workers {workers}")
    elif window == "GS":  # USER 2026-10-07: GS-heal adjuster stage (standalone runner, heal3+); gated by V15_GS_FLEET=1
        body = (f"mkdir -p {chain}/gs_a{attempt} && nice -n 10 /home/niels/.conda/envs/binance_env_mac/bin/python -u tools/v15_graph_search.py --symsides {ss} --out {chain}/gs_a{attempt} "
                f"--method gs --budget ${{V15_GS_BUDGET:-1200}} --workers {workers} --parallel 1 --progress-dirs {pdir},data/reports/lifecycle_pilot")
    else:
        prev = (f"{pdir}/{ss}_v14_progress.json" if attempt == 1 else
                f"$(/home/niels/.conda/envs/binance_env_mac/bin/python -c \"import json;print(json.load(open('{chain}/repair_a{attempt-1}/{ss}_365_cycle.json'))['final_progress'])\")")
        body = (f"mkdir -p {chain}/repair_a{attempt} && nice -n 10 /home/niels/.conda/envs/binance_env_mac/bin/python -u tools/v15_365_cycle.py --sym-side {ss} --progress {prev} "
                f"--template {t} --work {chain}/repair_a{attempt} --rounds 3 --workers {workers}")
    return _spawn_guard(ss, window, body)


def _launched_actions(actions):
    """USER 2026-10-14 pair-in-RAM: a pure-30D multi-side launch collapses to the FIRST side — the
    pilot calculates the partner in-process before the symbol leaves RAM (V15_PAIR_MODE=0 restores
    one-process-per-side). Chain windows (365D/REPAIR/GS) are never collapsed."""
    acts = list(actions or [])
    if os.environ.get("V15_PAIR_MODE", "1") == "0":
        return acts
    if len(acts) > 1 and all(a.get("window") == "30D" for a in acts):
        return acts[:1]
    return acts


def pair_launch_cmd(host, actions, pdir, workers):
    """one ssh command that detaches every side job of a symbol (subshell > /dev/null so ssh returns at once)."""
    parts = []
    for a in _launched_actions(actions):
        body = side_cmd(host, a["sym"], a["side"], a["window"], pdir, a.get("attempt", 1), workers)
        parts.append(f"(cd {host['root']} && mkdir -p logs && setsid nohup bash -c {shlex.quote(body)} >> /tmp/sweep_{a['sym']}_{a['side']}_{a['window']}.log 2>&1 < /dev/null &) > /dev/null 2>&1")
    return "; ".join(parts)


def _dispatch_stale_recent(window_s=150.0, path="/tmp/v15_dispatch.log"):
    """USER LAW 2026-10-20: sides the dispatch job REFUSED on a stale NPZ (tail-verified ok=false) in the last window, with their tail age
    in minutes behind the latest closed bar, and the launches whose last bar was verified. Read-only, never raises."""
    out = {"refused": 0, "ages_min": {}, "verified_launch_ok": 0}
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 600000))
            lines = f.read().decode("utf8", "ignore").splitlines()[1:]
        cut = time.time() - window_s
        for ln in lines:
            try:
                r = json.loads(ln)
                t = time.mktime(time.strptime(r.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            except Exception:
                continue
            if t < cut or r.get("event") != "tail-verified":
                continue
            if r.get("ok") is False:
                out["refused"] += 1
                out["ages_min"][r.get("symbol")] = r.get("npz_age_min")
            elif r.get("ok") is True:
                out["verified_launch_ok"] += 1
    except Exception:
        pass
    try:  # the pilot's own start check (v15_pilot STALE_NPZ_SKIPPED) leaves <progress_dir>/v18_skips/<SS>.json - count the fresh ones with their tail age
        import glob
        pdir = open(os.path.expanduser("~/v15_current_progress_dir.txt")).read().strip()
        sk = {}
        for f in glob.glob(os.path.join(pdir, "v18_skips", "*.json")):
            if time.time() - os.path.getmtime(f) <= window_s * 4:
                d = json.load(open(f))
                if "STALE_NPZ" in str(d.get("reason")):
                    sk[d.get("sym_side")] = d.get("npz_age_min")
        out["pilot_skips_600s"] = sk
    except Exception:
        pass
    return out


def _npz_hold_triage(sym):
    """USER 2026-10-12 dispatch gate: a worker NPZ held for tail-staleness — is S1's own NPZ fresh (worker
    sync lag, converges on its own via sync_indicators.sh) or stale/missing (daemon/refetch problem, needs an
    agent)? S1-local, read-only, never raises. Returns a short tag for the skip log."""
    try:
        import numpy as _np
        import npz_cutoffs as _nc  # flat style: this module puts ROOT/tools on sys.path (the worker heredoc uses tools.npz_cutoffs instead)
        p = ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"
        if not p.exists():
            return "s1:missing"
        z = _np.load(str(p), allow_pickle=False)
        if "timestamps" not in z.files:
            return "s1:no-timestamps"
        t = _np.asarray(z["timestamps"], dtype="float64").ravel()
        t = t[_np.isfinite(t)]
        if t.size == 0:
            return "s1:no-timestamps"
        tail = float(t[-1])
        if tail > 1e12:
            tail /= 1000.0
        ok, _ = _nc.tail_currency(tail, venue_of(sym))
        return "s1:fresh" if ok else "s1:stale"
    except Exception as e:
        return f"s1:check-fail {e}"[:40]


def npz_prep(host, symbol):
    """USER 2026-10-13: "the npz goes WITH THE PUSH of the sym_side — it gets calculated on s1 before dispatching."
    So the regen runs HERE (the scheduler host = s1, the NPZ source) via tools/v15_npz_prep.py and the fresh NPZ is
    PUSHED to the target host with md5 verify (--push), before the pilot is launched. A target that is this host needs
    no push. Returns the tool's JSON ({ok, action, reason, npz_last_bar}); None = could not run (launch proceeds).
    Blocking by design: ok=False keeps the sym_side out of the dispatch instead of burning hours on a stale tail."""
    # SINGLE-FLIGHT (USER 2026-10-13): an NPZ rebuild spawns a multi-worker precompute. Several dispatches at once
    # therefore stampede s1 — observed 34 concurrent `backtest_v8_precompute` workers pinning it at load 46 while 3
    # orphaned blanket-refresh drivers ran. flock serialises every rebuild: one at a time, fleet-wide, no stampede.
    cmd = ["flock", "-w", os.environ.get("V15_NPZ_LOCK_WAIT_S", "600"), "/tmp/v15_npz_prep.lock",
           sys.executable, str(ROOT / "tools" / "v15_npz_prep.py"), "--symbol", str(symbol)]
    tgt = (host.get("ssh") or [None])[0]
    if tgt and str(tgt) not in ("127.0.0.1", "localhost", "::1"):
        cmd += ["--push", str(tgt)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        lines = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except Exception:
        return None


_NPZ_PREFLIGHT_ON = os.environ.get("V15_SCHED_NPZ_PREFLIGHT", "1") == "1"
_RUN_STALE_ACK = "i-accept-old-npz"


def _run_stale_allowed():
    """USER 2026-10-20: "no more backtests can run on old npz's". V15_SCHED_RUN_STALE=1 alone no longer buys it.

    That one variable used to be the whole escape hatch, and it was being honoured somewhere nobody was
    looking: measured 2026-10-20 from S1's /tmp/v15_dispatch.log, 612 dispatches that day logged
    `npz-degraded ... DEGRADED-STALE-TAIL(allowed by V15_SCHED_RUN_STALE=1)` and then launched anyway — the
    worst nameable one SAP_LONG at 14:21:11Z on a tail 18.6 h / 73 bars behind a 0.85 h limit, while the
    fleet scheduler's own cron had V15_SCHED_RUN_STALE=0. Running on bars that old is the silent 0-trades
    producer, and a board published from it wears a date its data does not back.

    Deliberate use survives, but it now takes two keys in the same breath:
        V15_SCHED_RUN_STALE=1 V15_SCHED_RUN_STALE_ACK=i-accept-old-npz
    so no inherited or forgotten environment can turn the law off by itself."""
    if os.environ.get("V15_SCHED_RUN_STALE", "0") != "1":
        return False
    return os.environ.get("V15_SCHED_RUN_STALE_ACK", "") == _RUN_STALE_ACK


def _s1_source_tail_ok(symbol):
    """(ok, why) — can the JIT tail refresh still reach the latest closed bar for this symbol from HERE?

    Under the JIT law S1's own NPZ tail is legitimately behind until tools/v15_dispatch_job.tail_refresh
    appends the missing closed bars (~9 s), so "S1 is behind" is not by itself a reason to skip. What IS a
    reason is a gap no tail-append can close: the refresh pulls a bounded window (fetch_fresh asks for
    gap+3 bars, stocks come from the local klines cache), so a tail tens of bars behind needs a rebuild, and
    booking a dispatch for it only burns the slot — the job refreshes, still misses the latest bar and
    refuses with action=stale-npz. Measured 2026-10-20: SAP's source NPZ was 73 bars / 18.6 h behind.

    So: within V15_SCHED_SRC_MAX_BARS_BEHIND bars -> the job can fix it, proceed. Beyond -> ask for the
    sanctioned prep and let the next symbol take the slot. No NPZ at all is handled by the npz-missing
    branch, and every error fails OPEN (the dispatch job verifies the bytes again before it launches)."""
    try:
        if str(ROOT / "tools") not in sys.path:
            sys.path.insert(0, str(ROOT / "tools"))
        from npz_bar_refresh import npz_last_ts as _last
        import npz_cutoffs as _nc

        last = _last(str(ROOT / "backtest_v8" / "indicators"), symbol)
        if last is None:
            return True, "no-npz-ts"
        cut = _nc.last_closed_bar() if _nc.is_crypto_symbol(symbol) else _nc.last_rth_close_bar()
        if not cut:
            return True, "no-cutoff"
        behind = int((int(cut) - int(last)) // _nc.BAR)
        limit = int(os.environ.get("V15_SCHED_SRC_MAX_BARS_BEHIND", "8"))
        if behind > limit:
            return False, f"source {behind} bars ({behind * _nc.BAR / 3600.0:.1f}h) behind > {limit}"
        return True, f"source {behind} bars behind"
    except Exception as exc:
        return True, f"source-check-unavailable ({type(exc).__name__})"
_NPZ_DEFER_MAX = int(float(os.environ.get("V15_SCHED_NPZ_DEFER_MAX", "3") or 3))
_NPZ_REBUILD_COOLDOWN_S = float(os.environ.get("V15_SCHED_NPZ_REBUILD_COOLDOWN_S", "1800") or 1800)
_PAIR_FRESH_REQUEUE = os.environ.get("V15_SCHED_PAIR_FRESH_REQUEUE", "0") == "1"


def _prep_disposition(pr):
    """Thin adapter: the meaning of a prep verdict lives ONCE, in tools/v15_dispatch_job.prep_disposition,
    next to the prep() that produces it (that module is import-safe — no argv read at import time).
    PUSH ORDER: v15_dispatch_job.py must reach a host BEFORE this file does.
    The fallback is deliberately stricter than the real table, never looser: launch only on an explicit
    ok=True and never request a rebuild, so a missing dependency can only make the gate safer."""
    try:
        if str(ROOT / "tools") not in sys.path:
            sys.path.insert(0, str(ROOT / "tools"))
        from v15_dispatch_job import prep_disposition as _pd
    except Exception:
        if pr is not None and pr.get("ok") is True:
            return True, "ok", str(pr.get("reason") or ""), False
        return False, "retry", "prep_disposition unavailable — fail closed", False
    return _pd(pr)


def _npz_preflight(symbol, window_days=30, ind_dir=None, check_freshness=True):
    """Verify the NPZ that will ACCOMPANY the dispatch — on S1, the source the push copies from — BEFORE a
    board is launched on it (USER 2026-10-15: "BEFORE any calculations start a verification of npz data").

    The prep tool's own ok=True is NOT sufficient: measured `v15_npz_prep.py --symbol ATOMUSDT --dry-run`
    returned ok=true/action=would_regen while reporting `tail 24.0 bars behind latest closed 15m` and
    kline_lag_h 0.7, and a rebuild in flight leaves a 1-bar NPZ visible to dispatch (census 2026-10-15:
    ADP/AEM/ALMU/AXTI/CDW all bars=1 span=0.0d while 3 precompute jobs rebuilt them).

    check_freshness=False is for the PRE-dispatch structural screen only: under the JIT law S1's tail is
    legitimately behind until the dispatch job refreshes it, so staleness there is not a defect. The
    post-prep call runs with freshness ON, against the bytes that will actually travel.

    -> (ok, reason, res). A missing preflight module fails OPEN (logged) so the fleet never deadlocks on a
    tool that has not been pushed yet; a real verdict is authoritative and fails CLOSED."""
    if not _NPZ_PREFLIGHT_ON:
        return True, "preflight-off", {}
    try:
        if str(ROOT / "tools") not in sys.path:
            sys.path.insert(0, str(ROOT / "tools"))
        import v15_npz_preflight as _pf
    except Exception as e:
        return True, f"preflight-unavailable ({type(e).__name__})", {}
    _lim = None
    if check_freshness:
        try:
            from v15_dispatch_job import npz_tail_limit_h as _tl
            _lim = _tl(symbol)  # USER 2026-10-15: inside RTH a stock tail must be from the latest market hour
        except Exception:
            _lim = None
    try:
        res = _pf.verify(symbol, window_days=window_days, ind_dir=ind_dir, check_freshness=check_freshness, max_tail_age_h=_lim)
    except Exception as e:
        return True, f"preflight-crash ({type(e).__name__})", {}
    reasons = [str(x) for x in (res.get("reasons") or [])]
    # Same rule as v15_dispatch_job.npz_preflight: older bars are a FLEET policy (V15_SCHED_RUN_STALE, and the
    # tail-stale branch of the is_ready gate above), not this gate's call — blocking on them stopped every
    # crypto dispatch on 2026-10-15. A structural/thin/holed build always blocks: that is the 0-trades producer.
    if not res.get("ok") and reasons and all(r.startswith("stale tail:") for r in reasons) and _run_stale_allowed():
        return True, "DEGRADED-STALE-TAIL(allowed by V15_SCHED_RUN_STALE=1 + ACK): " + "; ".join(reasons)[:220], res
    return bool(res.get("ok")), "; ".join(reasons)[:300], res


def _npz_gap_screen(symbol, ind_dir=None):
    """Second pre-dispatch screen, STOCKS ONLY: session count + intra-window holes, complementary to
    _npz_preflight (structural rot + staleness). USER 2026-10-15: "as stocks are where the real money goes".

    Measured on s1 2026-10-15: 32 of 162 stock NPZs are holed — 17 x "20 sessions in last 60d, 29 d gap
    (2026-08-26->09-24)", 5 x "1 session" (ADP AEM ALMU AXTI CDW), 5 x "19 sessions, 35 d gap" — and those
    PASS a span/window check, because the tail is current and bars exist inside the window; only the session
    census sees the hole. Meanwhile 36 of 37 rows in data/alerts/stock_npz_gap.jsonl were
    `v15_dispatch_job.fast_push refused=True` (GOOGL at 04:02, 04:06, 04:10, 04:14 — every tick, no rebuild
    requested, no escalation), so the refusal has to move BEFORE the dispatch and share the skip ->
    one-rebuild -> requeue -> alert machinery instead of looping.

    Uses stock_npz_gap_guard.check_timestamps, which is PURE: gate()/gate_npz() append an alert row on every
    call and that append is exactly the per-tick noise being replaced. Fails OPEN (an unreadable or missing
    NPZ is _npz_preflight's refusal, not this one's). -> (ok, reason, info)."""
    if venue_of(symbol) != "stocks" or os.environ.get("V15_SCHED_NPZ_GAP_SCREEN", "1") != "1":
        return True, "", {}
    try:
        import numpy as _np
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.stock_npz_gap_guard import check_timestamps as _ct
        p = pathlib.Path(ind_dir or (ROOT / "backtest_v8" / "indicators")) / f"{str(symbol).upper()}.npz"
        with _np.load(str(p), allow_pickle=True) as z:
            info = _ct(z["timestamps"])
    except Exception as e:
        return True, f"gap-screen-unavailable ({type(e).__name__})", {}
    return bool(info.get("ok")), str(info.get("reason") or "")[:300], info


def _npz_defer_note(st, sym, kind, reason, now_ts=None, cooldown_s=None, max_defer=None, may_rebuild=True):
    """Bookkeep one deferral in the scheduler's own persistent state and answer whether a rebuild should be
    REQUESTED now. Clock and limits are injected so this is testable. -> (want_rebuild, n, alert).

    A rebuild is requested at most once per cooldown per symbol ("once per deferral, not per tick"), never
    for a transient kind, never for an alert kind, and never once the symbol has been deferred more than
    V15_SCHED_NPZ_DEFER_MAX times — past that it is surfaced instead of spun."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    cd = _NPZ_REBUILD_COOLDOWN_S if cooldown_s is None else float(cooldown_s)
    mx = _NPZ_DEFER_MAX if max_defer is None else int(max_defer)
    book = st.setdefault("npz_defer", {})
    e = book.setdefault(sym, {"n": 0, "first": now_ts, "last_rebuild": None})
    e["n"] = int(e.get("n") or 0) + 1
    e["last"], e["kind"], e["reason"] = now_ts, kind, str(reason)[:300]
    alert = bool(e["n"] > mx or kind == "alert")
    # a never-requested symbol may rebuild at once (last_rebuild None); after that, once per cooldown. Measuring
    # the first request against 0.0 would have made the cooldown depend on the clock's origin, not on elapsed time.
    _lr = e.get("last_rebuild")
    _due = _lr is None or (now_ts - float(_lr)) >= cd
    want = bool(may_rebuild and kind == "block" and not alert and _due)
    if want:
        e["last_rebuild"] = now_ts
    return want, e["n"], alert


def _npz_defer_clear(st, sym):
    """A symbol that passes the preflight again is re-admitted WITH PRIORITY (it already lost its turn).
    -> the deferral count it carried (0 = it was never deferred), so the caller can prioritise only real returns."""
    return int((st.setdefault("npz_defer", {}).pop(sym, None) or {}).get("n") or 0)


_ASYNC_DISPATCH = os.environ.get("V15_SCHED_ASYNC_DISPATCH", "1") == "1"
_PAIR_ALWAYS = os.environ.get("V15_SCHED_PAIR_ALWAYS", "1") == "1"
_ALL_LAUNCH_NPZ = os.environ.get("V15_SCHED_ALL_LAUNCH_NPZ", "1") == "1"


def _parity_gate_ok(venue, side, template_dir, cache, gate=None):
    """2026-10-13: run the pilot's own startup parity gate (switch_parity.startup_gate, cached, ~0.07 s) once per
    cat_side per tick. A cat_side whose defaults are disparate makes every v15_pilot exit at startup ("[PARITY-GATE]
    ... NOT running") — measured 16:38Z: all crypto 30D launches refused, each burning a resume slice of an
    unfinished board. Gate crash = allow (same as the pilot). Kill switch V15_SCHED_PARITY_PREFLIGHT=0."""
    if os.environ.get("V15_SCHED_PARITY_PREFLIGHT", "1") != "1":
        return True
    k = (venue, side)
    if k not in cache:
        cs = f"{'CRYPTO' if venue == 'crypto' else 'STOCKS'}_{side}"
        try:
            if gate is None:
                if str(ROOT) not in sys.path:
                    sys.path.insert(0, str(ROOT))
                import switch_parity as _spg
                gate = _spg.startup_gate
            g = gate(cs, template_path=str(ROOT / template_dir / f"TEMPLATE_{cs}.xlsx"))
            cache[k] = (bool(g.get("ok")), "; ".join(str(x) for x in (g.get("hard") or [])[:3])[:300])
        except Exception as e:
            cache[k] = (True, f"gate-crash {type(e).__name__}")
    return cache[k][0]
_DISPATCH_GRACE_S = float(os.environ.get("V15_DISPATCH_GRACE_S", "2400"))


def _dispatch_q_mark(st, keys, now_ts, grace_s=None):
    """2026-10-13: remember async dispatches (key -> ts) so later ticks never re-dispatch while the job builds the NPZ;
    entries older than 2x grace are pruned (a job that died without launching frees the side again)."""
    g = _DISPATCH_GRACE_S if grace_s is None else float(grace_s)
    dq = st.setdefault("dispatch_q", {})
    for k in [k for k, v in dq.items() if (now_ts - float(v or 0)) > 2 * g]:
        dq.pop(k, None)
    for k in keys:
        dq[k] = now_ts
    return dq


DRAIN_FILE = ROOT / "data" / "fleet_drain_hosts.json"
PIN_FILE = ROOT / "data" / "v15_sched_pinned_sides.json"
FREEZE_FILE = ROOT / "data" / "npz_frozen_syms.json"
_PIN_SLICE_CAP = 40


def _load_drain(path=None):
    """2026-10-13: hosts that get NO new launches (running pilots finish untouched). Lives OUTSIDE
    fleet_hosts_final.json because the autopilot self-heal rewrites that file from hcloud. Union of
    data/fleet_drain_hosts.json {"hosts": [...]} and env V15_SCHED_DRAIN_HOSTS (comma list). Reversible: empty the list."""
    out = {x.strip() for x in os.environ.get("V15_SCHED_DRAIN_HOSTS", "").split(",") if x.strip()}
    try:
        out |= {str(x) for x in (json.loads(pathlib.Path(path or DRAIN_FILE).read_text()).get("hosts") or [])}
    except Exception:
        pass
    return out


def _load_pinned(path=None):
    """2026-10-13: operator-pinned sym_sides {"sides": [...], "hosts": [...]} — launched before everything else,
    exempt from tier deferral / fresh-skip / base wait / venue phase / quota / allowlist, only on "hosts"
    (empty = any non-drained host). A pinned side that is done (final_gain in the round) is left alone."""
    try:
        d = json.loads(pathlib.Path(path or PIN_FILE).read_text())
        return {str(x) for x in (d.get("sides") or [])}, {str(x) for x in (d.get("hosts") or [])}
    except Exception:
        return set(), set()


def _slice_progress_reset(st, key, progress_mt):
    """2026-10-13 RESUME: the 20-slice budget exists to stop crash loops. A board whose progress json advanced since
    its previous launch is making progress, so its slice counter restarts — an unfinished board that writes rows is
    never exhausted (measured: DOGEUSDC_LONG at 15/20 slices burned by start-up refusals, not by work)."""
    if progress_mt is None:
        return
    sm = st.setdefault("slice_mt", {})
    prev = sm.get(key)
    if prev is not None and float(prev) != float(progress_mt):
        st.setdefault("slices", {})[key] = 0
    sm[key] = float(progress_mt)


def _live_dispatch_syms():
    """Symbols with a v15_dispatch_job.py process alive on this host (the scheduler host runs every job)."""
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    syms = set()
    for line in out.splitlines():
        if "v15_dispatch_job.py" in line and "--symbol" in line:
            a = line.split()
            try:
                syms.add(a[a.index("--symbol") + 1])
            except (ValueError, IndexError):
                pass
    return syms


def _prune_finished_dispatches(st, now_ts, live, min_age_s=180.0):
    """A dispatch whose job has exited (launched -> the pilot is visible as running/started; skipped -> retry) leaves
    the grace at once: the 40-min grace blocked the relaunch of ABBV_LONG after its pilot died (17:06Z)."""
    if live is None:
        return []
    dq, dh = st.get("dispatch_q") or {}, st.get("dispatch_host") or {}
    gone = []
    for k in list(dq):
        sym = k.split("|")[0].rsplit("_", 1)[0]
        try:
            age = now_ts - float(dq[k] or 0)
        except (TypeError, ValueError):
            age = 1e9
        if age > min_age_s and sym not in live:
            dq.pop(k, None)
            gone.append(k)
    for sym in [x for x in dh if x not in {k.split("|")[0].rsplit("_", 1)[0] for k in dq}]:
        dh.pop(sym, None)
    return gone


def _dispatching_syms(st, now_ts, grace_s=None):
    """{sym: host} for async dispatches still inside their grace (NPZ being built, pilot not yet running)."""
    g = _DISPATCH_GRACE_S if grace_s is None else float(grace_s)
    dq, dh = st.get("dispatch_q") or {}, st.get("dispatch_host") or {}
    out = {}
    for k, ts in dq.items():
        try:
            fresh = (now_ts - float(ts or 0)) <= g
        except (TypeError, ValueError):
            fresh = False
        sym = k.split("|")[0].rsplit("_", 1)[0]
        if fresh and sym in dh:
            out[sym] = dh[sym]
    for sym in [x for x in dh if x not in {k.split("|")[0].rsplit("_", 1)[0] for k in dq}]:
        dh.pop(sym, None)
    return out


# every file v15_universe.build() derives the tradeable universe from (crypto account lists + tradeable_keys.json,
# stocks from symbols_trb_long/short.json). Watched together so a change to ANY of them rebuilds the universe.
_TRADEABLE_SOURCES = ("tradeable_keys.json", "symbols_trb_long.json", "symbols_trb_short.json",
                      "symbols_flz_long.json", "symbols_flz_short.json", "symbols_men.json",
                      "symbols_men_long.json", "symbols_men_short.json",
                      "symbols_ang_long.json", "symbols_ang_short.json")

_FILE_TRADEABLE = {"ts": 0.0, "set": None}
_TRADEABLE_MEMO = {"ts": 0.0, "set": None}
_TRADEABLE_TTL_S = float(os.environ.get("V15_TRADEABLE_TTL_S", "5"))


def _file_tradeable():
    """Side-exact tradeable sym_sides straight from the files the user named: tradeable_keys.json (crypto,
    acct:SYM_SIDE), symbols_trb_long/short.json and the STATIC TradierConfig.TRADIER_MANDATORY_{LONG,SHORT}_TRB
    (stocks). Cached 60 s. Returns an empty set on any failure so the caller fails OPEN — a scheduler that stops
    dispatching costs more than a wasted board."""
    if _FILE_TRADEABLE["set"] is not None and time.time() - _FILE_TRADEABLE["ts"] < 60:
        return _FILE_TRADEABLE["set"]
    out = set()
    try:
        keys = _jload(ROOT / "tradeable_keys.json", None)
        keys = keys.get("keys") if isinstance(keys, dict) else keys
        for k in keys or []:
            out.add(str(k).split(":", 1)[-1].strip().upper())
        for fname, side in (("symbols_trb_long.json", "LONG"), ("symbols_trb_short.json", "SHORT")):
            v = _jload(ROOT / fname, None)
            v = v.get("symbols") if isinstance(v, dict) else v
            for sym in v or []:
                out.add(f"{str(sym).strip().upper()}_{side}")
        sys.path.insert(0, str(ROOT))
        from config_tradier import TradierConfig as _TC
        c = _TC()
        for attr, side in (("TRADIER_MANDATORY_LONG_TRB", "LONG"), ("TRADIER_MANDATORY_SHORT_TRB", "SHORT")):
            for sym in getattr(c, attr, None) or []:
                out.add(f"{str(sym).strip().upper()}_{side}")
    except Exception as exc:
        print(f"[sched] file tradeable set unavailable ({exc!r}) — intersection skipped (fail-open)", flush=True)
        return set()
    _FILE_TRADEABLE.update(ts=time.time(), set=out)
    return out


def _tradeable_now():
    """The tradeable sym_side set as it is RIGHT NOW, both venues (U.build -> allowed_sym_sides, deny-filtered).

    USER 2026-10-15: "make sure v15_fleet_scheduler always checks whether a sym_side is still tradeable before
    queuing it as the list changes constantly". The universe is only rebuilt when tradeable_keys.json is newer than
    the build, so a sym_side selected earlier in a tick — or already sitting in the dispatch queue — could still be
    launched after it stopped being tradeable, burning a worker slot on a board nothing will ever trade.

    U.build is 2-11 ms, so this is re-read per dispatch behind a 5 s memo. NOTE it covers BOTH venues on purpose:
    tradeable_keys.json is crypto-only (247 keys), so checking that file alone would drop every stock dispatch.
    allowed_sym_sides is 131 crypto + 133 stocks.

    On a read failure it returns the last good set, and None if there has never been one — the caller then dispatches
    anyway. That is deliberate: a wasted sweep costs compute, a scheduler that stops dispatching costs every board.
    """
    now = time.time()
    if _TRADEABLE_MEMO["set"] is not None and now - _TRADEABLE_MEMO["ts"] < _TRADEABLE_TTL_S:
        return _TRADEABLE_MEMO["set"]
    try:
        fresh = {str(x).upper() for x in (U.build(ROOT).get("allowed_sym_sides") or [])}
        # USER 2026-10-16: "make sure ONLY tradeable_keys are considered (or tradeable stock) ... v15_fleet_scheduler
        # needs to check BEFORE WASTING CPU on npz refresh, dispatching, server time". U.build's allowed_sym_sides is
        # 3 wider than the raw files (DVN_LONG, JNJ_SHORT, VZ_SHORT — the known non-tradeable OPEN positions), so the
        # side-exact file set is intersected in here, at the gate every dispatch already passes through, which is
        # upstream of v15_dispatch_job's tail_refresh + NPZ push. The mandatory stock lists are NOT lost: both sides
        # read TRADIER_MANDATORY_{LONG,SHORT}_TRB (v15_universe.py:123 and _file_tradeable below), verified 0 sym_sides
        # dropped by the intersection that the files allow. Fail-open: an empty/unreadable file set changes nothing.
        _ft = _file_tradeable()
        if fresh and _ft:
            _cut = fresh - _ft
            if _cut:
                print(f"[sched] tradeable intersect: {len(fresh)} -> {len(fresh & _ft)} sym_sides, not in tradeable_keys/trb/mandatory: {sorted(_cut)[:12]}", flush=True)
            fresh &= _ft
        if fresh:
            _TRADEABLE_MEMO.update(ts=now, set=fresh)
            return fresh
        print("[sched] tradeable re-check: empty allowed_sym_sides — keeping last known set", flush=True)
    except Exception as exc:
        print(f"[sched] tradeable re-check failed ({exc!r}) — keeping last known set", flush=True)
    return _TRADEABLE_MEMO["set"]


def _drop_untradeable(symbol, acts, tag):
    """Filter a dispatch down to sides that are STILL tradeable. Returns the surviving actions (possibly empty)."""
    allowed = _tradeable_now()
    if not allowed:
        return list(acts)
    keep, dropped = [], []
    for a in acts:
        key = f"{a.get('sym')}_{a.get('side')}".upper()
        (keep if key in allowed else dropped).append(a)
    if dropped:
        names = ",".join(f"{a.get('sym')}_{a.get('side')}" for a in dropped)
        print(f"[sched] NOT_TRADEABLE_ANYMORE skip {names} (tag={tag}) — dropped at dispatch, universe moved on", flush=True)
    return keep


def _dispatch_async(host, symbol, acts, pdir, workers, tag):
    """2026-10-13: hand NPZ rebuild + md5 push + pilot launch to tools/v15_dispatch_job.py (detached, setsid) so the
    tick returns in seconds. The job launches only after its NPZ is ready (JIT law). The caller holds the slot + a
    dispatch grace (st["dispatch_q"]) so the next tick never double-dispatches."""
    acts = _drop_untradeable(symbol, acts, tag)
    if os.environ.get("V15_SCHED_HARD_RECENT", "1") == "1":
        _keep = []
        for _a in acts:
            _ss_a = f"{_a.get('sym', symbol)}_{_a.get('side')}"
            if _a.get("window", "30D") == "30D" and _boarded_within(_ss_a, _HARD24.get("at"), _HARD24.get("now") or time.time(), pins=_HARD24.get("pins")):
                print(f"[sched] HARD-24H {_ss_a}: boarded {(float(_HARD24.get('now') or time.time()) - float(_HARD24['at'][_ss_a])) / 3600.0:.1f} h ago (published or refused) — never re-dispatched inside 24 h (tag={tag})", flush=True)
                _HARD24.setdefault("blocked", []).append(_ss_a)
                continue
            _keep.append(_a)
        acts = _keep
    if not acts:
        print(f"[sched] dispatch SKIPPED for {symbol}: no tradeable side left (tag={tag})", flush=True)
        return False
    import tempfile
    tgt = (host.get("ssh") or [None])[0]
    push = str(tgt) if tgt and str(tgt) not in ("127.0.0.1", "localhost", "::1") else ""
    fd, cmd_file = tempfile.mkstemp(prefix=f"v15_dispatch_{symbol}_", suffix=".sh", dir="/tmp")
    with os.fdopen(fd, "w") as f:
        f.write(pair_launch_cmd(host, acts, pdir, workers))
    cmd = [sys.executable, str(ROOT / "tools" / "v15_dispatch_job.py"), "--symbol", str(symbol), "--push", push, "--via", host.get("_via", host["ssh"][0]), "--cmd-file", cmd_file, "--tag", tag]
    subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open("/tmp/v15_dispatch_job.err", "a"), start_new_session=True)
    return True


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
    try:
        # USER 2026-10-15: "tradeable_keys is crypto only, symbols_trb_long/short are the stocks version that also
        # keeps updating". This trigger watched ONLY tradeable_keys.json, so a stock list change never rebuilt the
        # universe and the scheduler kept selecting stock sym_sides from a stale list (v15_universe:120 builds stocks
        # from symbols_trb_{long,short}.json, crypto from symbols_{flz,men,ang}_{long,short}.json). Every source the
        # universe reads is watched now, and the log says which one moved.
        _tk_newest, _tk_src = 0.0, ""
        for _name in _TRADEABLE_SOURCES:
            try:
                _m = (ROOT / _name).stat().st_mtime
            except OSError:
                continue
            if _m > _tk_newest:
                _tk_newest, _tk_src = _m, _name
        _tk_mtime = _tk_newest
        _ub = datetime.datetime.fromisoformat(uni.get("built_utc", "")).timestamp()
    except Exception:
        _tk_mtime, _ub, _tk_src = 0, 0, ""
    if _tk_mtime > _ub:
        # USER 2026-10-10: tradeable universe churns live — a universe older than its sources
        # schedules dead work. Rebuild in-tick (quorum-guarded: a bad build serves last_good).
        uni = U.build(ROOT, now); how = "rebuilt-tradeable-changed"
        print(f"[sched] universe rebuilt: {_tk_src or 'a tradeable source'} newer than universe build", flush=True)
    stocks, crypto = list(uni["stocks"]), list(uni["crypto"])
    for _pss in sorted(_load_pinned()[0]):  # 2026-10-13: operator-pinned sides are scheduled even outside today's universe
        _psym = _pss.rsplit("_", 1)[0]
        _plst = crypto if venue_of(_psym) == "crypto" else stocks
        if _psym not in _plst:
            _plst.append(_psym)
    rank = {v: legacy_rank(cfg, v) for v in ("stocks", "crypto")}
    for v, lst in (("stocks", stocks), ("crypto", crypto)):
        lst.sort(key=lambda s: (rank[v].get(s, 10 ** 6), s))  # worst-first per the previous order file, unknown last
    st = load_state()
    st = _fresh_daily_state(st, now.strftime("%Y%m%d"))
    st.setdefault("held", {}); st.setdefault("attempts", {}); st.setdefault("slices", {}); st.setdefault("ready", {}); st.setdefault("launched_at", {})
    try:
        _now0 = now.timestamp()
        st["launched_at"] = {k: v for k, v in st["launched_at"].items() if _now0 - float(v or 0) < 3600}
    except Exception:
        pass
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
                _reap_started = {x for _s in stats.values() if _s for x in (_s.get("started") or [])}
                for k_, w_ in _reap_burns(r_["actions"], st.get("last_act")).items():
                    _rk_ss, _, _rk_w = (k_.split("|") + ["", ""])[:3]
                    if _rk_w == "30D" and _rk_ss in _reap_started:
                        st["slices"][k_] = st["slices"].get(k_, 0) + 4 * w_  # USER 2026-10-12: reaps burn the slice budget (x4 preserves parks-after-2-trips under cap 20)
                    else:
                        st["attempts"][k_] = st["attempts"].get(k_, 0) + w_
                for a_ in r_["actions"]:
                    print(f"[reap] {h_['name']} {a_['ss']} {a_['why']} pids={a_['pids']}", flush=True)
                for a_ in r_["actions"]:
                    if a_["why"].startswith(("stuck:", "wedged:", "hardcap")):
                        try:
                            with open(ROOT / "data" / "reports" / "stuck_boards.jsonl", "a", encoding="utf-8") as _fq:
                                _fq.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": h_["name"], "ss": a_["ss"], "why": a_["why"], "pids": a_["pids"], "review": "pending-muse-agent"}) + "\n")
                        except Exception:
                            pass
    cur_pdir = next((x["pdir"] for x in stats.values() if x and x.get("pdir")), None)
    if cur_pdir and st.get("attempts_pdir") != cur_pdir:
        st["attempts"] = {}
        st["slices"] = {}
        st["attempts_pdir"] = cur_pdir  # SLOT fix 2026-10-01: day-level attempt counters carried over from the previous run's progress dir exhausted every chain action of the new run, so pairs held slots forever
    is_open, mto = us_market_open(now), minutes_to_open(now)
    vorder = venue_order(now)
    # ---- merge host views
    running, owner, started, done, quar, redo, v365, repair, gs, gains = {}, {}, set(), set(), set(), set(), {}, {}, {}, {}
    done_host, v365_host, rep_host = {}, {}, {}
    _drained = _load_drain()
    _pin_sides, _pin_hosts = _load_pinned()
    started_mt = {}
    _newest_mt = {}
    for n, s in stats.items():
        if not s:
            continue
        for ss, r in s["running"].items():
            running[ss] = {**r, "host": n}
            owner.setdefault(ss.rsplit("_", 1)[0], n)
        for ss, _mt in (s.get("started_mt") or {}).items():
            if n not in _drained and _mt is not None and _mt > started_mt.get(ss, -1):
                started_mt[ss] = _mt
        for ss in s["started"]:
            started.add(ss)
            if n not in _drained:  # 2026-10-13: a draining host (s5/s6 about to be deleted) owns nothing new — its boards re-home on s1/s2
                owner.setdefault(ss.rsplit("_", 1)[0], n)
        for ss, _mt in (s.get("started_mt") or {}).items():
            if _mt is not None and _mt > _newest_mt.get(ss, (-1, None))[0]:
                _newest_mt[ss] = (_mt, n)
        for ss in s["done"]:
            done.add(ss); done_host.setdefault(ss, n)
        quar |= set(s.get("quarantined", []))
        redo |= set(s.get("redo", []))
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
    # 2026-10-13 RESUME (newest board wins): a side counts as done only when its NEWEST progress (any host, drained
    # included) is the finished one — an older final elsewhere must not hide a newer half-filled board (GALAUSDT_LONG:
    # s5 final 39.26 at 3280 rows vs a newer 289-row S1 board).
    _stale_done = sorted(ss for ss in done if ss in _newest_mt and ss not in set((stats.get(_newest_mt[ss][1]) or {}).get("done") or []))
    for ss in _stale_done:
        done.discard(ss)
        done_host.pop(ss, None)
    stocks, crypto = _deny_tokenized(stocks), _deny_tokenized(crypto)  # USER 2026-10-13: tokenized stocks (AAPLUSDT…) are NOT tradeable — never scheduled, never held
    stocks, crypto = _deny_untradeable(stocks, "stocks"), _deny_untradeable(crypto, "crypto")  # USER 2026-10-15: outside the tradeable universe = no NPZ build, no board, no CPU
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

    _npz_dead = _npz_dead_syms(readiness_by_host) if os.environ.get("V15_SCHED_NPZ_DEAD_PHASE", "1") == "1" else set()
    if _npz_dead:
        print(f"[sched] NPZ-DEAD {len(_npz_dead)} symbol(s) unloadable on every host -> excluded from the venue phase (rebuild them): {sorted(_npz_dead)[:12]}", flush=True)

    # ---- per-symbol chain status
    def _same_npz_as_last_board(sym, side):
        """USER 2026-10-16: refresh = re-measure on NEW bars. The newest publish manifest's npz_md5 equal to the current S1
        NPZ md5 = no bar arrived since that board (stocks over a weekend) -> a re-board only re-measures noise; skip it and
        give the slot to a side with new data. Missing manifest / md5 = not same (fail open). V15_SCHED_REFRESH_SAME_NPZ=1 =
        refresh anyway."""
        if os.environ.get("V15_SCHED_REFRESH_SAME_NPZ", "0") == "1":
            return False
        import glob as _gl
        try:
            mans = sorted(_gl.glob(os.path.join(os.path.expanduser("~"), "binance-sandbox", "SPREADSHEETS", "V15_V16_CELL_BY_CELL", f"{sym}_{side}_*_manifest.json")), key=os.path.getmtime)
            if not mans:
                return False
            last = str((json.loads(pathlib.Path(mans[-1]).read_text()) or {}).get("npz_md5") or "")
            cur = _npz_md5_cached(sym, _npz_md5_cache)
        except Exception:
            return False
        if last and cur and last == cur:
            _same_npz_skips.append(f"{sym}_{side}")
            return True
        return False

    def side_state(sym, side, host_name, _tiers=None):
        ss = f"{sym}_{side}"
        if ss in running:
            return "running", None
        _no_chain = os.environ.get("V15_SCHED_NO_CHAIN") == "1" and not (ROOT / "data" / "autopilot" / "chain_mode.flag").exists()
        _quar = _quar_terminal(ss, quar, _no_chain)
        _non_short = side == "SHORT" and venue_of(sym) == "stocks" and sym in NON_SHORTABLE
        # USER 2026-10-12: refresh (rm progress + requeue) skips fresh (<4d matrix) sides — without this the rm fires and fresh-skip blocks the relaunch, destroying the board.
        if _refresh_due(ss, _tiers, _now_ts) and not _quar and not _non_short and ss not in _fresh_map and not _same_npz_as_last_board(sym, side) and not _unmoved(sym, side) and not _boarded_within(ss, _HARD24.get("at"), _now_ts, pins=_HARD24.get("pins")):
            _rm_mtime = ((_tiers.get(ss) or {}).get("mtime"))
            _rkey = f"{ss}|{_rm_mtime}"
            _rd = st.setdefault("refresh_done", {})
            if _rd.get(_rkey) != cur_pdir:
                _rm_host = next((h for h in hosts if h["name"] == done_host.get(ss)), None)
                if _rm_host is not None and _refresh_tick_n[0] < _REFRESH_MAX_RM_PER_TICK and cur_pdir:
                    _pq = shlex.quote(cur_pdir)
                    _sq = shlex.quote(ss)
                    _rm_cmd = (f"rm -f {_pq}/{_sq}_v14_progress.json "
                               f"{_pq}/../chain/v365/{_sq}_365_cycle.json "
                               f"{_pq}/../chain/repair_a*/{_sq}_365_cycle.json "
                               f"{_pq}/../chain/gs_a*/{_sq}*.json && echo RMDONE")
                    _rm_out = None
                    try:
                        _rm_out = sh_ssh(_rm_host, _rm_cmd, 30)
                    except Exception:
                        _rm_out = None
                    if _rm_out and "RMDONE" in _rm_out:
                        _rd[_rkey] = cur_pdir
                        _refresh_tick_n[0] += 1
                        print(f"[sched] 30D-REFRESH {ss} on {_rm_host['name']} ({(_tiers.get(ss) or {}).get('tier')}-tier, gain older than its refresh window) -> progress+chain cleared, requeue need30", flush=True)
                        if len(_rd) > 2000:
                            for _dk in list(_rd.keys())[:500]:
                                _rd.pop(_dk, None)
                        return "need30", 1
                # USER 2026-10-17: this whole branch means "a FINISHED board is past its refresh window — delete its
                # progress and requeue". For a side that never finished there is no done_host, so _rm_host is None and
                # it fell through to terminal_ok: permanently un-schedulable. TIAUSDC_LONG hit this after the pilot was
                # OOM-killed mid-LONG as TIAUSDC_SHORT's PAIR-IN-RAM partner — 1876 computed cells, no publish, and the
                # scheduler then listed it in own_unfinished every tick while refusing to queue it. An unfinished side
                # must RESUME (need30 keeps its done dict, so nothing is recomputed), never go terminal.
                return ("terminal_ok", None) if ss in done else ("need30", 1)
            return "need30", 1
        if _non_short:
            return "terminal_ok", None  # USER 2026-10-02: config_tradier NON_SHORTABLE: no SHORT compute, ever
        if _quar:
            return _quar
        if ss not in done:
            if ss in redo:  # USER 2026-10-10 (82 redo backlog): paid board awaiting refill resumes directly — no base gate (it already ran), no deferral, no attempt cap
                return "need30", 1
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
        if os.environ.get("V15_SCHED_30D_ONLY", "1") == "1":
            return "terminal_ok", None  # 2026-10-14 365D LAW: 365D never gates and never REDOs -> no 365D verify / REPAIR / GS chain launches (running ones finish). V15_SCHED_30D_ONLY=0 restores the chain.
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
        # USER 2026-10-09: 30D SUPERSEDES 365D ALWAYS. A bad 365D verdict is RECORDED (tiers + audit
        # input) but never triggers REPAIR/GS adjusters and never blocks the 30D set — the 30D window
        # is the tradeable truth (365D lacks current-regime relevance). Positive-30D -> terminal_ok;
        # unmeasured-30D -> terminal_ok (board finished; golive gates on evidence). Non-positive-30D
        # -> terminal_failing (nothing to supersede with). Golive still
        # applies its own evidence gates (trades>0, engine-accept). In-flight repair/GS drain untouched;
        # no NEW adjuster launches unless operator sets V15_SCHED_FORCE_REPAIR=1.
        if os.environ.get("V15_SCHED_FORCE_REPAIR", "0") != "1":
            try:
                _g30 = (gains.get(ss) or {}).get("gain")
                if _g30 is not None and float(_g30) > 0:
                    return "terminal_ok", None
            except Exception:
                pass
            if (gains.get(ss) or {}).get("gain") is None:
                return "terminal_ok", None
            return "terminal_failing", None
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
    _ledger = _ledger_load(now_ts=_now_ts)  # USER 2026-10-12: gain+bh+trades ledger decides DAILY / WEEKLY / BIWEEKLY cadence
    _gtiers = _gain_tiers(_seen, ledger=_ledger)  # winners-first scheduling; loser tiers defer fresh boards (chains still drain)
    if _ledger:
        print(f"[sched] cadence ledger: {len(_ledger)} sides (W={sum(1 for v in _gtiers.values() if v.get('tier') == 'W')} M={sum(1 for v in _gtiers.values() if v.get('tier') == 'M')} L={sum(1 for v in _gtiers.values() if v.get('tier') == 'L')}, defer M={_GAIN_TIER_DEFER_DAYS['M']}d L={_GAIN_TIER_DEFER_DAYS['L']}d)", flush=True)
    _deferred_sides = []
    try:
        _chain_ts = _chain_stamp_ts(os.path.join(ROOT, "data", "daily_chain"))
    except Exception:
        _chain_ts = 0.0
    try:
        # USER 2026-10-17 dead switch: HOME is defined only INSIDE the HOST_PY raw string (line ~128), so it is not a
        # module attribute — this raised NameError every tick and the bare except below set _fresh_map = {}. Measured:
        # map size 0 while 186 of 602 matrices qualified, i.e. _FRESH_SKIP_DAYS (cron sets V15_FRESH_SKIP_DAYS=0.33,
        # so 8 h) has never once skipped anything, and the `ss not in _fresh_map` guard above could never fire.
        # CLAUDE.md: a dead switch is implemented, never left dead.
        _fresh_map = _fresh_computed_sides(os.path.join(os.path.expanduser("~"), "binance-sandbox", "SPREADSHEETS", "V15_V16_CELL_BY_CELL"), now=_now_ts, min_ts=_chain_ts)
    except Exception:
        _fresh_map = {}
    _fresh_skipped = []
    _pair_stale = []
    _npz_md5_cache = {}
    # USER 2026-10-17 new-information KPI: one manifest index + NPZ-end cache per tick; _unmoved = boarded < 24 h ago on an
    # NPZ that has not advanced >= 4 h past that publish (re-board = same window = same result). Fail-open on any error.
    _mdir_now = os.path.join(os.path.expanduser("~"), "binance-sandbox", "SPREADSHEETS", "V15_V16_CELL_BY_CELL")
    try:
        _mindex = _manifest_index(_mdir_now)
        try:
            _bat = {}
            for _ssm, _rec in (_mindex or {}).items():
                if _rec and _rec[0]:
                    _bat[_ssm] = max(float(_rec[0]), _bat.get(_ssm, 0.0))
            for _hn, _sx in (stats or {}).items():
                for _ssm, _tt in ((_sx or {}).get("boarded_at") or {}).items():
                    if _tt:
                        _bat[_ssm] = max(float(_tt), _bat.get(_ssm, 0.0))
            _HARD24.update(at=_bat, pins=set(_pin_sides or ()), now=_now_ts, blocked=[])
        except Exception as _he:
            print(f"[sched] HARD-24H index failed ({_he}) — rule fail-open this tick", flush=True)
    except Exception:
        _mindex = {}
    _npz_end_cache = {}
    _recent_unmoved_skips = []
    _recent_syms = None

    def _unmoved(sym, side):
        ss_ = f"{sym}_{side}"
        if ss_ not in _mindex:
            return False
        if sym not in _npz_end_cache:
            _npz_end_cache[sym] = _npz_end_ts(ROOT / "backtest_v8" / "indicators" / f"{sym}.npz")
        try:
            hit = _recent_unmoved(ss_, _mindex, _npz_end_cache[sym], _now_ts)
        except Exception:
            hit = False
        if hit and ss_ not in _recent_unmoved_skips:
            _recent_unmoved_skips.append(ss_)
        return hit

    _same_npz_skips = []
    _live_syms_now = _live_position_syms(_now_ts)
    _fresh_pri = priority_syms()
    _refresh_tick_n = [0]
    _allowed = set(uni.get("allowed_sym_sides") or [])  # USER 2026-10-06: only tradeable keys are calculated
    if not _allowed:
        # USER 2026-10-10: empty allowlist fail-OPENS the entire fleet (every sym_side calculated).
        # Stay fail-open (never idle paid workers) but scream once per tick so the universe gets fixed.
        print(f"[sched] CRITICAL: universe allowed_sym_sides EMPTY ({uni.get('date')}) — filter DISABLED, entire fleet calculating. Fix universe NOW.", flush=True)
    for sym in all_syms:
        h = owner.get(sym)
        _st = {side: (("terminal_not_tradeable", None) if _allowed and f"{sym}_{side}" not in _allowed else side_state(sym, side, h, _gtiers)) for side in ("LONG", "SHORT")}
        for side, v in _st.items():
            # USER 2026-10-10 (1-result-per-2h stall): deferral skips FRESH boards only — an
            # already-started side (REDO refill / crashed resume) is a chain continuation and
            # must drain, never defer. Starving resumes behind phantom tier memory froze the fleet.
            # USER 2026-10-10 (82 redo backlog): needs_redo sides are paid work awaiting finalise —
            # never deferred at any tier (completion, not rescue).
            if v[0] in ("need30", "waiting_base") and f"{sym}_{side}" not in started and f"{sym}_{side}" not in redo and _side_deferred(f"{sym}_{side}", _gtiers, _now_ts):
                _st[side] = ("terminal_deferred", None)
                _deferred_sides.append(f"{sym}_{side}")
            # USER 2026-10-12: never re-calculate a sym_side computed in the last 4 days (matrix mtime).
            # terminal_fresh (NOT terminal_deferred) so backfill can never relaunch it; started/redo drain, priority syms exempt.
            # USER 2026-10-12: W-tier (DAILY) sides are exempt from the 4-day fresh skip — their 24h refresh is the point.
            # 2026-10-17 USER (SNXUSDT re-boarded 3x in 24 h): priority symbols bypass only the 4-day fresh map, never the
            # 24 h recent-unmoved guard; only a symbol with an open live position may re-board inside 24 h.
            if _st[side][0] in ("need30", "waiting_base") and f"{sym}_{side}" not in started and f"{sym}_{side}" not in redo and ((sym not in _fresh_pri and f"{sym}_{side}" in _fresh_map and _side_tier(f"{sym}_{side}", _gtiers) != "W") or (sym not in _live_syms_now and _unmoved(sym, side))):
                _st[side] = ("terminal_fresh", None)
                _fresh_skipped.append(f"{sym}_{side}")
        # USER 2026-10-13 pair currency: one side re-queues while its sibling's only board ran on an
        # older NPZ -> the sibling was never calculated on this data; re-queue it too (both sides of a
        # tradeable symbol are calculated on the same NPZ; BH then decides where the time goes, in-pilot).
        _stale_sd = _pair_stale_sibling(sym, _st, os.path.join(os.path.expanduser("~"), "binance-sandbox", "SPREADSHEETS", "V15_V16_CELL_BY_CELL"), _npz_md5_cached(sym, _npz_md5_cache))
        if _stale_sd and f"{sym}_{_stale_sd}" not in started and f"{sym}_{_stale_sd}" not in redo and not _unmoved(sym, _stale_sd):
            print(f"[sched] PAIR-NPZ-STALE {sym}_{_stale_sd}: manifest NPZ != current NPZ -> requeue need30 (pair calculated on same data)", flush=True)
            _st[_stale_sd] = ("need30", 1)
            _pair_stale.append(f"{sym}_{_stale_sd}")
        for side in ("LONG", "SHORT"):
            if f"{sym}_{side}" in _pin_sides and f"{sym}_{side}" not in done and _st[side][0] != "running":
                _st[side] = ("need30", 1)  # 2026-10-13 operator pin: bypass deferral/fresh-skip/base-wait/allowlist
        chain_state[sym] = _st
    _phase, _phase_pending = _venue_phase(chain_state, launchable=lambda _s, _sd: _s not in _npz_dead and _phase_launchable(f"{_s}_{_sd}", st, started, redo, quar, _pin_sides), now=now)  # USER 2026-10-12: stocks first until every tradeable stock has passed; USER 2026-10-15 (06.3): only really-launchable sides hold the phase (exhausted incl. pin cap + quarantined soft-skips excluded); USER 2026-10-15: clock-aware (stocks in RTH, crypto outside)
    _rth_now = us_market_open(now)
    _phase_log = {"mode": _VENUE_ONLY or "none", "phase": _phase, "stocks_launchable": len(_phase_pending), "sample": sorted(_phase_pending)[:12],
                  "us_rth": bool(_rth_now), "venue_clock": os.environ.get("V15_SCHED_VENUE_CLOCK", "1") == "1", "min_to_open": round(minutes_to_open(now) or -1)}
    print(f"[sched] venue phase={_phase} (mode={_VENUE_ONLY or 'none'}, rth={_rth_now}, launchable stock sides={len(_phase_pending)})", flush=True)

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
        elif sym in st["held"].get(hn, {}) and not terminal(sym) and os.environ.get("V15_SCHED_CHAIN_HOLD", "0") == "1":
            held[hn][sym] = "chain"  # 2026-10-14 USER: a slot is a RUNNING pilot; persisted holds without a process (365D/REPAIR waits) idled s2 at 26% with 8/9 "held". Opt-in only.
    if not (args.dry_run or sim):
        _pruned = _prune_finished_dispatches(st, _now_ts, _live_dispatch_syms())
        if _pruned:
            print(f"[sched] dispatch finished -> grace released: {_pruned}", flush=True)
    for sym, hn in _dispatching_syms(st, _now_ts).items():
        # 2026-10-13: an async dispatch builds its NPZ for up to ~25 min before the pilot runs; without this the slot
        # looked free (no progress, no process) and the next ticks over-dispatched the host (s2: 6 jobs for 4 slots).
        if hn in held and sym in all_syms and not terminal(sym) and not any(sym in d for d in held.values()):
            held[hn][sym] = "dispatching"
    st["held"] = {hn: {s: st["held"].get(hn, {}).get(s, now.isoformat()) for s in d} for hn, d in held.items()}
    foreign = {ss: r for ss, r in running.items() if ss.rsplit("_", 1)[0] not in universe_syms["stocks"] | universe_syms["crypto"]}
    # USER 2026-10-10 ZERO WASTE: auto-kill double-confirmed dead pilots — running sym_side absent from
    # a FRESH universe allowlist (today's file or just-rebuilt; open positions are already IN allowed,
    # so absence = dead). Sym_side-precise: never kills the good side of a half-allowed symbol, only
    # current-pdir pilots. Stale universe / dry-run / sim / V15_SCHED_AUTOKILL_DEAD=0 = report only.
    _ak_report, _ak_killed = {}, {}
    _ak_fresh = how in ("file", "rebuilt-tradeable-changed")
    _ak_on = os.environ.get("V15_SCHED_AUTOKILL_DEAD", "1") == "1"
    _ak_allowed = set(uni.get("allowed_sym_sides") or [])
    if _ak_allowed:
        _dead_by_host = {}
        for _ss, _r in running.items():
            if _ss in _ak_allowed:
                continue
            _hn = (_r or {}).get("host")
            _hp = (stats.get(_hn) or {}).get("pdir") if _hn else None
            if not _hn or not _hp or (_r or {}).get("pdir") != _hp:
                continue
            _dead_by_host.setdefault(_hn, {"pdir": _hp, "ss": []})["ss"].append(_ss)
        for _hn, _dd in _dead_by_host.items():
            _ak_report[_hn] = sorted(_dd["ss"])
        if _dead_by_host and _ak_fresh and _ak_on and not (args.dry_run or sim):
            for _h in hosts:
                _dd = _dead_by_host.get(_h["name"])
                if not _dd:
                    continue
                _res = host_py(_h, "kill", {"symbols": [], "sym_sides": sorted(_dd["ss"]), "pdir": _dd["pdir"], "apply": True})
                _ak_killed[_h["name"]] = _res
                print(f"[autokill] {_h['name']} dead={sorted(_dd['ss'])} res={_res}", flush=True)
        elif _dead_by_host:
            print(f"[autokill] REPORT-ONLY (fresh={_ak_fresh} on={_ak_on} dry={bool(args.dry_run or sim)}): {json.dumps(_ak_report)}", flush=True)
    log = {"now": now.isoformat(), "market_open": is_open, "venue_order": vorder, "priority_syms": sorted(priority_syms()), "min_to_open": round(mto), "universe_source": how,
           "universe": uni["counts"], "reaped": reaped, "hosts": {}, "launched": [], "skipped_unready": {}, "foreign_running": {k: v["host"] for k, v in foreign.items()},
           "autokilled": _ak_killed, "autokill_report": _ak_report}

    # ---- candidate work
    # (A) adopted chain work: owner host known, not terminal, not currently running anything for that side; (B) brand-new symbols
    _tmpl_dir_by_host = {h_["name"]: h_.get("template_dir", "SPREADSHEETS") for h_ in hosts}
    _parity_cache = {}

    def pending_actions(sym, host_name):
        acts = []
        for side, (s, att) in chain_state[sym].items():
            if s in ("need30", "need365", "needrepair", "needgs"):
                w = {"need30": "30D", "need365": "365D", "needrepair": "REPAIR", "needgs": "GS"}[s]
                key = f"{sym}_{side}|{w}{att if w in ('REPAIR', 'GS') else ''}"
                if _launch_exhausted(key, w, f"{sym}_{side}", st, started, redo, _pin_sides):
                    continue
                if w == "30D" and not sim and not _parity_gate_ok(venue_of(sym), side, _tmpl_dir_by_host.get(host_name) or next(iter(_tmpl_dir_by_host.values()), "SPREADSHEETS"), _parity_cache):
                    log.setdefault("skipped_parity_gate", {}).setdefault(f"{'CRYPTO' if venue_of(sym) == 'crypto' else 'STOCKS'}_{side}", _parity_cache.get((venue_of(sym), side), (None, ""))[1])
                    continue  # 2026-10-13: the pilot refuses to fill (PARITY-GATE hard disparity) -> launching only burned a slice/attempt per tick  # USER 2026-10-09: 30D 6->3 fresh (hopeless sides stop burning slots); USER 2026-10-10: redo resume FREE; USER 2026-10-12: sliced (started) 30D sides draw the 20-slice budget, not attempts
                acts.append({"sym": sym, "side": side, "window": w, "attempt": att or 1, "key": key})
        return acts

    _CONT_FREEZE_ON = os.environ.get("V15_SCHED_CONT_NPZ_FREEZE", "1") == "1"
    _overage = {}  # sym -> NPZ age h: unfinished, not running, NPZ older than V15_CONT_NPZ_MAX_AGE_H -> freeze released, restarts on a fresh NPZ

    def _cont_sides(sym_):
        """USER 2026-10-13 V16: sides of sym_ with an UNFINISHED board in the round (sliced / crashed resume / needs_redo)."""
        if sym_ in _overage:
            return []
        return [ss_ for ss_ in (f"{sym_}_LONG", f"{sym_}_SHORT") if (ss_ in started and ss_ not in done) or ss_ in redo]

    def _cont_age_ok(rd_):
        try:
            return float(rd_.get("age_h")) <= float(os.environ.get("V15_CONT_NPZ_MAX_AGE_H", "24"))
        except (TypeError, ValueError):
            return False

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
    # USER 2026-10-09: winners first, losers last. USER 2026-10-14: ranking top-25 breaks tier ties
    # (BEST_WORST gain tier stays primary; the ranking boost weighs less than a tier step by construction).
    _rkpairs = frozenset()
    try:
        _rkpairs = _RANKPRI.ranking_sets()["pairs"]
    except Exception:
        pass
    _trk = lambda s: (_sym_tier_rank(s, _gtiers), 0 if s in _rkpairs else 1, sym_rank_key(s, vrank, all_syms, pri))
    new_syms.sort(key=_trk)
    adopted = [s for s in all_syms if s in owner and not terminal(s)]
    adopted.sort(key=_trk)
    _native_syms = {s for s in all_syms if venue_of(s) == "stocks"}  # TEMPORARY USER 2026-10-10: base-name set for the stocks-only gate (USDT stock variants pass)
    mapped_unready = {}
    HB, launched_keys = {}, set()
    npz_prefetch = []  # USER 2026-10-13: (host, [next candidate syms]) — s1 pre-generates their NPZ after the launch pass
    try:
        # 2026-10-20 USER LAW: COVERAGE metric + catch-up mode with hysteresis + throughput line (see _coverage/_select_catchup_mode/_throughput)
        _mrows = _manifest_rows(os.path.join(os.path.expanduser("~"), "binance-sandbox", "SPREADSHEETS", "V15_V16_CELL_BY_CELL"))
        _rc = st.setdefault("cov_remote", {})
        for _hh in hosts:
            _tg = (_hh.get("_via") or (_hh.get("ssh") or [""])[0])
            if not _tg or _tg in ("127.0.0.1", "localhost") or args.dry_run or sim:
                continue
            _ent = _rc.get(_hh["name"]) or [0.0, []]
            if _now_ts - float(_ent[0]) > 300.0:
                try:
                    _rr = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", _tg if "@" in _tg else f"niels@{_tg}", "python3", "-c", shlex.quote(_REMOTE_MANIFEST_PY), "binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"],
                                         capture_output=True, text=True, timeout=25)
                    if _rr.returncode == 0:
                        _ent = [_now_ts, json.loads(_rr.stdout.strip().splitlines()[-1])]
                except Exception:
                    pass
                _rc[_hh["name"]] = _ent
            _mrows += _ent[1]
        _allowed_l = uni.get("allowed_sym_sides") or []
        _cov = _coverage(_allowed_l, _mrows, _now_ts, npz_min=float(os.environ.get("V15_SCHED_COVERAGE_NPZ_MIN", "20")))  # informational only
        _ages = _qualifying_ages(_allowed_l, _mrows, _now_ts, npz_min=float(os.environ.get("V15_SCHED_COVERAGE_NPZ_MIN", "20")))
        _live, _live_age_h = {}, None
        try:
            _lj = json.loads(pathlib.Path(ROOT / "data" / "v15_live_gain_7d.json").read_text())
            _live_age_h = (_now_ts - float(_lj.get("generated_ts") or 0)) / 3600.0
            if _live_age_h <= float(os.environ.get("V15_SCHED_LIVE_GAIN_MAX_AGE_H", "12")):
                _live = _lj.get("sym_sides") or {}
                _trading = set(_lj.get("trading") or []) | set(uni.get("open_position_sym_sides") or [])
            else:
                _trading = set(uni.get("open_position_sym_sides") or [])
        except Exception:
            _trading = set(uni.get("open_position_sym_sides") or [])
        if os.environ.get("V15_SCHED_TRADING_FILES", "1") == "1":
            _trading |= _trading_file_sides()  # folded in from the retired V15_TRADING_CATCHUP cron
        _any_h, _tr_h = float(os.environ.get("V15_SCHED_CATCHUP_ANY_D", "9")) * 24.0, float(os.environ.get("V15_SCHED_CATCHUP_TRADING_D", "4")) * 24.0
        _rules = _catchup_rules(_ages, _trading, _any_h, _tr_h)
        _now_mode = bool(_rules["mode"])
        st["catchup_mode"] = _now_mode
        _CATCHUP_MODE[0] = _now_mode
        _bgain = {k: v.get("final_gain") for k, v in (_ledger or {}).items() if isinstance(v, dict)} if isinstance(_ledger, dict) else {}
        _prio, _gtable, _cad = _refresh_priority(_ages, _live, _trading, _any_h, _tr_h, _bgain)
        _ORDER_BY_HOST.clear()
        _PLABEL.clear()
        _PAGE.clear()
        _psym = {}
        for _k, _v in _prio.items():
            _sy = _k.rsplit("_", 1)[0]
            if _sy not in _psym or _v < _psym[_sy]:
                _psym[_sy] = _v
                _PLABEL[_sy] = _rule_label(_v, _cad.get(_k))
                _PAGE[_sy] = None if _ages.get(_k) is None else round(_ages[_k], 1)
        _AGE_KEY[0] = (lambda b, _m=_psym: _m.get(b, (2, 0.0, 0.0)))
        _thr = _throughput(_mrows, _now_ts)
        log["coverage"] = {"coverage": _cov["coverage"], "covered": _cov["covered"], "n": _cov["n"], "published_24h": _cov["published_24h"], "median_board_age_h": _cov["median_board_age_h"],
                           "mode": "catchup" if _now_mode else "slow", "per_cat": _cov["per_cat"], "throughput": _thr,
                           "rules": {k: v for k, v in _rules.items() if k not in ("over_any", "over_trading")}, "over_trading_sample": _rules["over_trading"][:12], "over_any_sample": _rules["over_any"][:12],
                           "top_gainers": _gtable, "live_gain": {"file_age_h": (None if _live_age_h is None else round(_live_age_h, 1)), "sym_sides": len(_live), "trading": len(_trading)}}
        print(f"[sched] mode={'CATCHUP' if _now_mode else 'slow'} max_age_any={_rules['max_age_any_h']}h max_age_trading={_rules['max_age_trading_h']}h "
              f"behind>{_any_h/24:.0f}d={_rules['n_over_any']}/{_rules['n_tradeable']} (never-qualifying {_rules['n_never_qualifying']}) trading>{_tr_h/24:.0f}d={_rules['n_trading_over']}/{_rules['n_trading']}; "
              f"coverage(info)={_cov['coverage']:.3f}; published/h per host {_thr['published_per_host']} target {_thr['target_per_host']}/h/host; both-sides symbols (1h) {_thr['symbols_both_sides']}; "
              f"top gainers {[(g['sym_side'], g['pnl_usd_7d'], g['board_age_h'], g['cadence_h']) for g in _gtable[:3]]}", flush=True)
        if not (args.dry_run or sim):
            _fp = ROOT / "data" / "reports" / "fleet_coverage.json"
            _fp.parent.mkdir(parents=True, exist_ok=True)
            _tmp = _fp.with_suffix(".json.tmp")
            _tmp.write_text(json.dumps({"now": now.isoformat(), **log["coverage"]}, indent=1))
            os.replace(_tmp, _fp)
    except Exception as _e_cov:
        print(f"[sched] coverage FAILED (non-fatal): {_e_cov}", flush=True)
    log["venue_phase"] = _phase_log
    log["npz_dead"] = sorted(_npz_dead)
    _pin_syms = {x.rsplit("_", 1)[0] for x in _pin_sides}
    _cont_all = (started - done) | redo
    if os.environ.get("V15_SCHED_CONT_OVERAGE_RESTART", "1") == "1" and not (args.dry_run or sim):
        _cap_c = float(os.environ.get("V15_CONT_NPZ_MAX_AGE_H", "24"))

        def _end_of(_sy):
            if _sy not in _npz_end_cache:
                _npz_end_cache[_sy] = _npz_end_ts(ROOT / "backtest_v8" / "indicators" / f"{_sy}.npz")
            return _npz_end_cache[_sy]

        _overage.update(_cont_overage(_cont_all, running, _end_of, _now_ts, _cap_c))
        if _overage:
            log["cont_overage_restart"] = dict(_overage)
            for _sy, _ag in sorted(_overage.items()):
                print(f"[sched] CONT-OVERAGE {_sy}: unfinished board, NPZ {_ag} h old > V15_CONT_NPZ_MAX_AGE_H {_cap_c:g} h -> freeze released, treated as a fresh launch (JIT NPZ refresh)", flush=True)
    _frz = set()  # 2026-10-15: always defined — the preflight's rebuild gate reads it, and dry-run/sim never builds it
    try:  # 2026-10-13 RULE#4 resume guard registry (tools/v15_npz_freeze.py): every NPZ writer on S1 skips these symbols
        if not (args.dry_run or sim):
            import v15_npz_freeze as _nf
            _ov_sides = {f"{_s}_{_d}" for _s in _overage for _d in ("LONG", "SHORT")}
            _frz = _nf.build(started - _ov_sides, done, redo - _ov_sides, running)
            _nf.write(_frz, FREEZE_FILE)
            log["npz_frozen_syms"] = len(_frz)
    except Exception as _nfe:
        log["npz_freeze_error"] = str(_nfe)[:120]
    for h in _host_order(hosts, held):  # USER 2026-10-12: most free capacity first (load divided across s1/s2/s5/s6)
        s = stats[h["name"]]
        if not s:
            log["hosts"][h["name"]] = "UNREACHABLE"
            continue
        if h["name"] in _drained:
            log["hosts"][h["name"]] = {"draining": True, "running": sorted(s.get("running", {}))}
            continue  # 2026-10-13: no new launches of any kind (30D/365D/GS/backfill/steal); running pilots finish
        cap = max(2, min(12, int(h.get("max_pairs", 3))))
        workers = int(os.environ.get("V15_SCHED_WORKERS_PER_SIDE") or h.get("workers_per_side", 3))  # 2026-10-14: boards use ~1.3 cores (measured) — 2 workers halve RAM -> more boards per host
        cpu = float(s["busy_nonnice_pct"]) if s.get("busy_nonnice_pct") is not None else float(s["busy_pct"]) if s.get("busy_pct") is not None else 100.0 * s["load1"] / max(1, s["nproc"])  # real busy% (load1 lags and over-reads stalled threads)
        pairs = {}
        for ss, r in s["running"].items():
            # 2026-10-17: only THIS round's production boards are pairs that grow to the venue peak. A foreign run (S2 gate
            # bench champ+cand at 2 workers, A/B arms) is already inside mem_avail; booking it as a pair growing to the
            # 19-21 GB pair peak added ~18 GB of phantom debt per bench pilot and idled S2 from 12:48Z to 16:20Z (0 boards).
            if isinstance(r, dict) and "prod_pids" in r and not r.get("prod_pids"):
                continue
            pairs.setdefault(ss.rsplit("_", 1)[0], 0.0)
            pairs[ss.rsplit("_", 1)[0]] += r["pss_mb"]
        pair_measure = [v for k, v in pairs.items() if k in universe_syms["stocks"] | universe_syms["crypto"] and v > 0]
        est_pair = _est_pair_mb(pair_measure, float(os.environ.get("V15_SCHED_PAIR_FLOOR_MB", "0") or 0) or cfg.get("pair_floor_mb", 12000))  # 2026-10-14: measured 1.7-3.7 GB/side at 2 workers -> floor 4000 via env (max measured x1.25 still wins)  # USER 2026-10-08 #2: AUTOPSY-INERT boards peak ~4GB/pair -> floor from cfg (measured max x1.25 still wins)
        # USER 2026-10-17: never launch what the reaper will kill — a new pair is budgeted at the host's 24 h peak pair PSS
        # (x1.1), and mature-but-growing pairs carry debt too. V15_SCHED_PEAK_ADMIT=0 restores the old estimate.
        # Venue-aware: a stocks board peaks ~2 GB, a crypto board 4-9 GB — one host-wide number either starved S2 of stocks
        # boards that fit or admitted crypto boards that did not. Each venue: max(floor, measured x1.25, 24 h peak x1.1).
        _peak_on = os.environ.get("V15_SCHED_PEAK_ADMIT", "1") == "1"
        est_v, peak_v = {}, {}
        # 2026-10-17 PER-SIDE: the 24 h peak was booked per PAIR (L+S summed, 17-21 GB) and a single-side launch was budgeted
        # at 1.1x that, so S1 with 24 GB free and no board running sat idle ("mem guard", est 20.7 GB > 18.1 GB). Memory is
        # per board: peak + debt are per running SIDE; a launch is budgeted at one side's peak (a pair launch is caught by
        # the next tick's debt, and the reaper floor stays). V15_SCHED_PEAK_PER_SIDE=0 restores the pair accounting.
        _per_side = os.environ.get("V15_SCHED_PEAK_PER_SIDE", "1") == "1"
        _units = pairs
        if _per_side:
            _units = {}
            for _ss_u, _r_u in s["running"].items():
                if isinstance(_r_u, dict) and "prod_pids" in _r_u and not _r_u.get("prod_pids"):
                    continue
                _units[_ss_u] = float(_r_u.get("pss_mb") or 0.0)
        if _peak_on:
            for _vn, _vfloor in (("crypto", float(os.environ.get("V15_SCHED_PAIR_FLOOR_MB", "0") or 0) or cfg.get("pair_floor_mb", 12000)), ("stocks", float(os.environ.get("V15_SCHED_PAIR_FLOOR_MB_STOCKS", "2500") or 2500))):
                _vm = {k: v for k, v in _units.items() if k.rsplit("_", 1)[0] in universe_syms[_vn] and v > 0} if _per_side else {k: v for k, v in pairs.items() if k in universe_syms[_vn] and v > 0}
                _bk = f"{h['name']}|{_vn}|side" if _per_side else f"{h['name']}|{_vn}"
                peak_v[_vn] = max(_host_peak_pair_mb(st, _bk, _vm, _now_ts, workers=(_workers_for("XUSDT", workers) if _vn == "crypto" else workers)), float(_vfloor) / 1.25)
                est_v[_vn] = max(_est_pair_mb(list(_vm.values()), _vfloor), 1.1 * peak_v[_vn])
            est_pair = max(est_v.values())
        _est_sym = lambda _s: est_v.get(venue_of(_s), est_pair) if _peak_on else est_pair
        reserve = max(h.get("mem_reserve_mb", 3000), int(h.get("oom_mb", 500)) + 2000)
        if _peak_on:
            _debt = 0.0
            for _ps_, _m_ in _units.items():
                # a running pair still grows to the host's measured peak for its venue (not to est, which already
                # carries the x1.25/x1.1 headroom for an unseen NEW board — counting that twice idled S1 at 2 boards)
                _p_ = peak_v.get(venue_of(_ps_.rsplit("_", 1)[0] if _per_side else _ps_), _est_sym(_ps_.rsplit("_", 1)[0] if _per_side else _ps_))
                _debt += max(0.0, float(_p_) - float(_m_ or 0))
            proj_mem = s["mem_avail_mb"] - reserve - _debt
        else:
            proj_mem = s["mem_avail_mb"] - reserve - _growth_debt_mb(pairs, est_pair)
        swap_ok = _swap_admit_ok(s)
        disk_ok = _disk_admit_ok(s)
        used = len(held[h["name"]])
        if os.environ.get("V15_SCHED_MEM_CAP", "1") == "1":
            # 2026-10-13 USER "all servers at full capacity": max_pairs is the floor; free memory (after reserve + growth
            # debt of running pairs) buys more pair slots up to 12. The cpu target + mem/swap/disk guards still gate each admission.
            cap = max(cap, min(12, used + int(max(0.0, proj_mem) // max(1.0, est_pair))))
        info = {"cpu": round(cpu), "cpu_real": round(float(s.get("busy_pct") or 0)), "mem_avail_mb": s["mem_avail_mb"], "disk_avail_mb": s.get("disk_avail_mb"), "slots": f"{used}/{cap}", "held": sorted(held[h["name"]]), "est_pair_mb": round(est_pair), "est_venue_mb": {k: round(v) for k, v in est_v.items()}, "proj_mem_mb": round(proj_mem),
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
        if _recent_syms is None:
            _recent_syms = _recent_board_syms(all_syms, _mindex, _allowed, _now_ts)
        order = _place_order(held_ordered, [sym for sym in adopted if owner.get(sym) == h["name"] and sym not in held[h["name"]]],
                             [sym for sym in new_syms if venue_of(sym) in h["venues"]], _gtiers, redo, st["attempts"], st.get("launched_at"), pri, cont=_cont_all, pinned=_pin_sides, recent=_recent_syms, age_key=_AGE_KEY[0])
        _ORDER_BY_HOST[h["name"]] = [_o[0] for _o in order if _o[1]]
        # 2026-10-13 USER LAW: a server never takes a new symbol while one of its own boards is unfinished — enforced by
        # _place_order (pinned=0, unfinished/redo=1, fresh=2): every unfinished board this host owns is evaluated and
        # admitted before any fresh pair in the same tick; slots/mem/cpu/budget that stop it stop the fresh pairs too.
        # Boards that cannot launch (parity-gated, budget-exhausted, NPZ unready) are logged, never a host-wide deadlock.
        _own_cont = sorted({ss for ss in _cont_all if owner.get(ss.rsplit("_", 1)[0]) == h["name"] and ss not in running})
        if _own_cont:
            log.setdefault("own_unfinished", {})[h["name"]] = _own_cont
        # 2026-10-14: the stocks phase holds crypto back ONLY while a stock side can really launch on this host now and
        # the US market is open; otherwise it falls through to "any" (05:06Z deadlock: market closed, 34 "launchable" stock
        # sides all pair-gated, 23 crypto venue-blocked, s2 at 26% cpu).
        _host_phase = _phase
        if _phase == "stocks" and os.environ.get("V15_SCHED_PHASE_FALLTHROUGH", "1") == "1":
            if not is_open:
                _host_phase = "any"
            else:
                _can_stock = False
                for _ps, _pns in order:
                    if not _pns or venue_of(_ps) != "stocks" or (_ps in owner and owner[_ps] != h["name"]):
                        continue
                    _pa = [a for a in pending_actions(_ps, h["name"]) if a["key"] not in launched_keys and not _launched_recently(a["key"], st.get("launched_at"), _now_ts) and not _launched_recently(a["key"], st.get("dispatch_q"), _now_ts, _DISPATCH_GRACE_S) and f"{a['sym']}_{a['side']}" not in running]
                    if _pa and _pair_gate_ok(_ps, _pa, owner, chain_state, st["attempts"], pend_sides={a["side"] for a in pending_actions(_ps, h["name"]) if f"{a['sym']}_{a['side']}" not in running}):
                        _can_stock = True
                        break
                if not _can_stock:
                    _host_phase = "any"
            if _host_phase != _phase:
                log.setdefault("venue_fallthrough", []).append(h["name"])
        # 2026-10-20 USER (fleet concurrency 9.3 -> 1.0): the host-wide guard compared proj_mem against the MAX floor over both venues
        # (crypto 12000) even when only stocks (floor 4000) can launch this tick, so "mem guard" overstated scarcity. The admission
        # estimate is now the max over the venues this tick's phase allows. V15_SCHED_GATE_VENUE_FLOOR=0 restores the old max.
        _gate_venues = sorted(est_v) if (not _peak_on or os.environ.get("V15_SCHED_GATE_VENUE_FLOOR", "1") != "1" or _host_phase not in est_v) else [_host_phase]
        est_gate = max([est_v[_gv] for _gv in _gate_venues] or [est_pair]) if _peak_on else est_pair
        HB[h["name"]]["est_pair"] = est_gate
        info["est_gate_mb"] = round(est_gate)
        info["mem_source"] = "rss-fallback" if any(isinstance(_r_m, dict) and _r_m.get("mem_source") == "rss-fallback" for _r_m in s["running"].values()) else "pss(smaps_rollup)"
        info["gate_venues"] = _gate_venues
        info["gate_inputs"] = {"mem_avail_mb": s["mem_avail_mb"], "reserve_mb": round(reserve), "proj_mem_mb": round(proj_mem), "est_gate_mb": round(est_gate), "floor_crypto_mb": os.environ.get("V15_SCHED_PAIR_FLOOR_MB", ""), "floor_stocks_mb": os.environ.get("V15_SCHED_PAIR_FLOOR_MB_STOCKS", ""), "phase": _host_phase}
        for sym, needs_slot in order:
            _pinned_sym = sym in _pin_syms
            if _pinned_sym and _pin_hosts and h["name"] not in _pin_hosts:
                continue
            if needs_slot and new_launched >= args.max_launch:
                continue  # budget counts only NEW pairs; never break (a break skips held chains sorted later and froze the fleet 03:00-03:35Z)
            if needs_slot and (used >= cap or proj_mem < _est_sym(sym) or cpu >= cfg.get("cpu_target_pct", 90) or not swap_ok or not disk_ok):
                continue  # over-cap/exhausted host: skip this admission, keep evaluating (held chains need no slot)
            if venue_of(sym) not in h["venues"]:
                continue
            # USER 2026-10-18 "100% stocks until all stocks are done": a PRIORITY PIN is a hint about ORDER, it may not
            # override an explicit venue pin. V15_SCHED_PRIORITY_SYMS is regenerated with crypto symbols at the front
            # (RLCUSDT, BBUSDT, COMPUSDT, ... at 00:30Z), and because pinned symbols skipped this gate entirely the
            # fleet launched SKYUSDT/ZROUSDT/PUMPUSDT while pinned to stocks. A pin now bypasses the venue gate only
            # when no V15_SCHED_VENUE_ONLY is set (the old behaviour, which exists so a hand-pinned symbol can jump an
            # auto-derived phase).
            _pin_may_bypass_venue = _pinned_sym and not _VENUE_ONLY
            # `needs_slot` alone left a hole: a HELD chain launches without needing a new slot, so it never reached the
            # venue gate and the 00:36Z tick started KMNOUSDT/ZROUSDT while pinned to stocks. An explicit venue pin
            # applies to every launch, slot or no slot.
            if (needs_slot or _VENUE_ONLY) and not _pin_may_bypass_venue and not _venue_allowed(sym, _native_syms, phase=_host_phase):
                log.setdefault("skipped_venue_only", []).append(f"{h['name']}:{sym}")
                continue  # TEMPORARY USER 2026-10-10: stocks-only weekend push — new crypto pairs blocked; held chains (needs_slot=False) drain untouched
            if sym in owner and owner[sym] != h["name"]:
                continue
            if needs_slot and not _pinned_sym and _quota_blocks(sym, _gtiers, chain_state) and nonw_used >= nonw_quota and f"{sym}_LONG" not in redo and f"{sym}_SHORT" not in redo:
                log.setdefault("skipped_quota", []).append(f"{h['name']}:{sym}")
                continue  # USER 2026-10-10 (82 redo backlog): refills never consume winner quota
            if not needs_slot and (proj_mem < _est_sym(sym) / 2 or cpu >= 120 or not swap_ok or not disk_ok):
                continue
            if not needs_slot and held[h["name"]].get(sym) != "running" and (running_cnt >= cap or chain_launched >= max(args.max_launch, 4)):
                continue
            rd = is_ready(h["name"], sym)
            _cont_ss = _cont_sides(sym)
            if not rd.get("ok") and str(rd.get("reason") or "").startswith(("tail-stale:", "stale:")) and (_run_stale_allowed() or (_cont_ss and _CONT_FREEZE_ON and _cont_age_ok(rd))):
                # USER 2026-10-13 V16: an unfinished board resumes on the NPZ it was measured on (RULE#4) — a tail-stale
                # gate would hold it forever, a rebuild would wipe it. Frozen resume is consistent by construction.
                log.setdefault("cont_npz_frozen", []).append(f"{h['name']}:{sym}:{','.join(_cont_ss)}:{rd.get('reason')}")
                rd = dict(rd, ok=True)
            if (not rd.get("ok") and str(rd.get("reason") or "").startswith(("tail-stale:", "stale:")) and not _cont_ss and needs_slot and _ASYNC_DISPATCH
                    and os.environ.get("V15_SCHED_DISPATCH_NPZ", "1") == "1" and not (args.dry_run or sim)):
                # USER LAW 2026-10-20: with V15_SCHED_RUN_STALE=0 a stale WORKER copy must not strand the side here - the dispatch job is
                # the gate: it tail-refreshes S1's NPZ, pushes it md5-verified and REFUSES the launch (kind stale-npz, counted) when the
                # NPZ's own last bar is still > V15_NPZ_MAX_AGE_MIN behind. Counted per tick below with the worker tail age.
                #
                # USER 2026-10-20 "if it can't, skip temporarily and dispatch next one that can": a stale WORKER copy is
                # genuinely not a blocker, because the job pushes S1's copy over it. A stale S1 SOURCE is a different
                # thing — then the job will refresh, fail to reach the latest bar and refuse (exit 3), and the slot was
                # spent on a side that could never run. So the source is checked HERE: fresh/refreshable source ->
                # proceed as before; stale source -> ask for the sanctioned prep and give the slot to the next symbol.
                _src_ok, _src_why = _s1_source_tail_ok(sym)
                if not _src_ok:
                    log.setdefault("npz_source_stale_skipped", []).append(f"{h['name']}:{sym}:{_src_why}")
                    if os.environ.get("V15_SCHED_NPZ_REFRESH", "1") == "1":
                        npz_prep(h, sym)
                    continue
                log.setdefault("npz_stale_jit", []).append(f"{h['name']}:{sym}:{rd.get('age_h')}h")
                rd = dict(rd, ok=True)
            if not rd.get("ok") and rd.get("reason") == "npz missing" and needs_slot and not _cont_ss and _ASYNC_DISPATCH and os.environ.get("V15_SCHED_DISPATCH_NPZ", "1") == "1" and not (args.dry_run or sim):
                # 2026-10-13: a worker without the NPZ is not unready — the async dispatch job builds/pushes it (md5-verified)
                # right before launch, and skips the launch itself when S1 cannot produce it (MRK/V idled s2 at 16:54Z).
                log.setdefault("npz_missing_jit", []).append(f"{h['name']}:{sym}")
                rd = dict(rd, ok=True)
            if not rd.get("ok"):
                _rsn = rd.get("reason") or ""
                if _rsn.startswith("tail-stale:"):
                    _rsn += f" [{_npz_hold_triage(sym)}]"
                log["skipped_unready"][sym] = f"{h['name']}: {_rsn}"
                continue
            acts = pending_actions(sym, h["name"])
            acts = [a for a in acts if _repair_sticky_ok(a, rep_host, h["name"])]
            acts = [a for a in acts if a["key"] not in launched_keys and not _launched_recently(a["key"], st.get("launched_at"), _now_ts) and not _launched_recently(a["key"], st.get("dispatch_q"), _now_ts, _DISPATCH_GRACE_S)]
            acts = [a for a in acts if f"{a['sym']}_{a['side']}" not in running]  # USER 2026-10-11: never launch a side with a live pilot anywhere (kills 5x dup stacks; backfill had this, steal gained it same turn)
            acts, _unreach = _deny_unreachable_sides(acts, sym, h["name"])  # USER 2026-10-20: no account can open these, so a board for them buys nothing
            if _unreach:
                log.setdefault("skipped_side_unreachable", []).extend(_unreach)
            if not acts:
                # USER 2026-10-13: never drop a candidate silently — an idling fleet must name its own blocker.
                log.setdefault("skipped_no_actions", []).append(f"{h['name']}:{sym}")
                continue
            if needs_slot and _PAIR_ALWAYS and len(acts) == 1 and acts[0]["window"] == "30D":
                # USER 2026-10-13: "HOW CAN IT ONLY BE CALCULATING SHORT AND NOT LONG IF IT HAS THE NPZ IN MEMORY" — the sibling
                # side always rides along on the same NPZ load (unless it is running or was just dispatched).
                _sib = "LONG" if acts[0]["side"] == "SHORT" else "SHORT"
                _sk = f"{sym}_{_sib}|30D"
                _sib_state = ((chain_state.get(sym) or {}).get(_sib) or (None,))[0]
                _sib_needs = _sib_state in ("need30", "waiting_base") or os.environ.get("V15_SCHED_SIB_NEED_ONLY", "1") == "0"
                if not _sib_needs:
                    # 2026-10-16: the sibling rides along ONLY when it needs a 30D board itself — a finished/fresh/deferred
                    # sibling re-ran on every launch of its pair (PUMPUSDT_SHORT re-published 57.32 eight times on 10-16).
                    log.setdefault("pair_sibling_skipped_done", []).append(f"{h['name']}:{sym}_{_sib}:{_sib_state}")
                elif f"{sym}_{_sib}" not in running and _sk not in launched_keys and not _launched_recently(_sk, st.get("dispatch_q"), _now_ts, _DISPATCH_GRACE_S) and not _launched_recently(_sk, st.get("launched_at"), _now_ts):
                    acts.append({"sym": sym, "side": _sib, "window": "30D", "attempt": 1, "key": _sk})
                    log.setdefault("pair_sibling_added", []).append(f"{h['name']}:{sym}_{_sib}")
            if needs_slot and not _pair_gate_ok(sym, acts, owner, chain_state, st["attempts"], pend_sides={a["side"] for a in pending_actions(sym, h["name"]) if f"{a['sym']}_{a['side']}" not in running}):  # fresh syms: every launchable side covered; terminal/capped sides don't block siblings
                log.setdefault("skipped_pair_gate", []).append(f"{h['name']}:{sym}:" + str([a.get('side') for a in acts]))
                continue
            # ---- PRE-DISPATCH NPZ VERIFICATION (USER 2026-10-15) -------------------------------------
            # "BEFORE any calculations start a verification of npz data, recalc if needed while the symbol is
            # skipped and the next good one runs, then requeue the one that could have caused 0 trades instead
            # of blindly following a list of symbols."
            # STRUCTURAL screen only here (check_freshness=False): under the JIT law S1's tail is legitimately
            # behind until the dispatch job refreshes it, so staleness is not a defect at this point. What this
            # catches is the rebuild-in-flight race — a 1-bar/truncated/all-zero NPZ visible to dispatch (census
            # 2026-10-15: ADP/AEM/ALMU/AXTI/CDW all bars=1 span=0.0d while 3 precompute jobs rebuilt them) and
            # a span under the 0.80 coverage floor the engine would use to THROW THE FINISHED BOARD AWAY.
            # Reference: ACEUSDT_LONG_bhm14p68_gain0p00_t0_30d_matrix.xlsx burned a whole board for 0 trades
            # inside the 09-24..10-01 klines-tail corruption window; its NPZ verifies fine today.
            _pf_ok, _pf_why, _pf_res = _npz_preflight(sym, 30, check_freshness=False)
            if not _pf_ok:
                _frozen_now = sym in _frz or bool(_cont_ss) or any(x in running for x in (f"{sym}_LONG", f"{sym}_SHORT"))
                _want_rb, _dn, _alert = _npz_defer_note(st, sym, "block", _pf_why, now_ts=_now_ts, may_rebuild=not _frozen_now)
                log["skipped_unready"][sym] = f"{h['name']}: npz-preflight #{_dn} {_pf_why}" + (" [FROZEN: no rebuild]" if _frozen_now else "")
                try:
                    _pf_mod = sys.modules.get("v15_npz_preflight")
                    if _pf_mod is not None and _pf_res:
                        _pf_mod.record_deferral(_pf_res)
                except Exception:
                    pass
                if _alert:
                    log.setdefault("npz_preflight_alert", []).append(f"{h['name']}:{sym}:n{_dn}:{_pf_why}")
                if _want_rb and not (args.dry_run or sim) and os.environ.get("V15_SCHED_NPZ_REFRESH", "1") == "1":
                    # the sanctioned rebuild path (single-flight flock + v15_npz_prep.py), requested ONCE per
                    # cooldown — never a direct precompute spawn, never under a frozen symbol.
                    log.setdefault("npz_preflight_rebuild", []).append(f"{h['name']}:{sym}")
                    npz_prep(h, sym)
                continue  # the next GOOD symbol takes this slot; this one is requeued when it passes again
            # STOCKS second screen: session census / intra-window holes. Same machinery, one path.
            _gp_ok, _gp_why, _gp_info = _npz_gap_screen(sym)
            # A gate that fails OPEN must never do it silently — a NameError in the screen read as "usable" for
            # every holed stock NPZ until this surfaced it (2026-10-15). Fail-open is logged, every tick.
            for _uw in (_pf_why, _gp_why):
                if _uw and ("unavailable" in _uw or "-crash" in _uw):
                    log.setdefault("npz_screen_degraded", {})[f"{sym}"] = _uw[:160]
            if not _gp_ok:
                _frozen_now = sym in _frz or bool(_cont_ss) or any(x in running for x in (f"{sym}_LONG", f"{sym}_SHORT"))
                _want_rb, _dn, _alert = _npz_defer_note(st, sym, "block", _gp_why, now_ts=_now_ts, may_rebuild=not _frozen_now)
                log["skipped_unready"][sym] = f"{h['name']}: npz-gap #{_dn} {_gp_why}" + (" [FROZEN: no rebuild]" if _frozen_now else "")
                if _alert:
                    log.setdefault("npz_gap_alert", []).append(f"{h['name']}:{sym}:n{_dn}:sessions{_gp_info.get('sessions')}:{_gp_why}"[:300])
                if _want_rb and not (args.dry_run or sim) and os.environ.get("V15_SCHED_NPZ_REFRESH", "1") == "1":
                    log.setdefault("npz_gap_rebuild", []).append(f"{h['name']}:{sym}")
                    npz_prep(h, sym)
                continue
            _pf_back = _npz_defer_clear(st, sym)
            if _pf_back:
                log.setdefault("npz_preflight_requeued", []).append(f"{h['name']}:{sym}:after{_pf_back}")
            _async_npz = False
            _frz_why = ("continuation " + ",".join(_cont_ss)) if _cont_ss else ("sibling running " + ",".join(x for x in (f"{sym}_LONG", f"{sym}_SHORT") if x in running) if any(x in running for x in (f"{sym}_LONG", f"{sym}_SHORT")) else "")
            if needs_slot and _CONT_FREEZE_ON and _frz_why and os.environ.get("V15_SCHED_NPZ_REFRESH", "1") == "1":
                # USER 2026-10-13 V16: a rebuild here archived whole boards (NPZ-CHANGED-REFILL, 120k rows wiped fleet-wide)
                # or tripped the FINAL midrun-swap refusal of a running sibling. Fresh boards still get the JIT NPZ.
                log.setdefault("npz_frozen", []).append(f"{h['name']}:{sym}:{_frz_why}")
                # 2026-10-15 OPEN TRADEOFF, decided by the operator, NOT here (V15_SCHED_PAIR_FRESH_REQUEUE=0 default).
                # When a PAIR goes out together the freeze is right: both sides measured on the same fresh bars.
                # When the sibling is dispatched HOURS later it inherits bars that old (measured: CHRUSDT's NPZ tail
                # was 6.89 h old while its LONG side ran). ON = requeue the late sibling for a fresh-NPZ dispatch;
                # OFF = today's behaviour, "pair calculated on same data" wins. A continuation is NEVER requeued:
                # RULE#4 says a resuming board stays on the NPZ its rows were measured on.
                if _PAIR_FRESH_REQUEUE and needs_slot and not _cont_ss and _frz_why.startswith("sibling running"):
                    log.setdefault("pair_fresh_requeue", []).append(f"{h['name']}:{sym}:{_frz_why}")
                    continue
            elif needs_slot and os.environ.get("V15_SCHED_DISPATCH_NPZ", "1") == "1" and os.environ.get("V15_SCHED_NPZ_REFRESH", "1") == "1" and not (args.dry_run or sim):  # USER 2026-10-13: NPZ is calculated on the latest S1 klines right before a symbol is sent  # USER 2026-10-13: EVERY venue — fresh NPZ computed on s1 ships with the dispatch
                if _ASYNC_DISPATCH:
                    _async_npz = True  # 2026-10-13: rebuild+push+launch run in a detached job (a synchronous rebuild outlived the 110 s tick timeout -> zero launches)
                else:
                    pr = npz_prep(h, sym)
                    # USER 2026-10-15: ok=None is NOT a pass. `if pr is not None and not pr.get("ok")` let every
                    # unknown verdict through (20 of 909 launches on s1, 2.2%: slot-wait-timeout, frozen,
                    # push-failed, reaped prep). Each kind now gets its own disposition.
                    _l_ok, _l_kind, _l_why, _l_rb = _prep_disposition(pr)
                    if not _l_ok:
                        _want_rb, _dn, _alert = _npz_defer_note(st, sym, _l_kind, _l_why, now_ts=_now_ts, may_rebuild=_l_rb and sym not in _frz)
                        log["skipped_unready"][sym] = f"{h['name']}: npz_prep[{_l_kind}] #{_dn} {_l_why}"
                        if _alert:
                            log.setdefault("npz_prep_alert", []).append(f"{h['name']}:{sym}:{_l_kind}:n{_dn}:{_l_why}"[:300])
                        continue
                    # ...and the prep's own ok=True is not sufficient either: verify the bytes that will travel,
                    # freshness INCLUDED (measured: ATOMUSDT prep ok=true with a 6 h-old tail).
                    _po_ok, _po_why, _po_res = _npz_preflight(sym, 30, check_freshness=True)
                    if not _po_ok:
                        _want_rb, _dn, _alert = _npz_defer_note(st, sym, "block", _po_why, now_ts=_now_ts, may_rebuild=False)
                        log["skipped_unready"][sym] = f"{h['name']}: npz-postprep #{_dn} {_po_why}"
                        try:
                            _pf_mod = sys.modules.get("v15_npz_preflight")
                            if _pf_mod is not None and _po_res:
                                _pf_mod.record_deferral(_po_res)
                        except Exception:
                            pass
                        if _alert:
                            log.setdefault("npz_preflight_alert", []).append(f"{h['name']}:{sym}:postprep:n{_dn}:{_po_why}"[:300])
                        continue
            tag = f"{h['name']}:{sym}:" + "+".join(f"{a['side'][0]}{a['window']}" + (f"#a{a['attempt']}" if a["window"] == "REPAIR" else "") for a in acts)
            if args.dry_run or sim:
                log["launched"].append(tag + " (dry)")
                for a in acts:
                    launched_keys.add(a["key"])
            else:
                pd = s.get("pdir") or cfg.get("fallback_pdir")
                if not pd:
                    # 2026-10-20 WP1: s5/s6 were revived without ~/v15_current_progress_dir.txt, so every candidate was dropped HERE
                    # with no log line and the host reported "no eligible ready symbol" for 40+ min. Never drop silently.
                    log.setdefault("skipped_no_pdir", []).append(f"{h['name']}:{sym}")
                    continue
                if _async_npz or _ALL_LAUNCH_NPZ:  # USER 2026-10-13: S1 recalcs + sends the latest NPZ for EVERY launch, every host incl. itself
                    # USER 2026-10-19 ("NO progress"): _dispatch_async returns False when it dispatched NOTHING (every
                    # side dropped as untradeable or by the HARD-24H rule). The result was ignored, so the tick marked
                    # the key launched, set the dispatch grace and reported it in `launched` — the fleet then idled
                    # while the log claimed 2-4 launches every tick. VICR_LONG's sweep log was 4 DAYS old while being
                    # "launched" every 2 minutes. A skipped dispatch is now visible and retried, not booked.
                    if not _dispatch_async(h, sym, acts, pd, _workers_for(sym, workers), tag):
                        log.setdefault("dispatch_skipped", []).append(tag)
                        continue
                    _dispatch_q_mark(st, [a["key"] for a in acts], _now_ts)
                    st.setdefault("dispatch_host", {})[sym] = h["name"]
                    log.setdefault("dispatch_async", []).append(tag)
                else:
                    # USER 2026-10-19 ("NO progress"): the dispatch result was NEVER checked. A dropped ssh (S1 had 61
                    # established sshd connections against `MaxStartups 50:100:200`, so sshd randomly refuses above 50)
                    # left the scheduler marking the key launched, setting launched_at — which then makes
                    # _launched_recently SUPPRESS the retry — and reporting it in `launched`. Result: the log claimed
                    # 3 launches per tick while nothing spawned (VICR_LONG's sweep log was hours stale with 0
                    # processes, RRC_SHORT's from 11:36) and the fleet ran ONE board. A failed dispatch must not be
                    # recorded as a launch; the next tick retries it. A TIMEOUT still counts as launched because the
                    # job may have started and the spawn guard dedups.
                    _rc, _err = 0, ""
                    for _t_i, _target in enumerate([h.get("_via") or h["ssh"][0]] + [x for x in h["ssh"] if x != (h.get("_via") or h["ssh"][0])]):
                        try:
                            _r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", _target, pair_launch_cmd(h, acts, pd, _workers_for(sym, workers))],
                                                timeout=40, capture_output=True, text=True)
                            _rc, _err = _r.returncode, (_r.stderr or "").strip()
                            if _rc == 0:
                                h["_via"] = _target
                                break
                            print(f"[sched] launch ssh rc={_rc} via {_target} for {h['name']} {sym}: {_err[:140]}", flush=True)
                        except subprocess.TimeoutExpired:
                            print(f"[sched] launch ssh timeout {h['name']} {sym} (job may still have started; pgrep dedups)", flush=True)
                            _rc = 0
                            break
                        except Exception as _le:
                            _rc, _err = 99, str(_le)[:140]
                    if _rc != 0:
                        log.setdefault("dispatch_failed", []).append(f"{tag}:rc{_rc}:{_err[:60]}")
                        print(f"[sched] DISPATCH FAILED {h['name']} {sym} rc={_rc} — NOT marked launched, retrying next tick", flush=True)
                        continue
                for a in acts:
                    if a["window"] == "30D":
                        _slice_progress_reset(st, a["key"], started_mt.get(f"{a['sym']}_{a['side']}"))
                    _burn_launch(a["key"], a["window"], f"{a['sym']}_{a['side']}", st, started)
                    st.setdefault("last_act", {})[a["key"].split("|")[0]] = a["key"]
                    st["launched_at"][a["key"]] = _now_ts
                    launched_keys.add(a["key"])
                log["launched"].append(tag)
            if needs_slot:
                used += 1; proj_mem -= _est_sym(sym) + float(rd.get("size_mb") or 0)
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
        # USER 2026-10-13 ("whenever a server is about to finish the last round of a symbol it gets the next symbol
        # ready with a new npz"): remember this host's next dispatch candidates so s1 can PRE-GENERATE their NPZ while
        # the current boards finish. Dispatch then never waits on an NPZ, and a stale tail never reaches a worker.
        try:
            _pf_n = int(os.environ.get("V15_SCHED_NPZ_PREFETCH_N", "1"))
        except (TypeError, ValueError):
            _pf_n = 1
        # Only prefetch for a host that is ABOUT TO FREE UP (USER 2026-10-13: "send the latest npz along with the job
        # just before the server finishes the previous calc"). An NPZ is backtest fuel and goes stale in ~15 min, so
        # rebuilding one for a host that just started a full set of boards is waste. Gate: the host has no free slot
        # AND its oldest running board has been going at least V15_SCHED_NPZ_PREFETCH_MIN_AGE_MIN (default 15) minutes.
        _pf_ready = used >= cap
        if _pf_ready:
            try:
                _pf_age_min = float(os.environ.get("V15_SCHED_NPZ_PREFETCH_MIN_AGE_MIN", "15"))
            except (TypeError, ValueError):
                _pf_age_min = 15.0
            _oldest = 0.0
            for _rss, _rr in (s.get("running") or {}).items():
                try:
                    _oldest = max(_oldest, float(_rr.get("age_min") or _rr.get("etime_min") or 0.0))
                except (TypeError, ValueError, AttributeError):
                    continue
            if _oldest and _oldest < _pf_age_min:
                _pf_ready = False
        if _pf_n > 0 and _pf_ready:
            _pf = []
            for _s, _ns in order:
                if not _ns or _s in held[h["name"]] or f"{_s}_LONG" in running or f"{_s}_SHORT" in running:
                    continue
                if _CONT_FREEZE_ON and _cont_sides(_s) and _cont_age_ok(readiness_by_host.get(h["name"], {}).get(_s) or {}):
                    continue  # USER 2026-10-13 V16: never prefetch-rebuild under an unfinished board (RULE#4 wipe); past the age cap it refreshes
                if venue_of(_s) not in h["venues"] or not _venue_allowed(_s, _native_syms, phase=_phase):
                    continue
                _pf.append(_s)
                if len(_pf) >= _pf_n:
                    break
            if _pf:
                npz_prefetch.append((h, _pf))
        if launched == 0 and used < cap and proj_mem >= est_gate and cpu < cfg.get("cpu_target_pct", 90) and swap_ok and disk_ok and new_launched < args.max_launch:  # USER 2026-10-10: backfill must respect the disk guard — it fires exactly when the guarded main loop correctly launches 0
            _bf_ts = st.setdefault("backfill_ts", {})
            blo = []
            for ss in _deferred_sides:
                if ss in _fresh_map:
                    continue  # USER 2026-10-12: 4-day fresh-skip — backfill never re-calculates a side computed in the last 4 days
                sym2, sd2 = ss.rsplit("_", 1)
                if venue_of(sym2) not in h["venues"]:
                    continue
                if not _venue_allowed(sym2, _native_syms, phase=_phase):
                    continue  # TEMPORARY USER 2026-10-10: stocks-only weekend push — backfill takes new slots, crypto blocked
                if sym2 in owner and owner[sym2] != h["name"]:
                    continue
                if sym2 in held[h["name"]]:
                    continue
                if f"{sym2}_LONG" in running or f"{sym2}_SHORT" in running:
                    continue
                if _now_ts - float(_bf_ts.get(sym2, 0) or 0) < 3600:
                    continue
                key2 = f"{ss}|30D"
                if _launch_exhausted(key2, "30D", ss, st, started, redo) or key2 in launched_keys:
                    continue
                try:
                    t2 = ((_gtiers or {}).get(ss) or {}).get("tier", "M")
                    g2 = float((_seen.get(ss) or {}).get("gain") or -1e18)
                except (TypeError, ValueError):
                    t2, g2 = "M", -1e18
                blo.append((0 if t2 == "M" else 1, -g2, ss, sym2, sd2, key2))
            blo.sort()
            for _, _, ss, sym2, sd2, key2 in blo:
                if used >= cap or proj_mem < est_gate or new_launched >= args.max_launch:
                    break
                rd = is_ready(h["name"], sym2)
                if not rd.get("ok"):
                    continue
                acts = [{"sym": sym2, "side": sd2, "window": "30D", "attempt": 1, "key": key2}]
                pd = s.get("pdir") or cfg.get("fallback_pdir")
                if not pd:
                    # 2026-10-20 WP1: s5/s6 were revived without ~/v15_current_progress_dir.txt, so every candidate was dropped HERE
                    # with no log line and the host reported "no eligible ready symbol" for 40+ min. Never drop silently.
                    log.setdefault("skipped_no_pdir", []).append(f"{h['name']}:{sym}")
                    continue
                tag = f"BACKFILL {h['name']}:{sym2}:{sd2[0]}30D"
                if args.dry_run or sim:
                    log["launched"].append(tag + " (dry)")
                    launched_keys.add(key2)
                else:
                    if _ALL_LAUNCH_NPZ:
                        _dispatch_async(h, sym2, acts, pd, _workers_for(sym2, workers), f"{h['name']}:{sym2}:{sd2[0]}30D")
                        _dispatch_q_mark(st, [key2], _now_ts)
                    else:
                        try:
                            subprocess.run(["ssh", "-o", "BatchMode=yes", h.get("_via", h["ssh"][0]), pair_launch_cmd(h, acts, pd, _workers_for(sym2, workers))], timeout=40)
                        except subprocess.TimeoutExpired:
                            print(f"[sched] backfill launch ssh timeout {h['name']} {sym2} (job may still have started; pgrep dedups)", flush=True)
                    _burn_launch(key2, "30D", ss, st, started)
                    st.setdefault("last_act", {})[ss] = key2
                    _bf_ts[sym2] = _now_ts
                    launched_keys.add(key2)
                    log["launched"].append(tag)
                used += 1
                proj_mem -= est_gate + float(rd.get("size_mb") or 0)
                held[h["name"]][sym2] = "new"
                st["held"].setdefault(h["name"], {})[sym2] = now.isoformat()
                owner[sym2] = h["name"]
                launched += 1
                new_launched += 1
        info["launched_pairs"] = launched
        info["slots"] = f"{len(held[h['name']])}/{cap}"
        HB[h["name"]].update(used=len(held[h["name"]]), proj_mem=proj_mem, launched=launched, new_launched=new_launched, held_syms=sorted(held[h["name"]]))
        if cpu < cfg.get("cpu_target_pct", 90) and launched == 0:
            info["idle_reason"] = "disk guard" if not disk_ok else "slots full" if used >= cap else "mem guard" if proj_mem < est_gate else "NO PROGRESS DIR on host (~/v15_current_progress_dir.txt missing)" if not (s.get("pdir") or cfg.get("fallback_pdir")) else "no eligible ready symbol for this host"
            info["idle_detail"] = {"proj_mem_mb": round(proj_mem), "est_gate_mb": round(est_gate), "mem_avail_mb": s["mem_avail_mb"], "venues": _gate_venues, "used": used, "cap": cap, "cpu": round(cpu), "npz_stale_jit": len(log.get("npz_stale_jit", [])), "npz_stale_held": sum(1 for v_ in (log.get("skipped_unready") or {}).values() if "stale" in str(v_) and str(v_).startswith(h["name"])), "npz_stale_refused_150s": _dispatch_stale_recent()}
        log["hosts"][h["name"]] = info
    if _pin_sides:  # 2026-10-13: every pinned side names its state each tick (an idle pin must explain itself)
        _ps_log = {}
        for _pss in sorted(_pin_sides):
            _psym, _pside = _pss.rsplit("_", 1)
            _cs = (chain_state.get(_psym) or {}).get(_pside)
            _ps_log[_pss] = {"chain": _cs[0] if _cs else "not-in-universe", "owner": owner.get(_psym), "running": (running.get(_pss) or {}).get("host"),
                             "done": _pss in done, "started": _pss in started, "dispatching": _psym in _dispatching_syms(st, _now_ts),
                             "launched": any(t.split(":")[1] == _psym for t in log["launched"] if t.count(":") >= 2) or any(t.split(":")[1] == _psym for t in log.get("dispatch_async", []))}
        log["pin_status"] = _ps_log
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
            acts = [a for a in pending_actions(sym, h["name"]) if a["key"] not in launched_keys and not _launched_recently(a["key"], st.get("launched_at"), _now_ts) and (a["window"] == "365D" or (a["window"] == "REPAIR" and a["attempt"] == 1)) and f"{sym}_{a['side']}" not in running]
            if not acts:
                continue
            if B["new_launched"] >= args.max_launch or len(held[h["name"]]) >= B["cap"] or B["proj_mem"] < B["est_pair"] or B["cpu"] >= cfg.get("cpu_target_pct", 90) or not _disk_admit_ok(s):  # USER 2026-10-10: pass-2 steal must respect the disk guard
                break
            if venue_of(sym) not in h["venues"]:
                continue
            rd = is_ready(h["name"], sym)
            if not rd.get("ok"):
                continue
            if not _pair_gate_ok(sym, acts, owner, chain_state, st["attempts"], pend_sides={a["side"] for a in pending_actions(sym, h["name"]) if f"{a['sym']}_{a['side']}" not in running}):
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
                if _ALL_LAUNCH_NPZ:
                    _dispatch_async(h, sym, acts, pd, _workers_for(sym, B["workers"]), tag.replace("[steal]", ""))
                    _dispatch_q_mark(st, [a["key"] for a in acts], _now_ts)
                else:
                    try:
                        subprocess.run(["ssh", "-o", "BatchMode=yes", h.get("_via", h["ssh"][0]), pair_launch_cmd(h, acts, pd, _workers_for(sym, B["workers"]))], timeout=40)
                    except subprocess.TimeoutExpired:
                        print(f"[sched] steal launch ssh timeout {h['name']} {sym} (job may still have started; pgrep dedups)", flush=True)
                for a in acts:
                    st["attempts"][a["key"]] = st["attempts"].get(a["key"], 0) + 1
                    st.setdefault("last_act", {})[a["key"].split("|")[0]] = a["key"]
                    st["launched_at"][a["key"]] = _now_ts
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
    # ---- NPZ PREFETCH (USER 2026-10-13): s1 readies the NEXT symbol's NPZ while the current boards finish, so a
    # freed slot dispatches instantly on a fresh tail. Runs AFTER the launch pass (never delays a launch), bounded per
    # tick, and skipped while s1 is busy so the NPZ job can never eat the compute s1 needs for its own boards
    # ("always has 95% free for its own calculations"). Prefetch failures are informational: dispatch re-checks anyway.
    if npz_prefetch and os.environ.get("V15_SCHED_NPZ_PREFETCH", "0") == "1" and not (args.dry_run or sim):  # 2026-10-13 USER: build ONLY the NPZ the server is about to load (dispatch job) — speculative prefetch wasted CPU and blocked the 110 s tick
        try:
            _pf_budget = int(os.environ.get("V15_SCHED_NPZ_PREFETCH_BUDGET", "4"))
        except (TypeError, ValueError):
            _pf_budget = 4
        try:
            _pf_maxload = float(os.environ.get("V15_SCHED_NPZ_PREFETCH_MAXLOAD", "1.6"))
        except (TypeError, ValueError):
            _pf_maxload = 1.6
        try:
            _l1 = float(open("/proc/loadavg").read().split()[0]) / max(1, os.cpu_count() or 1)
        except Exception:
            _l1 = 0.0
        if _l1 > _pf_maxload:
            log["npz_prefetch"] = {"skipped": f"s1 load {_l1:.2f}x cores > {_pf_maxload}x (keeping s1 free for its own calculations)"}
        else:
            _pf_done, _pf_hold = [], []
            for _h, _syms in npz_prefetch:
                for _s in _syms:
                    if len(_pf_done) + len(_pf_hold) >= _pf_budget:
                        break
                    _r = npz_prep(_h, _s)
                    if _r is None:
                        continue
                    (_pf_done if _r.get("ok") else _pf_hold).append(f"{_h['name']}:{_s}:{_r.get('action') or _r.get('reason')}")
            log["npz_prefetch"] = {"ready": _pf_done, "held": _pf_hold, "load_x": round(_l1, 2)}
            if _pf_done or _pf_hold:
                print(f"[sched] npz prefetch ready={len(_pf_done)} held={len(_pf_hold)} {_pf_done[:4]}", flush=True)
    # ---- SETTINGS HISTORY (USER 2026-10-13): "the db written by the fleet orchestrator with each sym_side's historical
    # settings day by day ... updated when the json is written for any sym_side as soon as its new settings have been
    # calculated." The orchestrator owns it: whenever this tick sees a sym_side that PUBLISHED since the last tick, it
    # fires tools/v15_settings_history.py detached (flock = single instance) to append that board's full override set +
    # metrics to per_sym_history. HISTORY ONLY — per_sym_active stays behind the go-live gate. Never blocks the tick.
    try:
        _hist_seen = st.setdefault("hist_seen", {})
        _fresh_pub = []
        for _ss, _g in (gains or {}).items():
            try:
                _mt = float((_g or {}).get("mtime") or 0.0)
            except (TypeError, ValueError):
                continue
            if _mt > float(_hist_seen.get(_ss) or 0.0):
                _fresh_pub.append(_ss)
                _hist_seen[_ss] = _mt
        if len(_hist_seen) > 4000:
            for _k in sorted(_hist_seen, key=lambda k: _hist_seen[k])[:1000]:
                _hist_seen.pop(_k, None)
        if _fresh_pub and not (args.dry_run or sim):
            subprocess.Popen(["setsid", "flock", "-n", "/tmp/v15_settings_history.lock",
                              sys.executable, str(ROOT / "tools" / "v15_settings_history.py"), "--window-h", "6"],
                             cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            log["settings_history"] = {"new_publishes": sorted(_fresh_pub)[:12], "n": len(_fresh_pub), "spawned": True}
            print(f"[sched] settings-history: {len(_fresh_pub)} new publish(es) -> recording settings to per_sym_history", flush=True)
    except Exception as _he:
        log["settings_history"] = {"error": str(_he)[:160]}
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
    log["fresh_skipped"] = {"sides": len(_fresh_skipped), "sample": _fresh_skipped[:30]}
    log["refresh_skip_same_npz"] = {"sides": len(_same_npz_skips), "sample": _same_npz_skips[:30]}
    log["recent_unmoved_skip"] = {"sides": len(_recent_unmoved_skips), "sample": _recent_unmoved_skips[:30], "recent_sort_last": len(_recent_syms or ())}
    try:  # USER 2026-10-11 (every sym gets its turn): read-only audit — why each non-terminal sym did NOT launch this tick. Guarded: a logging bug must never kill the tick.
        _starve = {"running": [], "waiting_base": [], "capped": [], "venue_gated": [], "unready": [], "unowned_ready": [], "owner_ready": [], "owned_busy": []}
        for _ssym in all_syms:
            if terminal(_ssym):
                continue
            _acts = pending_actions(_ssym, "")
            if not _acts:
                _states = [s for s, _a in (chain_state.get(_ssym) or {}).values()]
                _starve["running" if "running" in _states else "capped" if any(s in ("need30", "need365", "needrepair", "needgs") for s in _states) else "waiting_base"].append(_ssym)
                continue
            if not _venue_allowed(_ssym, _native_syms, phase=_phase):
                _starve["venue_gated"].append(_ssym)
                continue
            if not any(is_ready(_hn, _ssym).get("ok") for _hn in readiness_by_host):
                _starve["unready"].append(_ssym)
                continue
            _oh = owner.get(_ssym)
            if not _oh:
                _starve["unowned_ready"].append(_ssym)
                continue
            _hb = HB.get(_oh)
            _room = bool(_hb) and len(held.get(_oh, {})) < _hb["cap"] and _hb["proj_mem"] >= _hb["est_pair"] and _hb["cpu"] < float(cfg.get("cpu_target_pct", 90))
            _starve["owner_ready" if _room else "owned_busy"].append(_ssym)
        log["starve_audit"] = {k: {"n": len(v), "syms": sorted(v)[:12]} for k, v in _starve.items()}
    except Exception as _e_audit:
        log["starve_audit_error"] = f"{type(_e_audit).__name__}: {_e_audit}"
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
    log["npz_stale"] = {"jit_dispatched": len(log.get("npz_stale_jit", [])), "held_unready": sum(1 for v_ in (log.get("skipped_unready") or {}).values() if "stale" in str(v_)), "dispatch_refused_last150s": _dispatch_stale_recent()}
    log["ready_summary"] = {}
    for hn, rb in readiness_by_host.items():
        hist = {}
        for sym_, r_ in rb.items():
            k = "ok" if r_.get("ok") else re.sub(r"[\d.]+", "#", str(r_.get("reason")))
            hist[k] = hist.get(k, 0) + 1
        log["ready_summary"][hn] = hist
    try:
        _lh = []
        for _t in list(log["launched"]) + list(log.get("dispatch_async", [])):
            _t = _t.replace(" (dry)", "")
            _pp = _t.split(":")
            if len(_pp) >= 2:
                _lh.append((_pp[0], _pp[1]))
        _seen_l, _lh2 = set(), []
        for _x in _lh:
            if _x not in _seen_l:
                _seen_l.add(_x)
                _lh2.append(_x)

        def _explain(hn, sy):
            hi = log["hosts"].get(hn) if isinstance(log.get("hosts"), dict) else None
            if sy in (log.get("skipped_unready") or {}):
                return "npz-unready: " + str(log["skipped_unready"][sy])[:80]
            if any(t.split(":")[1] == sy for t in (log.get("dispatch_skipped") or []) if t.count(":") >= 1):
                return "dispatch-skipped (not tradeable anymore)"
            if f"{hn}:{sy}" in (log.get("skipped_venue_only") or []):
                return "venue-pin"
            if any(f"{hn}:{sy}_" in x for x in (log.get("pair_sibling_skipped_done") or [])):
                return "sibling-terminal"
            if sy in {x.rsplit("_", 1)[0] for x in (log.get("own_unfinished", {}).get(hn) or [])}:
                return "own-unfinished-resume"
            if isinstance(hi, dict):
                try:
                    used, cap = [int(z) for z in str(hi.get("slots", "0/0")).split("/")]
                    est = (hi.get("est_venue_mb") or {}).get(venue_of(sy))
                    if used >= cap:
                        return "host-skip: slots full"
                    if est is not None and hi.get("proj_mem_mb", 0) < est:
                        return "host-skip: mem guard (approx, end-of-tick state)"
                    if hi.get("idle_reason") in ("mem guard", "disk guard"):
                        return "host-skip: " + hi["idle_reason"]
                except Exception:
                    pass
            return None

        _aud = _order_audit(_ORDER_BY_HOST, _lh2, _explain, lambda sy: _PLABEL.get(sy, "oldest"), lambda sy: _PAGE.get(sy))
        log["order_audit"] = {"top": _aud["top"], "violations": _aud["violations"], "launches_checked": len(_aud["checks"])}
        for _hn, _tp in _aud["top"].items():
            print(f"[sched] ORDER-AUDIT {_hn}: " + " | ".join(f"{i + 1}.{e['sym']}({e['rule']}, age {e['board_age_h']}h)" for i, e in enumerate(_tp)), flush=True)
        for _c in _aud["checks"]:
            print(f"[sched] ORDER-CHECK {_c['host']}: launched {_c['launched']} at rank {_c['rank']}; skipped ahead {_c['skipped_ahead']}", flush=True)
        for _v in _aud["violations"]:
            print(f"[sched] ORDER_VIOLATION {_v['host']}: launched {_v['launched']} (rank {_v['rank']}) ahead of unlaunched {_v['unlaunched_ahead']}: {_v['cause']}", flush=True)
        if not (args.dry_run or sim):
            _fo = ROOT / "data" / "reports" / "fleet_launch_order.json"
            _ft = _fo.with_suffix(".json.tmp")
            _ft.write_text(json.dumps({"now": now.isoformat(), "mode": "catchup" if _CATCHUP_MODE[0] else "slow", **log["order_audit"], "checks": _aud["checks"]}, indent=1))
            os.replace(_ft, _fo)
    except Exception as _e_aud:
        print(f"[sched] order audit FAILED (non-fatal): {_e_aud}", flush=True)
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
    # 2026-10-13 USER full capacity: the hosts file is rewritten by the autopilot self-heal; an env floor keeps the cpu target
    # (50 there idled s1/s5/s6 at 13-69% busy). V15_SCHED_CPU_TARGET_MIN=0 restores the file value.
    try:
        cfg["cpu_target_pct"] = max(float(cfg.get("cpu_target_pct", 90)), float(os.environ.get("V15_SCHED_CPU_TARGET_MIN", "90")))
    except (TypeError, ValueError):
        pass
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
