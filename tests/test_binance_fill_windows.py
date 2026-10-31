from tools import binance_fills_fetch as fetcher


def test_trade_fetch_continues_past_a_short_first_seven_day_page(monkeypatch):
    calls = []
    def get(session, key, secret, path, params):
        calls.append(params)
        return [{"time": params["startTime"], "id": len(calls)}]
    monkeypatch.setattr(fetcher, "_get", get)
    start, end = 1000, 1000 + 8 * 86400000
    rows = fetcher.fetch_trades(None, "", "", "TEST", start, end)
    assert len(rows) == 2
    assert calls[1]["startTime"] == calls[0]["endTime"] + 1
    assert calls[-1]["endTime"] == end


def test_order_fetch_paginates_a_full_page_then_remaining_window(monkeypatch):
    calls = []
    def get(session, key, secret, path, params):
        calls.append(params)
        return [{"time": index + 1000, "orderId": index} for index in range(1000)] if len(calls) == 1 else []
    monkeypatch.setattr(fetcher, "_get", get)
    rows = fetcher.fetch_orders(None, "", "", "TEST", 1000, 10000)
    assert len(rows) == 1000 and len(calls) == 2
    assert calls[1]["startTime"] == 2000
