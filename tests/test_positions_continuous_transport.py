from tools import mac_positions_push_executors as transport


def test_destination_tries_alternate_after_failure_and_remembers_success(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "LAST_ROUTE", {})
    monkeypatch.setattr(transport, "ROUTES", {"crypto": [("s1-pub", "binance"), ("s1-int", "binance")]})
    calls = []
    def push(host, tree, local, digest):
        calls.append(host)
        return {"host": host, "tree": tree, "ok": host == "s1-int"}
    monkeypatch.setattr(transport, "push", push)
    result = transport.push_destination("crypto", "binance", tmp_path / "book", "hash")
    assert calls == ["s1-pub", "s1-int"] and result[-1]["ok"]
    calls.clear()
    transport.push_destination("crypto", "binance", tmp_path / "book", "hash")
    assert calls == ["s1-int"]


def test_stock_delivery_requires_both_execution_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    monkeypatch.setattr(transport, "document", lambda *args: {"held_total": 1, "sides": ["X_LONG"], "books": [], "unreadable": []})
    monkeypatch.setattr(transport, "push_destination", lambda venue, tree, *args: [{"tree": tree, "ok": tree == "binance"}])
    result = transport.run(("stocks",))["venues"]["stocks"]
    assert result["delivered"] == ["binance"]
    assert result["ok"] is False


def test_atomic_book_replacement_is_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    monkeypatch.setattr(transport, "CRYPTO_ACCOUNTS", ("ang",))
    folder = tmp_path / "ang"
    folder.mkdir()
    path = folder / "long_positions.json"
    path.write_text('{"qty":1}')
    before = transport.book_fingerprint("crypto")
    replacement = folder / "new.json"
    replacement.write_text('{"qty":2}')
    replacement.replace(path)
    assert transport.book_fingerprint("crypto") != before


def test_record_and_source_timestamp_use_same_open_file(monkeypatch, tmp_path):
    import json
    import os
    path = tmp_path / "book.json"
    path.write_text('{"old":1}')
    os.utime(path, (100, 100))
    original = json.load
    def replacing_load(source):
        replacement = path.with_suffix(".new")
        replacement.write_text('{"new":2}')
        os.utime(replacement, (200, 200))
        replacement.replace(path)
        return original(source)
    monkeypatch.setattr(transport.json, "load", replacing_load)
    doc, error, epoch = transport.read_book(path)
    assert doc == {"old": 1} and error is None and epoch == 100
    assert path.stat().st_mtime == 200


def test_stock_fixed_stop_is_bound_to_exact_position_lifecycle():
    position = {"opened_at": "2026-10-31T00:00:00Z"}
    registry = {"levels": {"trb:X_LONG": {"sig": "oa1793404800000000", "level": 95}}}
    assert transport.stock_fixed_stop_context(position, "trb:X_LONG", registry)["fixed_stop_price"] == 95
    registry["levels"]["trb:X_LONG"]["sig"] = "2026-10-30T00:00:00Z"
    assert transport.stock_fixed_stop_context(position, "trb:X_LONG", registry) == {}
