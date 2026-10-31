import asyncio
import json

import pytest

from tradier_account_events import DurableAccountEventJournal, create_account_session, decode_account_events, group_managers_by_stream_identity, make_rest_reconciler, run_account_event_stream


class StreamAPI:
    _current_url = "https://api.tradier.com/v1"
    _current_key = "multiprocess-test-token"


def _hold_stream_lease(root, result_queue, release_event, publish_before_wait=False):
    journal = DurableAccountEventJournal(root, StreamAPI())
    fd = journal.try_acquire_owner()
    if fd is not None and publish_before_wait:
        journal.publish({"event": "order", "account": "private-account-id", "id": "8", "executed_quantity": 1}, fd)
        with open(journal.journal_path, "ab") as stream:
            stream.write(b'{"seq":2,"event":')
            stream.flush()
            __import__("os").fsync(stream.fileno())
    result_queue.put(fd is not None)
    if fd is not None:
        release_event.wait(20)
        journal.release_owner(fd)


def _takeover_and_publish(root, result_queue, stop_event):
    journal = DurableAccountEventJournal(root, StreamAPI())
    deadline = __import__("time").time() + 15
    fd = None
    while fd is None and __import__("time").time() < deadline:
        fd = journal.try_acquire_owner()
        if fd is None:
            __import__("time").sleep(0.05)
    if fd is None:
        result_queue.put(False)
        return
    try:
        result_queue.put(True)
        journal.publish({"event": "order", "account": "private-account-id", "id": "9", "executed_quantity": 1}, fd)
    finally:
        journal.release_owner(fd)


def _read_journal_fanout(root, account_id, result_queue):
    journal = DurableAccountEventJournal(root, StreamAPI())
    fd = journal.try_acquire_consumer(account_id)
    if fd is None:
        result_queue.put(None)
        return
    try:
        rows = journal.read_after(0)
        result_queue.put((rows[0][0]["event"], journal.cursor_path(account_id).name))
    finally:
        journal.release_consumer(fd)


class FakeAPI:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def _request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        return self.response


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        return next(self.messages)


class FakePositionManager:
    def __init__(self, account_id, base_path, url="https://api.tradier.com/v1", key="token"):
        self.api_client = type("API", (), {"_current_id": account_id, "_current_url": url, "_current_key": key})()
        self.base_path = base_path
        self._positions_dirty = False
        self.position_reads = 0
        self.processed = []
        self.saved = 0
        self.next_positions = [{"symbol": "TEST", "quantity": "2"}]

    async def fetch_positions_from_api(self):
        self.position_reads += 1
        return self.next_positions

    async def process_account_update(self, data=None):
        self.processed.append(data)
        self._positions_dirty = True

    async def save_all(self):
        self.saved += 1
        self.get_position_file("LONG").write_text(f"long-{self.saved}")
        self.get_position_file("SHORT").write_text(f"short-{self.saved}")

    def get_position_file(self, side):
        return self.base_path / f"{side.lower()}_positions.json"


@pytest.mark.asyncio
async def test_create_account_session_uses_read_context_and_validates_response():
    api = FakeAPI({"stream": {"url": "wss://ws.tradier.com/v1/accounts/events", "sessionid": "session"}})
    assert await create_account_session(api) == ("wss://ws.tradier.com/v1/accounts/events", "session")
    assert api.calls == [("POST", "/accounts/events/session", {"use_data_context": False})]


@pytest.mark.parametrize("response", [None, {}, {"stream": {"url": "ws://insecure", "sessionid": "x"}},
                                      {"stream": {"url": "wss://ws.tradier.com/v1/accounts/events"}}])
@pytest.mark.asyncio
async def test_create_account_session_rejects_incomplete_response(response):
    with pytest.raises(RuntimeError):
        await create_account_session(FakeAPI(response))


def test_decode_account_events_handles_object_list_and_ignores_nonobjects():
    assert decode_account_events('{"event":"order"}') == [{"event": "order"}]
    assert decode_account_events('[{"event":"order"}, 1, null]') == [{"event": "order"}]
    assert decode_account_events('null') == []


def test_managers_share_one_stream_per_token_and_environment():
    from pathlib import Path

    a = FakePositionManager("live-a", Path("."))
    b = FakePositionManager("live-b", Path("."))
    c = FakePositionManager("paper-c", Path("."), url="https://sandbox.tradier.com/v1", key="token")
    groups = group_managers_by_stream_identity([a, b, c])
    assert len(groups) == 2
    assert groups[0][1] == [a, b]
    assert groups[1][1] == [c]


@pytest.mark.asyncio
async def test_stream_forwards_order_event_but_heartbeat_only_updates_liveness():
    api = FakeAPI({"stream": {"url": "wss://ws.tradier.com/v1/accounts/events", "sessionid": "session"}})
    stop = asyncio.Event()
    status = {}
    received = []
    socket = FakeSocket(['{"event":"heartbeat"}', '{"event":"order","status":"filled","executed_quantity":2}'])
    connections = []

    def connect(url, **kwargs):
        connections.append((url, kwargs))
        return socket

    async def on_event(event, received_at):
        received.append((event, received_at))
        stop.set()

    await run_account_event_stream(api, on_event, status, stop, connect=connect)

    assert connections[0][0] == "wss://ws.tradier.com/v1/accounts/events"
    assert socket.sent == [{"events": ["order"], "sessionid": "session", "excludeAccounts": []}]
    assert [event for event, _ in received] == [{"event": "order", "status": "filled", "executed_quantity": 2}]
    assert status["connected"] is False
    assert status["events_received"] == 1
    assert status["last_heartbeat_at"] <= status["last_event_at"]
    assert api.calls[0][1] == "/accounts/events/session"


@pytest.mark.asyncio
async def test_order_fill_event_triggers_rest_confirmed_persist_once_per_cumulative_fill(tmp_path):
    manager = FakePositionManager("account-1", tmp_path)
    reconcile = make_rest_reconciler([manager])
    event = {"event": "order", "account": "account-1", "id": 9, "status": "filled", "executed_quantity": 2}

    assert await reconcile(event, 100.0) is True
    assert await reconcile(event, 101.0) is False
    assert manager.position_reads == 1
    assert manager.processed == [manager.next_positions]
    assert manager.saved == 1
    assert manager._positions_dirty is True


@pytest.mark.asyncio
async def test_failed_rest_confirmation_does_not_consume_websocket_fill(tmp_path):
    manager = FakePositionManager("account-1", tmp_path)
    manager.next_positions = None
    reconcile = make_rest_reconciler([manager])
    event = {"event": "order", "account": "account-1", "id": 9, "executed_quantity": 2}

    assert await reconcile(event, 100.0) is False
    manager.next_positions = []
    assert await reconcile(event, 101.0) is True
    assert manager.position_reads == 2
    assert manager.processed == [[]]


def test_multiprocess_stream_lease_allows_only_one_owner(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    release = ctx.Event()
    first = ctx.Process(target=_hold_stream_lease, args=(str(tmp_path), results, release))
    second = ctx.Process(target=_hold_stream_lease, args=(str(tmp_path), results, release))
    first.start()
    assert results.get(timeout=10) is True
    second.start()
    assert results.get(timeout=10) is False
    release.set()
    first.join(timeout=10)
    second.join(timeout=10)
    assert first.exitcode == 0
    assert second.exitcode == 0
    assert "multiprocess-test-token" not in str(list(tmp_path.rglob("*")))


def test_owner_death_releases_lease_for_takeover_and_journal_replay(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    release = ctx.Event()
    owner = ctx.Process(target=_hold_stream_lease, args=(str(tmp_path), results, release, True))
    owner.start()
    assert results.get(timeout=10) is True
    takeover_results = ctx.Queue()
    takeover = ctx.Process(target=_takeover_and_publish, args=(str(tmp_path), takeover_results, release))
    takeover.start()
    owner.terminate()
    owner.join(timeout=10)
    assert takeover_results.get(timeout=10) is True
    takeover.join(timeout=10)
    assert takeover.exitcode == 0
    journal = DurableAccountEventJournal(tmp_path, StreamAPI())
    records = journal.read_after(0)
    assert len(records) == 2
    assert [record[0]["seq"] for record in records] == [1, 2]
    assert [record[0]["event"]["id"] for record in records] == ["8", "9"]


def test_multiprocess_fanout_gives_each_account_its_own_durable_cursor(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    journal = DurableAccountEventJournal(tmp_path, StreamAPI())
    owner_fd = journal.try_acquire_owner()
    assert owner_fd is not None
    journal.publish({"event": "order", "account": "private-account-id", "id": "10", "executed_quantity": 2}, owner_fd)
    journal.release_owner(owner_fd)
    results = ctx.Queue()
    readers = [ctx.Process(target=_read_journal_fanout, args=(str(tmp_path), account, results)) for account in ("account-a", "account-b")]
    for reader in readers:
        reader.start()
    records = [results.get(timeout=10), results.get(timeout=10)]
    for reader in readers:
        reader.join(timeout=10)
        assert reader.exitcode == 0
    assert [row[0]["id"] for row in records] == ["10", "10"]
    assert len({row[1] for row in records}) == 2
    assert all(row[1].endswith(".json") for row in records)

