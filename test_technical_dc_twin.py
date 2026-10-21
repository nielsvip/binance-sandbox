"""TECHNICAL-DC live-twin parity (2026-10-04 wiring mandate).

Proves vec_decisions.dc_channel_exits.resolve_technical_dc / technical_dc_exit
reproduce v12_quick_engine EXIT_STRUCTURAL TECHNICAL_DC semantics exactly:
  STOP   LONG px<=dc_low_TF*(1-buf)  / SHORT px>=dc_high_TF*(1+buf)
  TARGET LONG px>=dc_high_TF*(1-buf) / SHORT px<=dc_low_TF*(1+buf)
multi-TF OR, 5m->3m alias, OFF = inert.
Known delta (documented, not tested): vec shifts the STOP channel one bar back
when DC_PRIOR_BAR_CHANNEL=True; live uses the current indicator value.
"""
import vec_decisions.dc_channel_exits as X


def _get(d):
    return lambda k, default: d.get(k, default)


def test_off_is_inert():
    s, t = X.resolve_technical_dc(_get({}))
    assert s == [] and t == []
    s, t = X.resolve_technical_dc(_get({"TECHNICAL_DC_STOP_TF": "OFF", "TECHNICAL_DC_TARGET_TF": ""}))
    assert s == [] and t == []
    assert X.technical_dc_exit(100.0, True, [], [], lambda f: 0.0) == (False, "")


def test_stop_long_short():
    s, t = X.resolve_technical_dc(_get({"TECHNICAL_DC_STOP_TF": "15m"}))
    assert len(s) == 1 and t == []
    assert s[0]["field_long"] == "dc_low_15m" and s[0]["field_short"] == "dc_high_15m"
    assert abs(s[0]["buf"] - 0.0025) < 1e-12
    lvl = lambda f: {"dc_low_15m": 100.0, "dc_high_15m": 110.0}.get(f, 0.0)
    assert X.technical_dc_exit(99.7, True, s, [], lvl)[0] is True
    assert X.technical_dc_exit(99.8, True, s, [], lvl)[0] is False
    assert X.technical_dc_exit(110.3, False, s, [], lvl)[0] is True
    assert X.technical_dc_exit(110.2, False, s, [], lvl)[0] is False


def test_target_long_short():
    s, t = X.resolve_technical_dc(_get({"TECHNICAL_DC_TARGET_TF": "1h"}))
    assert s == [] and len(t) == 1
    assert t[0]["field_long"] == "dc_high_1h" and t[0]["field_short"] == "dc_low_1h"
    assert abs(t[0]["buf"] - 0.001) < 1e-12
    lvl = lambda f: {"dc_high_1h": 100.0, "dc_low_1h": 90.0}.get(f, 0.0)
    assert X.technical_dc_exit(99.95, True, [], t, lvl)[0] is True
    assert X.technical_dc_exit(99.85, True, [], t, lvl)[0] is False
    assert X.technical_dc_exit(89.95, False, [], t, lvl)[0] is True
    assert X.technical_dc_exit(90.15, False, [], t, lvl)[0] is False


def test_target_crossing_long_short():
    """USER 2026-10-21 TECHNICAL parity: the vector books tech closes only on a
    FRESH cross (loop 22579-22696) + min_hold; the level-only twin fired every
    bar price sat at target (DOGE/XMR kills sat AT target ~breakeven)."""
    s, t = X.resolve_technical_dc(_get({"TECHNICAL_DC_TARGET_TF": "15m"}))
    assert len(t) == 1 and abs(t[0]["buf"] - 0.001) < 1e-12
    lvl = lambda f: {"dc_high_15m": 100.0, "dc_low_15m": 90.0}.get(f, 0.0)
    lvlp = lambda f: {"dc_high_15m": 100.0, "dc_low_15m": 90.0}.get(f, 0.0)
    # Sitting AT target with no fresh cross -> hold (the DOGE/XMR shape:
    # prev close 99.95 never went below the 99.90 band edge).
    assert X.dc_target_cross(100.0, 99.95, 100.0, 100.0, True, t[0]) is False
    assert X.technical_dc_exit_crossing(100.0, 99.95, True, [], t, lvl, lvlp) == (
        False,
        "",
    )
    # Fresh cross through the buffer band -> fire with the same reason string.
    fire, reason = X.technical_dc_exit_crossing(100.05, 99.85, True, [], t, lvl, lvlp)
    assert fire and reason.startswith("TECHNICAL_TARGET dc_15m_high")
    # Short mirror: prev above band, now at/below.
    lvl3 = lambda f: {"dc_high_15m": 110.0, "dc_low_15m": 90.0}.get(f, 0.0)
    lvl3p = lambda f: {"dc_high_15m": 110.0, "dc_low_15m": 90.5}.get(f, 0.0)
    assert X.dc_target_cross(89.95, 90.60, 90.0, 90.5, False, t[0]) is True
    assert X.dc_target_cross(89.95, 89.90, 90.0, 90.5, False, t[0]) is False
    # Missing prev data -> deny (fail-closed, like the vector's i-1 guard).
    assert X.dc_target_cross(100.05, 0.0, 100.0, 99.5, True, t[0]) is False
    assert X.dc_target_cross(100.05, 99.80, 0.0, 99.5, True, t[0]) is False


def test_multi_tf_or_and_alias():
    s, t = X.resolve_technical_dc(_get({"TECHNICAL_DC_STOP_TF": "15m, 5m", "TECHNICAL_DC_STOP_BUFFER_PCT": 0.25}))
    assert [x["tf"] for x in s] == ["15m", "3m"]
    lvl = lambda f: {"dc_low_15m": 100.0, "dc_low_3m": 200.0}.get(f, 0.0)
    assert X.technical_dc_exit(199.0, True, s, [], lvl)[0] is True
    assert X.technical_dc_exit(250.0, True, s, [], lvl)[0] is False


def test_stop_wins_over_target_and_reasons():
    s, t = X.resolve_technical_dc(_get({"TECHNICAL_DC_STOP_TF": "15m", "TECHNICAL_DC_TARGET_TF": "15m"}))
    lvl = lambda f: 100.0
    fire, reason = X.technical_dc_exit(50.0, True, s, t, lvl)
    assert fire and reason.startswith("TECHNICAL_STOP dc_15m_low")
    fire, reason = X.technical_dc_exit(150.0, True, s, t, lvl)
    assert fire and reason.startswith("TECHNICAL_TARGET dc_15m_high")


def test_vec_formula_agreement_grid():
    import random
    random.seed(7)
    for is_long in (True, False):
        for tf_raw in ("15m", "15m,1h", "5m", "OFF"):
            d = {"TECHNICAL_DC_STOP_TF": tf_raw, "TECHNICAL_DC_TARGET_TF": tf_raw}
            s, t = X.resolve_technical_dc(_get(d))
            for _ in range(200):
                px = random.uniform(50, 150)
                lv = {f"dc_low_{x}": random.uniform(50, 150) for x in ("15m", "1h", "3m")}
                lv.update({f"dc_high_{x}": random.uniform(50, 150) for x in ("15m", "1h", "3m")})
                lvl = lambda f, _lv=lv: _lv.get(f, 0.0)
                live_fire, _ = X.technical_dc_exit(px, is_long, s, t, lvl)
                tfs = [{"5m": "3m"}.get(p, p) for p in tf_raw.split(",") if p and p != "OFF"]
                exp = False
                for tf in tfs:
                    lo, hi = lv[f"dc_low_{tf}"], lv[f"dc_high_{tf}"]
                    if is_long:
                        if px <= lo * 0.9975 or px >= hi * 0.999:
                            exp = True
                    elif px >= hi * 1.0025 or px <= lo * 0.999:
                        exp = True
                assert live_fire == exp, (is_long, tf_raw, px)
