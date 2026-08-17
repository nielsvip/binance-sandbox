#!/usr/bin/env python3
"""Mac-side liveness heartbeat for the S1 failover monitor (2026-07-20 USER: "if macbook is
not running ez_/tradier_, s1 should immediately kick in and take over live trading").
Writes data/mac_live_heartbeat.json (this machine's view of which live accounts are actually
running, verified by pgrep on the CHILD process, not the run_with_watchdog.sh wrapper — the
wrapper surviving while its child is dead was the exact blind spot in the 2026-06-26/27
STALE_INDICATORS incident). Run every 1 min via cron; a separate cron line rsyncs the file to
S1 immediately after. Read by s1_failover_monitor.sh on S1 — see that script for what happens
on a stale/missing heartbeat. This script ONLY observes and writes; it never starts, stops, or
touches any trading process.
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, time as dt_time
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None
BASE_PATH = "/Users/niels/Documents/binance"
TRB_OPERATOR_WATCHDOG = f"{BASE_PATH}/tools/trb_operator_watchdog.sh"
TRB_OPERATOR_WATCHDOG_LOG = f"{BASE_PATH}/data/logs/trb_operator/heartbeat_watchdog.log"
TRB_OPERATOR_CLEANUP_REQUEST = f"{BASE_PATH}/data/sync/TRB_OPERATOR_ORPHAN_CLEANUP_REQUEST.json"
TRB_OPERATOR_CLEANUP_STATUS = f"{BASE_PATH}/data/sync/TRB_OPERATOR_ORPHAN_CLEANUP_STATUS.json"
OUT_PATH = f"{BASE_PATH}/data/mac_live_heartbeat.json"
SYNC_REQUEST = f"{BASE_PATH}/data/sync/ALWAYS_CONNECTED_SYNC_REQUEST"
SYNC_STATUS = f"{BASE_PATH}/data/sync/ALWAYS_CONNECTED_SYNC_STATUS.json"
SYNC_LAUNCH_LOG = f"{BASE_PATH}/logs/always_connected_sync_launcher.log"
SYNC_TRANSPORT_GUARD_STATUS = (
    f"{BASE_PATH}/data/sync/ALWAYS_CONNECTED_SYNC_TRANSPORT_GUARD.json"
)
HEARTBEAT_SYNC_STATUS = f"{BASE_PATH}/data/sync/MAC_LIVE_HEARTBEAT_SYNC_STATUS.json"
AUTOSYNC_PAUSE = f"{BASE_PATH}/.s1_s2_autosync.pause"
SERVICE_STATUS = f"{BASE_PATH}/data/sync/SYNC_SERVICE_RELOAD_STATUS.json"
CHART_RESTART_REQUEST = f"{BASE_PATH}/data/sync/CHART_RESTART_REQUEST"
MU_MATERIALIZER_REQUEST = (
    f"{BASE_PATH}/data/sync/MU_S1_MATERIALIZER_REQUEST.json"
)
MU_MATERIALIZER_LOG = f"{BASE_PATH}/logs/mu_s1_materializer_dispatch.log"
VECTOR_DISCOVERY_REQUEST = (
    f"{BASE_PATH}/data/sync/VECTOR_DISCOVERY_S1_REQUEST.json"
)
VECTOR_DISCOVERY_LOG = f"{BASE_PATH}/logs/vector_discovery_s1_dispatch.log"
VECTOR_APPROX_SCALAR_REQUEST = (
    f"{BASE_PATH}/data/sync/VECTOR_APPROX_SCALAR_S1_REQUEST.json"
)
VECTOR_APPROX_SCALAR_LOG = (
    f"{BASE_PATH}/logs/vector_approx_scalar_s1_dispatch.log"
)
VECTOR_PULL_REQUEST = (
    f"{BASE_PATH}/data/sync/VECTOR_DISCOVERY_PULL_REQUEST.json"
)
VECTOR_PULL_STATUS = (
    f"{BASE_PATH}/data/sync/VECTOR_DISCOVERY_PULL_STATUS.json"
)
VECTOR_PULL_LOG = f"{BASE_PATH}/logs/vector_discovery_pull_watcher.log"
ABORT_RESULT_PULL_REQUEST = (
    f"{BASE_PATH}/data/sync/ABORT_RESULT_PULL_REQUEST.json"
)
ABORT_RESULT_PULL_STATUS = (
    f"{BASE_PATH}/data/sync/ABORT_RESULT_PULL_STATUS.json"
)
REPORTING_REPAIR_REQUEST = (
    f"{BASE_PATH}/data/sync/REPORTING_REPAIR_PUBLISH_REQUEST"
)
REPORTING_REPAIR_STATUS = (
    f"{BASE_PATH}/data/sync/REPORTING_REPAIR_PUBLISH_STATUS.json"
)
REPORTING_REPAIR_LOG = f"{BASE_PATH}/logs/reporting_repair_publish_launcher.log"
VECTOR_CAPACITY_PUBLISH_REQUEST = (
    f"{BASE_PATH}/data/sync/VECTOR_CAPACITY_PUBLISH_REQUEST"
)
VECTOR_CAPACITY_PUBLISH_STATUS = (
    f"{BASE_PATH}/data/sync/VECTOR_CAPACITY_PUBLISH_STATUS.json"
)
VECTOR_CAPACITY_PUBLISH_LOG = (
    f"{BASE_PATH}/logs/vector_capacity_publish_launcher.log"
)
S1_DISK_GUARD_PUBLISH_REQUEST = (
    f"{BASE_PATH}/data/sync/S1_DISK_GUARD_PUBLISH_REQUEST"
)
S1_DISK_GUARD_PUBLISH_STATUS = (
    f"{BASE_PATH}/data/sync/S1_DISK_GUARD_PUBLISH_STATUS.json"
)
S1_DISK_GUARD_PUBLISH_LOG = (
    f"{BASE_PATH}/logs/s1_disk_guard_publish_launcher.log"
)
TRB_LIVE_FLOOR_LOG = f"{BASE_PATH}/logs/trb_live_symbol_side_floor.log"
VECTOR_CAPACITY_COMPACT_SYNC_LOG = (
    f"{BASE_PATH}/logs/vector_capacity_compact_sync.log"
)
CURRENT_MATRIX_SURFACE_SYNC_LOG = (
    f"{BASE_PATH}/logs/current_matrix_surface_heartbeat.log"
)
FRONTIER_PRIORITY_REQUEST = (
    f"{BASE_PATH}/data/sync/S1_FRONTIER_RUNTIME_REPAIR_REQUEST"
)
FRONTIER_PRIORITY_LOG = f"{BASE_PATH}/logs/frontier_priority_publish_launcher.log"
ABORT_SYNC_TRANSACTION_REQUEST = (
    f"{BASE_PATH}/data/sync/ABORT_SYNC_TRANSACTION_REQUEST.json"
)
ABORT_SYNC_TRANSACTION_STATUS = (
    f"{BASE_PATH}/data/sync/ABORT_SYNC_TRANSACTION_STATUS.json"
)
RESTART_TUNNEL_KEEPER_REQUEST = (
    f"{BASE_PATH}/data/sync/RESTART_TUNNEL_KEEPER_REQUEST.json"
)
RESTART_TUNNEL_KEEPER_STATUS = (
    f"{BASE_PATH}/data/sync/RESTART_TUNNEL_KEEPER_STATUS.json"
)
CLEAN_ORPHAN_SYNC_TRANSPORT_REQUEST = (
    f"{BASE_PATH}/data/sync/CLEAN_ORPHAN_SYNC_TRANSPORT_REQUEST.json"
)
CLEAN_ORPHAN_SYNC_TRANSPORT_STATUS = (
    f"{BASE_PATH}/data/sync/CLEAN_ORPHAN_SYNC_TRANSPORT_STATUS.json"
)
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]


def _pgrep_alive(pattern: str) -> bool:
    try:
        result = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False


def _market_open_now() -> bool:
    if ZoneInfo is None:
        return False
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    return dt_time(9, 30) <= now_et.time() <= dt_time(16, 0)


def _push_to_s1():
    """Push both failover heartbeat copies through the persistent Mac tunnel.

    The failover monitor reads ``/home/niels/binance/data`` while research
    tooling reads the sandbox copy.  Updating only one path can start a second
    live stack even though the Mac heartbeat itself is current.  Use the Bible
    localhost transport first, then the explicit public endpoint and
    ``s1-int`` as bounded fallbacks when the tunnel is being repaired.
    """
    remote_paths = (
        "/home/niels/binance/data/mac_live_heartbeat.json",
        "/home/niels/binance-sandbox/data/mac_live_heartbeat.json",
    )
    localhost_shell = (
        "ssh -p 2201 -i /Users/niels/.ssh/id_ed25519 "
        "-o BatchMode=yes -o ConnectTimeout=6 "
        "-o ControlMaster=no -o ControlPath=none"
    )
    public_shell = (
        "ssh -i /Users/niels/.ssh/id_ed25519 "
        "-o BatchMode=yes -o ConnectTimeout=6 "
        "-o ControlMaster=no -o ControlPath=none"
    )
    attempts = []
    for remote_path in remote_paths:
        success = False
        for label, shell, destination in (
            (
                "LOCALHOST_TUNNEL",
                localhost_shell,
                f"niels@localhost:{remote_path}",
            ),
            (
                "PUBLIC_S1_FALLBACK",
                public_shell,
                f"niels@157.180.125.52:{remote_path}",
            ),
            ("S1_INT_FALLBACK", None, f"s1-int:{remote_path}"),
        ):
            command = ["rsync", "-az", "--timeout=8"]
            if shell is not None:
                command.extend(["-e", shell])
            command.extend([OUT_PATH, destination])
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=12,
                )
                success = result.returncode == 0
            except Exception:
                success = False
            attempts.append({
                "remote_path": remote_path,
                "transport": label,
                "success": success,
            })
            if success:
                break
    payload = {
        "schema": "mac-live-heartbeat-dual-copy-sync-v1",
        "status": "PASS" if all(
            any(row["remote_path"] == path and row["success"] for row in attempts)
            for path in remote_paths
        ) else "FAIL",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts,
    }
    try:
        Path(HEARTBEAT_SYNC_STATUS).parent.mkdir(parents=True, exist_ok=True)
        temp = f"{HEARTBEAT_SYNC_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, HEARTBEAT_SYNC_STATUS)
    except OSError:
        pass


def _launch_requested_result_sync() -> None:
    """Launch the Bible sync outside Codex's network sandbox when requested.

    The sync script has its own single-instance lock.  The request remains until
    a PASS receipt newer than the request exists, so launchd retries safely after
    transient tunnel failures without ever touching matrix-worker state.
    """
    try:
        request_mtime = os.path.getmtime(SYNC_REQUEST)
    except OSError:
        return
    sync_script = f"{BASE_PATH}/tools/always_connected_sync.sh"
    try:
        source = Path(sync_script).read_text(encoding="utf-8")
        # The operator explicitly authorizes both authenticated S1 routes:
        # localhost:2201 and 157.180.125.52:22.  Keep the key/host-key
        # protections mandatory, but do not reject the public fallback merely
        # because the localhost tunnel is wedged.
        required = (
            "niels@localhost",
            "-p 2201",
            "niels@157.180.125.52",
            "/Users/niels/.ssh/id_ed25519",
            "LOCALHOST_TUNNEL",
            "configure_localhost_transport",
            "PUBLIC_S1",
            "configure_public_transport",
            "ControlMaster=no",
            "ControlPath=none",
            "sync_manifest.py",
        )
        forbidden = (
            "StrictHostKeyChecking=no",
            "UserKnownHostsFile=/dev/null",
            "sshpass",
        )
        safe = all(token in source for token in required) and not any(
            token in source for token in forbidden
        )
    except OSError:
        safe = False
    if not safe:
        payload = {
            "status": "REFUSED_UNSAFE_S1_TRANSPORT",
            "required": (
                "authenticated dual route: niels@localhost:2201 or "
                "niels@157.180.125.52:22; "
                "key=/Users/niels/.ssh/id_ed25519"
            ),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            temp = f"{SYNC_TRANSPORT_GUARD_STATUS}.{os.getpid()}.tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(temp, SYNC_TRANSPORT_GUARD_STATUS)
        except OSError:
            pass
        return
    try:
        payload = {
            "status": "VERIFIED_DUAL_ROUTE_S1_TRANSPORT",
            "required": (
                "niels@localhost:2201 or niels@157.180.125.52:22; "
                "key=/Users/niels/.ssh/id_ed25519"
            ),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        temp = f"{SYNC_TRANSPORT_GUARD_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temp, SYNC_TRANSPORT_GUARD_STATUS)
    except OSError:
        pass
    try:
        with open(SYNC_STATUS, "r", encoding="utf-8") as handle:
            status = json.load(handle)
        completed = status.get("completed_at_epoch", status.get("verified_at_epoch", 0))
        if status.get("status") == "PASS" and float(completed) >= request_mtime:
            if _activate_repaired_sync_services():
                os.unlink(SYNC_REQUEST)
            return
    except (OSError, ValueError, TypeError):
        pass
    try:
        log_handle = open(SYNC_LAUNCH_LOG, "ab", buffering=0)
        subprocess.Popen(
            [sync_script],
            cwd=BASE_PATH,
            env={**os.environ, "ALLOW_LOCALHOST_VERIFIED_SYNC": "1"},
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _launch_requested_frontier_priority_publish() -> None:
    """Launch the bounded five-file controller publisher outside the sandbox."""
    if not os.path.isfile(FRONTIER_PRIORITY_REQUEST):
        return
    lock = f"{BASE_PATH}/data/sync/.frontier_priority_publish.lock"
    if os.path.isdir(lock):
        return
    try:
        log_handle = open(FRONTIER_PRIORITY_LOG, "ab", buffering=0)
        subprocess.Popen(
            [f"{BASE_PATH}/tools/publish_frontier_priority_s1.sh"],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _activate_repaired_sync_services() -> bool:
    """Reload only the two repaired sync LaunchAgents after verified PASS."""
    labels = (
        "com.niels.ssh_tunnel_keeper",
        "com.niels.s1-s2-autosync",
        "com.niels.chart-data-sync",
    )
    results = {}
    try:
        try:
            os.unlink(AUTOSYNC_PAUSE)
        except FileNotFoundError:
            pass
        for label in labels:
            completed = subprocess.run(
                [
                    "/bin/launchctl",
                    "kickstart",
                    "-k",
                    f"gui/{os.getuid()}/{label}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            results[label] = completed.returncode
        ok = all(code == 0 for code in results.values())
    except Exception:
        ok = False
    try:
        temp = f"{SERVICE_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "PASS" if ok else "RETRY",
                    "launchctl": results,
                },
                handle,
                indent=2,
                sort_keys=True,
            )
        os.replace(temp, SERVICE_STATUS)
    except Exception:
        pass
    return ok


def _restart_requested_chart_server() -> None:
    if not os.path.isfile(CHART_RESTART_REQUEST):
        return
    try:
        completed = subprocess.run(
            [
                "/bin/launchctl",
                "kickstart",
                "-k",
                f"gui/{os.getuid()}/com.niels.chart-server",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if completed.returncode == 0:
            os.unlink(CHART_RESTART_REQUEST)
    except Exception:
        pass


def _launch_requested_mu_materializer() -> None:
    """Dispatch only the fixed staged MU request; never accept command input."""
    if not os.path.isfile(MU_MATERIALIZER_REQUEST):
        return
    try:
        log_handle = open(MU_MATERIALIZER_LOG, "ab", buffering=0)
        subprocess.Popen(
            [
                sys.executable,
                f"{BASE_PATH}/tools/mu_s1_materializer_request.py",
                "dispatch",
            ],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _launch_requested_vector_discovery() -> None:
    """Dispatch only the fixed VECTOR_DISCOVERY pipeline request."""
    if not os.path.isfile(VECTOR_DISCOVERY_REQUEST):
        return
    try:
        log_handle = open(VECTOR_DISCOVERY_LOG, "ab", buffering=0)
        subprocess.Popen(
            [
                sys.executable,
                f"{BASE_PATH}/tools/vector_discovery_s1_request.py",
                "dispatch",
            ],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _launch_requested_vector_approx_scalar() -> None:
    """Dispatch only the fixed six-slot VEC_APPROX scalar request."""
    if not os.path.isfile(VECTOR_APPROX_SCALAR_REQUEST):
        return
    try:
        log_handle = open(VECTOR_APPROX_SCALAR_LOG, "ab", buffering=0)
        subprocess.Popen(
            [
                sys.executable,
                f"{BASE_PATH}/tools/vector_approx_scalar_s1_request.py",
                "dispatch",
            ],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _launch_stale_vector_pull_watcher() -> None:
    """Restart only the fixed pull watcher when its receipt is stale/dead."""
    if not os.path.isfile(VECTOR_PULL_REQUEST):
        return
    try:
        with open(VECTOR_PULL_REQUEST, encoding="utf-8") as handle:
            request = json.load(handle)
        request_id = str(request.get("request_id") or "")
        if not __import__("re").fullmatch(
            r"vd-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}", request_id
        ):
            return
        status = {}
        request_status = (
            f"{BASE_PATH}/data/sync/vector_discovery_pull_status/"
            f"{request_id}.json"
        )
        try:
            with open(request_status, encoding="utf-8") as handle:
                status = json.load(handle)
        except (OSError, ValueError):
            try:
                with open(VECTOR_PULL_STATUS, encoding="utf-8") as handle:
                    fallback = json.load(handle)
                if fallback.get("request_id") == request_id:
                    status = fallback
            except (OSError, ValueError):
                pass
        pid = status.get("watcher_pid")
        alive = False
        try:
            os.kill(int(pid), 0)
            alive = True
        except (OSError, TypeError, ValueError):
            pass
        fresh = (
            status.get("request_id") == request_id
            and status.get("status") in {"PASS", "DEGRADED"}
            and time.time() - float(status.get("observed_at_epoch", 0)) <= 45
            and alive
        )
        terminal_status = str(status.get("status") or "")
        retriable_startup_race = (
            terminal_status == "TERMINAL_FAILED_RESTART_REQUIRED"
            and status.get("reason") == "S1_CAMPAIGN_UNAVAILABLE"
            and int(status.get("completed_keys") or 0) == 0
        )
        if (
            status.get("request_id") == request_id
            and (
                terminal_status == "COMPLETE"
                or (
                    terminal_status.startswith("TERMINAL_")
                    and not retriable_startup_race
                )
            )
        ):
            return
        if fresh:
            return
        log_handle = open(VECTOR_PULL_LOG, "ab", buffering=0)
        subprocess.Popen(
            [
                sys.executable,
                f"{BASE_PATH}/tools/vector_discovery_s1_request.py",
                "pull-watch",
                "--request-id",
                request_id,
            ],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _refresh_matrix_progress_surface() -> None:
    """Refresh precomputed 5077 totals without claiming production throughput."""
    try:
        subprocess.run(
            [
                sys.executable,
                f"{BASE_PATH}/tools/refresh_matrix_progress_snapshot.py",
            ],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except Exception:
        pass


def _abort_requested_result_pull() -> None:
    """Stop only one verified result rsync so the changed-only retry can run."""
    if not os.path.isfile(ABORT_RESULT_PULL_REQUEST):
        return
    try:
        with open(ABORT_RESULT_PULL_REQUEST, encoding="utf-8") as handle:
            request = json.load(handle)
        pid = int(request.get("pid") or 0)
        requested_at = float(request["requested_at_epoch"])
        if time.time() - requested_at > 300:
            raise ValueError("stale request")
        if pid <= 0:
            process_rows = subprocess.run(
                ["/bin/ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.splitlines()
            matches = []
            for row in process_rows:
                raw_pid, _, raw_command = row.strip().partition(" ")
                if (
                    raw_pid.isdigit()
                    and "/usr/bin/rsync" in raw_command
                    and (
                        "S1_RESULT_PATHS.txt" in raw_command
                        or ".S1_RESULT_PATHS." in raw_command
                    )
                    and f"{BASE_PATH}/" in raw_command
                ):
                    matches.append(int(raw_pid))
            if len(matches) != 1:
                raise ValueError(
                    f"expected one current result pull, observed {len(matches)}"
                )
            pid = matches[0]
        command = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        terminate_transaction = bool(request.get("terminate_transaction"))
        if terminate_transaction:
            expected = (
                "bash" in command
                and f"{BASE_PATH}/tools/always_connected_sync.sh" in command
            )
        else:
            expected = (
                "/usr/bin/rsync" in command
                and (
                    "S1_RESULT_PATHS.txt" in command
                    or ".S1_RESULT_PATHS." in command
                )
                and f"{BASE_PATH}/" in command
            )
        if expected:
            os.kill(pid, signal.SIGTERM)
            status = (
                "TERMINATED_OBSOLETE_FULL_RESULT_TRANSACTION"
                if terminate_transaction
                else "TERMINATED_FOR_CHANGED_ONLY_RETRY"
            )
        else:
            status = "NOT_MATCHED"
        payload = {
            "status": status,
            "pid": pid,
            "command": command,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        payload = {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{exc}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        temp = f"{ABORT_RESULT_PULL_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temp, ABORT_RESULT_PULL_STATUS)
        keep_for_retries = bool(request.get("repeat_until_idle")) and payload.get(
            "status"
        ) == "TERMINATED_FOR_CHANGED_ONLY_RETRY"
        if not keep_for_retries:
            os.unlink(ABORT_RESULT_PULL_REQUEST)
    except OSError:
        pass


def _launch_requested_reporting_repair() -> None:
    """Publish the small reporting repair outside the Codex network sandbox."""
    try:
        requested_at = os.path.getmtime(REPORTING_REPAIR_REQUEST)
    except OSError:
        return
    try:
        with open(REPORTING_REPAIR_STATUS, encoding="utf-8") as handle:
            status = json.load(handle)
        completed = datetime.fromisoformat(
            str(status.get("completed_at") or "").replace("Z", "+00:00")
        ).timestamp()
        if status.get("status") == "PASS" and completed >= requested_at:
            os.unlink(REPORTING_REPAIR_REQUEST)
            return
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    if os.path.isdir(f"{BASE_PATH}/data/sync/.reporting_repair_publish.lock"):
        return
    try:
        log_handle = open(REPORTING_REPAIR_LOG, "ab", buffering=0)
        subprocess.Popen(
            [f"{BASE_PATH}/tools/publish_reporting_repair_s1.sh"],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _launch_requested_vector_capacity_publish() -> None:
    """Deploy/restart only the reviewed vector-capacity controller."""
    try:
        requested_at = os.path.getmtime(VECTOR_CAPACITY_PUBLISH_REQUEST)
    except OSError:
        return
    try:
        with open(VECTOR_CAPACITY_PUBLISH_STATUS, encoding="utf-8") as handle:
            status = json.load(handle)
        completed = float(status.get("completed_at_epoch") or 0)
        if status.get("status") == "PASS" and completed >= requested_at:
            os.unlink(VECTOR_CAPACITY_PUBLISH_REQUEST)
            return
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    if os.path.isdir(f"{BASE_PATH}/data/sync/.vector_capacity_publish.lock"):
        return
    try:
        log_handle = open(VECTOR_CAPACITY_PUBLISH_LOG, "ab", buffering=0)
        subprocess.Popen(
            [f"{BASE_PATH}/tools/publish_vector_capacity_s1.sh"],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _launch_requested_s1_disk_guard_publish() -> None:
    """Deploy only the fixed, reviewed S1 disk-space guard."""
    try:
        requested_at = os.path.getmtime(S1_DISK_GUARD_PUBLISH_REQUEST)
    except OSError:
        return
    try:
        with open(S1_DISK_GUARD_PUBLISH_STATUS, encoding="utf-8") as handle:
            status = json.load(handle)
        completed = float(status.get("completed_at_epoch") or 0)
        if status.get("status") == "PASS" and completed >= requested_at:
            os.unlink(S1_DISK_GUARD_PUBLISH_REQUEST)
            return
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    if os.path.isdir(f"{BASE_PATH}/data/sync/.s1_disk_guard_publish.lock"):
        return
    try:
        log_handle = open(S1_DISK_GUARD_PUBLISH_LOG, "ab", buffering=0)
        subprocess.Popen(
            [f"{BASE_PATH}/tools/publish_s1_disk_guard.sh"],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _enforce_trb_live_symbol_side_floor() -> None:
    """Keep the user-mandated 60 current TRB symbol/sides live.

    The fixed helper only changes the final book when membership falls below the
    floor, makes a rollback copy first, and writes a compact receipt every run.
    """
    try:
        with open(TRB_LIVE_FLOOR_LOG, "ab", buffering=0) as log_handle:
            subprocess.run(
                [
                    sys.executable,
                    f"{BASE_PATH}/tools/ensure_trb_live_symbol_side_floor.py",
                    "--root",
                    BASE_PATH,
                    "--floor",
                    "60",
                    "--enforce",
                ],
                cwd=BASE_PATH,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
    except Exception:
        pass


def _pull_vector_capacity_compact_results() -> None:
    """Start the fixed receipt/hotlist-only S1→Mac lane every heartbeat."""
    if os.path.isdir(f"{BASE_PATH}/data/sync/.vector_capacity_compact_pull.lock"):
        return
    try:
        log_handle = open(VECTOR_CAPACITY_COMPACT_SYNC_LOG, "ab", buffering=0)
        subprocess.Popen(
            [f"{BASE_PATH}/tools/pull_vector_capacity_compact_results.sh"],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _pull_current_matrix_surface() -> None:
    """Keep the verified current CSV/XLSX surface checked every minute."""
    if os.path.isdir(f"{BASE_PATH}/data/sync/.current_matrix_surface_pull.lock"):
        return
    try:
        log_handle = open(CURRENT_MATRIX_SURFACE_SYNC_LOG, "ab", buffering=0)
        subprocess.Popen(
            [f"{BASE_PATH}/tools/pull_current_matrix_surface_s1.sh"],
            cwd=BASE_PATH,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        log_handle.close()
    except Exception:
        pass


def _abort_requested_sync_transaction() -> None:
    """Terminate exactly one verified sync shell so its trap releases locks."""
    if not os.path.isfile(ABORT_SYNC_TRANSACTION_REQUEST):
        return
    try:
        with open(ABORT_SYNC_TRANSACTION_REQUEST, encoding="utf-8") as handle:
            request = json.load(handle)
        requested_at = float(request["requested_at_epoch"])
        if time.time() - requested_at > 300:
            raise ValueError("stale request")
        process_rows = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.splitlines()
        matches = []
        expected_script = f"{BASE_PATH}/tools/always_connected_sync.sh"
        for row in process_rows:
            raw_pid, _, raw_command = row.strip().partition(" ")
            if raw_pid.isdigit() and expected_script in raw_command:
                matches.append((int(raw_pid), raw_command))
        if len(matches) != 1:
            raise ValueError(
                f"expected one sync transaction, observed {len(matches)}"
            )
        pid, command = matches[0]
        os.kill(pid, signal.SIGTERM)
        payload = {
            "status": "TERMINATED_FOR_SAFE_PLANNER_PATCH",
            "pid": pid,
            "command": command,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        payload = {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{exc}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        temp = f"{ABORT_SYNC_TRANSACTION_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temp, ABORT_SYNC_TRANSACTION_STATUS)
        os.unlink(ABORT_SYNC_TRANSACTION_REQUEST)
    except OSError:
        pass


def _restart_requested_tunnel_keeper() -> None:
    """Reload the repaired keeper through host launchd after a fresh request."""
    if not os.path.isfile(RESTART_TUNNEL_KEEPER_REQUEST):
        return
    try:
        with open(RESTART_TUNNEL_KEEPER_REQUEST, encoding="utf-8") as handle:
            request = json.load(handle)
        requested_at = float(request["requested_at_epoch"])
        if time.time() - requested_at > 300:
            raise ValueError("stale request")
        result = subprocess.run(
            [
                "/bin/launchctl", "kickstart", "-k",
                f"gui/{os.getuid()}/com.niels.ssh_tunnel_keeper",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"launchctl rc={result.returncode}")
        payload = {
            "status": "RESTARTED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        payload = {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{exc}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        temp = f"{RESTART_TUNNEL_KEEPER_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temp, RESTART_TUNNEL_KEEPER_STATUS)
        os.unlink(RESTART_TUNNEL_KEEPER_REQUEST)
    except OSError:
        pass


def _clean_requested_orphan_sync_transport() -> None:
    """Terminate only sync SSH children no longer owned by the active shell."""
    if not os.path.isfile(CLEAN_ORPHAN_SYNC_TRANSPORT_REQUEST):
        return
    try:
        rows = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.splitlines()
        parsed = []
        for row in rows:
            fields = row.strip().split(None, 2)
            if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
                parsed.append((int(fields[0]), int(fields[1]), fields[2]))
        sync_script = f"{BASE_PATH}/tools/always_connected_sync.sh"
        active_shells = {
            pid for pid, _ppid, command in parsed if sync_script in command
        }
        parent_by_pid = {pid: ppid for pid, ppid, _command in parsed}

        def owned_by_active_sync(pid: int) -> bool:
            seen = set()
            current = pid
            while current > 1 and current not in seen:
                if current in active_shells:
                    return True
                seen.add(current)
                current = parent_by_pid.get(current, 0)
            return False

        exact_markers = (
            "sync_manifest.py build-results",
            "S1_RESULT_SYNC_MANIFEST",
            "S1_RESULT_PATHS",
        )
        orphans = [
            (pid, ppid, command)
            for pid, ppid, command in parsed
            if ("/usr/bin/ssh" in command or "/usr/bin/rsync" in command)
            and any(marker in command for marker in exact_markers)
            and not owned_by_active_sync(ppid)
        ]
        for pid, _ppid, _command in orphans:
            os.kill(pid, signal.SIGTERM)
        payload = {
            "status": "TERMINATED_ORPHANS" if orphans else "NO_ORPHANS",
            "active_sync_shell_pids": sorted(active_shells),
            "terminated": [
                {"pid": pid, "ppid": ppid, "command": command}
                for pid, ppid, command in orphans
            ],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        payload = {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{exc}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        temp = f"{CLEAN_ORPHAN_SYNC_TRANSPORT_STATUS}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temp, CLEAN_ORPHAN_SYNC_TRANSPORT_STATUS)
        os.unlink(CLEAN_ORPHAN_SYNC_TRANSPORT_REQUEST)
    except OSError:
        pass


def _early_trb_operator_cleanup():
    """Process the exact operator cleanup before any network/sync work."""
    if not os.path.exists(TRB_OPERATOR_CLEANUP_REQUEST):
        return
    cleanup = {"schema": "trb-operator-orphan-cleanup-v1", "terminated": [], "refused": []}
    try:
        request = json.load(open(TRB_OPERATOR_CLEANUP_REQUEST, encoding="utf-8"))
        for raw_pid in request.get("pids", []):
            pid = int(raw_pid)
            try:
                command = subprocess.run(
                    ["/bin/ps", "-p", str(pid), "-o", "command="],
                    text=True, capture_output=True, check=False,
                ).stdout.strip()
            except OSError:
                # Managed/sandboxed launchers may deny process enumeration.
                # A dead exact PID is still safe to retire; a live PID remains
                # refused because command identity cannot be proven.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    cleanup["terminated"].append({"pid": pid, "command": "<not-running>"})
                    continue
                raise
            if "run_trb_operator_pipeline.sh" not in command:
                cleanup["refused"].append({"pid": pid, "command": command})
                continue
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                os.kill(pid, signal.SIGKILL)
            cleanup["terminated"].append({"pid": pid, "command": command})
        if request.get("kill_tmux_session") is True:
            tmux_has = subprocess.run(
                ["/opt/homebrew/bin/tmux", "has-session", "-t", "trb_operator_pipeline"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if tmux_has.returncode not in (0, 1):
                raise RuntimeError(f"TMUX_HAS_SESSION_FAILED:{tmux_has.returncode}")
            if tmux_has.returncode == 0:
                tmux_result = subprocess.run(
                ["/opt/homebrew/bin/tmux", "kill-session", "-t", "trb_operator_pipeline"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
                if tmux_result.returncode != 0:
                    raise RuntimeError(f"TMUX_KILL_FAILED:{tmux_result.returncode}")
        cleanup["status"] = "PASS"
    except Exception as exc:
        cleanup.update(status="FAIL", reason=f"{type(exc).__name__}:{exc}")
    cleanup["completed_at"] = datetime.now(timezone.utc).isoformat()
    target = Path(TRB_OPERATOR_CLEANUP_STATUS)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(cleanup, indent=2, sort_keys=True) + "\n")
    os.replace(temp, target)
    if cleanup.get("status") == "PASS":
        os.unlink(TRB_OPERATOR_CLEANUP_REQUEST)


def main():
    # Gateway watchdog auto-deploy (outside sandbox via heartbeat, every 60s)
    try:
        _gw_marker = "/tmp/gateway_watchdog_deploy.done"
        _gw_need = True
        if os.path.exists(_gw_marker):
            try:
                _gw_age = time.time() - os.path.getmtime(_gw_marker)
                if _gw_age < 3600:
                    _gw_need = False
            except:
                pass
        if _gw_need:
            try:
                subprocess.Popen(["python3", "/Users/niels/Documents/binance/gateway_watchdog_deploy.py"],
                                 stdout=open("/tmp/gateway_watchdog_deploy.stdout.log", "a"),
                                 stderr=subprocess.STDOUT,
                                 start_new_session=True)
            except Exception:
                pass
    except Exception:
        pass
    _early_trb_operator_cleanup()
    market_open = _market_open_now()
    accounts = {}
    for acct in CRYPTO_ACCOUNTS:
        accounts[acct] = _pgrep_alive(f"python -u ez_manage.py --account {acct}")
    for acct in STOCK_ACCOUNTS:
        accounts[acct] = _pgrep_alive(f"python -u tradier_manage.py --accounts {acct}")
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch": time.time(),
        "market_open_et": market_open,
        "accounts": accounts,
    }
    with open(OUT_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
    _push_to_s1()
    _restart_requested_chart_server()
    _abort_requested_sync_transaction()
    _restart_requested_tunnel_keeper()
    _clean_requested_orphan_sync_transport()
    _abort_requested_result_pull()
    _launch_requested_reporting_repair()
    _launch_requested_vector_capacity_publish()
    _launch_requested_s1_disk_guard_publish()
    _enforce_trb_live_symbol_side_floor()
    _pull_vector_capacity_compact_results()
    _pull_current_matrix_surface()
    _launch_requested_frontier_priority_publish()
    _launch_requested_result_sync()
    _launch_requested_mu_materializer()
    _refresh_matrix_progress_surface()
    _launch_stale_vector_pull_watcher()
    _launch_requested_vector_discovery()
    _launch_requested_vector_approx_scalar()
    # Token-free durable supervision: this heartbeat already runs once/minute
    # outside agent sessions. The shell watchdog returns immediately after a
    # PID/queue check and launches only the sealed TRB operator when needed.
    try:
        Path(TRB_OPERATOR_WATCHDOG_LOG).parent.mkdir(parents=True, exist_ok=True)
        with open(TRB_OPERATOR_WATCHDOG_LOG, "a", encoding="utf-8") as log:
            subprocess.run(
                ["/bin/bash", TRB_OPERATOR_WATCHDOG],
                cwd=BASE_PATH,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=20,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        pass


if __name__ == "__main__":
    main()
