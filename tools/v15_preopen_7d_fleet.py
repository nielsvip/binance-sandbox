"""Fleet scheduler's persistent ownership ledger for the isolated pre-open 7D pass.

The ordinary 30D scheduler calls ``tick`` after probing its hosts. A DONE
daily-defaults stamp is mandatory. Every side has one recorded owner and one
launcher result; no 7D file is promoted by this module.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.v15_preopen_7d_select import select
from tools.v15_best_first import tradeable

NY = ZoneInfo("America/New_York")
CATS = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
PILOT_PYTHON = "/home/niels/.conda/envs/binance_env_mac/bin/python"


def _atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _market_window(now: dt.datetime) -> bool:
    local = now.astimezone(NY)
    return local.weekday() < 5 and local.time() < dt.time(9, 30)


def _defaults_proof(root: Path, now: dt.datetime) -> dict | None:
    day = now.astimezone(NY).strftime("%Y%m%d")
    try:
        published = json.loads((root / "data/published_selection.json").read_text())
        queue = select(root, now.timestamp(), published)
        meta = queue.get("_meta") or {}
        sources = queue.get("sym_sides") or {}
        if (not sources or meta.get("scope") != "fin_men_crypto"
                or meta.get("fin_book_source") != "men"
                or len(sources) != int(meta.get("n") or 0)
                or any(row.get("source_kind") != "per_sym_sql" or row.get("sql_baseline_ok") is not True
                       for row in sources.values())):
            return None
        fingerprint = hashlib.sha256(json.dumps(sorted(
            (ss, row.get("source_row_sha256")) for ss, row in sources.items()
        ), separators=(",", ":")).encode()).hexdigest()
        return {"source": "per_sym_active_sql", "source_db_sha256": meta.get("source_db_sha256"),
                "chain_sha256": fingerprint, "defaults_round": day}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _host_preference(cat: str, hosts: list[dict], available: set[str]) -> list[str]:
    venue, side = cat.split("_", 1)
    primary = [h["name"] for h in hosts if h["name"] in available
               and venue.lower() in h.get("venues", []) and side in h.get("sides", [])]
    secondary = [h["name"] for h in hosts if h["name"] in available
                 and venue.lower() in h.get("venues", []) and h["name"] not in primary]
    overflow = [h["name"] for h in hosts if h["name"] in available
                and h["name"] not in primary + secondary]
    return primary + secondary + overflow


def allocate(queue: dict, hosts: list[dict], available: set[str], existing: dict | None = None) -> dict:
    """Deterministic preferred ownership, with room for host removal/addition."""
    placements = dict(existing or {})
    counts = {h["name"]: 0 for h in hosts if h["name"] in available}
    for ss, row in placements.items():
        if row.get("host") in counts and row.get("status") not in ("failed", "lost"):
            counts[row["host"]] += 1
    for ss, source in sorted((queue.get("sym_sides") or {}).items()):
        if ss in placements and placements[ss].get("status") in ("running", "valid", "failed"):
            continue
        if ss in placements and placements[ss].get("host") in available:
            continue
        cat = source.get("cat_side")
        eligible = _host_preference(cat, hosts, available) if cat in CATS else []
        if not eligible:
            placements[ss] = {"host": None, "status": "pending", "why": "no reachable host"}
            continue
        # Prefer the normal category owner; spread multiple sides across equally
        # preferred boxes before borrowing another category's worker.
        venue, side = cat.split("_", 1)
        primary = [h["name"] for h in hosts if h["name"] in eligible
                   and venue.lower() in h.get("venues", []) and side in h.get("sides", [])]
        same_venue = [h["name"] for h in hosts if h["name"] in eligible
                      and venue.lower() in h.get("venues", [])]
        preferred = primary or same_venue or eligible
        host = min(preferred, key=lambda name: (counts[name], name))
        counts[host] += 1
        placements[ss] = {"host": host, "status": "pending", "attempts": 0}
    return placements


def _remote(host: dict, command: str, timeout: int = 20) -> subprocess.CompletedProcess:
    targets = list(dict.fromkeys([host.get("_via")] + host.get("ssh", [])))
    last = None
    for target in filter(None, targets):
        try:
            result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                     "-o", "ControlMaster=no", "-o", "ControlPath=none", target,
                                     command], capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                host["_via"] = target
                return result
            last = result
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError(f"{host['name']} command failed: {(last.stderr if last else 'unreachable')[:180]}")


def _sync_queue(host: dict, queue_path: Path, remote_root: str) -> None:
    target = host.get("_via") or host["ssh"][0]
    dest = f"{remote_root}/data/preopen_7d/fleet/{queue_path.parent.name}/{queue_path.name}"
    _remote(host, f"mkdir -p {shlex.quote(remote_root + '/data/preopen_7d/fleet/' + queue_path.parent.name)}")
    result = subprocess.run(["rsync", "-az", "--timeout=25", "-e",
                             "ssh -o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=no -o ControlPath=none",
                             str(queue_path), f"{target}:{dest}"], capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError(f"{host['name']} queue transfer failed: {result.stderr[:180]}")


def _launch(host: dict, ss: str, queue_path: Path, day: str, root: str) -> None:
    remote_queue = f"{root}/data/preopen_7d/fleet/{day}/{queue_path.name}"
    result_dir = f"{root}/data/preopen_7d/fleet/{day}/results"
    result_path = f"{result_dir}/{ss}/launcher.json"
    stderr_path = f"{result_dir}/{ss}/launcher.stderr"
    cmd = (f"cd {shlex.quote(root)} && mkdir -p {shlex.quote(result_dir + '/' + ss)} && "
           f"test ! -s {shlex.quote(result_path)} && "
           f"V15_7D_PILOT_FILE=v15_pilot.py V15_7D_ALREADY_PAUSED=1 "
           f"nohup {PILOT_PYTHON} -u tools/v15_preopen_7d_launch.py "
           f"--queue {shlex.quote(remote_queue)} --sym-side {shlex.quote(ss)} "
           f"--result-dir {shlex.quote(result_dir)} --workers 7 --timeout-sec 2400 "
           f"--python {PILOT_PYTHON} > {shlex.quote(result_path)} 2> {shlex.quote(stderr_path)} < /dev/null &")
    _remote(host, cmd, timeout=15)


def _result(host: dict, ss: str, day: str, root: str) -> dict | None:
    path = f"{root}/data/preopen_7d/fleet/{day}/results/{ss}/launcher.json"
    try:
        raw = _remote(host, f"test -s {shlex.quote(path)} && tail -n 1 {shlex.quote(path)}", timeout=8).stdout
        row = json.loads(raw)
        return row if row.get("sym_side") == ss and "valid" in row else None
    except (RuntimeError, ValueError, TypeError):
        return None


def _launch_replay(host: dict, ss: str, day: str, root: str, minute: int = 15) -> None:
    result_dir = f"{root}/data/preopen_7d/fleet/{day}/results"
    queue = f"{root}/data/preopen_7d/fleet/{day}/queue.json"
    log = f"{result_dir}/{ss}/replay.log"
    cmd = (f"cd {shlex.quote(root)} && rm -f {shlex.quote(result_dir + '/' + ss + '/replay.json')} && nohup {PILOT_PYTHON} -u "
           f"tools/v15_preopen_7d_replay.py --queue {shlex.quote(queue)} "
           f"--result-dir {shlex.quote(result_dir)} --sym-side {shlex.quote(ss)} --bar-minute {minute} "
           f"> {shlex.quote(log)} 2>&1 < /dev/null &")
    _remote(host, cmd, timeout=15)


def _replay_result(host: dict, ss: str, day: str, root: str) -> dict | None:
    path = f"{root}/data/preopen_7d/fleet/{day}/results/{ss}/replay.json"
    try:
        row = json.loads(_remote(host, f"test -s {shlex.quote(path)} && cat {shlex.quote(path)}", timeout=8).stdout)
        return row if row.get("sym_side") == ss and "valid" in row else None
    except (RuntimeError, ValueError, TypeError):
        return None


def _ensure_collect(root: Path, folder: Path, day: str, state: dict) -> None:
    if not any(row.get("status") == "valid" and row.get("replay_status") == "valid"
               for row in state.get("placements", {}).values()):
        state["collection_status"] = "no_valid_replay"
        _atomic(folder / "state.json", state)
        return
    if (folder / "collection.json").exists() or state.get("collector_attempts", 0) >= 4:
        return
    if state.get("collector_started_utc"):
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(state["collector_started_utc"])).total_seconds()
            if age < 300:
                return
        except (ValueError, TypeError):
            pass
    log = folder / "collector.log"
    with log.open("ab") as output:
        subprocess.Popen(["flock", "-n", f"/tmp/v15_preopen_7d_collect_{day}.lock",
                          PILOT_PYTHON, "-u", str(root / "tools/v15_preopen_7d_collect.py"),
                          "--day", day, "--hosts", str(folder / "hosts.json")],
                         cwd=root, stdout=output, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    state["collector_started_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state["collector_attempts"] = state.get("collector_attempts", 0) + 1
    _atomic(folder / "state.json", state)


def tick(root: Path, now: dt.datetime, hosts: list[dict], stats: dict,
         dry_run: bool = False, drained: set[str] | None = None) -> dict | None:
    """Run the FIN/MEN SQL-based 7D queue during the morning optimization window."""
    if not _market_window(now):
        local = now.astimezone(NY)
        if local.weekday() < 5 and local.time() >= dt.time(9, 30):
            folder = root / "data/preopen_7d/fleet" / local.strftime("%Y%m%d")
            state_path = folder / "state.json"
            if state_path.exists() and not dry_run:
                state = json.loads(state_path.read_text())
                if state.get("phase") in ("running", "await_replay"):
                    for row in state.get("placements", {}).values():
                        if row.get("status") in ("pending", "running"):
                            row.update({"status": "failed", "why": "market-open deadline"})
                        if row.get("status") == "valid" and row.get("replay_status") not in ("valid", "failed"):
                            row.update({"replay_status": "failed", "why": "market-open replay deadline"})
                    state["phase"] = "complete"
                    state["updated_utc"] = now.isoformat()
                    _atomic(state_path, state)
                for host in hosts:
                    if stats.get(host["name"]):
                        try:
                            _remote(host, "pkill -TERM -f '^/home/niels/.conda/envs/binance_env_mac/bin/python( -u)? .*/v15_pilot_7d[.]py --sym-side' || true")
                            _remote(host, "rm -f /tmp/v15_preopen_7d.pause")
                        except RuntimeError:
                            pass
                if state.get("phase") == "complete":
                    _ensure_collect(root, folder, local.strftime("%Y%m%d"), state)
        return None
    proof = _defaults_proof(root, now)
    if proof is None:
        return {"phase": "waiting_for_verified_per_sym_sql", "suppress_30d": False}
    day = proof["defaults_round"]
    folder = root / "data/preopen_7d/fleet" / day
    queue_path = folder / "queue.json"
    state_path = folder / "state.json"
    if not state_path.exists():
        published = json.loads((root / "data/published_selection.json").read_text())
        queue = select(root, now.timestamp(), published)
        queue["_meta"].update(proof)
        queue["_meta"]["require_replay"] = True
        queue["_meta"]["bar_minute"] = 15
        reachable = {h["name"] for h in hosts if stats.get(h["name"])}
        available = reachable - set(drained or ())
        placements = allocate(queue, hosts, available)
        state = {"day": day, "created_utc": now.isoformat(), "phase": "running",
                 "defaults": proof, "placements": placements, "queue_n": len(placements),
                 "bar_policy": "09:15 ET if optimization completes by 09:00; otherwise 09:00 ET for completed sides",
                 "bar_minute": 15}
        if not dry_run:
            _atomic(queue_path, queue)
            _atomic(folder / "hosts.json", {"hosts": hosts})
            _atomic(state_path, state)
    else:
        queue = json.loads(queue_path.read_text())
        state = json.loads(state_path.read_text())
        if state.get("defaults", {}).get("chain_sha256") != proof["chain_sha256"]:
            return {"phase": "defaults_changed_after_queue_freeze", "suppress_30d": True}
        # TRB's side books can populate after the first premarket tick. Add
        # newly eligible sides until 09:00 ET without changing an already
        # launched side's verified 30D source or ownership.
        if state.get("phase") == "running" and now.astimezone(NY).time() < dt.time(9, 0):
            try:
                current = select(root, now.timestamp(), json.loads((root / "data/published_selection.json").read_text()))
                added = set(current["sym_sides"]) - set(queue["sym_sides"])
                for ss in sorted(added):
                    queue["sym_sides"][ss] = current["sym_sides"][ss]
                    for account, sides in current["accounts"].items():
                        if ss in sides:
                            queue["accounts"][account].append(ss)
                if added:
                    for sides in queue["accounts"].values():
                        sides.sort()
                    queue["_meta"]["n"] = len(queue["sym_sides"])
                    state["queue_n"] = len(queue["sym_sides"])
                    if not dry_run:
                        _atomic(queue_path, queue)
                    state.setdefault("added_late", []).extend(sorted(added))
            except (OSError, ValueError, TypeError) as exc:
                state["universe_refresh_error"] = str(exc)
        reachable = {h["name"] for h in hosts if stats.get(h["name"])}
        available = reachable - set(drained or ())
        state["placements"] = allocate(queue, hosts, available, state.get("placements"))
        if not dry_run:
            _atomic(folder / "hosts.json", {"hosts": hosts})
    eligible_now = tradeable(root)
    for ss, row in state["placements"].items():
        if ss in eligible_now or row.get("status") == "failed":
            continue
        was_running = row.get("status") == "running"
        row.update({"status": "failed", "replay_status": "failed", "why": "side left current tradeable book"})
        if was_running and not dry_run and row.get("host") in reachable:
            try:
                _remote(next(h for h in hosts if h["name"] == row["host"]),
                        "pkill -TERM -f " + shlex.quote("v15_pilot_7d.py --sym-side " + ss) + " || true")
            except (RuntimeError, StopIteration):
                pass
    if dry_run:
        return {"phase": "dry_run", "queue_n": state["queue_n"],
                "by_host": {h["name"]: sum(x.get("host") == h["name"] for x in state["placements"].values()) for h in hosts},
                "suppress_30d": False}
    local_time = now.astimezone(NY).time()
    if state.get("phase") == "complete":
        _ensure_collect(root, folder, day, state)
        return {"phase": "complete", "queue_n": state["queue_n"], "suppress_30d": False}
    # The presence marker stops the worker's own 30D top-up crons. It is
    # removed only after every side is terminal or the market opens.
    for host in hosts:
        if host["name"] not in reachable:
            continue
        try:
            _remote(host, "touch /tmp/v15_preopen_7d.pause")
        except RuntimeError:
            pass
    by_name = {h["name"]: h for h in hosts}
    for ss, row in state["placements"].items():
        if row.get("status") != "running" or row.get("host") not in reachable:
            continue
        host = by_name[row["host"]]
        result = _result(host, ss, day, "/home/niels/binance-sandbox")
        if result is not None:
            row.update({"status": "valid" if result.get("valid") else "failed",
                        "result": result, "finished_utc": now.isoformat()})
        elif ss not in ((stats.get(row["host"]) or {}).get("running") or {}):
            try:
                started = dt.datetime.fromisoformat(row["started_utc"])
                age = (now - started).total_seconds()
            except (KeyError, ValueError, TypeError):
                age = 0
            if age > 180:
                row.update({"status": "pending" if row.get("attempts", 0) < 2 else "failed",
                            "why": "launcher vanished without a result"})
    if state.get("phase") == "running" and local_time >= dt.time(9, 0):
        unfinished = [ss for ss, row in state["placements"].items()
                      if row.get("status") in ("pending", "running")]
        if unfinished:
            state["bar_minute"] = 0
            queue["_meta"]["bar_minute"] = 0
            _atomic(queue_path, queue)
            for ss in unfinished:
                state["placements"][ss].update({"status": "failed", "why": "09:00 fallback cutoff"})
            state["phase"] = "await_replay"
            state["fallback_failed"] = unfinished
            for host in hosts:
                if host["name"] in reachable:
                    try:
                        _remote(host, "pkill -TERM -f '^/home/niels/.conda/envs/binance_env_mac/bin/python( -u)? .*/v15_pilot_7d[.]py --sym-side' || true")
                    except RuntimeError:
                        pass
    for host in hosts:
        name = host["name"]
        if name not in available:
            continue
        if state.get("phase") != "running":
            continue
        active_sides = set((stats.get(name) or {}).get("running") or {})
        active_sides.update(ss for ss, x in state["placements"].items()
                            if x.get("host") == name and x.get("status") == "running")
        active = len(active_sides)
        free = max(0, 4 - active)
        if not free:
            continue
        pending = [ss for ss, row in state["placements"].items()
                   if row.get("host") == name and row.get("status") == "pending"]
        if not pending:
            continue
        try:
            _sync_queue(host, queue_path, "/home/niels/binance-sandbox")
        except RuntimeError as exc:
            state.setdefault("host_errors", {})[name] = str(exc)
            continue
        for ss in pending[:min(free, 2)]:
            try:
                _launch(host, ss, queue_path, day, "/home/niels/binance-sandbox")
                state["placements"][ss].update({"status": "running", "started_utc": now.isoformat(),
                                                  "attempts": state["placements"][ss].get("attempts", 0) + 1})
            except RuntimeError as exc:
                state.setdefault("host_errors", {})[name] = str(exc)
                break
    if all(row.get("status") in ("valid", "failed") for row in state["placements"].values()):
        state["phase"] = "await_replay" if any(row.get("status") == "valid" for row in state["placements"].values()) else "complete"
    if state["phase"] == "await_replay" and local_time >= dt.time(9, state.get("bar_minute", 15)):
        replay_slots = {h["name"]: sum(x.get("host") == h["name"] and x.get("replay_status") == "running"
                                        for x in state["placements"].values()) for h in hosts}
        for ss, row in state["placements"].items():
            if row.get("status") != "valid" or row.get("host") not in reachable:
                continue
            host = by_name[row["host"]]
            if row.get("replay_status") == "running":
                replay = _replay_result(host, ss, day, "/home/niels/binance-sandbox")
                if replay is not None:
                    attempts = row.get("replay_attempts", 0)
                    row.update({"replay_status": "valid" if replay.get("valid") else ("failed" if attempts >= 3 else None),
                                "replay": replay})
                    replay_slots[row["host"]] -= 1
            if row.get("replay_status") is None and replay_slots[row["host"]] < 4:
                try:
                    _launch_replay(host, ss, day, "/home/niels/binance-sandbox", state.get("bar_minute", 15))
                    row["replay_status"] = "running"
                    row["replay_attempts"] = row.get("replay_attempts", 0) + 1
                    replay_slots[row["host"]] += 1
                except RuntimeError as exc:
                    state.setdefault("host_errors", {})[row["host"]] = str(exc)
        if all(row.get("status") != "valid" or row.get("replay_status") in ("valid", "failed")
               for row in state["placements"].values()):
            state["phase"] = "complete"
    if state["phase"] == "complete":
        for host in hosts:
            if host["name"] in available:
                try:
                    _remote(host, "rm -f /tmp/v15_preopen_7d.pause")
                except RuntimeError:
                    pass
    state["updated_utc"] = now.isoformat()
    _atomic(state_path, state)
    if state["phase"] == "complete":
        _ensure_collect(root, folder, day, state)
    counts = {status: sum(x.get("status") == status for x in state["placements"].values())
              for status in ("pending", "running", "valid", "failed")}
    return {"phase": state["phase"], "queue_n": state["queue_n"], "counts": counts,
            "state": str(state_path), "suppress_30d": state["phase"] == "running"}
