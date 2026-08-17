from types import SimpleNamespace

import numpy as np

from v8_completed_snapshot_adapter import inject_completed_snapshot_v8


def _store():
    arrays = {}
    for tf in ("5m", "15m", "1h", "4h", "D"):
        arrays[f"timestamp_{tf}"] = np.array([100, 200, 200], dtype=np.int64)
        for offset, stem in enumerate((
            "open", "high", "low", "close", "stoch_k", "stoch_d",
            "wt1", "wt2", "dc_high", "dc_low", "bb_upper", "bb_lower", "atr",
        ), start=1):
            arrays[f"{stem}_{tf}"] = np.array(
                [10.0 + offset, 20.0 + offset, 20.0 + offset]
            )
        for offset, stem in enumerate(
            ("high", "low", "stoch_k", "stoch_d", "dc_high", "dc_low"),
            start=1,
        ):
            arrays[f"{stem}_{tf}_prev"] = np.array(
                [1.0 + offset, 2.0 + offset, 2.0 + offset]
            )
        arrays[f"wt_cross_{tf}"] = np.array([0, 1, 0], dtype=np.int8)
    return SimpleNamespace(arrays=arrays)


def test_exact_v8_snapshot_ignores_generic_mark_and_uses_distinct_parent_identity():
    store = _store()
    indicators = {
        "current_price": 999.0,
        "mark_price": 999.0,
        "close_15m": 999.0,
        "dc_low_15m": 999.0,
    }
    omissions = inject_completed_snapshot_v8(
        store, 2, indicators, enabled=True, decision_ts=300
    )
    assert omissions == {}
    assert indicators["_completed_source_ts_15m"] == 200
    assert indicators["_completed_source_ts_15m_prev"] == 100
    assert indicators["_completed_close_15m"] == 24.0
    assert indicators["_completed_dc_low_15m"] == 30.0
    assert indicators["_completed_dc_low_15m_prev"] == 8.0
    assert indicators["_completed_dc_low_15m"] != indicators["mark_price"]
    assert indicators["_completed_wt1_15m_prev"] == 17.0
    assert indicators["_completed_wt_cross_15m"] == "NONE"


def test_v8_snapshot_omits_whole_timeframe_instead_of_falling_back():
    store = _store()
    del store.arrays["atr_4h"]
    indicators = {"atr_4h": 999.0, "current_price": 999.0}
    omissions = inject_completed_snapshot_v8(
        store, 2, indicators, enabled=True, decision_ts=300
    )
    assert "4h" in omissions
    assert not any(key.startswith("_completed_") and key.endswith("_4h")
                   for key in indicators)
    assert indicators["atr_4h"] == 999.0


def test_v8_snapshot_master_off_is_a_noop():
    indicators = {"current_price": 999.0}
    assert inject_completed_snapshot_v8(
        _store(), 2, indicators, enabled=False, decision_ts=300
    ) == {}
    assert indicators == {"current_price": 999.0}
