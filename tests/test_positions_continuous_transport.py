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
