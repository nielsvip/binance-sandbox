# Promotion plan 20260929 (DRY RUN — nothing applied)
Driver = winsorized mean (each delta capped at +/-10.0pp so no single symbol drives a promotion); gate: score>0 AND pos_sym>=2 AND pos_sym/n>=0.05. raw_avg + median shown as cross-checks. Sizing params EXCLUDED from promotion (gain-inflation via quantity, not a real edge) except STDEV_SLOPE_SIZING.

## CRYPTO_LONG — 20 candidates (sizing_excluded: 2)
| kind | name | promote_value | score(winsor) | raw_avg | pos_sym/n | median |
|---|---|---|---|---|---|---|
| switch | UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED | False | +2.7987 | +54.7154 | 23/52 | +0.0000 |
| switch | STDEV_SLOPE_SIZING_4H_MAX | 1 | +1.9362 | +2.0978 | 2/8 | +0.0000 |
| switch | WT_15M_BOUNCE_OPEN_ENABLED | False | +1.3795 | +2.2642 | 24/54 | +0.0000 |
| switch | BB_SQUEEZE_WIDTH_PERCENTILE | 0.15 | +0.6908 | +1.0487 | 7/58 | +0.0000 |
| switch | WT_DC_ENTRY_THRESHOLD | 45 | +0.5278 | +0.7685 | 11/57 | +0.0000 |
| switch | MACD_ZERO_CROSS_SCORE | 0.5 | +0.4940 | +2.9079 | 4/49 | +0.0000 |
| switch | HIGH_GAIN_AUGMENTATION_MIN_SIZE | 50 | +0.4924 | +0.4924 | 4/49 | +0.0000 |
| switch | WT_PERCENTILE_EXIT_OS_D | 2.0 | +0.3351 | +0.5177 | 3/53 | +0.0000 |
| switch | HLR_REENTRY_MULT_D | 2.5 | +0.2703 | +0.3527 | 3/51 | +0.0000 |
| switch | WT_MOMENTUM_EXIT_THRESHOLD | 1 | +0.2454 | +0.2524 | 5/54 | +0.0000 |
| switch | STDEV_SLOPE_SIZING_ENABLED | False | +0.2219 | +0.2219 | 12/49 | +0.0000 |
| switch | MIN_HOLD_BARS_BEFORE_EXIT | 10 | +0.1905 | +2.6237 | 3/49 | +0.0000 |
| switch | WT_LOWER_CROSS_EXIT_TF | 1h | +0.1619 | +0.2579 | 27/49 | +0.0566 |
| switch | COOLDOWN_BARS | 0 | +0.1564 | +0.1564 | 4/49 | +0.0000 |
| switch | WT_4H_VEL_EXIT_REQUIRE_PROFIT | 2.0 | +0.1539 | +0.2747 | 3/50 | +0.0000 |
| switch | DAYTRADE_DC_TARGET_TF | 1h | +0.1358 | -0.6557 | 22/49 | +0.0000 |
| switch | WT_COMPOSITE_ENTRY_OK | -20 | +0.1351 | +0.1351 | 3/49 | +0.0000 |
| switch | MTF_WT_CROSS_EXIT_TF | 1h | +0.1174 | +0.1174 | 15/49 | +0.0000 |
| switch | WT_DC_STOCH_THRESHOLD_LONG | 20 | +0.0437 | +0.0437 | 3/49 | +0.0000 |
| switch | MTF_DC_REJECT_EXIT_TF | 4h | +0.0069 | +0.0069 | 3/49 | +0.0000 |

_sizing-excluded (not promoted): MIN_POSITION_SIZE, START_POSITION_SIZE_

## CRYPTO_SHORT — 15 candidates (sizing_excluded: 2)
| kind | name | promote_value | score(winsor) | raw_avg | pos_sym/n | median |
|---|---|---|---|---|---|---|
| switch | BB_SQUEEZE_ENTRY_ENABLED | True | +2.6305 | +5.8021 | 37/72 | +0.0114 |
| switch | STDEV_SLOPE_SIZING_ENABLED | True | +1.7175 | +2.7056 | 29/62 | +0.0000 |
| switch | BTC_GUARANTEED_REENTRY_ENABLED | True | +1.0962 | +3.4444 | 17/61 | +0.0000 |
| switch | EMA50_15M_ENTRY_FILTER_PCT | 0 | +1.0874 | +3.1027 | 14/66 | +0.0000 |
| switch | REENTRY_ENTRY_FILTER_ENABLED | True | +0.9746 | +1.6177 | 15/59 | +0.0000 |
| switch | DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED | False | +0.9627 | +2.8111 | 13/60 | +0.0000 |
| switch | AUGMENT_MIN_GAIN_PCT | 0.75 | +0.6752 | +10.9525 | 17/61 | +0.0000 |
| switch | EMA50_15M_ENTRY_FILTER_ENABLED | True | +0.3649 | +0.9361 | 4/66 | +0.0000 |
| switch | BB_PULLBACK_GATE_TF | 15m | +0.2458 | +0.3344 | 14/67 | +0.0000 |
| switch | COOLDOWN_BARS | 3 | +0.1829 | +0.1829 | 10/35 | +0.0000 |
| switch | SHORT_DC_LOW_BREAK_ENABLED | False | +0.1346 | +0.1346 | 11/62 | +0.0000 |
| switch | WT_DIV_EXIT_ENABLED | False | +0.1040 | +0.1040 | 6/62 | +0.0000 |
| switch | SCALP_V3_AUG_BE_STOP_PCT | 0.1 | +0.0389 | +0.2988 | 6/60 | +0.0000 |
| switch | TECHNICAL_DC_TARGET_TF | 4h | +0.0070 | +0.0070 | 6/60 | +0.0000 |
| switch | VIGILANCE_GUARD_ENABLED | True | +0.0046 | +0.0046 | 28/60 | +0.0000 |

_sizing-excluded (not promoted): MIN_POSITION_SIZE, START_POSITION_SIZE_

## STOCKS_LONG — 10 candidates (sizing_excluded: 2)
| kind | name | promote_value | score(winsor) | raw_avg | pos_sym/n | median |
|---|---|---|---|---|---|---|
| switch | BB_SQUEEZE_ENTRY_ENABLED | True | +0.9021 | +0.9073 | 50/108 | +0.0000 |
| switch | UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED | False | +0.7618 | +4.5692 | 17/101 | +0.0000 |
| switch | WT_15M_BOUNCE_OPEN_ENABLED | True | +0.5631 | +1.8146 | 20/112 | +0.0000 |
| switch | DAYTRADE_DC_STOP_TF | 15m | +0.4786 | +0.4786 | 35/101 | +0.0000 |
| switch | NOLOSS_BYPASS_WT_5OF5_MIN_TFS | 3 | +0.3929 | +0.5643 | 22/101 | +0.0000 |
| switch | REENTRY_SIZE_BREAKOUT_MULT | 0 | +0.2671 | +0.3068 | 6/101 | +0.0000 |
| switch | GAP_PER_SYMBOL_AVG_THRESH_PCT | 0.30 | +0.1375 | +0.1822 | 6/103 | +0.0000 |
| switch | LEGACY_REENTRY_PSR_DC_BOUNCE | 2.0 | +0.0542 | +0.0542 | 6/102 | +0.0000 |
| switch | WT_DC_HTF_GATE | 4h_D | +0.0476 | +0.0357 | 18/105 | +0.0000 |
| switch | PARTIAL_PROFIT_LOCK_FRAC_TRADIER | 0.625 | +0.0376 | +0.0376 | 22/101 | +0.0000 |

_sizing-excluded (not promoted): MIN_POSITION_SIZE, START_POSITION_SIZE_

## STOCKS_SHORT — 6 candidates (sizing_excluded: 2)
| kind | name | promote_value | score(winsor) | raw_avg | pos_sym/n | median |
|---|---|---|---|---|---|---|
| switch | WT_DIV_EXIT_ENABLED | False | +0.3191 | +0.3072 | 15/84 | +0.0000 |
| switch | BB_SQUEEZE_ENTRY_ENABLED | False | +0.2699 | +0.9040 | 12/92 | +0.0000 |
| switch | DAYTRADE_DC_STOP_TF | 15m | +0.1110 | +0.1110 | 30/84 | +0.0000 |
| switch | UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED | False | +0.1017 | +9.2113 | 17/84 | +0.0000 |
| switch | PARTIAL_PROFIT_LOCK_FRAC_TRADIER | 0.625 | +0.0576 | +0.0576 | 15/84 | +0.0000 |
| switch | WT_D_BOUNCE_DD_STOP_ENABLED | False | +0.0546 | +0.0546 | 19/84 | +0.0000 |

_sizing-excluded (not promoted): MIN_POSITION_SIZE, START_POSITION_SIZE_
