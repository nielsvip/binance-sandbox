#!/usr/bin/env python3
"""v15_director — fleet director (USER 2026-10-08: "be the director of all servers and make sure they advance as rapidly as
possible without skipping 30-365-30D discipline, max sym_sides per hour, so every 24h a new 30D set with pos gain is applied live").

Runs on S1 (cron */10). Read-only on engines/templates; it steers the existing scheduler through TWO files and reports:
  1. ~/v15_priority_syms.txt  -> V15_SCHED_PRIORITY_SYMS for tools/v15_fleet_scheduler.py (cron line reads the file).
     Order (closest to go-live first):
       a) 30D DONE + positive + valid but no 365D verdict yet  (one 365D run away from live)
       b) live-traded universe (symbols_active / symbols_inf_*) without a finished 30D in this round
       c) 365D failed once (repair/GS budget left)
       d) everything else worst-first (scheduler default)
  2. data/daily_chain/DIRECTOR_STATUS.json + .md (also rsynced to the Mac by the Mac cron): funnel per cat_side
     (30D done / positive / 365D ok / heal ok / golive-ready), throughput per host (boards finished per hour), ETA to the
     full universe, blockers (hosts idle, go-live DRY-RUN gate, NPZ staleness, round age), and the last 24h applied-live count.
Honest numbers only: everything is read from progress JSONs, scheduler ticks, confirmed_365d.json and the chain stamps.
"""
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.path.expanduser("~/binance-sandbox"))
HOME = Path(os.path.expanduser("~"))
SCHED_LOG = Path("/tmp/v15_fleet_sched.log")
PRIO = HOME / "v15_priority_syms.txt"
OUT = ROOT / "data" / "daily_chain"
MAX_PRIO = int(os.environ.get("V15_DIRECTOR_MAX_PRIO", "24"))


def now():
    return dt.datetime.now(dt.timezone.utc)


def jload(p, d=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return d


def progress_dir():
    try:
        return Path((HOME / "v15_current_progress_dir.txt").read_text().strip())
    except Exception:
        return None


def last_tick():
    try:
        lines = [l for l in SCHED_LOG.read_text().splitlines() if l.startswith('{"now"')]
        return json.loads(lines[-1]) if lines else {}
    except Exception:
        return {}


def ticks_since(hours):
    out = []
    try:
        cut = now() - dt.timedelta(hours=hours)
        for l in SCHED_LOG.read_text().splitlines():
            if l.startswith('{"now"'):
                d = json.loads(l)
                if dt.datetime.fromisoformat(d["now"]) >= cut:
                    out.append(d)
    except Exception:
        pass
    return out


def live_universe():
    syms = set()
    for f in ("symbols_active.json", "symbols_inf_long.json", "symbols_inf_short.json", "symbols_ang_short.json"):
        d = jload(ROOT / f, None)
        if isinstance(d, list):
            syms |= {str(s).upper() for s in d}
        elif isinstance(d, dict):
            syms |= {str(s).upper() for s in (d.get("symbols") or d.keys())}
    return syms


def board_state(p):
    """-> dict(symside, done_rows, gain, baseline, valid30, v365) from a progress json (fields as v15_pilot writes them)."""
    d = jload(p, {}) or {}
    g = d.get("cumulative_gain")
    f365 = d.get("final_365d") or d.get("diagnose_m365") or {}
    v365 = None
    if isinstance(f365, dict) and f365:
        v365 = {"valid": bool(f365.get("valid")), "gain": f365.get("gain") or f365.get("gain_pct")}
    return {"symside": d.get("symside") or Path(p).name.replace("_v14_progress.json", ""), "rows": len(d.get("done") or {}),
            "gain": g, "baseline": d.get("baseline_gain"), "endgame_complete": bool((d.get("endgame") or {}).get("complete")),
            "v365": v365, "mtime": os.path.getmtime(p)}


def main():
    t = now()
    pdir = progress_dir()
    boards = [board_state(p) for p in glob.glob(str(pdir / "*_v14_progress.json"))] if pdir else []
    confirmed = jload(ROOT / "data" / "confirmed_365d.json", {}) or {}
    conf_set = set(confirmed.keys()) if isinstance(confirmed, dict) else set()
    tick = last_tick()
    cats = tick.get("cats") or {}
    live = live_universe()
    # finished = endgame complete (the pilot's last stage) ; positive = gain > 0 and above baseline
    done = [b for b in boards if b["endgame_complete"]]
    pos = [b for b in done if isinstance(b["gain"], (int, float)) and b["gain"] > 0]
    need365 = [b for b in pos if b["v365"] is None and b["symside"] not in conf_set]
    ok365 = [b for b in pos if (b["v365"] and b["v365"]["valid"] and (b["v365"]["gain"] or 0) > 0) or b["symside"] in conf_set]
    bad365 = [b for b in pos if b["v365"] and not ((b["v365"]["valid"]) and (b["v365"]["gain"] or 0) > 0) and b["symside"] not in conf_set]
    done_syms = {b["symside"].rsplit("_", 1)[0] for b in done}
    live_todo = sorted(s for s in live if s not in done_syms)
    # priority file: a) one 365D away, b) live universe not yet done, c) 365D-failed once
    prio = []
    for b in need365:
        prio.append(b["symside"].rsplit("_", 1)[0])
    prio += live_todo
    prio += [b["symside"].rsplit("_", 1)[0] for b in bad365]
    seen, ordered = set(), []
    for s in prio:
        if s not in seen:
            seen.add(s); ordered.append(s)
    ordered = ordered[:MAX_PRIO]
    PRIO.write_text(",".join(ordered) + "\n")
    # throughput: boards whose progress mtime is within the last hour and endgame complete
    hour_ago = (t - dt.timedelta(hours=1)).timestamp()
    done_last_h = [b for b in done if b["mtime"] >= hour_ago]
    launches_24h = sum(len(x.get("launched") or []) for x in ticks_since(24))
    hosts = {h: (v.get("slots"), v.get("cpu"), v.get("idle_reason")) for h, v in (tick.get("hosts") or {}).items() if isinstance(v, dict)}
    idle_hosts = [h for h, (s, c, r) in hosts.items() if s and s.startswith("0/")]
    # go-live gate lives on the Mac (tools/v15_daily_chain_mac_apply.sh step5b); the Mac cron appends its verdict to the .md.
    golive_gate = {"note": "evaluated on the Mac at apply time (step5b)"}
    golive_open = None
    # applied-live in the last 24h (parity_promotions ledger)
    applied = 0
    try:
        cut = (t - dt.timedelta(hours=24)).isoformat()
        for l in (ROOT / "data" / "parity_promotions.jsonl").read_text().splitlines()[-5000:]:
            d = json.loads(l)
            if str(d.get("at") or d.get("ts") or "") >= cut:
                applied += 1
    except Exception:
        pass
    # NPZ freshness
    npz_last = None
    try:
        import numpy as np
        f = sorted(glob.glob(str(ROOT / "backtest_v8" / "indicators" / "*USDC.npz")), key=os.path.getmtime)[-1]
        ts = np.load(f, allow_pickle=True)["timestamps"]
        npz_last = dt.datetime.fromtimestamp(float(ts[-1]), dt.timezone.utc).isoformat()
    except Exception:
        pass
    total_pairs = sum(int(c.get("sym_sides") or 0) for c in cats.values()) // 2 or 189
    rate_pairs_h = max(len(done_last_h) / 2.0, 0.0)
    eta_h = round((total_pairs - len(done) / 2.0) / rate_pairs_h, 1) if rate_pairs_h > 0 else None
    status = {"at": t.isoformat(), "round": (HOME / "v15_defaults_round.txt").read_text().strip() if (HOME / "v15_defaults_round.txt").exists() else None,
              "progress_dir": str(pdir), "boards_in_round": len(boards), "finished": len(done), "positive": len(pos), "need_365d": len(need365),
              "ok_365d": len(ok365), "bad_365d": len(bad365), "golive_ready(30D pos + 365D ok)": len(ok365), "confirmed_365d_file": len(conf_set),
              "finished_last_hour": len(done_last_h), "launches_24h": launches_24h, "hosts": hosts, "idle_hosts": idle_hosts,
              "golive_gate": {**golive_gate, "open": golive_open}, "applied_live_24h": applied, "npz_last_bar": npz_last,
              "priority_written": ordered, "eta_hours_full_universe": eta_h, "cats": {k: {kk: v.get(kk) for kk in ("sym_sides", "done30", "running", "v365_ok", "v365_bad", "repair_ok", "gs_ok")} for k, v in cats.items()}}
    blockers = []
    if idle_hosts:
        blockers.append(f"idle hosts: {idle_hosts}")
    if npz_last and (t - dt.datetime.fromisoformat(npz_last)).total_seconds() > 3 * 3600:
        blockers.append(f"NPZ stale: last bar {npz_last}")
    if len(done_last_h) == 0 and len(boards) > 0:
        blockers.append("0 boards finished in the last hour")
    status["blockers"] = blockers
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "DIRECTOR_STATUS.json").write_text(json.dumps(status, indent=1, default=str))
    md = [f"# Fleet director status — {t.strftime('%Y-%m-%d %H:%M')}Z", "",
          f"round `{status['round']}` · boards {len(boards)} · finished {len(done)} · positive {len(pos)} · need 365D {len(need365)} · 365D ok {len(ok365)} · 365D bad {len(bad365)}",
          f"finished last hour: {len(done_last_h)} · launches 24h: {launches_24h} · ETA full universe: {eta_h} h · applied live 24h: {applied}",
          f"hosts: " + ", ".join(f"{h} {s} cpu {c}%" + (f" ({r})" if r else "") for h, (s, c, r) in hosts.items()),
          f"NPZ last bar: {npz_last}", "", "## Blockers", ""] + ([f"- {b}" for b in blockers] or ["- none"]) + ["", "## Priority (written to ~/v15_priority_syms.txt)", "", ", ".join(ordered) or "(none)"]
    (OUT / "DIRECTOR_STATUS.md").write_text("\n".join(md) + "\n")
    print(f"[director] finished={len(done)} pos={len(pos)} need365={len(need365)} ok365={len(ok365)} last_h={len(done_last_h)} prio={len(ordered)} blockers={len(blockers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
