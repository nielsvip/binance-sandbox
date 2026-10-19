"""Non-blocking client for the shared indicator memory server (ez_share_ind.py, 127.0.0.1:50005).

2026-10-14 (USER: "if the bootstrap fails make sure that gets fixed as it affects everything"): ez_share_ind.get_shared_memory_client
does a multiprocessing BaseManager.connect() — a blocking auth handshake with NO timeout — and callers ran it on the asyncio event loop
thread (ez_positions_service.IndicatorsBridge.__init__ via _ensure_indicators_bridge, ez_indicators._connect_shared_memory). A wedged
server (pid up since 10-05, 158 connections, test connect hung 138 s then reset) froze the whole loop: the positions_service bootstrap
`asyncio.wait_for(..., 75)` could not even fire, ez_manage flz/inf looped through 100+ failed startups. Here the connect + get_store +
health_check run in a daemon thread with a hard timeout; on timeout the caller gets None and keeps its existing fallbacks (Redis /
market_data files). A timed-out attempt's thread is abandoned (daemon) and cannot block the caller.
"""
import threading
import time

_LAST_FAIL = {"t": 0.0}
_LAST_ERROR = {"reason": "", "t": 0.0}


def connect_store(address=("127.0.0.1", 50005), authkey=b"ez_secret_key", timeout=3.0, backoff_s=30.0):
    """Proxy to the shared IndicatorStore, or None if the server does not answer within `timeout` s. After a failure, further attempts
    within `backoff_s` return None immediately (no thread pile-up against a wedged server)."""
    if time.time() - _LAST_FAIL["t"] < backoff_s:
        return None
    box = {}

    def _work():
        try:
            from ez_share_ind import get_shared_memory_client
            client = get_shared_memory_client(address=address, authkey=authkey)
            if client is None:
                return
            store = client.get_store()  # pylint: disable=no-member
            store.health_check()
            box["store"] = store
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc

    th = threading.Thread(target=_work, name="shared_mem_connect", daemon=True)
    th.start()
    th.join(timeout)
    store = box.get("store")
    if store is None:
        _LAST_FAIL["t"] = time.time()
        # 2026-10-19: record WHY. The caller's message was "Could not connect to Shared Memory (timeout/None)" and
        # it was logged 3785 times on 2026-10-19 alone with no cause attached, because the real exception landed in
        # this function's local box and was discarded. Distinguishing "still handshaking" (server saturated) from an
        # exception is the whole diagnosis: on 2026-10-19 23:3xZ the port accepted TCP in 0.00s while 328
        # connections were ESTABLISHED to it (151 held by the server, 26 by ez_manage --account fin after 29 min of
        # life), so the handshake was hanging on a saturated server, not failing.
        _LAST_ERROR["reason"] = "still handshaking after %.1fs (server saturated?)" % timeout if th.is_alive() else repr(box.get("err"))
        _LAST_ERROR["t"] = time.time()
    return store


def last_error():
    """Why the most recent connect_store() returned None, for the caller's log line. '' if never failed."""
    return _LAST_ERROR.get("reason") or ""
