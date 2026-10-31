import errno
import json
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

_installed = None
_attempts = []
_lock = threading.Lock()

def install(audit_path, run_root):
    global _installed
    raw = Path(audit_path)
    path = raw.resolve()
    root = Path(run_root).resolve()
    if not raw.is_absolute() or not root.is_dir() or not path.parent.is_dir() or not path.is_relative_to(root):
        raise RuntimeError("Replay network audit requires an existing run-owned directory")
    existing = getattr(socket, "_frozen_replay_guard", None)
    if existing is not None:
        if str(path) != existing["audit_path"]:
            raise RuntimeError("Replay network guard already owns a different audit file")
        return require_installed()
    loaded = [name for name in ("config", "config_tradier", "ez_manage", "tradier_manage", "ez_positions_quick", "ez_positions_service", "per_sym_store", "backtest_v12_engine") if name in sys.modules]
    if loaded:
        raise RuntimeError(f"Replay network guard must precede project imports: {loaded}")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["EZ_SKIP_ENV_GPG"] = "1"
    def blocked(operation, address):
        record = {"record": "blocked_replay_network_attempt", "pid": os.getpid(), "time_ns": time.time_ns(), "operation": operation, "address": str(address)}
        record["callsite"] = [{"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_stack(limit=18)[:-1]]
        with _lock:
            _attempts.append(record)
            with path.open("a") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        raise OSError(errno.ENETUNREACH, "Network I/O is disabled in frozen replay")
    def connect(sock, address):
        return blocked("connect", address)
    def connect_ex(sock, address):
        return blocked("connect_ex", address)
    def sendto(sock, *args):
        return blocked("sendto", args[-1] if args else None)
    def getaddrinfo(host, port, *args, **kwargs):
        return blocked("getaddrinfo", (host, port))
    def gethostbyname(host):
        return blocked("gethostbyname", host)
    def gethostbyname_ex(host):
        return blocked("gethostbyname_ex", host)
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    _installed = path
    socket._frozen_replay_guard = {"audit_path": str(path), "attempts": _attempts, "socket_type": socket.socket, "hooks": [(socket.socket, "connect", connect), (socket.socket, "connect_ex", connect_ex), (socket.socket, "sendto", sendto), (socket, "getaddrinfo", getaddrinfo), (socket, "gethostbyname", gethostbyname), (socket, "gethostbyname_ex", gethostbyname_ex)]}
    return status()

def status():
    guard = getattr(socket, "_frozen_replay_guard", None)
    installed = bool(guard) and socket.socket is guard["socket_type"] and all(getattr(owner, name) is callback for owner, name, callback in guard["hooks"])
    return {"installed": bool(installed), "audit_path": guard["audit_path"] if guard else None, "blocked_attempts": len(guard["attempts"]) if guard else 0, "dotenv_disabled": os.environ.get("PYTHON_DOTENV_DISABLED") == "1", "gpg_disabled": os.environ.get("EZ_SKIP_ENV_GPG") == "1"}

def require_installed():
    state = status()
    if not state["installed"] or not state["dotenv_disabled"] or not state["gpg_disabled"]:
        raise RuntimeError("Frozen replay requires its pre-import network guard and disabled dotenv/GPG")
    return state
