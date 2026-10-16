"""V17 2026-10-13 AUGMENT_INTO_REENTRY native wiring: rule per knob (live_twins/reentry_augment.py = twin of v12_quick_engine
_air_carry_mult/_open), crypto ez_manage._air_native_carry, stocks tradier_manage._air_native_carry_t, exactly-one-path, and
that every surface (execute_now x2, backtest_v12_engine seams, QuickConfig) carries the call/field."""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import reentry_augment as RA  # noqa: E402

T0 = 1_760_000_000.0


def _get(**over):
    d = dict(RA.DEFAULTS)
    d["AUGMENT_INTO_REENTRY_ENABLED"] = True
    d.update(over)
    return lambda k, default=None: d.get(k, default)


def _log(entry=100.0, close_ts=T0, augmented=False, extra=()):
    log = [{"ts": close_ts - 3600, "action": "OPEN", "price": entry, "reason": "X"}]
    if augmented:
        log.append({"ts": close_ts - 1800, "action": "AUGMENT", "price": entry * 1.05, "reason": "A"})
    log.append({"ts": close_ts - 900, "action": "REDUCE", "price": entry, "reason": "R"})
    log.append({"ts": close_ts, "action": "CLOSE", "price": entry * 0.99, "reason": "C"})
    return log + list(extra)


def test_long_higher_low_carries_and_lower_does_not():
    d = RA.decide(_get(), True, 101.0, T0 + 600, _log(), 10.0)
    assert d["add"] == pytest.approx(10.0) and d["qty"] == pytest.approx(20.0) and d["mult"] == pytest.approx(1.0)
    assert RA.decide(_get(), True, 99.0, T0 + 600, _log(), 10.0)["add"] == 0.0


def test_short_lower_high_mirror():
    assert RA.decide(_get(), False, 99.0, T0 + 600, _log(), 10.0)["add"] == pytest.approx(10.0)
    assert RA.decide(_get(), False, 101.0, T0 + 600, _log(), 10.0)["add"] == 0.0


def test_require_higher_low_off_carries_either_side():
    g = _get(REENTRY_AUGMENT_REQUIRE_HIGHER_LOW=False)
    assert RA.decide(g, True, 50.0, T0 + 600, _log(), 10.0)["add"] > 0
    assert RA.decide(g, False, 500.0, T0 + 600, _log(), 10.0)["add"] > 0


def test_window_sec_limit_and_zero_means_no_limit():
    assert RA.decide(_get(REENTRY_AUGMENT_WINDOW_SEC=3600.0), True, 101.0, T0 + 3601, _log(), 10.0)["add"] == 0.0
    assert RA.decide(_get(REENTRY_AUGMENT_WINDOW_SEC=3600.0), True, 101.0, T0 + 3600, _log(), 10.0)["add"] > 0
    assert RA.decide(_get(REENTRY_AUGMENT_WINDOW_SEC=0.0), True, 101.0, T0 + 10 * 86400, _log(), 10.0)["add"] > 0


def test_no_prior_augment_on_off():
    assert RA.decide(_get(), True, 101.0, T0 + 60, _log(augmented=True), 10.0)["add"] == 0.0
    assert RA.decide(_get(REENTRY_AUGMENT_REQUIRE_NO_PRIOR_AUGMENT=False), True, 101.0, T0 + 60, _log(augmented=True), 10.0)["add"] > 0


def test_hl_ref_entry_vs_low():
    bars = [(T0 - 3600, 97.0, 103.0), (T0 - 2700, 95.0, 104.0), (T0 - 900, 96.0, 102.0), (T0, 98.0, 101.0), (T0 + 900, 50.0, 200.0)]
    g_low = _get(REENTRY_AUGMENT_HL_REF="low")
    # entry ref 100: px 98 is not a higher low; low ref = min low while held = 95 -> 98 IS a higher low
    assert RA.decide(_get(), True, 98.0, T0 + 60, _log(), 10.0, bars_loader=lambda: bars)["add"] == 0.0
    assert RA.decide(g_low, True, 98.0, T0 + 60, _log(), 10.0, bars_loader=lambda: bars)["add"] > 0
    # SHORT 'low' = max high while held = 104 (the bar after the close is excluded)
    assert RA.decide(g_low, False, 103.5, T0 + 60, _log(), 10.0, bars_loader=lambda: bars)["add"] > 0
    assert RA.decide(g_low, False, 104.5, T0 + 60, _log(), 10.0, bars_loader=lambda: bars)["add"] == 0.0
    # no bars -> falls back to the entry price (vec behaviour)
    assert RA.decide(g_low, True, 98.0, T0 + 60, _log(), 10.0, bars_loader=lambda: None)["add"] == 0.0


def test_size_mult_cap_whole_shares_and_max_augments():
    assert RA.decide(_get(REENTRY_AUGMENT_SIZE_MULT=0.5), True, 101.0, T0 + 60, _log(), 10.0)["qty"] == pytest.approx(15.0)
    assert RA.decide(_get(), True, 101.0, T0 + 60, _log(), 10.0, cap_usd=1515.0)["qty"] == pytest.approx(15.0)
    assert RA.decide(_get(), True, 101.0, T0 + 60, _log(), 10.0, cap_usd=500.0)["add"] == 0.0  # cap never shrinks qty0
    assert RA.decide(_get(REENTRY_AUGMENT_SIZE_MULT=0.05), True, 101.0, T0 + 60, _log(), 10.0, whole=True)["add"] == 0.0  # 10.5 -> 10 shares
    assert RA.decide(_get(MAX_AUGMENTS_PER_POSITION=0), True, 101.0, T0 + 60, _log(), 10.0)["add"] == 0.0


def test_fail_closed_lifecycles():
    # open while the log says open (missed close) -> no completed previous trade
    log = [{"ts": T0 - 100, "action": "OPEN", "price": 100.0}, {"ts": T0 - 50, "action": "OPEN", "price": 100.0}]
    assert RA.prev_trade(log) is None
    # stocks broker re-detection is not a new lifecycle
    log = _log()[:1] + [{"ts": T0 - 3599, "type": "OPEN", "price": 100.2, "reason": "BROKER_SYNC_OPEN_FROM_FLAT_STUB"}] + _log()[1:]
    assert RA.prev_trade(log)["entry_price"] == 100.0
    # REDUCE to flat without CLOSE -> unknown -> no carry
    assert RA.prev_trade([{"ts": T0 - 100, "action": "OPEN", "price": 100.0}, {"ts": T0, "action": "REDUCE", "price": 99.0}]) is None
    # ISO timestamps (tracker trade_log) parse
    iso = [{"ts": "2026-10-13T10:00:00+00:00", "action": "OPEN", "price": 1.0}, {"ts": "2026-10-13T10:15:00+00:00", "action": "CLOSE", "price": 1.0}]
    assert RA.prev_trade(iso)["close_ts"] == pytest.approx(RA.epoch("2026-10-13T10:15:00Z"))


def test_carried_open_counts_as_augment_and_is_never_carried_twice():
    log = _log() + [{"ts": T0 + 60, "action": "OPEN", "price": 101.0, "reason": "REENTRY_X" + RA.CARRY_TAG + "1.000"}]
    assert RA.current_carried(log) is True
    assert RA.is_carried("REENTRY_X |AIR_CARRY1.000") and RA.is_vec_sized("X |VEC_EXACT")


def test_ez_manage_native_carry(monkeypatch):
    import ez_manage as E

    knobs = {"AUGMENT_INTO_REENTRY_ENABLED": True, "MAX_POSITION_SIZE": 0.0}
    monkeypatch.setattr(E, "_psym_get", lambda s, sd, k, d: knobs.get(k, d))
    monkeypatch.setattr(E.config, "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED", True, raising=False)
    q, r, i = E._air_native_carry("AAAUSDT", "LONG", 101.0, 10.0, "REENTRY_PRICE_CROSS", T0 + 60, _log())
    assert q == pytest.approx(20.0) and r.endswith(" |AIR_CARRY1.000") and i["applied"]
    assert E._air_native_carry("AAAUSDT", "LONG", 101.0, 10.0, "OPEN_X |VEC_EXACT", T0 + 60, _log())[0] == 10.0  # vec twin sized it
    assert E._air_native_carry("AAAUSDT", "LONG", 101.0, 20.0, r, T0 + 60, _log())[0] == 20.0  # never twice
    monkeypatch.setattr(E.config, "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED", False, raising=False)
    assert E._air_native_carry("AAAUSDT", "LONG", 101.0, 10.0, "REENTRY_PRICE_CROSS", T0 + 60, _log())[0] == 10.0
    monkeypatch.setattr(E.config, "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED", True, raising=False)
    knobs["AUGMENT_INTO_REENTRY_ENABLED"] = False
    assert E._air_native_carry("AAAUSDT", "LONG", 101.0, 10.0, "REENTRY_PRICE_CROSS", T0 + 60, _log())[0] == 10.0


def test_tradier_native_carry(monkeypatch):
    import tradier_manage as TM

    knobs = {"AUGMENT_INTO_REENTRY_ENABLED": "True", "MAX_SYMBOL_VALUE_TRADIER": 0.0, "HARD_MAX_SYMBOL_VALUE_TRADIER": 1e9}
    monkeypatch.setattr(TM, "_cfg", lambda k, d=None, a=None, s=None, sd=None, **kw: knobs.get(k, d))
    monkeypatch.setattr(TM.config, "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED", True, raising=False)
    monkeypatch.setattr(TM.config, "TRB_MAX_SYMBOL_VALUE", 1e9, raising=False)
    q, r, i = TM._air_native_carry_t("trb", "AAA", "SHORT", 99.0, 7.0, "REENTRY_MONITOR", T0 + 60, _log())
    assert q == 14.0 and " |AIR_CARRY" in r
    # the hard symbol cap binds on the carried size (held 0, $1000 -> 10 shares @ 99)
    knobs["HARD_MAX_SYMBOL_VALUE_TRADIER"] = 1000.0
    assert TM._air_native_carry_t("trb", "AAA", "SHORT", 99.0, 7.0, "REENTRY_MONITOR", T0 + 60, _log())[0] == 10.0
    assert TM._air_native_carry_t("trb", "AAA", "SHORT", 99.0, 7.0, "OPEN |VEC_EXACT", T0 + 60, _log())[0] == 7.0


def test_every_surface_is_wired():
    import ez_manage as E
    import tradier_manage as TM
    import v12_quick_engine as V

    src_ez = inspect.getsource(E.MultiAccountTradeManager.execute_now)
    assert "_air_native_carry(" in src_ez and "_air_trade_log(self, position_key)" in src_ez
    # 2026-10-15: the execute_trade_action carry-count call is gone with MAX_AUGMENTS_PER_POSITION (USER "delete the
    # switch completely"). It existed only to make a carried open count as augment #1 against that cap, so
    # reentry_augment.current_carried now has no production caller; the unit tests above still pin its behaviour.
    src_tm = inspect.getsource(TM.TradierTradeManager.execute_now)
    assert "_air_native_carry_t(" in src_tm and "_air_history_log(" in src_tm
    bt = (ROOT / "backtest_v12_engine.py").read_text()
    assert bt.count("_air_ledger_record(") >= 3 and "ez_manage._air_native_carry(" in bt
    assert "ez_manage._AIR_BARS_LOADER = _air_npz_bars_loader(stores)" in bt and "tm_mod._AIR_LOG_LOADER" in bt
    q = V.QuickConfig()
    for k in RA.KEYS + (RA.LIVE_KEY,):
        assert hasattr(q, k), k
    assert q.AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED is True
    vx = (ROOT / "live_twins" / "vec_exact.py").read_text()
    assert "carry_mult" in vx and "carry_live_on" in vx
