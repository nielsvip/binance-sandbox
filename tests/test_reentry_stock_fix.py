"""Stock reentry fix validation — mirrors crypto fix for Tradier."""
import config_tradier

def test_stock_thresholds_relaxed():
    assert config_tradier.TradierConfig.TRADIER_REENTRY_HARDCOOL_MIN == 15.0
    assert config_tradier.TradierConfig.REENTRY_STOCH_K_MAX_LONG == 80.0
    assert config_tradier.TradierConfig.REENTRY_STOCH_K_MIN_SHORT == 20.0
    assert config_tradier.TradierConfig.REENTRY_GOLDEN_BLOCK_ENABLED is False

def test_stock_price_cross_not_blocked_by_tiny_k():
    # Simulate tradier price_cross_back confirmation with moderate k=60 (was blocked at 40)
    import config_tradier as cfg
    from vec_decisions.guaranteed_price_cross_reentry import reentry_confirmation_gate
    ind = {"wt1_3m": 10, "wt2_3m": -10, "wt1_5m": 10, "wt2_5m": -10, "wt1_15m": -5, "wt2_15m": 5,
           "wt1_1h": 10, "wt2_1h": -10, "stoch_k_15m": 60, "stoch_k_15m_prev": 58, "stoch_k_1h": 55,
           "ha_4h": "neutral", "dc_basis_4h": 0, "basis_4h": 0, "high_5m": 101, "high_5m_prev": 100, "low_5m": 99, "low_5m_prev": 98}
    # Use tradier config as cfg object (has 80 threshold)
    ok, _ = reentry_confirmation_gate(ind, True, cfg.TradierConfig, 100, exit_price=99)
    assert ok is True, "stock moderate LONG k=60 should PASS with 80 threshold"

def test_stock_golden_block_disabled():
    assert config_tradier.TradierConfig.REENTRY_GOLDEN_BLOCK_ENABLED is False
