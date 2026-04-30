"""
Shared memory indicator store for easy communication between processes.
"""
import logging
import signal
import sys
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

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [SHARED_MEM] %(message)s")
logger = logging.getLogger("ez_share_ind")

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

    import threading
    def _heartbeat():
        while True:
            time.sleep(60)
            logger.info("[SHARED_MEM] ❤️ Heartbeat — server alive on port 50005")
    threading.Thread(target=_heartbeat, daemon=True).start()
    try:
        server.serve_forever()
    except Exception as e:
        logger.error("❌ Server crashed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    start_shared_memory_server()