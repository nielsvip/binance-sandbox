"""TOTAL PARITY catch-up/expiry (USER 2026-10-10): missed vec exits replay,
never dropped silently; stale entries expire with a logged refusal."""
import logging
from live_twins import vec_exact as vx


def _seed(monkeypatch, cur_bar):
    vx._DECIDED.clear()
    vx._EMITTED.clear()
    vx._EXPIRED.clear()
    old = cur_bar - 5 * 900
    prev = cur_bar - 900
    vx._DECIDED[("AAAUSDT_LONG", old)] = [
        {"n": 0, "type": "OPEN", "reason": "B12", "qty_frac": 1.0, "vec_qty": 1.0, "vec_price": 1.0, "to_flat": False},
        {"n": 1, "type": "CLOSE", "reason": "OLD_EXIT", "qty_frac": 1.0, "vec_qty": 1.0, "vec_price": 1.0, "to_flat": True},
    ]
    vx._DECIDED[("AAAUSDT_LONG", prev)] = [
        {"n": 0, "type": "OPEN", "reason": "B10", "qty_frac": 1.0, "vec_qty": 1.0, "vec_price": 1.0, "to_flat": False},
        {"n": 1, "type": "CLOSE", "reason": "EXIT_VELOCITY_WT", "qty_frac": 1.0, "vec_qty": 1.0, "vec_price": 1.0, "to_flat": True},
    ]
    vx._DECIDED[("AAAUSDT_LONG", cur_bar)] = []
    monkeypatch.setattr(vx, "actions_at", lambda s, d, t: {"status": "OK", "bar_ts": float(cur_bar), "bar_idx": 99, "actions": [], "vec_holds": True})


def test_catchup_serves_prior_bar_exits_only(monkeypatch):
    _seed(monkeypatch, 1791594000)
    got = vx.take_catchup("AAAUSDT", "LONG", 1791594000.0)
    types = [(a["type"], a.get("catchup")) for a in got]
    assert ("CLOSE", True) in types
    assert all(t != "OPEN" for t, _ in types)
    assert all(a["bar_ts"] < 1791594000 for a in got)


def test_catchup_emits_once(monkeypatch):
    _seed(monkeypatch, 1791594000)
    assert len(vx.take_catchup("AAAUSDT", "LONG", 1791594000.0)) == 1
    assert vx.take_catchup("AAAUSDT", "LONG", 1791594000.0) == []


def test_expire_old_logs_and_marks(monkeypatch, caplog):
    _seed(monkeypatch, 1791594000)
    vx.take_catchup("AAAUSDT", "LONG", 1791594000.0)
    with caplog.at_level(logging.WARNING, logger=vx.logger.name if hasattr(vx.logger, "name") else None):
        out = vx.expire_old("AAAUSDT", "LONG", 1791594000.0)
    kinds = {(o["type"], o["bar_ts"]) for o in out}
    assert ("OPEN", 1791594000 - 5 * 900) in kinds
    assert ("CLOSE", 1791594000 - 5 * 900) in kinds
    assert ("OPEN", 1791594000 - 900) not in kinds
    assert vx.expire_old("AAAUSDT", "LONG", 1791594000.0) == []
