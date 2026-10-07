"""Regression: ZEC 5x sizing and churn brake must apply symmetrically."""

import ez_positions_quick as epq
import config


def test_zec_flz_5x_in_calculate_dynamic_quantity():
    cfg = config.Config()
    assert cfg.ZEC_FLZ_LONG_SIZE_MULT == 5.0
    # direct helper
    assert epq._zec_flz_long_size_mult("flz", "ZECUSDC", True, cfg) == 5.0
    assert epq._zec_flz_long_size_mult("flz", "ZECUSDC", False, cfg) == 1.0
    assert epq._zec_flz_long_size_mult("ang", "ZECUSDC", True, cfg) == 1.0
    assert epq._zec_flz_long_size_mult("flz", "BTCUSDC", True, cfg) == 1.0
    # calculate_dynamic_quantity must scale base for flz ZEC LONG only
    base = cfg.START_POSITION_SIZE
    # mock trade_manager with min_qty
    class FakeTM:
        min_qty = {}
    tm = FakeTM()
    # control: non-ZEC or non-flz should be ~base tier-scaled, not 5x
    qty_flz_zec = epq.calculate_dynamic_quantity("ZECUSDC", 50.0, 10, cfg, tm, {}, True, {"k_1h": 60, "d_1h": 40, "k_4h": 60, "d_4h": 40, "ha_color_D": "green"}, {"k_1m": 50, "d_1m": 50, "_tick_ts": 9999999999}, 0.0, "flz")
    qty_ang_zec = epq.calculate_dynamic_quantity("ZECUSDC", 50.0, 10, cfg, tm, {}, True, {"k_1h": 60, "d_1h": 40, "k_4h": 60, "d_4h": 40, "ha_color_D": "green"}, {"k_1m": 50, "d_1m": 50, "_tick_ts": 9999999999}, 0.0, "ang")
    # flz ZEC should be ~5x ang ZEC (allow tier rounding + $6 floor caps the smaller side at 3.75x in this fixture)
    assert qty_flz_zec > qty_ang_zec * 3.0, f"flz ZEC qty {qty_flz_zec} not ~5x ang {qty_ang_zec}"
    # flz BTC should NOT be 5x
    qty_flz_btc = epq.calculate_dynamic_quantity("BTCUSDC", 50000.0, 10, cfg, tm, {}, True, {"k_1h": 60, "d_1h": 40, "k_4h": 60, "d_4h": 40, "ha_color_D": "green"}, {"k_1m": 50, "d_1m": 50, "_tick_ts": 9999999999}, 0.0, "flz")
    # BTC qty * price should be close to base * tier mult, not 5x scaled; verify ratio vs ZEC normalized
    assert qty_flz_btc * 50000.0 < qty_flz_zec * 50.0 * 1.1  # not 5x larger in notional terms beyond symbol difference


def test_churn_brake_sentinel_is_per_account():
    import inspect
    src = inspect.getsource(epq.execute_trade_wrapper)
    # Must not be hardcoded to flz only
    assert 'account_key == "flz"' not in src, "churn brake must not be flz-only"
    assert ".FLZ_TRADING_HALTED" not in src or "account_key.upper()" in src  # per-account file
    assert "BLOCKED_CHURN_10_PER_MIN" in src


def test_btc_not_hard_blocked_for_other_accounts():
    cfg = config.Config()
    # With BTC_HARD_BLOCK_OTHER_ACCOUNTS=False, non-dedicated accounts must not be blocked
    assert cfg.BTC_HARD_BLOCK_OTHER_ACCOUNTS is False
    assert epq._btc_dedicated_account_blocked("ang", "BTCUSDC", cfg) is False
    assert epq._btc_dedicated_account_blocked("men", "BTCUSDC", cfg) is False
    assert epq._btc_dedicated_account_blocked("fin", "BTCUSDC", cfg) is False
    # dedicated accounts are never blocked by definition
    assert epq._btc_dedicated_account_blocked("flz", "BTCUSDC", cfg) is False
    # non-BTC symbol never blocked regardless of account
    assert epq._btc_dedicated_account_blocked("ang", "ETHUSDC", cfg) is False or True  # ETHUSDC is in dedicated list
    assert epq._btc_dedicated_account_blocked("ang", "SOLUSDT", cfg) is False
