import sys

import numpy as np

sys.path.insert(0, "tests")

import v12_quick_engine as V
from test_tradier_reviewed_exit_ledgers import _base_npz, _cfg
from vec_decisions.tradier_signal_score import paired_lower_tf_indicators


def test_paired_lower_tf_stock_score_inputs_use_15m_only_when_ltf_is_off():
    source = {"k_5m": 80.0, "k_15m": 20.0, "dc_low_5m_prev": 101.0, "dc_low_15m_prev": 99.0}
    assert paired_lower_tf_indicators(source, False) == {
        "k_5m": 20.0,
        "k_15m": 20.0,
        "dc_low_5m_prev": 99.0,
        "dc_low_15m_prev": 99.0,
    }
    assert paired_lower_tf_indicators(source, True) == source


def _mq_crossing_fixture(enabled):
    npz = _base_npz()
    n = len(npz["close"])
    fire_bar = 201
    values = {
        "k_15m": 50.0,
        "d_15m": 50.0,
        "k_5m": 20.0,
        "d_5m": 30.0,
        "k_4h": 50.0,
        "d_4h": 45.0,
        "mfi_4h": 41.0,
        "mfi_1h": 55.0,
        "mfi_15m": 20.0,
        "bb_width_1h": 6.0,
        "bb_width_4h": 9.0,
        "adx_1h": 18.0,
        "adx_4h": 24.0,
        "relative_volume_1h": 0.9,
        "rsi_1h": 55.0,
        "rsi_4h": 55.0,
    }
    for key, value in values.items():
        column = np.full(n, 50.0)
        column[fire_bar] = value
        npz[key] = column
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"stoch_k_{tf}"] = np.full(n, 60.0)
        npz[f"stoch_d_{tf}"] = np.full(n, 50.0)
    cfg = _cfg()
    cfg.LTF_SUB15M_TRADING_ENABLED = False
    cfg.MARKET_QUALITY_SCORE_ENABLED_TRADIER = enabled
    cfg.ENTRY_SCORE_THRESHOLD = 8.0
    cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False
    cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
    cfg.RSI_ENTRY_LONG_TRADIER = 42.0
    cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
    cfg.WT_COMPOSITE_SCORING_ENABLED = False
    cfg.BEAR_MARKET_MODE_TRADIER = False
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
    for key in (
        "TR_ADX4H_GATE_ENABLED",
        "TR_BBWIDTH4H_GATE_ENABLED",
        "TR_CHOP4H_GATE_ENABLED",
        "TR_MFI4H_LONG_ENABLED",
        "TR_DCWIDTH4H_SHORT_ENABLED",
        "WT_15M_BOUNCE_OPEN_ENABLED",
        "WT_SIMPLE_GUARANTEE_ENABLED",
    ):
        setattr(cfg, key, False)
    return npz, cfg, fire_bar


def test_native_score_fallback_changes_simulate_one_open_bar_with_market_quality():
    off_npz, off_cfg, fire_bar = _mq_crossing_fixture(False)
    on_npz, on_cfg, _ = _mq_crossing_fixture(True)
    off = V.simulate_one(off_npz, "SYNTH", True, off_cfg)
    on = V.simulate_one(on_npz, "SYNTH", True, on_cfg)
    off_opens = [row for row in off["ledger"] if row.get("type") == "OPEN"]
    on_opens = [row for row in on["ledger"] if row.get("type") == "OPEN"]
    assert off_opens == []
    assert len(on_opens) == 1
    assert on_opens[0]["bar"] == fire_bar
    assert "MQ_GOOD" in on_opens[0]["reason"]
