#!/usr/bin/env python3
"""v15_pusher_worker — infinite gain-pusher unit consumer (USER 2026-10-09: 24/7/365, never idle).

Loops forever: claim oldest inbox/*.json (atomic local rename to running/) ->
run v15_gain_pusher in anchor mode (subprocess, isolated, 1800s timeout) ->
write done/{round:04d}_{symside}_report.json + done/{round:04d}_{symside}_improved.json.
Empty inbox -> sleep 20s and retry (NEVER exits; the supervisor owns the count).

Started by tools/v15_pusher_supervisor.py (niced). One worker ~= 100% of 1 core.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UNIT_TIMEOUT = 1800
IDLE_SLEEP = 20


def log(msg):
    print(f"[pushwork {os.getpid()}] {msg}", flush=True)


def claim_one(inbox, running):
    cands = sorted(inbox.glob("*.json"))
    for c in cands:
        try:
            dest = running / c.name
            os.rename(c, dest)
            return dest
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def run_unit(unit_path, base):
    unit = json.loads(unit_path.read_text())
    rnd, ss = int(unit["round"]), str(unit["symside"])
    anchor = base / "anchors" / f"{ss}.json"
    if not anchor.exists():
        return {"symside": ss, "round": rnd, "error": "anchor missing, requeue"}, None
    tmp = base / "running" / f"tmp_{rnd:04d}_{ss}_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    rep_tmp = tmp / "report.json"
    cmd = [sys.executable, "-u", str(ROOT / "tools" / "v15_gain_pusher.py"),
           "--sym", ss, "--anchor-json", str(anchor), "--no-rerun-sheet",
           "--no-lock", "--round", str(rnd), "--out", str(tmp), "--report-out", str(rep_tmp)]
    env = dict(os.environ)
    env["PER_SYM_STORE_SQLITE_DISABLED"] = "1"
    env["V15_PUSHER_REGISTRY"] = str(base / "registry" / "PRIORITY_SWITCHES.json")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=UNIT_TIMEOUT)
        log(f"{ss} r{rnd} rc={p.returncode} secs={time.time() - t0:.0f}")
        if p.returncode != 0:
            (base / "logs" / f"fail_{rnd:04d}_{ss}.log").write_text((p.stdout or "")[-8000:] + "\n--- STDERR ---\n" + (p.stderr or "")[-8000:])
    except subprocess.TimeoutExpired:
        log(f"{ss} r{rnd} TIMEOUT {UNIT_TIMEOUT}s")
        shutil.rmtree(tmp, ignore_errors=True)
        return {"symside": ss, "round": rnd, "error": f"timeout>{UNIT_TIMEOUT}s"}, None
    try:
        rep = json.loads(rep_tmp.read_text())
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"symside": ss, "round": rnd, "error": f"no report: {e}"[:160]}, None
    try:
        rep["registry_version"] = json.loads((base / "registry" / "PRIORITY_SWITCHES.json").read_text()).get("_version", 0)
    except Exception:
        rep["registry_version"] = -1
    rep["anchor_rev"] = unit.get("anchor_rev", 0)
    improved_src = tmp / f"{ss}_improved.json"
    improved = None
    if improved_src.exists():
        improved = json.loads(improved_src.read_text())
    else:
        improved = json.loads(anchor.read_text())
        rep["stagnant"] = True
    shutil.rmtree(tmp, ignore_errors=True)
    return rep, improved


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/v15_pusher"))
    a = ap.parse_args()
    base = pathlib.Path(a.root)
    inbox, running, done = base / "inbox", base / "running", base / "done"
    for d in (inbox, running, done, base / "logs", base / "anchors", base / "registry"):
        d.mkdir(parents=True, exist_ok=True)
    log(f"up, base={base}")
    while True:
        unit_path = claim_one(inbox, running)
        if unit_path is None:
            time.sleep(IDLE_SLEEP)
            continue
        try:
            rep, improved = run_unit(unit_path, base)
        except Exception as e:
            rep, improved = {"symside": unit_path.stem, "error": f"worker raised {type(e).__name__}: {e}"[:200]}, None
        try:
            rnd, ss = int(rep.get("round", 0)), str(rep.get("symside", unit_path.stem))
            if rep.get("error"):
                json.dump(rep, open(done / f"{rnd:04d}_{ss}_ERROR.json", "w"), indent=1, default=str)
            else:
                json.dump(rep, open(done / f"{rnd:04d}_{ss}_report.json", "w"), indent=1, default=str)
                if improved is not None:
                    json.dump(improved, open(done / f"{rnd:04d}_{ss}_improved.json", "w"), indent=1, default=str)
            log(f"done {ss} r{rnd} improved_by={rep.get('improved_by')} err={rep.get('error')}")
        except Exception as e:
            log(f"write failed: {e}")
        try:
            unit_path.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
