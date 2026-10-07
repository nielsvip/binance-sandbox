"""tools/v15_trade_parity: round-trip folding (vec + scalar event shapes), matching, verdict, gap keys."""
from tools import v15_trade_parity as TP


def test_round_trips_fold_augment_reduce_and_open_tail():
    ev = [{"ts": 0, "type": "OPEN", "reason": "B12 x"}, {"ts": 900, "type": "AUGMENT", "reason": "UAG"},
          {"ts": 1800, "type": "REDUCE", "reason": "PPL_TP", "pnl_pct": 0.5}, {"ts": 2700, "type": "CLOSE", "reason": "DAYTRADE_TARGET dc", "pnl_pct": 1.0},
          {"ts": 3600, "type": "OPEN", "reason": "HARDCODED_RALLY_REENTRY"}]
    t = TP.round_trips(ev)
    assert len(t) == 2 and t[0]["n_aug"] == 1 and t[0]["n_red"] == 1 and abs(t[0]["pnl_pct"] - 1.5) < 1e-9
    assert t[1]["exit_ts"] is None


def test_scalar_shape_augment_and_reduce_actions():
    ev = [{"ts": 0, "type": "OPEN", "action": "OPEN", "reason": "WT_DC"}, {"ts": 60, "type": "OPEN", "action": "AUGMENT", "reason": "aug"},
          {"ts": 120, "type": "CLOSE", "action": "REDUCE", "reason": "r", "pnl_pct": 0.2}, {"ts": 180, "type": "CLOSE", "action": "CLOSE", "reason": "DC_STOP", "pnl_pct": -0.4}]
    t = TP.round_trips(ev)
    assert len(t) == 1 and t[0]["n_aug"] == 1 and t[0]["n_red"] == 1 and t[0]["exit_reason"] == "DC_STOP"


def test_match_and_verdict():
    v = TP.round_trips([{"ts": 0, "type": "OPEN", "reason": "A"}, {"ts": 900, "type": "CLOSE", "reason": "X"},
                        {"ts": 5000, "type": "OPEN", "reason": "HARDCODED_RALLY_REENTRY"}, {"ts": 6000, "type": "CLOSE", "reason": "Y", "pnl_pct": 2.0}])
    lv = TP.round_trips([{"ts": 300, "type": "OPEN", "action": "OPEN", "reason": "A"}, {"ts": 900, "type": "CLOSE", "action": "CLOSE", "reason": "X2"}])
    m = TP.match(v, lv, 1800)
    s = TP.summarize(v, lv, m, 0.8)
    assert s["matched"] == 1 and s["vec_match_rate"] == 0.5 and s["live_match_rate"] == 1.0 and s["verdict"] == "FAIL"
    assert any(k.startswith("VEC_ONLY entry:HARDCODED_RALLY_REENTRY") for k in s["gaps"])


def test_price_pnl_with_augment_and_reduce_long_and_short():
    ev = [{"ts": 0, "type": "OPEN", "qty": 1, "price": 100}, {"ts": 60, "type": "OPEN", "action": "AUGMENT", "qty": 1, "price": 110},
          {"ts": 120, "type": "CLOSE", "action": "REDUCE", "qty": 1, "price": 115}, {"ts": 180, "type": "CLOSE", "action": "CLOSE", "qty": 1, "price": 100}]
    t = TP.round_trips(ev, True)[0]
    # avg 105, peak notional 210; realised +10 then -5 = +5 -> 2.381%
    assert abs(t["pnl_px_pct"] - 5 / 210 * 100) < 1e-3 and TP.trip_pnl(t) == t["pnl_px_pct"]
    s = TP.round_trips([{"ts": 0, "type": "OPEN", "qty": 2, "price": 50}, {"ts": 60, "type": "CLOSE", "qty": 2, "price": 45}], False)[0]
    assert abs(s["pnl_px_pct"] - 10.0) < 1e-9
    assert TP.round_trips(ev)[0]["pnl_px_pct"] is None


def test_live_events_drop_other_side(tmp_path):
    live = {"execution_ledger": [{"ts": 0, "type": "OPEN", "position_key": "ang:X_LONG"}, {"ts": 1, "type": "OPEN", "position_key": "ang:X_SHORT"},
                                 {"ts": 2, "type": "CLOSE", "position_key": ""}]}
    ev, n = TP.live_events(live, "LONG")
    assert n == 1 and len(ev) == 2


def test_aggregate_uses_one_md5_set(tmp_path):
    import json
    base = {"verdict": "FAIL", "gaps": {"VEC_ONLY entry:B11": {"n": 2, "pnl_pct": 1.0}}}
    (tmp_path / "A_LONG_trade_parity.json").write_text(json.dumps(base | {"symside": "A_LONG", "engine_md5": {"set": "s1"}}))
    (tmp_path / "B_LONG_trade_parity.json").write_text(json.dumps(base | {"symside": "B_LONG", "engine_md5": {"set": "s1"}}))
    (tmp_path / "C_LONG_trade_parity.json").write_text(json.dumps(base | {"symside": "C_LONG", "engine_md5": {"set": "old"}}))
    r = TP.aggregate(str(tmp_path))
    assert r["md5_set"] == "s1" and r["register"][0]["n_symsides"] == 2 and r["register"][0]["n"] == 4
