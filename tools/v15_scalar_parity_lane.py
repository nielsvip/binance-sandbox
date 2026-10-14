#!/usr/bin/env python3
"""v15_scalar_parity_lane — S2-ONLY runner of REAL live-vs-vector trade parity (USER 2026-10-14: "run backtest_v12_engine … to get
real parity between the live and vector scripts instead of just overriding live with vector results … only on s2, give it plenty
of time to finish each, the order sounds perfect").
Cron tick (every 10 min, flock): reaps finished jobs, then starts the next queue items (data/parity/scalar_lane/queue.json, built
on the Mac by tools/v15_scalar_parity_queue.py, already in priority order) while slots and load allow.
Each job = tools/v15_trade_parity.py --sym-side SS --overrides-json <the published set live trades> on the same frozen 30D NPZ:
  vec leg  = v12_quick_engine (what the board measured)
  live leg = backtest_v12_engine.run_one -> real ez_manage / tradier_manage process_position
with TRADE_PARITY_VEC_EXACT=0: the live leg does NOT ingest the vector's actions (V8_VEC_EXACT_INGEST off) — the live scripts decide
on their own, so a divergence is measured, not overridden. nice 19, timeout PARITY_LANE_TIMEOUT_H (default 12 h) per job.
Results: data/parity/scalar_lane/<SS>__<md5_8>/<SS>_trade_parity.json (+ job.log, job.json); one result per (sym_side, set md5) —
a new published set re-queues it. STATUS.json / STATUS.md summarise the lane.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = ROOT / "data" / "parity" / "scalar_lane"
QUEUE = LANE / "queue.json"
MAX_JOBS = int(os.environ.get("PARITY_LANE_MAX_JOBS", "3"))
MAX_LOAD = float(os.environ.get("PARITY_LANE_MAX_LOAD", "14"))
TIMEOUT_H = float(os.environ.get("PARITY_LANE_TIMEOUT_H", "12"))
ALLOWED_HOSTS = ("s2",)


def _host_ok():
    h = socket.gethostname().lower()
    return any(a in h for a in ALLOWED_HOSTS) or os.environ.get("PARITY_LANE_HOST_OK") == "1"


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _jobdir(item):
    return LANE / f"{item['sym_side']}__{item['overrides_md5'][:8]}"


def _state(d):
    j = d / "job.json"
    if not j.exists():
        return "PENDING", {}
    meta = json.loads(j.read_text())
    if (d / f"{meta['sym_side']}_trade_parity.json").exists():
        return "DONE", meta
    if _alive(int(meta.get("pid", 0))):
        return "RUNNING", meta
    return "DIED", meta


def launch(item):
    d = _jobdir(item)
    d.mkdir(parents=True, exist_ok=True)
    (d / "overrides.json").write_text(json.dumps({"cumulative_overrides": item["overrides"]}, default=str))
    env = dict(os.environ, TRADE_PARITY_VEC_EXACT="0", V15_PARITY_FORCE_REAL="1", PYTHONUNBUFFERED="1")
    py = str(ROOT / ".venv" / "bin" / "python")
    cmd = ["nice", "-n", "19", "timeout", str(int(TIMEOUT_H * 3600)), py, "-u", "tools/v15_trade_parity.py", "--sym-side", item["sym_side"],
           "--overrides-json", str(d / "overrides.json"), "--out-dir", str(d)]
    log = open(d / "job.log", "ab")
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    meta = {k: item[k] for k in ("sym_side", "tier", "overrides_md5", "published_at", "history_id", "open_position", "trades_recent", "streak")}
    meta.update({"pid": p.pid, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "timeout_h": TIMEOUT_H, "vec_exact_ingest": False, "host": socket.gethostname()})
    (d / "job.json").write_text(json.dumps(meta, indent=1))
    return meta


def status(items):
    rows, counts = [], {"DONE": 0, "RUNNING": 0, "DIED": 0, "PENDING": 0}
    for it in items:
        st, meta = _state(_jobdir(it))
        counts[st] += 1
        r = {"sym_side": it["sym_side"], "tier": it["tier"], "state": st, "md5": it["overrides_md5"][:8]}
        if st == "DONE":
            rep = json.loads((_jobdir(it) / f"{it['sym_side']}_trade_parity.json").read_text())
            r.update({k: rep.get(k) for k in ("verdict", "vec_trips", "live_trips", "matched", "vec_match_rate", "live_match_rate", "exit_agree_rate")})
            r["secs"] = rep.get("secs")
        elif st in ("RUNNING", "DIED"):
            r["started"] = meta.get("started")
        rows.append(r)
    return counts, rows


def main():
    if not _host_ok():
        sys.exit(f"[lane] refused: S2-only lane (host {socket.gethostname()})")
    q = json.loads(QUEUE.read_text()) if QUEUE.exists() else {"items": []}
    items = q.get("items") or []
    load1 = os.getloadavg()[0]
    running = sum(1 for it in items if _state(_jobdir(it))[0] == "RUNNING")
    started = []
    for it in items:
        if running >= MAX_JOBS or os.getloadavg()[0] >= MAX_LOAD:
            break
        if _state(_jobdir(it))[0] != "PENDING":
            continue  # DONE, RUNNING, or DIED (a died/timed-out job is reported, not silently retried)
        started.append(launch(it)["sym_side"])
        running += 1
    counts, rows = status(items)
    st = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": socket.gethostname(), "load1": round(load1, 1), "max_jobs": MAX_JOBS,
          "max_load": MAX_LOAD, "timeout_h": TIMEOUT_H, "queue_built": (q.get("_meta") or {}).get("built"), "counts": counts, "started_now": started, "rows": rows}
    (LANE / "STATUS.json").write_text(json.dumps(st, indent=1, default=str))
    md = [f"# Scalar parity lane (S2) — {st['at']}", "", f"load1 {st['load1']} | jobs ≤{MAX_JOBS} | timeout {TIMEOUT_H}h | queue built {st['queue_built']} | {counts}", "",
          "| tier | sym_side | state | verdict | vec trips | live trips | matched | vec match | live match | exit agree |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["state"] != "PENDING":
            md.append(f"| {r['tier']} | {r['sym_side']} | {r['state']} | {r.get('verdict', '')} | {r.get('vec_trips', '')} | {r.get('live_trips', '')} | {r.get('matched', '')} | {r.get('vec_match_rate', '')} | {r.get('live_match_rate', '')} | {r.get('exit_agree_rate', '')} |")
    (LANE / "STATUS.md").write_text("\n".join(md) + "\n")
    print(f"[lane] load1={load1:.1f} counts={counts} started={started}")


if __name__ == "__main__":
    main()
