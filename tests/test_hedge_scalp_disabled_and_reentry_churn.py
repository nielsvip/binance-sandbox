"""Regression: hedge+scalp disabled everywhere, reentry (RECOVERY_AUGMENT) works without excessive churn."""
import config
import config_tradier
import v12_quick_engine
import tradier_manage
import ez_manage as ezm  # imported for gate inspection, not execution

def test_hedge_disabled_crypto():
    cfg = config.Config()
    assert cfg.HEDGE_MODE is False, "HEDGE_MODE must be False (OFF LIMITS per 2026-08-18)"
    assert cfg.HEDGE_MODE_TRADIER is False if hasattr(cfg, "HEDGE_MODE_TRADIER") else True  # crypto uses HEDGE_MODE
    assert cfg.OBLIGATORY_HEDGE_PCT == 0.0
    assert cfg.HEDGE_BANDAID_OFF_ENABLED is False
    assert cfg.HEDGE_DUAL_IF_HEDGE_MODE is False
    assert cfg.WT_15M_SAME_HEDGE_ENABLED is False
    assert cfg.HEDGE_CLOSE_SCALP_MODE is False, "HEDGE_CLOSE_SCALP_MODE must be False when hedge+scalp disabled"

def test_hedge_disabled_tradier():
    cfg = config_tradier.TradierConfig()
    assert cfg.HEDGE_MODE_TRADIER is False
    assert getattr(cfg, "SCALP_V3_ENABLED", False) is False

def test_scalp_disabled_everywhere():
    cfg = config.Config()
    assert cfg.SCALP_MODE is False
    assert cfg.SCALP_V3_ENABLED is False
    cfg_t = config_tradier.TradierConfig()
    assert getattr(cfg_t, "SCALP_V3_ENABLED", False) is False
    qc = v12_quick_engine.QuickConfig()
    assert qc.SCALP_MODE is False
    assert qc.SCALP_V3_ENABLED is False
    assert qc.HEDGE_MODE is False
    assert qc.HEDGE_MODE_TRADIER is False
    assert qc.HEDGE_CLOSE_SCALP_MODE is False

def test_recovery_augment_enabled_without_churn():
    cfg = config.Config()
    cfg_t = config_tradier.TradierConfig()
    qc = v12_quick_engine.QuickConfig()
    # must be ON (reentry, not augment)
    assert cfg.RECOVERY_AUGMENT_ENABLED is True
    assert cfg_t.RECOVERY_AUGMENT_ENABLED is True
    assert qc.RECOVERY_AUGMENT_ENABLED is True
    # REQUIRE WT 5m+15m bull/bear — prevents reentering when wt going down (k<80 alone not enough)
    assert cfg.RECOVERY_AUGMENT_REQUIRE_WT_CROSS is True
    assert cfg_t.RECOVERY_AUGMENT_REQUIRE_WT_CROSS is True
    assert qc.RECOVERY_AUGMENT_REQUIRE_WT_CROSS is True
    # band/size/age must be bounded to prevent churn
    assert cfg.RECOVERY_AUGMENT_BAND_PCT == 0.3
    assert cfg_t.RECOVERY_AUGMENT_BAND_PCT == 1.0
    assert qc.RECOVERY_AUGMENT_BAND_PCT == 1.0
    assert cfg.RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE is True
    assert cfg_t.RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE is True
    # anti-churn: max 8 trades/sym/day, one-fire, 240m window
    assert cfg.TRADES_PER_SYM_PER_DAY_MAX == 8
    assert cfg_t.TRADES_PER_SYM_PER_DAY_MAX == 8
    assert qc.TRADES_PER_SYM_PER_DAY_MAX == 8
    # cooldowns: REENTRY_COOLDOWN_S 60 is aggressive but capped by TRADES_PER_SYM
    assert cfg.REENTRY_COOLDOWN_S == 60.0
    assert cfg.TRADIER_POST_CLOSE_COOLDOWN_MIN == 15.0

def test_tradier_manage_gates_match_config():
    src = open("tradier_manage.py").read()
    # hedge gate reads config_tradier
    assert "_cfg_auto('HEDGE_MODE_TRADIER'" in src
    assert "_cfg_auto('SCALP_MODE'" in src
    # recovery is reentry, not augment
    assert "RECOVERY_AUGMENT — PARTIAL-CLOSE RECOVERY REENTRY" in src
    assert "_is_reentry = action in ('REENTRY', 'REENTRY_OPEN') or 'REENTRY' in (reason or '').upper() or 'RECOVERY_AUG' in" in src
    assert "_is_recovery_aug_exec" in src
    assert "HARD_MIN_GAIN_WALL" in src and "not _is_recovery_aug_exec" in src
    # WT rising LONG / falling SHORT — wt1>prev vs wt1<prev, k>=k_prev or d with htf backup
    assert "RECOVERY_AUGMENT_REQUIRE_WT_CROSS', True" in src
    assert "WT15_" in src and ("RISING" in src or "FALLING" in src)
    assert "k>=k_prev or d with htf backup" in src
    # ensure no short 80 / long 20 inversion in this gate
    assert "REENTRY_STOCH_K_MIN_SHORT', 80" not in src or "_ra_gate_ok = (_k5 > float(_cfg_auto('REENTRY_STOCH_K_MIN_SHORT', 80" not in src

def test_ez_manage_gates_match_config():
    src = open("ez_manage.py").read()
    assert "config.HEDGE_MODE" in src
    assert "SCALP_V3_ENABLED" in src
    # hedge blocked when config false
    assert 'config.HEDGE_MODE and "HEDGE" in reason.upper()' in src or 'not config.HEDGE_MODE' in src
