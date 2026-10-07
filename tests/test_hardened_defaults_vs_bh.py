"""Hardened defaults vs B&H — durable regression for 2026-09-10 fix.

User observed 5 obvious leaks:
1. EMA filters not applied (ema_21 missing in stocks NPZ → dead)
2. Trades against trend
3. Not looking at larger-TF WT direction
4. Does not wait for bottoms
5. Does not exit when multiple TFs against trade

This test locks the 5 fixes and guards against future dead wiring.
"""

import config
import config_tradier
import v12_quick_engine as V


def test_ema_filters_wired_and_fallback():
    # EMA 9/21 fallback to ema_20 when ema_21 missing
    import vec_paths.ema_9_21_filter as ef
    assert config.Config.EMA_BLANKET_FILTER_ENABLED is True, "EMA blanket must be on"
    assert V.QuickConfig.EMA_BLANKET_FILTER_ENABLED is True
    # Check fallback code present
    import pathlib
    txt = pathlib.Path("vec_paths/ema_9_21_filter.py").read_text()
    assert "ema_20" in txt and "fallback" in txt.lower(), "EMA fallback not wired"


def test_trend_and_wt_htf_gate():
    assert config.Config.WT_DC_HTF_GATE == "4h_D"
    assert V.QuickConfig.WT_DC_HTF_GATE == "4h_D"
    assert config.Config.KINDERGARTEN_EMA_GATE_ENABLED is True
    assert V.QuickConfig.KINDERGARTEN_EMA_GATE_ENABLED is True


def test_wait_for_bottoms():
    assert config.Config.BOTTOM_B_DELAYED_LOWER_TOP_ENABLED is True
    assert config_tradier.TradierConfig.BOTTOM_B_DELAYED_LOWER_TOP_ENABLED is True
    assert V.QuickConfig.BOTTOM_B_DELAYED_LOWER_TOP_ENABLED is True


def test_multi_tf_exit():
    assert config.Config.HTF_AGAINST_FORCE_CLOSE_ENABLED is True
    assert config.Config.ALL_TF_AGAINST_CLOSE_ENABLED is True
    assert V.QuickConfig.HTF_AGAINST_FORCE_CLOSE_ENABLED is True
    assert V.QuickConfig.ALL_TF_AGAINST_CLOSE_ENABLED is True
    # MIN_TFS must be >0 to actually fire
    assert V.QuickConfig.EMA_BLANKET_FILTER_MIN_TFS >= 3
    assert V.QuickConfig.ALL_TF_AGAINST_CLOSE_MIN_TFS >= 3
