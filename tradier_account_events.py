"""Tradier account-event WebSocket transport.

Account events are hints to reconcile through the authenticated positions REST
endpoint; this module never treats an event as a position snapshot or places an
order. The caller owns event-to-account routing and REST reconciliation.
"""
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import time
import hmac
from pathlib import Path

import websockets


ACCOUNT_EVENTS = ("order",)
DEFAULT_HEARTBEAT_TIMEOUT_S = 90.0
MAX_RECONNECT_DELAY_S = 30.0
logger = logging.getLogger("tradier_account_events")
MAX_EVENTS_PER_POLL = 100
MAX_EVENT_LINE_BYTES = 1024 * 1024
MAX_EXECUTION_WATERMARKS = 2000


def stream_identity(api):
    url = str(getattr(api, "_current_url", "")).rstrip("/").lower()
    token = str(getattr(api, "_current_key", "") or "").strip()
    if not url or not token:
        raise RuntimeError("Tradier account stream identity requires an environment URL and token")
    return hashlib.sha256((url + "\0" + token).encode()).hexdigest()


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
            await manager.save_all()
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
            status.update(connected=False, error=type(exc).__name__, disconnected_at=time.time())
            logger.warning(f"[ACCOUNT_WS] disconnected: {type(exc).__name__}")
            if stop_event is not None and stop_event.is_set():
                break
            await sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY_S)
    status["connected"] = False


class DurableAccountEventJournal:
    """Single-owner append journal with per-account durable consumer cursors."""
    def __init__(self, base_path, api):
        self.identity = stream_identity(api)
        self.directory = Path(base_path) / "data" / "tradier_account_events" / self.identity
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.directory / "events.jsonl"
        self.owner_path = self.directory / "owner.lock"
        self.consumer_dir = self.directory / "consumers"
        self.consumer_dir.mkdir(parents=True, exist_ok=True)
        self.owner_fds = set()

    def _account_digest(self, account_id):
        return hashlib.sha256(str(account_id).encode()).hexdigest()

    def cursor_path(self, account_id):
        return self.consumer_dir / (self._account_digest(account_id) + ".json")

    def try_acquire_owner(self):
        fd = os.open(self.owner_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        self.owner_fds.add(fd)
        self.repair_incomplete_tail()
        return fd

    def release_owner(self, fd):
        if fd not in self.owner_fds:
            return
        self.owner_fds.remove(fd)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def try_acquire_consumer(self, account_id):
        path = self.consumer_dir / (self._account_digest(account_id) + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd

    @staticmethod
    def release_consumer(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def repair_incomplete_tail(self):
        if not self.journal_path.exists():
            self.journal_path.touch(mode=0o600)
            return
        with open(self.journal_path, "r+b") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            if end == 0:
                return
            stream.seek(end - 1)
            if stream.read(1) == b"\n":
                return
            position = end
            while position > 0:
                start = max(0, position - 65536)
                stream.seek(start)
                chunk = stream.read(position - start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    stream.truncate(start + newline + 1)
                    stream.flush()
                    os.fsync(stream.fileno())
                    return
                position = start
            stream.truncate(0)
            stream.flush()
            os.fsync(stream.fileno())

    def _last_seq(self):
        return self.current_position()["seq"]

    def current_position(self):
        try:
            with open(self.journal_path, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                end = stream.tell()
                position = end
                complete_end = 0
                while position > 0:
                    start = max(0, position - 65536)
                    stream.seek(start)
                    chunk = stream.read(position - start)
                    newline = chunk.rfind(b"\n")
                    if newline >= 0:
                        complete_end = start + newline + 1
                        break
                    position = start
                if complete_end == 0:
                    return {"seq": 0, "offset": 0}
                line_end = complete_end - 1
                position = line_end
                line_start = 0
                while position > 0:
                    start = max(0, position - 65536)
                    stream.seek(start)
                    chunk = stream.read(position - start)
                    newline = chunk.rfind(b"\n")
                    if newline >= 0:
                        line_start = start + newline + 1
                        break
                    position = start
                stream.seek(line_start)
                line = stream.read(complete_end - line_start - 1)
                try:
                    seq = int(json.loads(line).get("seq") or 0)
                except (ValueError, TypeError, AttributeError):
                    seq = 0
                return {"seq": seq, "offset": complete_end}
        except OSError:
            return {"seq": 0, "offset": 0}

    def publish(self, event, owner_fd):
        if owner_fd not in self.owner_fds:
            raise RuntimeError("account-event publish requires current journal-owner lease")
        seq = self._last_seq() + 1
        record = {"seq": seq, "published_at": time.time(), "event": event}
        data = (json.dumps(record, separators=(",", ":"), default=str) + "\n").encode()
        fd = os.open(self.journal_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short append to account-event journal")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return seq

    def read_after(self, offset, max_events=MAX_EVENTS_PER_POLL, max_bytes=1024 * 1024):
        result = []
        try:
            with open(self.journal_path, "rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                offset = min(max(0, int(offset or 0)), size)
                stream.seek(offset)
                consumed = 0
                while len(result) < max_events and consumed < max_bytes:
                    line = stream.readline(min(MAX_EVENT_LINE_BYTES, max_bytes - consumed) + 1)
                    if not line or not line.endswith(b"\n"):
                        break
                    consumed += len(line)
                    end = stream.tell()
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        record = {"seq": None, "event": None}
                    result.append((record, end))
        except FileNotFoundError:
            pass
        return result

    def read_cursor(self, account_id):
        try:
            value = json.loads(self.cursor_path(account_id).read_text())
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def write_cursor(self, account_id, state):
        path = self.cursor_path(account_id)
        tmp = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(state, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        try:
            fd = os.open(self.consumer_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


async def _run_journal_owner(api, journal, stop_event, connect=websockets.connect, sleep=asyncio.sleep):
    while not stop_event.is_set():
        fd = journal.try_acquire_owner()
        if fd is None:
            await sleep(0.25)
            continue
        logger.info("[ACCOUNT_WS] journal owner acquired")

        async def publish(event, received_at):
            journal.publish({"received_at": received_at, "body": event}, fd)

        try:
            await run_account_event_stream(api, publish, stop_event=stop_event, connect=connect, sleep=sleep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[ACCOUNT_WS] owner loop restarted: {type(exc).__name__}")
        finally:
            journal.release_owner(fd)
        await sleep(0.25)


async def _run_journal_consumer(manager, journal, stop_event, poll_interval=0.25, max_events=MAX_EVENTS_PER_POLL):
    account_id = str(manager.api_client._current_id or "")
    if not account_id:
        logger.error("[ACCOUNT_WS] account consumer skipped: missing configured account id")
        return
    fd = None
    while fd is None and not stop_event.is_set():
        fd = journal.try_acquire_consumer(account_id)
        if fd is None:
            await asyncio.sleep(poll_interval)
    if fd is None:
        return
    try:
        state = journal.read_cursor(account_id)
        if state is None:
            bootstrap_position = journal.current_position()
            while not stop_event.is_set():
                async with getattr(manager, "_api_sync_lock", asyncio.Lock()):
                    positions = await manager.fetch_positions_from_api()
                    if positions is not None:
                        await manager.process_account_update(data=positions)
                        await manager.save_all()
                if positions is not None:
                    break
                await asyncio.sleep(poll_interval)
            if stop_event.is_set():
                return
            state = {**bootstrap_position, "executions": {}}
            journal.write_cursor(account_id, state)
        reconcile = make_rest_reconciler([manager])
        executions = state.setdefault("executions", {})
        while not stop_event.is_set():
            records = journal.read_after(state.get("offset", 0), max_events=max_events)
            if not records:
                await asyncio.sleep(poll_interval)
                continue
            for record, end_offset in records:
                seq = record.get("seq") if isinstance(record, dict) else None
                wrapped = record.get("event") if isinstance(record, dict) else None
                body = wrapped.get("body") if isinstance(wrapped, dict) else None
                record_account = str((body or {}).get("account") or "") if isinstance(body, dict) else ""
                if record_account == account_id and isinstance(body, dict) and body.get("event") == "order":
                    order_id = str(body.get("id") or "")
                    try:
                        executed = float(body.get("executed_quantity") or 0.0)
                    except (TypeError, ValueError):
                        executed = 0.0
                    if order_id and executed > float(executions.get(order_id, 0.0)):
                        try:
                            accepted = await reconcile(body, float(wrapped.get("received_at") or time.time()))
                        except Exception as exc:
                            logger.warning(f"[ACCOUNT_WS] reconcile deferred: {type(exc).__name__}")
                            break
                        if not accepted:
                            await asyncio.sleep(poll_interval)
                            break
                        executions[order_id] = executed
                        if len(executions) > MAX_EXECUTION_WATERMARKS:
                            executions = dict(list(executions.items())[-MAX_EXECUTION_WATERMARKS:])
                            state["executions"] = executions
                if not isinstance(seq, int):
                    seq = int(state.get("seq", 0)) + 1
                state["seq"] = max(int(state.get("seq", 0)), seq)
                state["offset"] = end_offset
                journal.write_cursor(account_id, state)
    finally:
        journal.release_consumer(fd)


async def run_shared_account_event_transport(api, managers, base_path, stop_event, connect=websockets.connect):
    """Elect one token/environment-wide stream owner and durably fan events to local account writers."""
    if not managers:
        return
    journal = DurableAccountEventJournal(base_path, api)
    tasks = [asyncio.create_task(_run_journal_owner(api, journal, stop_event, connect=connect))]
    tasks.extend(asyncio.create_task(_run_journal_consumer(manager, journal, stop_event)) for manager in managers)
    await asyncio.gather(*tasks)
