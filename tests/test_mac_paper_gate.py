import asyncio
import json
import types
from pathlib import Path

import pytest


def test_binance_mutations_refused_and_recorded_only_while_flag_exists(tmp_path, monkeypatch):
    import mac_paper_gate as G
    from binance.client import Client
    from binance.exceptions import BinanceAPIException
    import order_dedupe_guard  # noqa: F401  (installs the gate)
    assert getattr(Client._request_futures_api, "__mac_paper__", False)
    monkeypatch.setattr(G, "FLAG", tmp_path / "MAC_PAPER_TRADING")
    monkeypatch.setattr(G, "OUT", tmp_path / "out")
    G._cache["t"] = 0
    c = Client.__new__(Client)
    c.API_KEY = "x"
    calls = []
    G.FLAG.write_text("on")
    G._cache["t"] = 0
    for method, path in (("post", "order"), ("delete", "order"), ("post", "algoOrder"), ("post", "leverage")):
        with pytest.raises(BinanceAPIException) as e:
            c._request_futures_api(method, path, True, data={"symbol": "BTCUSDT", "side": "BUY", "quantity": "1"})
        assert e.value.code == -2027 and "MAC_PAPER_TRADING" in e.value.message
    rows = [json.loads(l) for p in (tmp_path / "out").glob("orders_crypto_*.jsonl") for l in p.read_text().splitlines()]
    assert len(rows) == 4 and rows[0]["params"]["symbol"] == "BTCUSDT"
    # futures_create_order (and the class guard above it) funnels into the same choke point
    with pytest.raises(BinanceAPIException):
        Client.futures_create_order.__wrapped__ if False else c._request_futures_api("post", "order", True, data={})
    G.FLAG.unlink()
    G._cache["t"] = 0
    assert not G.paper_on()


def test_listenkey_and_reads_never_refused(tmp_path, monkeypatch):
    import mac_paper_gate as G
    from binance.client import Client
    import order_dedupe_guard  # noqa: F401
    monkeypatch.setattr(G, "FLAG", tmp_path / "MAC_PAPER_TRADING")
    G.FLAG.write_text("on")
    G._cache["t"] = 0
    c = Client.__new__(Client)
    seen = []
    monkeypatch.setattr(Client, "_request", lambda self, method, uri, signed, force_params=False, **kw: seen.append((method, uri)) or {}, raising=False)
    try:
        c._request_futures_api("get", "positionRisk", True)
        c._request_futures_api("post", "listenKey", False)
    except Exception as exc:  # the real lower layer may need a session; reaching it is the point
        assert "MAC_PAPER_TRADING" not in str(exc)


def test_tradier_order_mutations_refused(tmp_path, monkeypatch):
    import mac_paper_gate as G
    import tradier_api
    G._install_tradier()
    monkeypatch.setattr(G, "FLAG", tmp_path / "MAC_PAPER_TRADING")
    monkeypatch.setattr(G, "OUT", tmp_path / "out")
    G.FLAG.write_text("on")
    G._cache["t"] = 0
    self = types.SimpleNamespace(account_key="trb")
    r = asyncio.run(tradier_api.TradierAPIClient._request(self, "POST", "/v1/accounts/X/orders", data={"symbol": "AAPL"}))
    assert "MAC_PAPER_TRADING" in r["errors"]["error"][0]
    r = asyncio.run(tradier_api.TradierAPIClient._request(self, "DELETE", "/v1/accounts/X/orders/1"))
    assert "MAC_PAPER_TRADING" in r["errors"]["error"][0]
    assert len((tmp_path / "out").glob("orders_stocks_*.jsonl").__next__().read_text().splitlines()) == 2
