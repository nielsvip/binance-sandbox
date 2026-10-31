import asyncio
import json

import pytest

from tradier_account_events import create_account_session, decode_account_events, group_managers_by_stream_identity, make_rest_reconciler, run_account_event_stream


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

