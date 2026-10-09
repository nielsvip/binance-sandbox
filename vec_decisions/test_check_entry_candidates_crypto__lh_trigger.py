"""test_check_entry_candidates_crypto__lh_trigger — scalar<->vec parity + AGLD-shape regression (INV-0001)."""
import numpy as np

from vec_decisions import check_entry_candidates_crypto__lh_trigger as LH


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _cfg(**kw):
    d = {"ENTRY_LH_TRIGGER_ENABLED": True, "ENTRY_LH_TRIGGER_LOOKBACK_BARS": 96, "ENTRY_LH_TRIGGER_DC_THRESHOLD_PCT": 0.5}
    d.update(kw)
    return Cfg(**d)


def test_scalar_vec_parity_both_sides():
    rng = np.random.default_rng(11)
    n = 1500
    high = rng.uniform(0.5, 3.0, n)
    low = high - rng.uniform(0.01, 0.3, n)
    dch = np.maximum.accumulate(high) * 1.001
    dcl = np.minimum.accumulate(low) * 0.999
    for is_long in (True, False):
        for lb, pct in ((96, 0.5), (48, 0.5), (96, 2.0)):
            cfg = _cfg(ENTRY_LH_TRIGGER_LOOKBACK_BARS=lb, ENTRY_LH_TRIGGER_DC_THRESHOLD_PCT=pct)
            vec = LH.check_lh_trigger_entry_vec(cfg, high, low, dch, dcl, is_long)
            assert vec.dtype == bool and vec.shape == (n,)
            assert not vec[: lb - 1].any()
            for i in range(0, n, 53):
                ind = {"high": high[: i + 1].tolist(), "low": low[: i + 1].tolist(), "dc_high_1h": dch[i], "dc_low_1h": dcl[i]}
                fire, reason = LH.check_lh_trigger_entry(cfg, ind, is_long)
                assert fire == bool(vec[i]), (is_long, lb, pct, i)
                assert (reason == "") != fire


def test_default_off_inert_strict_edges_and_warmup():
    ind = {"high": [3.0] * 95 + [2.0], "low": [1.0] * 96, "dc_high_1h": 3.5, "dc_low_1h": 0.5}
    assert LH.check_lh_trigger_entry(Cfg(), ind, False) == (False, "")
    cfg = _cfg()
    assert LH.check_lh_trigger_entry(cfg, {"high": [2.0] * 96, "low": [1.0] * 96, "dc_high_1h": 3.0}, False)[0] is False
    assert LH.check_lh_trigger_entry(cfg, {"high": [1.0] * 10, "low": [1.0] * 10, "dc_high_1h": 3.0}, False)[0] is False
    assert LH.check_lh_trigger_entry(cfg, {"high": [0.0] * 96, "low": [0.0] * 96, "dc_high_1h": 3.0}, False)[0] is False


def test_agld_shape_fires_short_after_top_not_at_top():
    cfg = _cfg()
    top = 0.2292
    highs = [0.20] * 90 + [0.21, 0.22, 0.225, 0.228, 0.2292, 0.2280, 0.2265, 0.2270, 0.2240]
    lows = [h - 0.002 for h in highs]
    fires = []
    for i in range(95, len(highs)):
        ind = {"high": highs[: i + 1], "low": lows[: i + 1], "dc_high_1h": top, "dc_low_1h": 0.19}
        fires.append(LH.check_lh_trigger_entry(cfg, ind, False)[0])
    assert fires[0] is False
    assert fires[1] is True
    assert LH.check_lh_trigger_entry(cfg, {"high": highs, "low": lows, "dc_high_1h": top, "dc_low_1h": 0.19}, True)[0] is False
    retest = {"high": [0.20] * 95 + [top], "low": [0.19] * 96, "dc_high_1h": top * 1.001, "dc_low_1h": 0.19}
    assert LH.check_lh_trigger_entry(cfg, retest, False)[0] is False
