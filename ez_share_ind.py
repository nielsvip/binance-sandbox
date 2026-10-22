"""
Shared memory indicator store for easy communication between processes.
"""
import logging
import signal
import sys
import threading
import time
from multiprocessing.managers import BaseManager
import socket
from multiprocessing.managers import Server

class ReuseAddrServer(Server):
    """Server that sets SO_REUSEADDR to avoid port binding failures."""
    def serve_forever(self):
        if hasattr(self, 'listener') and hasattr(self.listener, '_listener') and hasattr(self.listener._listener, '_socket'):
            self.listener._listener._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().serve_forever()

    def accepter(self):
        """A DEAF SERVER CANNOT BE ALLOWED TO LOOK ALIVE (measured 2026-10-20 17:17-17:20Z).

        multiprocessing.managers.Server.accepter is `while True: try: accept() except OSError: continue` followed
        by an UNGUARDED `t.start()`. When the process is out of threads, `t.start()` raises RuntimeError, which is
        not an OSError, so it escapes the while loop and the accept thread DIES — permanently. The server then goes
        on serving its already-connected clients and printing its heartbeat forever, while the kernel completes TCP
        handshakes into a listen backlog nobody drains.

        That is exactly the state pid 97452 was found in after 21 h: 116 threads, 101 in os_read, 69 in take_gil,
        NO thread anywhere in accept(); three fresh connects produced zero new server-side fds and timed out in
        SYN_SENT ("TCP ok but NO challenge in 4s -> DEAF"). The damage was not local: ez_market_data.py:445 blocks
        in this handshake with no timeout BEFORE writing its first log line, so the watchdog killed it for silence
        78 times in 24 h (the worst churner in the system), and ez_indicators logged
        "Could not connect to Shared Memory ... still handshaking after 3.0s (server saturated?)" 1151 times.

        So: never let any exception out of the loop, and never let a failed handler thread take the listener with
        it. A connection we cannot serve is closed and the loop continues; the next client still gets a server.
        """
        while True:
            try:
                c = self.listener.accept()
            except OSError:
                continue
            except BaseException as exc:
                logger.error("[ACCEPTER] accept() failed (%s: %s) — loop continues", type(exc).__name__, exc)
                time.sleep(0.5)
                continue
            try:
                t = threading.Thread(target=self.handle_request, args=(c,))
                t.daemon = True
                t.start()
            except BaseException as exc:
                logger.error("[ACCEPTER] could not start a handler thread (%s: %s) — closing this connection, "
                             "KEEPING the accept loop alive (threads=%d)", type(exc).__name__, exc, threading.active_count())
                try:
                    c.close()
                except BaseException:
                    pass
                time.sleep(0.5)
                continue

# 2026-10-22: basicConfig runs ONLY for the standalone server. It used to run at
# import time, so every process importing get_shared_memory_client (ez_indicators,
# ez_manage, services) got its root logger reformatted to [SHARED_MEM] — asyncio's
# own "Task exception was never retrieved" lines wore the tag, and every record
# printed twice (importer's handler + this root handler). Importers configure
# their own logging; this module only needs a named logger.
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s [SHARED_MEM] %(message)s")
logger = logging.getLogger("ez_share_ind")

# The file run_with_watchdog.sh monitors for this script's liveness. Refreshed by the heartbeat ONLY when a real
# self-connect succeeds, so its mtime means "accepting clients", not merely "process exists".
import os as _os
_HEARTBEAT_FILE = _os.path.join(_os.path.expanduser("~"), "logs", "ez_share_ind.log")

class IndicatorStore:
    """Store for shared indicators across multiple processes."""

    def __init__(self):
        self._indicators = {}
        # We don't strictly need _last_update per symbol for this logic, 
        # but kept for compatibility.
        self._last_update = {}

    def update_symbol(self, symbol, data):
        """Update indicators for a specific symbol."""
        if symbol not in self._indicators:
            self._indicators[symbol] = {}
        self._indicators[symbol].update(data)
        self._last_update[symbol] = time.time()

    # --- NEW: BATCH UPDATE METHOD ---
    def update_batch(self, batch_data: dict):
        """
        Updates multiple symbols at once to reduce IPC overhead.
        batch_data format: { 'BTCUSDC': { ...data... }, 'ETHUSDC': { ... } }
        """
        ts = time.time()
        for symbol, data in batch_data.items():
            if symbol not in self._indicators:
                self._indicators[symbol] = {}
            self._indicators[symbol].update(data)
            self._last_update[symbol] = ts

    def get_symbol(self, symbol):
        """Retrieve the indicators for a given symbol."""
        return self._indicators.get(symbol, {})

    def get_all(self):
        """Return a copy of all stored indicators."""
        return self._indicators.copy()

    def health_check(self):
        """Check if the store is active."""
        return True

    def get_stats(self):
        """Get summary statistics of the data in the store."""
        return {
            "count": len(self._indicators),
            "keys": list(self._indicators.keys())[:10]
        }

_global_store = IndicatorStore()

def get_store_instance():
    """Return the global indicator store instance."""
    return _global_store

class IndicatorManager(BaseManager):
    """Manager class for sharing the IndicatorStore."""

IndicatorManager.register('get_store', callable=get_store_instance)
IndicatorManager._Server = ReuseAddrServer

def get_shared_memory_client(address=('127.0.0.1', 50005), authkey=b'ez_secret_key'):
    """Connect to the shared memory server and return a manager instance."""
    manager = IndicatorManager(address=address, authkey=authkey)
    try:
        manager.connect()
        return manager
    except Exception:
        return None

def start_shared_memory_server(address=('0.0.0.0', 50005), authkey=b'ez_secret_key'):
    """Start the shared memory manager server."""
    import subprocess
    try:
        result = subprocess.run(['fuser', '-k', f'{address[1]}/tcp'], capture_output=True, timeout=5)
        if result.returncode == 0:
            logger.warning("Killed previous process on port %s", address[1])
            time.sleep(1)
    except Exception:
        pass
    manager = IndicatorManager(address=address, authkey=authkey)
    server = None
    for i in range(30):
        try:
            server = manager.get_server()
            break
        except OSError as e:
            if "Address already in use" in str(e) or "Errno 98" in str(e):
                logger.warning("⚠️ Port %s busy (Attempt %d/30). Waiting...", address[1], i+1)
                if i == 5:
                    try:
                        subprocess.run(['fuser', '-k', f'{address[1]}/tcp'], capture_output=True, timeout=5)
                        logger.warning("Force-killed port %s holder at attempt 5", address[1])
                    except Exception:
                        pass
                time.sleep(1)
            else:
                raise e
    if not server:
        logger.error("❌ Could not bind port %s after 30 seconds. Exiting.", address[1])
        sys.exit(1)

    logger.info("🚀 Shared Memory Server STARTED on %s:%s", address[0], address[1])
    logger.info("   -> Bridge Active: Ready to merge data from Producers.")

    # Handle graceful shutdown
    def signal_handler(_sig, _frame):
        logger.info("Stopping server...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    def _heartbeat():
        """PROVE reachability, do not assert it. The old heartbeat printed "server alive" unconditionally from its
        own thread, so it kept saying so for 21 h while the accept loop was dead and every new client timed out —
        it was the one subsystem still working, and it was reporting on a subsystem that was not. A heartbeat that
        cannot fail is not a heartbeat. This one opens a real TCP connection to the listener and requires the
        challenge bytes back, which is precisely what a deaf server cannot do."""
        while True:
            time.sleep(60)
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(3.0)
            try:
                probe.connect(("127.0.0.1", 50005))
                reachable = bool(probe.recv(1))
                detail = "challenge received" if reachable else "connected but no challenge"
            except BaseException as exc:
                reachable, detail = False, f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    probe.close()
                except BaseException:
                    pass
            if reachable:
                logger.info("[SHARED_MEM] ❤️ Heartbeat — server ACCEPTING on port 50005 (%s, threads=%d)", detail, threading.active_count())
                # TOUCH THE WATCHDOG'S MONITORED FILE **ONLY WHEN THE PROBE SUCCEEDS**.
                # run_with_watchdog.sh monitors $LOGDIR/ez_share_ind.log, but this module's logging.basicConfig has
                # no filename, so everything went to stderr (captured as ez_share_ind_stderr.log) and
                # ez_share_ind.log NEVER EXISTED. The check is guarded by `-f "$_check_log"`, so the no-output
                # timeout never armed for this process at all — which is how it sat DEAF for 21 h (pid 97452,
                # Oct 19 20:06 -> Oct 20 17:26) with no watchdog able to notice.
                # Writing here, and only on success, makes that existing check real AND self-healing: a deaf server
                # stops refreshing this file, crosses NO_OUTPUT_TIMEOUT (600 s) and gets recycled automatically,
                # instead of starving every new client indefinitely.
                try:
                    with open(_HEARTBEAT_FILE, "a") as fh:
                        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ACCEPTING port 50005 threads={threading.active_count()}\n")
                except BaseException:
                    pass
            else:
                logger.critical("[SHARED_MEM] 💀 DEAF SERVER on port 50005 — the process is alive but NOT accepting "
                                "new clients (%s, threads=%d). NOT refreshing %s, so the watchdog's no-output timer "
                                "will recycle this process. Live producers that handshake without a timeout would "
                                "otherwise hang and be killed for silence.", detail, threading.active_count(), _HEARTBEAT_FILE)
    threading.Thread(target=_heartbeat, daemon=True).start()
    try:
        server.serve_forever()
    except Exception as e:
        logger.error("❌ Server crashed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    start_shared_memory_server()