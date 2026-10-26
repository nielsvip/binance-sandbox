#!/usr/bin/env python3
"""Mac handoff of a complete fleet 7D stage to FIN/TRC.

Cron may call this repeatedly. It does nothing until the S1 collector has
validated results and distributed vector sets. No 30D fallback is installed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NY = ZoneInfo("America/New_York")
S1 = os.environ.get("V15_7D_STAGE_HOST", "s1-pub")


def _atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
    os.replace(tmp, path)


def _fetch(remote: str, dest: Path, timeout: int = 45):
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["rsync", "-az", "--timeout=30", remote, str(dest) + "/"],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"7D stage pull failed: {r.stderr[-180:]}")


def _restart_fin(root: Path):
    result = subprocess.run(["pgrep", "-f", "^/bin/bash run_with_watchdog.sh ez_manage.py --account fin$"],
                            capture_output=True, text=True)
    old = [int(x) for x in result.stdout.split() if x.isdigit()]
    for pid in old:
        os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 15
    while any(_pid_alive(pid) for pid in old) and time.time() < deadline:
        time.sleep(0.25)
    if any(_pid_alive(pid) for pid in old):
        raise RuntimeError("FIN watchdog did not exit for 7D handoff")
    log = Path.home() / "logs/ez_manage_fin_7d_activation.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as output:
        subprocess.Popen(["/bin/bash", "run_with_watchdog.sh", "ez_manage.py", "--account", "fin"],
                         cwd=root, stdin=subprocess.DEVNULL, stdout=output,
                         stderr=subprocess.STDOUT, start_new_session=True)


def _pid_alive(pid: int):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _restart_trc(db: Path):
    label = "com.binance.trader.trc"
    path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    doc = plistlib.loads(path.read_bytes())
    env = dict(doc.get("EnvironmentVariables") or {})
    env.update({"VEC_LIVE_SCOPE": "7d", "PER_SYM_STORE_DB": str(db)})
    doc["EnvironmentVariables"] = env
    backup = ROOT / "backups/preopen_7d_activation_20261026"
    backup.mkdir(parents=True, exist_ok=True)
    if not (backup / path.name).exists():
        shutil.copy2(path, backup / path.name)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(plistlib.dumps(doc))
    os.replace(tmp, path)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"TRC 7D launch failed: {r.stderr[-180:]}")


def activate(now: dt.datetime | None = None, root: Path = ROOT):
    from tools.v15_preopen_7d_runtime import assert_ready, assert_vector_stage_ready
    now = now or dt.datetime.now(dt.timezone.utc)
    day = now.astimezone(NY).strftime("%Y%m%d")
    active = root / "data/preopen_7d/active"
    current = active / "current.json"
    remote_base = f"{S1}:binance-sandbox/data/preopen_7d/fleet/{day}/"
    try:
        _fetch(remote_base + "collection.json", active / "checks", timeout=20)
    except RuntimeError:
        return {"status": "waiting_for_collection", "day": day}
    collection = json.loads((active / "checks/collection.json").read_text())
    if collection.get("day") != day or collection.get("stage_only") is not True or "s2" not in collection.get("distributed", []):
        raise RuntimeError("7D collection has no verified S2 vector distribution")
    lane = active / day
    _fetch(remote_base + "stage/", lane, timeout=300)
    registry = assert_vector_stage_ready(lane)
    accounts = sorted({row.get("account") for ss, row in registry.items()
                       if isinstance(row, dict) and row.get("mode") == "live" and row.get("account") in ("fin", "trc")})
    if not accounts:
        raise RuntimeError("7D stage contains no FIN/TRC sides")
    db = lane / "per_sym_store_7d.db"
    if not db.is_file():
        raise RuntimeError("7D stage has no settings database")
    lane_live = root / "data/vec_live_7d"
    lane_live.mkdir(parents=True, exist_ok=True)
    for name in ("vec_driven_sets.json", "vec_driven.json", "validation.json"):
        tmp = lane_live / (name + ".tmp")
        shutil.copy2(lane / name, tmp)
        os.replace(tmp, lane_live / name)
    previous = json.loads(current.read_text()) if current.exists() else {}
    marker = {"day": day, "db": str(db), "accounts": accounts,
              "sets_sha256": json.loads((lane / "validation.json").read_text())["sets_sha256"],
              "activated_utc": now.isoformat()}
    for account in accounts:
        old_scope = os.environ.get("VEC_LIVE_SCOPE")
        old_db = os.environ.get("PER_SYM_STORE_DB")
        os.environ.update(VEC_LIVE_SCOPE="7d", PER_SYM_STORE_DB=str(db))
        try:
            assert_ready(account, root)
        finally:
            if old_scope is None: os.environ.pop("VEC_LIVE_SCOPE", None)
            else: os.environ["VEC_LIVE_SCOPE"] = old_scope
            if old_db is None: os.environ.pop("PER_SYM_STORE_DB", None)
            else: os.environ["PER_SYM_STORE_DB"] = old_db
    if previous.get("day") == day and previous.get("sets_sha256") == marker["sets_sha256"]:
        return {"status": "already_active", **marker}
    _atomic(current, marker)
    try:
        if "fin" in accounts:
            _restart_fin(root)
        if "trc" in accounts:
            _restart_trc(db)
    except Exception:
        if previous:
            _atomic(current, previous)
        else:
            current.unlink(missing_ok=True)
        raise
    return {"status": "activated", **marker}


if __name__ == "__main__":
    print(json.dumps(activate()))
