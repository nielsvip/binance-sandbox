"""Focused regression for 2026-09-09 P0 protections: 72h hold deadlock, emergency DC, gap reentry, broker-sync demand."""
import config_tradier
import tradier_manage
import v12_quick_engine

def test_tradier_min_hold_directional():
    cfg = config_tradier.TradierConfig()
    assert cfg.TRADIER_MIN_HOLD_MINUTES == 240.0, "hold must be 240m not 4320m (72h deadlock)"
    assert cfg.TRADIER_MIN_HOLD_MINUTES_SHORT == 60.0, "shorts must be 60m"

def test_gap_risk_enabled_by_default():
    cfg = config_tradier.TradierConfig()
    assert cfg.GAP_RISK_EXIT_ENABLED is True
    assert cfg.GAP_RISK_EXIT_SHORT_ENABLED is True
    assert cfg.GAP_RISK_EXIT_LONG_ENABLED is True
    assert cfg.GAP_RISK_REENTRY_ENABLED is True
    assert cfg.GAP_RISK_REENTRY_MAX_DAYS == 5

def test_gap_risk_quickconfig_parity():
    qc = v12_quick_engine.QuickConfig()
    assert qc.GAP_RISK_EXIT_ENABLED is True
    assert qc.GAP_RISK_REENTRY_ENABLED is True
    assert qc.GAP_RISK_REENTRY_MAX_DAYS == 5

def test_broker_sync_is_logical_demand():
    # BROKER_SYNC_STRICT must not exist as switchable, caps are logical demands
    assert not hasattr(config_tradier.TradierConfig(), "BROKER_SYNC_STRICT_FILTER_ENABLED"), "BROKER_SYNC_STRICT must be removed (logical demand, not switch)"
    cfg = config_tradier.TradierConfig()
    assert cfg.BROKER_SYNC_MAX_ADOPT_VALUE_USD == 2000.0
    assert cfg.BROKER_SYNC_MAX_TOTAL_VALUE_USD == 2500.0

def test_wt3m_capped_to_hard_max():
    cfg = config_tradier.TradierConfig()
    assert cfg.WT_3M_FORCE_OPEN_TARGET_USD == 2500.0
    assert cfg.WT_3M_FORCE_OPEN_SIZE_USD == 1200.0
    assert cfg.WT_3M_FORCE_OPEN_MAX_TRADES_PER_DAY == 4

def test_tradier_manage_has_gap_reentry_pending():
    # tradier_manage must have gap reentry state and broker-sync + exchange blocks
    src = open("tradier_manage.py").read()
    assert "_gap_reentry_pending" in src
    assert "BROKER_SYNC LOGICAL DEMAND" in src
    assert "EXCHANGE_OPEN_ORDER_BLOCK" in src
    assert "_wf_hard_cap" in src
    assert "_wf_dir" in src
