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
JIT NPZ (USER 2026-10-12/13 freshness + JIT law): each job is handed to S1's tools/v15_dispatch_job.py (the herd's own path:
tail refresh + md5-verified push of the symbol NPZ to S2), which then launches `--run-job` here; the job freezes a private copy
of that fresh NPZ (other NPZs symlinked) as TRADE_PARITY_NPZ_DIR so both legs read identical bytes. Never V12_ALLOW_STALE_NPZ.
"""
import hashlib
import json
import os
import pathlib
import shutil
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
S1 = os.environ.get("PARITY_LANE_S1", "niels@10.0.0.3")
SELF = os.environ.get("PARITY_LANE_SELF", "niels@10.0.0.4")
DISPATCH_GRACE_H = float(os.environ.get("PARITY_LANE_DISPATCH_GRACE_H", "3"))
STALE_RETRY_MIN = float(os.environ.get("PARITY_LANE_STALE_RETRY_MIN", "60"))
S1_BAN_FILE = os.environ.get("PARITY_LANE_S1_BAN_FILE", "binance/ez_s1_stack/data/ez_klines_ban_state.json")


def _is_crypto(sym_side):
    """A Binance venue key — the only kind a Binance REST ban on S1 can hold (USER 2026-10-18)."""
    return str(sym_side).upper().rsplit("_", 1)[0].endswith(("USDT", "USDC", "BUSD"))


def s1_ban_remaining_s():
    """Binance REST ban on S1's IP (ez_klines persists it): while active, a dispatch would make S1 call Binance (tail refresh)
    and extend the ban — the lane dispatches nothing. Unreadable -> 0 (the stale-NPZ check still protects the run)."""
    try:
        r = subprocess.run(SSH + [S1, f"cat ~/{S1_BAN_FILE}"], capture_output=True, text=True, timeout=30)
        return max(0.0, float(json.loads(r.stdout).get("ban_until_ms", 0)) / 1000.0 - time.time())
    except Exception:
        return 0.0
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


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
    sw = d / "stale_wait.json"
    if sw.exists():
        m = json.loads(sw.read_text())
        if time.time() - m.get("t", 0) < STALE_RETRY_MIN * 60:
            return "STALE_WAIT", m
        for f in ("stale_wait.json", "dispatch.json", "job.json"):
            (d / f).unlink(missing_ok=True)
        return "PENDING", {}
    j = d / "job.json"
    if not j.exists():
        dj = d / "dispatch.json"
        if dj.exists():
            m = json.loads(dj.read_text())
            return ("DISPATCHED", m) if time.time() - m.get("t", 0) < DISPATCH_GRACE_H * 3600 else ("DIED", dict(m, why="dispatch never launched the job"))
        return "PENDING", {}
    meta = json.loads(j.read_text())
    if (d / f"{meta['sym_side']}_trade_parity.json").exists():
        return "DONE", meta
    if _alive(int(meta.get("pid", 0))):
        return "RUNNING", meta
    return "DIED", meta


def dispatch(item):
    """Hand the job to S1's dispatcher: fresh NPZ pushed md5-verified to S2 first, then `--run-job` launched here."""
    d = _jobdir(item)
    d.mkdir(parents=True, exist_ok=True)
    (d / "overrides.json").write_text(json.dumps({"cumulative_overrides": item["overrides"]}, default=str))
    meta = {k: item[k] for k in ("sym_side", "tier", "overrides_md5", "published_at", "history_id", "open_position", "trades_recent", "streak")}
    (d / "item.json").write_text(json.dumps(meta, indent=1))
    sym = item["sym_side"].rsplit("_", 1)[0]
    rel = d.relative_to(ROOT)
    remote = f"cd ~/binance-sandbox && setsid -f /home/niels/.conda/envs/binance_env_mac/bin/python tools/v15_scalar_parity_lane.py --run-job {rel} < /dev/null > /dev/null 2>&1"
    cmd_file = f"/tmp/v15_parity_dispatch_{item['sym_side']}_{item['overrides_md5'][:8]}.sh"
    subprocess.run(SSH + [S1, f"cat > {cmd_file}"], input=remote, text=True, check=True, timeout=60)
    subprocess.run(SSH + [S1, f"cd ~/binance-sandbox && V15_PREV_BEST_SHIP=0 setsid -f /home/niels/.conda/envs/binance_env_mac/bin/python tools/v15_dispatch_job.py --symbol {sym} --push {SELF} --via {SELF} --cmd-file {cmd_file} --tag parity:{item['sym_side']} < /dev/null > /dev/null 2>&1"], check=True, timeout=60)
    (d / "dispatch.json").write_text(json.dumps({"t": time.time(), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "via": S1, "cmd_file": cmd_file}))
    return meta


def _freeze(d, symside, want_md5=None):
    """Private NPZ dir for this job: the job symbol's NPZ copied (immutable for the run), every other NPZ symlinked.

    USER 2026-10-15 "keep the 'stale' copy of the latest calc on s2 for backtest_v12": the NPZ that must be used is the
    one the PUBLISHED BOARD ran on (item npz_md5, from published_selection evidence.npz_id), kept in npz_archive — not a
    newly refreshed window. backtest_v12_engine then verifies the published result itself. This supersedes the
    2026-10-12 "never spend a run on a stale window" rule FOR THIS LANE: a board's own NPZ is by definition expired.
    Falls back to the current NPZ, flagged npz_exact=False, so a missing archive degrades the evidence instead of
    blocking the queue (3 STALE_WAIT items used to hold 419 PENDING)."""
    from tools.opt.evaluate_v12 import split_symside, resolve_tokenised
    npz_sym, _ = resolve_tokenised(split_symside(symside)[0])
    src = ROOT / "backtest_v8" / "indicators"
    fz = d / "npz"
    if fz.exists():
        shutil.rmtree(fz)
    fz.mkdir()
    for f in src.glob("*.npz"):
        if f.stem != npz_sym:
            os.symlink(f, fz / f.name)
    board, exact = _board_npz(npz_sym, want_md5)
    tgt = board if board is not None else (src / f"{npz_sym}.npz")
    if tgt.exists():
        shutil.copy2(tgt, fz / f"{npz_sym}.npz")
    return str(fz), (hashlib.md5((fz / f"{npz_sym}.npz").read_bytes()).hexdigest() if tgt.exists() else None), exact


ARCHIVE = ROOT / "data" / "parity" / "npz_archive"


def _board_npz(npz_sym, want_md5):
    """-> (path, exact). The board's own NPZ from the archive; archived on first sight while the live file still
    matches want_md5. One copy retained per symbol (older md5s pruned), so the store stays bounded at ~one NPZ per
    sym_side. (None, False) when the exact bytes are already gone — never fabricated, never silently substituted."""
    if not want_md5:
        return None, False
    try:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        keep = ARCHIVE / f"{npz_sym}__{want_md5}.npz"
        if keep.exists():
            return keep, True
        cur = ROOT / "backtest_v8" / "indicators" / f"{npz_sym}.npz"
        if cur.exists() and hashlib.md5(cur.read_bytes()).hexdigest() == want_md5:
            tmp = ARCHIVE / f".{npz_sym}.{os.getpid()}.tmp"
            shutil.copy2(cur, tmp)
            os.replace(tmp, keep)
            for old in ARCHIVE.glob(f"{npz_sym}__*.npz"):
                if old != keep:
                    old.unlink(missing_ok=True)
            return keep, True
    except Exception:
        pass
    return None, False


def run_job(rel):
    d = ROOT / rel
    meta = json.loads((d / "item.json").read_text())
    fz, md5, exact = _freeze(d, meta["sym_side"], meta.get("npz_md5"))
    from tools.opt.v12_pilot import npz_freshness
    fresh, why = npz_freshness(meta["sym_side"])
    # the board's own (expired) NPZ is the point of this lane — freshness is irrelevant once we have those bytes
    if not exact and not fresh:
        # the S1 push could not bring a fresh NPZ (e.g. 2026-10-14 Binance IP ban on S1 -> tail refresh fetched nothing):
        # never spend a run on a stale window (USER 2026-10-12) — wait, then re-dispatch
        shutil.rmtree(d / "npz", ignore_errors=True)
        (d / "stale_wait.json").write_text(json.dumps({"t": time.time(), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "why": why, "npz_md5": md5}))
        return 0
    env = dict(os.environ, TRADE_PARITY_VEC_EXACT="0", V15_PARITY_FORCE_REAL="1", PYTHONUNBUFFERED="1", TRADE_PARITY_NPZ_DIR=fz)
    meta.update({"npz_exact": bool(exact), "npz_md5_used": md5, "npz_md5_board": meta.get("npz_md5"), "npz_fresh": bool(fresh), "npz_why": None if exact else why})
    py = str(ROOT / ".venv" / "bin" / "python")
    cmd = ["nice", "-n", "19", "timeout", str(int(TIMEOUT_H * 3600)), py, "-u", "tools/v15_trade_parity.py", "--sym-side", meta["sym_side"],
           "--overrides-json", str(d / "overrides.json"), "--out-dir", str(d)]
    meta.update({"pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "timeout_h": TIMEOUT_H, "vec_exact_ingest": False,
                 "host": socket.gethostname(), "npz_frozen_dir": fz, "npz_md5_at_start": md5})
    (d / "job.json").write_text(json.dumps(meta, indent=1))
    with open(d / "job.log", "ab") as log:
        rc = subprocess.call(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    shutil.rmtree(d / "npz", ignore_errors=True)
    meta.update({"rc": rc, "ended": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    (d / "job.json").write_text(json.dumps(meta, indent=1))


STALE_RETRY_MIN = float(os.environ.get("V15_LANE_STALE_RETRY_MIN", "30"))


def _requeue_stale(items):
    """A STALE_WAIT item returns to PENDING after STALE_RETRY_MIN minutes, so the lane re-checks it when its NPZ has
    refreshed. Without this it stays STALE_WAIT forever: run_job writes stale_wait.json and the dispatch loop only
    ever picks PENDING, so nothing retries it. Returns the sym_sides put back in the queue. Never raises."""
    out = []
    for it in items:
        d = _jobdir(it)
        try:
            st, meta = _state(d)
        except Exception:
            continue
        if st != "STALE_WAIT":
            continue
        try:
            age_min = (time.time() - float(meta.get("t") or 0)) / 60.0
        except Exception:
            age_min = STALE_RETRY_MIN + 1.0
        if age_min >= STALE_RETRY_MIN:
            try:
                (d / "stale_wait.json").unlink()
                out.append(it["sym_side"])
            except Exception:
                pass
    return out


def status(items):
    rows, counts = [], {"DONE": 0, "RUNNING": 0, "DISPATCHED": 0, "STALE_WAIT": 0, "DIED": 0, "PENDING": 0}
    for it in items:
        st, meta = _state(_jobdir(it))
        counts[st] += 1
        r = {"sym_side": it["sym_side"], "tier": it["tier"], "state": st, "md5": it["overrides_md5"][:8]}
        if st == "DONE":
            rep = json.loads((_jobdir(it) / f"{it['sym_side']}_trade_parity.json").read_text())
            r.update({k: rep.get(k) for k in ("verdict", "vec_trips", "live_trips", "matched", "vec_match_rate", "live_match_rate", "exit_agree_rate")})
            r["secs"] = rep.get("secs")
        elif st in ("RUNNING", "DIED", "DISPATCHED", "STALE_WAIT"):
            r["started"] = meta.get("started") or meta.get("at")
            if meta.get("why"):
                r["why"] = meta["why"]
        rows.append(r)
    return counts, rows


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--run-job":
        sys.path.insert(0, str(ROOT))
        return run_job(sys.argv[2])
    if not _host_ok():
        sys.exit(f"[lane] refused: S2-only lane (host {socket.gethostname()})")
    q = json.loads(QUEUE.read_text()) if QUEUE.exists() else {"items": []}
    items = q.get("items") or []
    load1 = os.getloadavg()[0]
    running = sum(1 for it in items if _state(_jobdir(it))[0] in ("RUNNING", "DISPATCHED"))
    started = []
    ban_s = s1_ban_remaining_s()
    # USER 2026-10-15: a stale item must NEVER hold the queue. Measured before this change: 3 STALE_WAIT items
    # (BNO, COTIUSDT, PUMPUSDT — NPZs ending 2026-10-13/14 against a 10-15 17:15Z bar) were blocking 419 PENDING, so
    # the lane had produced 3 DONE in total. A stale NPZ is that ITEM's problem: it goes back to PENDING after
    # STALE_RETRY_MIN and is re-checked once its NPZ refreshes, while items whose NPZ is fresh keep the lane busy.
    requeued = _requeue_stale(items)
    # USER 2026-10-18: the ban hold is a CRYPTO hold. Measured today: S1 was 418-banned until 22:40Z (real — a REST probe
    # from S1 returns 418), and the global hold had parked the whole queue at RUNNING 0 with 360 PENDING, 221 of them
    # STOCK sym_sides. A stocks job reads S2's own stock NPZ store (S2 is the stocks refresh host) and never calls
    # Binance, so a Binance ban must not stop it. Crypto items still wait, for the original reason: dispatching one makes
    # S1 refresh its tail and extends the ban.
    hold = f"S1 Binance ban {ban_s:.0f}s more (crypto only)" if ban_s > 0 else ""
    for it in items:
        if running >= MAX_JOBS or os.getloadavg()[0] >= MAX_LOAD:
            break
        if ban_s > 0 and _is_crypto(it.get("sym_side", "")):
            continue
        if _state(_jobdir(it))[0] != "PENDING":
            continue  # DONE, RUNNING, or DIED (a died/timed-out job is reported, not silently retried)
        try:
            started.append(dispatch(it)["sym_side"])
        except Exception as e:
            print(f"[lane] dispatch {it['sym_side']} failed: {e}", flush=True)
            break
        running += 1
    counts, rows = status(items)
    st = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": socket.gethostname(), "load1": round(load1, 1), "max_jobs": MAX_JOBS,
          "max_load": MAX_LOAD, "timeout_h": TIMEOUT_H, "dispatch_hold": hold, "stale_requeued": requeued, "queue_built": (q.get("_meta") or {}).get("built"), "counts": counts, "started_now": started, "rows": rows}
    (LANE / "STATUS.json").write_text(json.dumps(st, indent=1, default=str))
    md = [f"# Scalar parity lane (S2) — {st['at']}", "", f"load1 {st['load1']} | jobs ≤{MAX_JOBS} | timeout {TIMEOUT_H}h | queue built {st['queue_built']} | {counts}", f"dispatch hold: {hold or 'none'}", "",
          "| tier | sym_side | state | verdict | vec trips | live trips | matched | vec match | live match | exit agree |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["state"] != "PENDING":
            md.append(f"| {r['tier']} | {r['sym_side']} | {r['state']} | {r.get('verdict', '')} | {r.get('vec_trips', '')} | {r.get('live_trips', '')} | {r.get('matched', '')} | {r.get('vec_match_rate', '')} | {r.get('live_match_rate', '')} | {r.get('exit_agree_rate', '')} |")
    (LANE / "STATUS.md").write_text("\n".join(md) + "\n")
    print(f"[lane] load1={load1:.1f} counts={counts} started={started} stale_requeued={requeued or '-'} hold={hold or '-'}")


if __name__ == "__main__":
    main()
