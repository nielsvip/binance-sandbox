# DEPLOY DECISION: APPLIED
_UTC_ 2026-04-27T18:21:25.186733+00:00

## Selected config
- pool_sharpe: 0.7052
- sym_sharpe: 0.7805
- avg_gain_trade: 0.4692%
- gain_per_yr: 8031.2%
- gain_sym_yr: 70.4487
- trades: 73,899
- max_dd_pct: 23.84%
- worker: w_explore4_80004 iter: 32

## Backup
- /Users/niels/Documents/binance/backups/before_AUTO_DEPLOY_202604271821.py

## Overrides applied (33)
- DC_RECOVERY_EXIT_ENABLED = True
- DC_RECOVERY_EXIT_TOLERANCE_PCT = 0.0625
- MIN_POSITION_SIZE = 62.5
- RZ_BOT_BB_THRESHOLD = 0.375
- BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE = -0.037500000000000006
- ALIGNMENT_GATE_TOTAL = 36
- ATR_ADAPTIVE_SIZING_TARGET_PCT = 1.5
- ATR_ADAPTIVE_STOP_ENABLED = True
- DELTA_GATE_HEDGE_OPEN = True
- DELTA_GATE_SBA = True
- DELTA_GATE_VOL_SPIKE = False
- DELTA_REENTRY_REQUIRE_NOT_EXITING = False
- MI_ENTRY_ENABLED_TRADIER = False
- MOM3_LONG_THRESHOLD = -0.75
- REENTRY2_STOCH_CROSS_ENABLED = False
- REGIME_ATR_RATIO_MIN = 0.125
- REGIME_RANGING_NOLOSS_MIN = 0.07500000000000001
- REGIME_RANGING_STALE_HOURS = 12.0
- REGIME_RANGING_WT_REDUCE_FRAC_MED = 0.8999999999999999
- SATOSHIT_EXIT_ENABLED = True
- SATOSHIT_EXIT_PARTIAL_PCT = 0.4375
- SATOSHIT_LONG_MFI_MAX_TRADIER = 120.0
- SATOSHIT_SHORT_MFI_MIN_TRADIER = 62.5
- STOCH_CROSS_ENTRY_TRADIER = True
- TRADIER_RSI_SHORT_REL_VOLUME_MIN = 2.4
- TRADIER_STOCH_ENTRY_SHORT_TRADIER = 52
- BB_SQUEEZE_ENABLED = True
- BB_RSI_STOCH_SCALP_ENABLED = True
- BB_RSI_STOCH_SCALP_SCORE = 12
- BOUNCE_AUGMENT_K_D_THRESHOLD = 45.0
- PARTIAL_PROFIT_LOCK_FRAC_TRADIER = 0.625
- DD_KELLY_ENABLED = True
- SQUEEZE_FIRE_BONUS_SCORE = 11.25

## Overrides skipped (not in live config) (25)
- ENTRY_SCORE_THRESHOLD: key not found in config_tradier.py (skipped — would be dead)
- ALL_TF_BRAKE_ENABLED: key not found in config_tradier.py (skipped — would be dead)
- ALL_TF_BRAKE_MIN_TFS: key not found in config_tradier.py (skipped — would be dead)
- DC_MOMENT_OPPOSE_THRESHOLD: key not found in config_tradier.py (skipped — would be dead)
- MFI_ENTRY_LONG_MAX: key not found in config_tradier.py (skipped — would be dead)
- REENTRY_PULL1_ENABLED: key not found in config_tradier.py (skipped — would be dead)
- REENTRY_PULL4_ENABLED: key not found in config_tradier.py (skipped — would be dead)
- HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: key not found in config_tradier.py (skipped — would be dead)
- AUGMENT_WT_4H_REQUIRE_HIGHER_WT: key not found in config_tradier.py (skipped — would be dead)
- FH_MOMENTUM_WINDOW_SEC: key not found in config_tradier.py (skipped — would be dead)
- MFI_SHORT_THRESHOLD_D: key not found in config_tradier.py (skipped — would be dead)
- WT_VEL_DECAY_EXIT_ENABLED: key not found in config_tradier.py (skipped — would be dead)
- STRENGTH_MIN_SCORE: key not found in config_tradier.py (skipped — would be dead)
- WT_VEL_MTF_EXIT_THRESHOLD: key not found in config_tradier.py (skipped — would be dead)
- WT_ALIGN_EXIT_MIN: key not found in config_tradier.py (skipped — would be dead)
- DC_POS_EXIT_THRESHOLD: key not found in config_tradier.py (skipped — would be dead)
- ADAPTIVE_EXIT_TFS_GAIN_PCT: key not found in config_tradier.py (skipped — would be dead)
- LE_TIER_SIZING_ENABLED: key not found in config_tradier.py (skipped — would be dead)
- K1H_RISING_LONG_MAX: key not found in config_tradier.py (skipped — would be dead)
- DYNAMIC_SCORE_AUGMENT_MIN_JUMP: key not found in config_tradier.py (skipped — would be dead)
- PARTIAL_EXIT_FRAC: key not found in config_tradier.py (skipped — would be dead)
- PARTIAL_TRAIL_ARM_PCT: key not found in config_tradier.py (skipped — would be dead)
- PARTIAL_PROFIT_LOCK_USE_MAKER: key not found in config_tradier.py (skipped — would be dead)
- RATIO_SENTIMENT_SHORT_MAX: key not found in config_tradier.py (skipped — would be dead)
- TRADEABLE_PRECOMPUTED_GATE_ENABLED: key not found in config_tradier.py (skipped — would be dead)

## Sync
{
  "s1-int": "ok",
  "s2-int": "ok"
}

## md5
{
  "local": "f01c4f6ab60d06f8c27871c8ffd2cf5e",
  "s1-int": "f01c4f6ab60d06f8c27871c8ffd2cf5e",
  "s2-int": "f01c4f6ab60d06f8c27871c8ffd2cf5e"
}

## Original overrides_json
```json
{"ENTRY_SCORE_THRESHOLD": 10.0, "DC_RECOVERY_EXIT_ENABLED": true, "DC_RECOVERY_EXIT_TOLERANCE_PCT": 0.0625, "ALL_TF_BRAKE_ENABLED": true, "ALL_TF_BRAKE_MIN_TFS": 5, "MIN_POSITION_SIZE": 62.5, "RZ_BOT_BB_THRESHOLD": 0.375, "DC_MOMENT_OPPOSE_THRESHOLD": 60.0, "MFI_ENTRY_LONG_MAX": 15.0, "REENTRY_PULL1_ENABLED": true, "REENTRY_PULL4_ENABLED": true, "HEDGE_CLOSE_REMOVE_FROM_TRADEABLE": false, "AUGMENT_WT_4H_REQUIRE_HIGHER_WT": true, "FH_MOMENTUM_WINDOW_SEC": 7200, "MFI_SHORT_THRESHOLD_D": 60.0, "WT_VEL_DECAY_EXIT_ENABLED": true, "STRENGTH_MIN_SCORE": 2.5, "WT_VEL_MTF_EXIT_THRESHOLD": -0.75, "WT_ALIGN_EXIT_MIN": 4, "DC_POS_EXIT_THRESHOLD": 2.0999999999999996, "ADAPTIVE_EXIT_TFS_GAIN_PCT": 2.5, "BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE": -0.037500000000000006, "ALIGNMENT_GATE_TOTAL": 36, "ATR_ADAPTIVE_SIZING_TARGET_PCT": 1.5, "ATR_ADAPTIVE_STOP_ENABLED": true, "DELTA_GATE_HEDGE_OPEN": true, "DELTA_GATE_SBA": true, "DELTA_GATE_VOL_SPIKE": false, "DELTA_REENTRY_REQUIRE_NOT_EXITING": false, "MI_ENTRY_ENABLED_TRADIER": false, "MOM3_LONG_THRESHOLD": -0.75, "REENTRY2_STOCH_CROSS_ENABLED": false, "REGIME_ATR_RATIO_MIN": 0.125, "REGIME_RANGING_NOLOSS_MIN": 0.07500000000000001, "REGIME_RANGING_STALE_HOURS": 12.0, "REGIME_RANGING_WT_REDUCE_FRAC_MED": 0.8999999999999999, "SATOSHIT_EXIT_ENABLED": true, "SATOSHIT_EXIT_PARTIAL_PCT": 0.4375, "SATOSHIT_LONG_MFI_MAX_TRADIER": 120.0, "SATOSHIT_SHORT_MFI_MIN_TRADIER": 62.5, "STOCH_CROSS_ENTRY_TRADIER": true, "TRADIER_RSI_SHORT_REL_VOLUME_MIN": 2.4, "TRADIER_STOCH_ENTRY_SHORT_TRADIER": 52, "BB_SQUEEZE_ENABLED": true, "BB_RSI_STOCH_SCALP_ENABLED": true, "BB_RSI_STOCH_SCALP_SCORE": 12, "BOUNCE_AUGMENT_K_D_THRESHOLD": 45.0, "LE_TIER_SIZING_ENABLED": true, "K1H_RISING_LONG_MAX": 45.0, "DYNAMIC_SCORE_AUGMENT_MIN_JUMP": 18.75, "PARTIAL_EXIT_FRAC": 0.25, "PARTIAL_TRAIL_ARM_PCT": 0.26249999999999996, "PARTIAL_PROFIT_LOCK_USE_MAKER": false, "PARTIAL_PROFIT_LOCK_FRAC_TRADIER": 0.625, "RATIO_SENTIMENT_SHORT_MAX": 90.0, "TRADEABLE_PRECOMPUTED_GATE_ENABLED": true, "DD_KELLY_ENABLED": true, "SQUEEZE_FIRE_BONUS_SCORE": 11.25}
```