import errno
import json
import os
import socket
import threading
import time
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
    if _installed is not None:
        if path != _installed:
            raise RuntimeError("Replay network guard already owns a different audit file")
        return status()
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    def blocked(operation, address):
        record = {"record": "blocked_replay_network_attempt", "pid": os.getpid(), "time_ns": time.time_ns(), "operation": operation, "address": str(address)}
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
    return status()

def status():
    return {"installed": _installed is not None, "audit_path": str(_installed) if _installed is not None else None, "blocked_attempts": len(_attempts), "dotenv_disabled": os.environ.get("PYTHON_DOTENV_DISABLED") == "1"}

def require_installed():
    state = status()
    if not state["installed"] or not state["dotenv_disabled"]:
        raise RuntimeError("Frozen replay requires its pre-import network guard and disabled dotenv")
    return state
