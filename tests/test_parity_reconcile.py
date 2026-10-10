"""TOTAL PARITY broker reconciler: missing broker fills appended once, logged fills matched."""
import json
import time
from tools.forward_parity import live_guardian as lg


def test_reconcile_appends_only_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "ROOT", tmp_path)
    hdir = tmp_path / "data" / "history" / "fin"
    hdir.mkdir(parents=True)
    now = time.time()
    rec = {"ts": __import__("datetime").datetime.fromtimestamp(now - 600, tz=__import__("datetime").timezone.utc).isoformat(), "type": "AUGMENT", "qty": 3.8, "price": 1.4068, "value": 5.35, "reason": "B12 |VEC_EXACT", "indicators": {}}
    (hdir / "XRPUSDC_LONG.jsonl").write_text(json.dumps(rec) + "\n")

    def fake_fetch(sym, start_ms):
        assert sym == "XRPUSDC"
        return [
            {"time": int((now - 600) * 1000), "qty": "3.8", "price": "1.4068", "side": "BUY", "positionSide": "LONG", "orderId": 1},
            {"time": int((now - 300) * 1000), "qty": "3.8", "price": "1.4100", "side": "SELL", "positionSide": "LONG", "orderId": 2},
        ]

    out = lg.reconcile_account("fin", fake_fetch, ["XRPUSDC"], now)
    assert out == {"symbols": 1, "broker_fills": 2, "matched": 1, "appended": 1}
    lines = (hdir / "XRPUSDC_LONG.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    added = json.loads(lines[1])
    assert added["type"] == "REDUCE" and "BROKER_RECONCILED" in added["reason"] and added["qty"] == 3.8
    out2 = lg.reconcile_account("fin", fake_fetch, ["XRPUSDC"], now)
    assert out2["appended"] == 0 and out2["matched"] == 2
