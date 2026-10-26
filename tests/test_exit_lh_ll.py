import numpy as np

from vec_decisions.exit_lh_ll import blocked_mask, enabled_timeframes, live_pass


TFS = ("15m", "1h", "4h", "D")


def _bars(n=3):
    bars = {}
    for tf in TFS:
        bars[f"wt_peak_structure_{tf}"] = np.zeros(n, dtype=np.int8)
        bars[f"wt_trough_structure_{tf}"] = np.zeros(n, dtype=np.int8)
        bars[f"low_{tf}"] = np.full(n, 100.0)
        bars[f"low_{tf}_prev"] = np.full(n, 100.0)
        bars[f"high_{tf}"] = np.full(n, 100.0)
        bars[f"high_{tf}_prev"] = np.full(n, 100.0)
    return bars


def _safe(npz, key, n, default):
    return np.asarray(npz.get(key, np.full(n, default)))


def _row(bars, index):
    return {key: value[index].item() for key, value in bars.items()}


def test_timeframe_switches_are_independent_and_legacy_is_15m():
    for tf in TFS:
        flags = {name: name == tf for name in TFS}
        assert enabled_timeframes(flags) == (tf,)
    assert enabled_timeframes({name: False for name in TFS}) == ()
    assert enabled_timeframes({name: False for name in TFS}, True) == ("15m",)
    assert enabled_timeframes({"1h": True}, True) == ("15m", "1h")


def test_each_timeframe_lh_pass_matches_live_and_vector():
    for tf in TFS:
        bars = _bars()
        bars[f"wt_peak_structure_{tf}"][1] = -1
        blocked = blocked_mask(bars, 3, (tf,), True, _safe)
        assert blocked.tolist() == [True, False, True]
        assert live_pass(_row(bars, 1), (tf,), True)
        assert not live_pass(_row(bars, 0), (tf,), True)


def test_each_timeframe_forming_lower_low_pass_matches_live_and_vector():
    for tf in TFS:
        bars = _bars()
        bars[f"low_{tf}"][1] = 99.0
        blocked = blocked_mask(bars, 3, (tf,), True, _safe)
        assert blocked.tolist() == [True, False, True]
        assert live_pass(_row(bars, 1), (tf,), True)


def test_short_higher_low_pass_and_or_across_timeframes():
    bars = _bars()
    bars["wt_trough_structure_4h"][1] = 1
    blocked = blocked_mask(bars, 3, ("15m", "4h"), False, _safe)
    assert blocked.tolist() == [True, False, True]
    assert live_pass(_row(bars, 1), ("15m", "4h"), False)


def test_disabled_switches_do_not_block():
    bars = _bars()
    bars["wt_peak_structure_15m"][1] = -1
    blocked = blocked_mask(bars, 3, (), True, _safe)
    assert not blocked.any()
    assert live_pass(_row(bars, 0), (), True)
