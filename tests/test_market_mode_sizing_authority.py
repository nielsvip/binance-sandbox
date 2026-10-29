"""OPERATOR 2026-10-29: START_POSITION_SIZE / MAX_POSITION_SIZE / MAX_ORDER_VALUE are owned by MARKET_MODE in ez_manage /
tradier_manage; TEMPLATE files have no say. The evaluator lays this venue's config-instance sizing over the config AFTER the
template layer and AFTER per-sym overrides, each venue reading only its own config module."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import v12_quick_engine as V  # noqa: E402
from tools.opt import evaluate_v12 as E  # noqa: E402


def test_crypto_sizing_comes_from_config_instance_not_sheet_or_set():
    import config as C
    inst = C.Config()
    cfg = V.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    cfg.START_POSITION_SIZE = 25.0  # a sheet bold / published-set value
    cfg.MAX_POSITION_SIZE = 780.0
    n = E._apply_market_mode_sizing(cfg, "crypto")
    assert n >= 3
    assert cfg.START_POSITION_SIZE == float(inst.START_POSITION_SIZE)
    assert cfg.MAX_POSITION_SIZE == float(inst.MAX_POSITION_SIZE)
    assert cfg.MAX_ORDER_VALUE == float(inst.MAX_ORDER_VALUE)
    assert cfg._MARKET_MODE_SIZING.startswith("config.py:Config[")


def test_stocks_sizing_comes_from_tradier_config_only():
    import config_tradier as CT
    inst = CT.TradierConfig()
    cfg = V.QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.START_POSITION_SIZE = 500.0  # the old hard-coded sweep value
    E._apply_market_mode_sizing(cfg, "tradier")
    assert cfg.START_POSITION_SIZE == float(inst.START_POSITION_SIZE)
    assert cfg.MAX_ORDER_VALUE == float(inst.MAX_ORDER_VALUE)
    assert cfg._MARKET_MODE_SIZING.startswith("config_tradier.py:TradierConfig[")


def test_venues_never_share_a_number_source():
    a, la = E._market_mode_sizing("crypto")
    b, lb = E._market_mode_sizing("tradier")
    assert la.split("[")[0] != lb.split("[")[0]
    assert set(a) >= {"START_POSITION_SIZE", "MAX_POSITION_SIZE", "MAX_ORDER_VALUE"}
    assert set(b) >= {"START_POSITION_SIZE", "MAX_POSITION_SIZE", "MAX_ORDER_VALUE"}
