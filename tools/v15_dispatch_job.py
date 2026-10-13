#!/usr/bin/env python3
"""Detached dispatch job (USER 2026-10-13: "the npz goes WITH THE PUSH of the sym_side, calculated on s1 before dispatching").
The fleet scheduler tick has a hard 110 s cron timeout; a synchronous NPZ rebuild (minutes per stock symbol once the US
market is open) killed every tick from 13:44Z and the fleet launched nothing. The tick now enqueues this job instead:
  1. NPZ rebuild + md5-verified push on S1 (per-symbol single flight + V15_NPZ_PREP_SLOTS concurrent builds, default 4),
  2. ssh launch of the pilot(s) on the target host — ONLY after step 1 (JIT law: NPZ fresh right before load).
ok=False from the prep tool = sym_side must not launch (stale tail it could not heal) -> skip, the scheduler retries later.
Prep tool crash / lock wait timeout = launch with what the host has (USER 2026-10-13: "work with what you have").
Every outcome is one JSON line in /tmp/v15_dispatch.log."""
import argparse, fcntl, json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = pathlib.Path(os.environ.get("V15_DISPATCH_LOG", "/tmp/v15_dispatch.log"))


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _acquire_slot(n_slots, wait_s):
    """Counting semaphore over flock files: at most n_slots NPZ builds at once (a stock build is ~20 single-core
    minutes; the old single global lock queued every dispatch of the fleet behind one build -> idle fleet)."""
    deadline = time.time() + wait_s
    while True:
        for i in range(max(1, n_slots)):
            f = open(f"/tmp/v15_npz_prep.slot{i}.lock", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f
            except OSError:
                f.close()
        if time.time() >= deadline:
            return None
        time.sleep(5)


def prep(symbol, push):
    """Per-symbol single flight (same symbol never built twice at once) + fleet-wide build slots."""
    t0 = time.time()
    sym_lock = open(f"/tmp/v15_npz_prep.{symbol.upper()}.lock", "w")
    wait_s = float(os.environ.get("V15_NPZ_LOCK_WAIT_S", "1500"))
    try:
        fcntl.flock(sym_lock, fcntl.LOCK_EX)
    except OSError:
        pass
    slot = _acquire_slot(int(os.environ.get("V15_NPZ_PREP_SLOTS", "4")), wait_s)
    if slot is None:
        return {"ok": None, "reason": "slot-wait-timeout", "prep_s": round(time.time() - t0, 1)}
    cmd = [sys.executable, str(ROOT / "tools" / "v15_npz_prep.py"), "--symbol", symbol]
    if push:
        cmd += ["--push", push]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=float(os.environ.get("V15_NPZ_PREP_TIMEOUT_S", "2400")), cwd=str(ROOT))
        lines = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
        res = json.loads(lines[-1]) if lines else {"ok": None, "reason": f"no-output rc={r.returncode} {(r.stderr or '')[-160:]}"}
    except subprocess.TimeoutExpired:
        res = {"ok": None, "reason": "prep-timeout"}
    except (OSError, ValueError) as e:
        res = {"ok": None, "reason": f"prep-error {e}"[:120]}
    finally:
        slot.close()
        sym_lock.close()
    res["prep_s"] = round(time.time() - t0, 1)
    return res


def _dedup_lock(tag):
    """One live job per dispatch tag (a dead tick that never saved dispatch_q re-dispatched the same tag every 2 min)."""
    f = open("/tmp/v15_dispatch_job." + "".join(c if c.isalnum() else "_" for c in tag) + ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--push", default="", help="target ssh host for the md5-verified NPZ push ('' = local host)")
    ap.add_argument("--via", required=True, help="ssh target that runs the launch command")
    ap.add_argument("--cmd-file", required=True, help="file holding the remote launch shell command (deleted after use)")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    cmd_path = pathlib.Path(a.cmd_file)
    remote = cmd_path.read_text()
    _guard = _dedup_lock(a.tag or a.symbol)
    if _guard is None:
        log({"event": "dup-skip", "symbol": a.symbol, "tag": a.tag})
        cmd_path.unlink(missing_ok=True)
        return 0
    res = prep(a.symbol, a.push)
    if res.get("ok") is False:
        log({"event": "skip", "symbol": a.symbol, "tag": a.tag, "prep": res})
        cmd_path.unlink(missing_ok=True)
        return 3
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", a.via, remote], timeout=60)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = "ssh-timeout (job may still have started; pgrep dedups)"
    log({"event": "launched", "symbol": a.symbol, "tag": a.tag, "via": a.via, "ssh_rc": rc, "prep": res})
    cmd_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
