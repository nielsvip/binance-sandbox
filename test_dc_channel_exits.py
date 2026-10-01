"""Unit tests for vec_decisions.dc_channel_exits (2026-09-29 grey-switch rewire).

Run: python test_dc_channel_exits.py  (no NPZ, no network, no live process)."""
import vec_decisions.dc_channel_exits as X


def _get(d):
    return lambda k, dflt: d.get(k, dflt)


def test_parse():
    assert X.parse_tf_list("OFF") == [] and X.parse_tf_list(None) == [] and X.parse_tf_list("") == []
    assert X.parse_tf_list("15m,1h") == ["15m", "1h"] and X.parse_tf_list("15m+1h|4h 15m") == ["15m", "1h", "4h"]
    assert X.parse_tf_list("5m") == ["3m"]


def test_defaults_inert():
    import config, config_tradier
    c, t = config.Config(), config_tradier.TradierConfig()
    for cfg in (c, t):
        s, g = X.resolve_daytrade_dc(lambda k, d: getattr(cfg, k, d))
        assert s == [] and [x["tf"] for x in g] == ["15m"], (s, g)  # DEF2 2026-10-01: user PROFIT_TARGET = dc_high_15m-0.1% default ON (stop stays OFF)
        assert X.wt_lower_cross_tf(getattr(cfg, "WT_LOWER_CROSS_EXIT_TF")) is None


def test_list_path():
    s, g = X.resolve_daytrade_dc(_get({"DAYTRADE_DC_STOP_TF": "15m,1h", "DAYTRADE_DC_TARGET_TF": "1h"}))
    lv = {"dc_low_15m": 100.0, "dc_low_1h": 98.0, "dc_high_1h": 110.0, "dc_high_15m": 105.0}
    f, r = X.daytrade_dc_exit(99.74, True, s, g, lv.get)
    assert f and r == "DAYTRADE_STOP dc_15m_low -0.25% STOP_BUF", r
    f, r = X.daytrade_dc_exit(99.8, True, s, g, lv.get)
    assert not f
    f, r = X.daytrade_dc_exit(109.9, True, s, g, lv.get)
    assert f and r == "DAYTRADE_TARGET dc_1h_high -0.10% TARGET_BUF", r
    f, r = X.daytrade_dc_exit(110.28, False, s, g, lambda k: {"dc_high_15m": 110.0, "dc_high_1h": 120.0, "dc_low_1h": 90.0}.get(k, 0.0))
    assert f and r == "DAYTRADE_STOP dc_15m_high +0.25% STOP_BUF", r


def test_alias_path_matches_tradier_live():
    s, g = X.resolve_daytrade_dc(_get({"DC_DAYTRADE_STOP_USE_DC_15M": True, "DC_DAYTRADE_STOP_USE_DC4_15M": True, "TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M": True}))
    assert [x["tag"] for x in s] == ["dc4_15m", "dc_15m"] and [x["tag"] for x in g] == ["dc_15m"]
    lv = {"dc_low4_15m": 100.0, "dc_low_15m": 99.0, "dc_high_15m": 105.0}
    f, r = X.daytrade_dc_exit(99.5, True, s, [], lv.get)
    assert f and r.startswith("DT_DC_4_15M_STOP"), r
    f, r = X.daytrade_dc_exit(99.5, True, s, [], {"dc_low_15m": 99.0}.get)
    assert not f
    f, r = X.daytrade_dc_exit(98.9, True, s, [], lambda k: {"dc_low_15m": 99.0}.get(k, 0.0))
    assert f and r.startswith("DT_DC_15M_STOP"), r
    f, r = X.daytrade_dc_exit(104.85, True, [], g, lv.get)
    assert f and r.startswith("DT_DC_15M_TARGET"), r
    s2, g2 = X.resolve_daytrade_dc(_get({"DAYTRADE_DC_STOP_TF": "1h", "DC_DAYTRADE_STOP_USE_DC_15M": True}))
    assert [x["tag"] for x in s2] == ["dc_1h"]
    _, g3 = X.resolve_daytrade_dc(_get({"DC_DAYTRADE_TARGET_USE_DC_15M": True, "TRADIER_DC_DAYTRADE_TARGET_DC_BUFFER_PCT": 0.0005}))
    assert abs(g3[0]["buf"] - 0.0005) < 1e-12


def test_wt_lower_cross():
    assert X.wt_lower_cross_fires(-5, 2, 3, 1, 99, 100, True)
    assert not X.wt_lower_cross_fires(-5, 2, 3, 1, 101, 100, True)
    assert not X.wt_lower_cross_fires(-5, 2, -1, 1, 99, 100, True)
    assert X.wt_lower_cross_fires(5, 2, 1, 3, 101, 100, False)
    assert not X.wt_lower_cross_fires(0, 2, 1, 3, 101, 100, False)
    ind = {"wt1_1h": -5, "wt2_1h": 2, "wt1_1h_prev": 3, "wt2_1h_prev": 1, "close_15m_prev": 100}
    assert X.wt_lower_cross_live(ind, "1h", 99, True)
    assert not X.wt_lower_cross_live({k: v for k, v in ind.items() if k != "wt1_1h_prev"}, "1h", 99, True)


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_"):
            _f()
            print("PASS", _n)
