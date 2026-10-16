#!/usr/bin/env python3
"""V15 PIPELINE HEALTH (USER 2026-10-15: "monitor and make sure all will keep running without interruptions").

One watchdog over the DATA chain that feeds every board, checked link by link, so a break is named instead of being
inferred hours later from bad gains. Each link below broke at least once on 2026-10-15 and each failure was silent:

  L1 ticks      gateway/Mac build klines from second-by-second ticks (1m -> 3/5m -> 15m -> 1h -> 4h -> D); the forming
                bar is published `provisional` and overwritten when the official OHLCV lands.
  L2 merge      klines_append appends those SHORT fresh files onto s1's COMPLETE store. Broke twice: a canonical lane
                ~5 h behind shadowed every newer bar ("a canonical file is the only source"), and the stock files in
                klines_cache_macbook/tradier/ were never globbed. Symptom: `up_to_date` for everything, which reads
                like success.
  L3 npz        npz_bar_refresh --loop appends the closed bar to every NPZ (~170 s/pass, append-only, local-only).
                Broke on timing: it fired at the bar boundary +75 s while L2 needs 150-550 s, so it asked for a bar
                the store did not have (EXCHANGE_BAR_NOT_READY 219-282 of 369). NPZ_LOOP_SETTLE_S=300 fixes it.
  L4 dispatch   the scheduler ticks every 2 min and must keep launching boards.
  L5 archive    the orchestrator copies each published board's own NPZ (data/parity/npz_archive) so s2's
                backtest_v12 parity verifies THAT result. If L5 stalls the bytes are gone within one JIT rebuild.

Red on any link -> data/reports/ALERT_V15_PIPELINE.txt + exit 1. Read-only everywhere; it never restarts anything
(the crons already do: klines_append */5, npz loop */15 watchdog, scheduler */2, parity lane */10).
Companion guards, not duplicated here: v15_throughput_guard (boards/h), v15_round_regression_guard (gain vs
best_start), v15_live_follows_boards_guard (live == published set).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reports" / "v15_pipeline_health.json"
ALERT = ROOT / "data" / "reports" / "ALERT_V15_PIPELINE.txt"

# one python per host; prints a single JSON line
PROBE_S1 = r'''
import glob, json, os, re, time, datetime as dt
NOW = time.time()
H = os.path.expanduser("~")
R = H + "/binance-sandbox"
out = {"host": os.uname().nodename, "now": NOW}

def last_bar(p):
    try:
        d = json.load(open(p))
        rows = d if isinstance(d, list) else (d.get("data") or d.get("bars") or [])
        if not rows:
            return None, None
        r = rows[-1]
        ts = r.get("timestamp") if isinstance(r, dict) else (r[0] if isinstance(r, (list, tuple)) and r else None)
        prov = bool(r.get("provisional")) if isinstance(r, dict) else False
        if ts is None:
            return None, None
        if isinstance(ts, str):
            ts = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        ts = float(ts)
        return (ts / 1000.0 if ts > 1e11 else ts), prov
    except Exception:
        return None, None

# L1/L2: for a sample of symbols, how far is s1's LONG store behind the freshest version on the box?
QUOTES = ("USDT", "USDC", "USD1", "FDUSD", "TUSD", "DAI")
npzs = [os.path.basename(p)[:-4] for p in glob.glob(R + "/backtest_v8/indicators/*.npz")]
crypto = sorted(s for s in npzs if s.endswith(QUOTES))[:12]
stocks = sorted(s for s in npzs if not s.endswith(QUOTES))[:12]
for label, syms, sub in (("crypto", crypto, ""), ("stocks", stocks, "tradier/")):
    lags, longest, dead = [], None, []
    for s in syms:
        best_fresh, best_long = None, None
        for root in ("klines_cache_macbook", "klines_cache_gateway", "klines_cache/gateway"):
            t, prov = last_bar(f"{R}/{root}/{sub}{s}_15m.json")
            if t and not prov and (best_fresh is None or t > best_fresh):
                best_fresh = t
        t, _ = last_bar(f"{R}/klines_cache/{sub}{s}_15m.json")
        best_long = t
        # DEAD symbols (no bar within 7 d on ANY source — BK, BLOK, CALM on 2026-10-15) are no longer collected.
        # Their lag is permanent and would mask a real regression behind a 26-day "worst", so they are counted
        # separately instead of being averaged into the health of the live set.
        if best_fresh and (NOW - best_fresh) > 7 * 86400:
            dead.append(s)
            continue
        if best_fresh and best_long:
            lag = (best_fresh - best_long) / 60.0
            lags.append(lag)
            if longest is None or lag > longest[0]:
                longest = (lag, s)
    out[f"merge_{label}"] = {"n": len(lags), "max_lag_min": round(max(lags), 1) if lags else None,
                             "median_lag_min": round(sorted(lags)[len(lags) // 2], 1) if lags else None,
                             "worst": longest[1] if longest else None, "dead": sorted(dead)}
# L3b: crypto NPZs the append loop must refuse forever (no htf_align -> REFUSE_LEGACY_HTF_ALIGN); they need a REBUILD
try:
    import zipfile
    legacy = []
    for p in glob.glob(R + "/backtest_v8/indicators/*.npz"):
        sym = os.path.basename(p)[:-4]
        if not sym.endswith(QUOTES):
            continue
        try:
            with zipfile.ZipFile(p) as z:
                if not any(n.startswith("htf_align") for n in z.namelist()):
                    legacy.append(sym)
        except Exception:
            pass
    out["npz_legacy_crypto"] = sorted(legacy)
except Exception as e:
    out["npz_legacy_err"] = type(e).__name__
# L2: appender heartbeat
try:
    p = H + "/logs/klines_append.log"
    out["append_log_age_min"] = round((NOW - os.path.getmtime(p)) / 60.0, 1)
    tail = open(p, errors="ignore").read()[-4000:]
    m = re.findall(r"DONE in ([\d.]+)s \| stats=\{([^}]*)\}", tail)
    if m:
        out["append_last_secs"] = float(m[-1][0])
        out["append_last_stats"] = m[-1][1][:160]
except Exception as e:
    out["append_err"] = type(e).__name__
# L3: npz loop alive + last cycle
out["npz_loop_pids"] = len([1 for d in glob.glob("/proc/[0-9]*") if "npz_bar_refresh" in (open(d + "/cmdline", errors="ignore").read() if os.path.exists(d + "/cmdline") else "") and "--loop" in (open(d + "/cmdline", errors="ignore").read() if os.path.exists(d + "/cmdline") else "")])
try:
    t = open("/tmp/npz_loop.log", errors="ignore").read()[-20000:]
    cyc = re.findall(r"\[npz_bar_refresh (\d\d:\d\d:\d\d)Z\] cycle done (.*)", t)
    if cyc:
        out["npz_cycle_last_utc"] = cyc[-1][0]
        body = cyc[-1][1]
        out["npz_cycle_installed"] = int((re.search(r"'INSTALLED': (\d+)", body) or [0, 0])[1]) if "INSTALLED" in body else 0
        out["npz_cycle_not_ready"] = int((re.search(r"'EXCHANGE_BAR_NOT_READY': (\d+)", body) or [0, 0])[1]) if "EXCHANGE_BAR_NOT_READY" in body else 0
        out["npz_cycle_no_new"] = int((re.search(r"'NO_NEW_ROWS': (\d+)", body) or [0, 0])[1]) if "NO_NEW_ROWS" in body else 0
except Exception as e:
    out["npz_cycle_err"] = type(e).__name__
try:
    envs = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            cl = open(d + "/cmdline", errors="ignore").read()
            if "npz_bar_refresh" in cl and "--loop" in cl:
                envs.append("NPZ_LOOP_SETTLE_S" in open(d + "/environ", errors="ignore").read())
        except Exception:
            pass
    out["npz_loop_has_settle"] = bool(envs and all(envs))
except Exception:
    pass
# L4: scheduler tick age
try:
    t = open("/tmp/v15_fleet_sched.log", errors="ignore").read()[-40000:]
    js = [l for l in t.splitlines() if l.startswith("{")]
    if js:
        d = json.loads(js[-1])
        out["sched_tick_age_min"] = round((NOW - dt.datetime.fromisoformat(d["now"]).timestamp()) / 60.0, 1)
        out["sched_launched"] = len(d.get("launched") or [])
except Exception as e:
    out["sched_err"] = type(e).__name__
# L5: publish-time npz archive
try:
    a = R + "/data/parity/npz_archive"
    fs = glob.glob(a + "/*.npz")
    out["archive_n"] = len(fs)
    out["archive_mb"] = round(sum(os.path.getsize(f) for f in fs) / 2**20, 1)
    out["archive_newest_min"] = round((NOW - max(os.path.getmtime(f) for f in fs)) / 60.0, 1) if fs else None
except Exception as e:
    out["archive_err"] = type(e).__name__
print(json.dumps(out))
'''


def probe(aliases, code, timeout=240):
    """Try each alias in turn. s1-int is the ControlMaster tunnel (127.0.0.1:2201) and it DOES drop — it was refused
    while writing this guard — so the public route is tried next; a monitor that reports "host unreachable" because
    its own tunnel died is worse than no monitor. Records which route answered."""
    errs = []
    for alias in ([aliases] if isinstance(aliases, str) else aliases):
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", alias, "python3 -"],
                               input=code, capture_output=True, text=True, timeout=timeout)
            for line in reversed((r.stdout or "").strip().splitlines()):
                if line.startswith("{"):
                    d = json.loads(line)
                    d["_via"] = alias
                    return d
            errs.append(f"{alias}: {(r.stderr or 'no output').strip()[-120:]}")
        except Exception as e:
            errs.append(f"{alias}: {type(e).__name__}")
    return {"error": " | ".join(errs)[:260]}


def judge(s1, a):
    """-> (reds, warns). Thresholds are the measured behaviour of each link, not guesses."""
    reds, warns = [], []
    if s1.get("error"):
        return [f"s1 unreachable: {s1['error']}"], []
    cm = s1.get("merge_crypto") or {}
    sm = s1.get("merge_stocks") or {}
    if cm.get("max_lag_min") is not None and cm["max_lag_min"] > a.crypto_lag_min:
        reds.append(f"L2 merge crypto: s1's long store is {cm['max_lag_min']:.0f} min behind the freshest copy on the box (worst {cm.get('worst')}) — klines_append is not merging")
    if sm.get("max_lag_min") is not None and sm["max_lag_min"] > a.stock_lag_min:
        reds.append(f"L2 merge stocks: {sm['max_lag_min']:.0f} min behind (worst {sm.get('worst')}) — check the tradier/ subdir path")
    if (s1.get("append_log_age_min") or 999) > a.append_age_min:
        reds.append(f"L2 appender silent for {s1.get('append_log_age_min')} min (cron */5) ")
    if not s1.get("npz_loop_pids"):
        reds.append("L3 npz append loop is not running (the */15 watchdog should relaunch it)")
    elif not s1.get("npz_loop_has_settle"):
        warns.append("L3 loop is running WITHOUT NPZ_LOOP_SETTLE_S — it will fire before klines_append finishes")
    nr, inst, nonew = s1.get("npz_cycle_not_ready"), s1.get("npz_cycle_installed"), s1.get("npz_cycle_no_new")
    if nr is not None and nr > a.not_ready_max and not (cm.get("max_lag_min") or 0) > a.crypto_lag_min:
        warns.append(f"L3 last cycle: EXCHANGE_BAR_NOT_READY={nr} while the merge looks healthy — settle may still be too short (installed={inst}, no_new={nonew})")
    if (s1.get("sched_tick_age_min") or 999) > a.sched_age_min:
        reds.append(f"L4 scheduler last ticked {s1.get('sched_tick_age_min')} min ago (cron */2)")
    leg = s1.get("npz_legacy_crypto") or []
    if leg:
        warns.append(f"L3 {len(leg)} crypto NPZ(s) carry no htf_align so the append loop refuses them forever — they need a full rebuild: {' '.join(leg[:6])}")
    if s1.get("archive_n") == 0:
        warns.append("L5 npz archive is empty — s2 parity cannot verify a board on its own NPZ yet")
    elif (s1.get("archive_newest_min") or 99999) > a.archive_age_min:
        warns.append(f"L5 newest archived board NPZ is {s1.get('archive_newest_min'):.0f} min old — publishes may not be recording")
    return reds, warns


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--crypto-lag-min", dest="crypto_lag_min", type=float, default=45.0, help="crypto bars close every 15 min")
    ap.add_argument("--stock-lag-min", dest="stock_lag_min", type=float, default=90.0, help="stocks: allow for the RTH grid + propagation")
    ap.add_argument("--append-age-min", dest="append_age_min", type=float, default=25.0)
    ap.add_argument("--sched-age-min", dest="sched_age_min", type=float, default=10.0)
    ap.add_argument("--not-ready-max", dest="not_ready_max", type=int, default=150)
    ap.add_argument("--archive-age-min", dest="archive_age_min", type=float, default=240.0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    s1 = probe(["s1-int", "s1-pub"], PROBE_S1)
    reds, warns = judge(s1, a)
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "s1": s1, "red": reds, "warn": warns, "ok": not reds}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1))
    tmp.replace(OUT)
    if not a.quiet:
        cm, sm = s1.get("merge_crypto") or {}, s1.get("merge_stocks") or {}
        print(f"[pipeline] merge lag crypto={cm.get('max_lag_min')}m stocks={sm.get('max_lag_min')}m | appender {s1.get('append_log_age_min')}m ago ({s1.get('append_last_secs')}s) | npz loop pids={s1.get('npz_loop_pids')} settle={s1.get('npz_loop_has_settle')} last cycle installed={s1.get('npz_cycle_installed')} not_ready={s1.get('npz_cycle_not_ready')} | sched {s1.get('sched_tick_age_min')}m | archive {s1.get('archive_n')} files {s1.get('archive_mb')}MB", flush=True)
        for r in reds:
            print(f"  RED  {r}", flush=True)
        for w in warns:
            print(f"  warn {w}", flush=True)
        if not reds and not warns:
            print("  all links green", flush=True)
    if reds:
        ALERT.write_text(json.dumps({"at": rep["at"], "red": reds, "warn": warns,
                                     "restarts": "klines_append cron */5 | npz loop cron */15 watchdog | scheduler cron */2 | parity lane cron */10 (all self-relaunching; this guard never restarts anything)"}, indent=1))
        return 1
    if ALERT.exists():
        ALERT.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
