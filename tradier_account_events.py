"""Tradier account-event WebSocket transport.

Account events are hints to reconcile through the authenticated positions REST
endpoint; this module never treats an event as a position snapshot or places an
order. The caller owns event-to-account routing and REST reconciliation.
"""
import asyncio
import json
import logging
import time
import hmac

import websockets


ACCOUNT_EVENTS = ("order",)
DEFAULT_HEARTBEAT_TIMEOUT_S = 90.0
MAX_RECONNECT_DELAY_S = 30.0
logger = logging.getLogger("tradier_account_events")


async def create_account_session(api):
    """Return the broker-provided WebSocket URL and short-lived session id."""
    response = await api._request("POST", "/accounts/events/session", use_data_context=False)
    stream = response.get("stream") if isinstance(response, dict) else None
    if not isinstance(stream, dict):
        raise RuntimeError("Tradier account session response missing stream object")
    url = stream.get("url")
    session_id = stream.get("sessionid")
    if not isinstance(url, str) or not url.startswith("wss://") or not isinstance(session_id, str) or not session_id:
        raise RuntimeError("Tradier account session response missing secure URL or session id")
    return url, session_id


def decode_account_events(message):
    """Decode one account-stream message to a list of event objects."""
    payload = json.loads(message) if isinstance(message, (str, bytes, bytearray)) else message
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [event for event in payload if isinstance(event, dict)]
    return []


def group_managers_by_stream_identity(managers):
    """Group managers that share one Tradier token and environment.

    Tradier permits one account-event session per token. The raw credentials
    are compared in memory and never returned, logged, or serialized.
    """
    groups = []
    for manager in managers:
        api = manager.api_client
        url = str(api._current_url)
        key = str(api._current_key or "")
        group = next((item for item in groups if item[0] == url and hmac.compare_digest(item[1], key)), None)
        if group is None:
            group = (url, key, api, [])
            groups.append(group)
        group[3].append(manager)
    return [(api, rows) for _url, _key, api, rows in groups]


def _positions_file_stamps(manager):
    stamps = []
    for side in ("LONG", "SHORT"):
        path = manager.get_position_file(side)
        try:
            stat = path.stat()
            stamps.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


def make_rest_reconciler(managers):
    """Route increasing execution events to their manager's REST-confirmed sync."""
    account_map = {str(manager.api_client._current_id): manager for manager in managers if manager.api_client._current_id}
    locks = {account: asyncio.Lock() for account in account_map}
    executions = {}

    async def reconcile(event, _received_at):
        if event.get("event") != "order":
            return False
        account = str(event.get("account") or "")
        manager = account_map.get(account)
        order_id = str(event.get("id") or "")
        try:
            executed = float(event.get("executed_quantity") or 0.0)
        except (TypeError, ValueError):
            return False
        if not manager or not order_id or executed <= executions.get((account, order_id), 0.0):
            return False
        async with getattr(manager, "_api_sync_lock", locks[account]):
            if executed <= executions.get((account, order_id), 0.0):
                return False
            positions = await manager.fetch_positions_from_api()
            if positions is None:
                return False
            await manager.process_account_update(data=positions)
            before_save = _positions_file_stamps(manager)
            await manager.save_all()
            if before_save == _positions_file_stamps(manager):
                return False
            executions[(account, order_id)] = executed
        return True

    return reconcile


async def run_account_event_stream(api, on_event, status=None, stop_event=None, heartbeat_timeout_s=DEFAULT_HEARTBEAT_TIMEOUT_S,
                                   connect=websockets.connect, sleep=asyncio.sleep):
    """Reconnect forever, forwarding account events to a caller-owned reconciler.

    The callback must confirm any position change with `api.get_account_positions()`
    (or the manager's equivalent) before writing position state. Heartbeats only
    maintain liveness and do not trigger REST reads.
    """
    status = status if status is not None else {}
    delay = 1.0
    while stop_event is None or not stop_event.is_set():
        try:
            url, session_id = await create_account_session(api)
            async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=10) as socket:
                await socket.send(json.dumps({"events": list(ACCOUNT_EVENTS), "sessionid": session_id, "excludeAccounts": []}))
                status.update(connected=True, error=None, last_event_at=time.time())
                logger.info("[ACCOUNT_WS] connected")
                delay = 1.0
                while stop_event is None or not stop_event.is_set():
                    message = await asyncio.wait_for(socket.recv(), timeout=heartbeat_timeout_s)
                    received_at = time.time()
                    for event in decode_account_events(message):
                        status["last_event_at"] = received_at
                        if event.get("event") == "heartbeat":
                            status["last_heartbeat_at"] = received_at
                            continue
                        status["events_received"] = int(status.get("events_received", 0)) + 1
                        result = on_event(event, received_at)
                        if asyncio.iscoroutine(result):
                            await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status.update(connected=False, error=f"{type(exc).__name__}: {str(exc)[:160]}", disconnected_at=time.time())
            logger.warning(f"[ACCOUNT_WS] disconnected: {type(exc).__name__}: {str(exc)[:160]}")
            if stop_event is not None and stop_event.is_set():
                break
            await sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY_S)
    status["connected"] = False
