"""Event-loop stall detector (2026-10-14, USER: "make sure ez_positions_service ITSELF is fixed").

ez_positions_service fell back to ez_positions_realtime 23x on 10-14 (heartbeat stale > 60 s) and a 30 s-timeout REST fetch took 47 s:
the asyncio loop itself was blocked by synchronous code, and py-spy needs root on macOS, so nothing showed WHAT blocked it.
start(name) runs a 1 s ticker coroutine on the loop and a daemon thread that, when the ticker is > STALL_S late, writes every
thread's stack (faulthandler) to ~/logs/loop_stall_<name>.log — once per stall, with the lag. Diagnostics only: no behaviour change.
"""
import asyncio
import faulthandler
import os
import threading
import time
from pathlib import Path

STALL_S = float(os.environ.get("LOOP_STALL_DUMP_S", "8"))
_state = {"tick": time.monotonic(), "dumped": False}


async def _ticker():
    while True:
        _state["tick"] = time.monotonic()
        await asyncio.sleep(1.0)


def _watch(name, path):
    while True:
        time.sleep(1.0)
        lag = time.monotonic() - _state["tick"]
        if lag > STALL_S and not _state["dumped"]:
            _state["dumped"] = True
            try:
                with open(path, "a") as fh:
                    fh.write(f"\n===== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {name} pid {os.getpid()} event loop blocked {lag:.1f}s — all threads:\n")
                    fh.flush()
                    faulthandler.dump_traceback(file=fh, all_threads=True)
            except Exception:
                pass
        elif lag <= 2.0 and _state["dumped"]:
            _state["dumped"] = False
            try:
                with open(path, "a") as fh:
                    fh.write(f"===== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {name} loop recovered\n")
            except Exception:
                pass


def start(name):
    """Call from inside the running event loop (async main)."""
    path = Path.home() / "logs" / f"loop_stall_{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    _state["tick"] = time.monotonic()
    asyncio.get_running_loop().create_task(_ticker())
    threading.Thread(target=_watch, args=(name, str(path)), name="loop_stall_watch", daemon=True).start()
    return str(path)
