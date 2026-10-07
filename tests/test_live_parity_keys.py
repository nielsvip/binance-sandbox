import numpy as np
import pandas as pd

import live_parity_keys as K


def _g(i, k, d=0):  # the live consumer's accessor (ez_manage.evaluate_multi_tf_exit)
    return float(i.get(k, d) or d)


def test_alias_keys():
    ind = {"wt_peak_15m": 55.5, "wt_trough_15m": -40.0, "ha_15m": "red", "ha_1h": "green", "ha_4h": None, "wt_peak_4h": None}
    K.derive_alias_keys(ind)
    assert ind["wt_peak_value_15m"] == 55.5 and ind["wt_trough_value_15m"] == -40.0
    assert "wt_peak_value_4h" not in ind and "ha_green_4h" not in ind
    assert ind["ha_green_15m"] == -1.0 and ind["ha_green_1h"] == 1.0


def test_consumer_semantics_with_falsy_quirk():
    ind = {"ha_15m": "red", "ha_1h": "green", "ha_4h": "doji"}
    K.derive_alias_keys(ind)
    long_pts = sum(1 for tf in ("15m", "1h", "4h") if _g(ind, f"ha_green_{tf}", 0.5) < 0.5)
    short_pts = sum(1 for tf in ("15m", "1h", "4h") if _g(ind, f"ha_green_{tf}", 0.5) > 0.5)
    assert (long_pts, short_pts) == (1, 1)  # red counts for a long exit, green for a short exit, neutral for neither


def _df(n=300, seed=1):
    r = np.random.default_rng(seed)
    c = 100 + np.cumsum(r.normal(0, 1, n))
    return pd.DataFrame({"open": c - r.normal(0, .3, n), "high": c + abs(r.normal(0, .8, n)), "low": c - abs(r.normal(0, .8, n)), "close": c, "volume": abs(r.normal(1000, 100, n))})


def test_bar_keys_match_reference_formulas():
    df = _df()
    out = {}
    K.derive_bar_keys(df, "1h", out)
    c = df["close"]
    assert abs(out["close_3bar_1h"] - c.iloc[-3:].mean()) < 1e-9
    assert abs(out["close_5bar_1h"] - c.iloc[-5:].mean()) < 1e-9
    e9, e21 = c.ewm(span=9, adjust=False).mean().iloc[-1], c.ewm(span=21, adjust=False).mean().iloc[-1]
    assert out["ema_9_above_21_1h"] == int(e9 > e21)
    assert abs(out["volume_sma_1h"] - df["volume"].iloc[-20:].mean()) < 1e-9
    assert "choppiness_4h" not in out


def test_choppiness_only_4h_and_bounded():
    out = {}
    K.derive_bar_keys(_df(), "4h", out)
    assert 0.0 <= out["choppiness_4h"] <= 100.0 and "volume_sma_1h" not in out


def test_never_raises():
    assert K.derive_bar_keys(None, "1h", {}) == {}
    assert K.derive_bar_keys(pd.DataFrame({"close": [1.0]}), "1h", {}) == {}
    assert K.derive_alias_keys(None) is None
