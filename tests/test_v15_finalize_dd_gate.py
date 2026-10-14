"""USER 2026-10-14: no xlsx may finalize with <10 trades or >30% max DD (DD is never a BEST_EFFORT near-miss)."""
import v15_pilot as P

OK = {"valid": True, "trades": 40, "tim_pct": 35.0, "gain_pct": 12.0, "max_dd_pct": 8.0}


def test_qualifies_within_spec():
    assert P._qualifies_30d(OK, 5.0) == (True, [])


def test_dd_over_limit_never_finalizes():
    v = dict(OK, max_dd_pct=30.5)
    ok, r = P._qualifies_30d(v, 5.0)
    assert not ok and any(x.startswith("DD ") for x in r)
    assert P._nearmiss_30d(v, 5.0) == (False, [])


def test_dd_unknown_fails_closed():
    v = dict(OK)
    v.pop("max_dd_pct")
    assert P._qualifies_30d(v, 5.0)[0] is False
    assert P._nearmiss_30d(v, 5.0) == (False, [])


def test_trades_floor_still_enforced():
    ok, r = P._qualifies_30d(dict(OK, trades=9), 5.0)
    assert not ok and any("trades" in x for x in r)
    assert P._nearmiss_30d(dict(OK, trades=9), 5.0) == (False, [])


def test_tim_only_miss_still_near_miss():
    assert P._nearmiss_30d(dict(OK, tim_pct=12.0), 5.0)[0] is True
