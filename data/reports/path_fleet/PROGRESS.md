# Stock Path Fleet

Updated: 2026-07-26T21:59:59.000861+00:00

This is research-only. A positive B&H comparison is insufficient: every exit must also beat the strongest frozen result using the identical entry schedule. LONG and SHORT are never pooled.

## Queue

| priority | path | kind | state | attempts | owner |
|---:|---|---|---|---:|---|
| 1 | `ENTRY_LADDER_GREEN` | ENTRY | SCREENED | 2 | short-mirror-backfill |
| 1 | `ENTRY_WT_DC` | ENTRY | SCREENED | 0 | entry-overlay |
| 1 | `EXIT_E02_DONCHIAN` | EXIT | SCREENED | 1 | codex-e02-path |
| 2 | `ENTRY_STOCH_HHHL` | ENTRY | SCREENED | 1 | entry-overlay |
| 2 | `BOTTOM_A_PROTECTIVE_TRAIL` | EXIT | SCREENED | 1 | bottom-a-agent |
| 2 | `BOTTOM_B_DELAYED_LOWER_TOP` | EXIT | SCREENED | 1 | bottom-b-agent |
| 2 | `BOTTOM_C_DELAYED_EMERGENCY` | EXIT | SCREENED | 1 | bottom-c-agent |
| 2 | `EXIT_STRUCTURAL_WT_LOWER_TOP` | EXIT | SCREENED | 1 | path-fleet-structural |
| 3 | `ENTRY_GOLDEN_RULE` | ENTRY | SCREENED | 0 | entry-overlay |
| 3 | `EXIT_PARTIAL_RUNNER` | EXIT | SCREENED | 1 | path-fleet-partial |
| 4 | `EXIT_WT_MTF` | EXIT | SCREENED | 1 | codex-wt-mtf-path |
| 5 | `EXIT_GR_OPPOSITE` | EXIT | SCREENED | 2 | codex-gr-opposite-level-path |
| 6 | `EXIT_E01_CHANDELIER` | EXIT | SCREENED | 1 | codex-e01-chandelier-path |
| 7 | `ENTRY_BB_RECOVERY` | ENTRY | SCREENED | 1 | entry-overlay |
| 7 | `EXIT_E05_DIVERGENCE_RETEST` | EXIT | SCREENED | 1 | path-fleet-e05 |
| 8 | `ENTRY_DELTA_MTF` | ENTRY | SCREENED | 1 | entry-overlay |
| 8 | `EXIT_E06_REGRESSION_RETEST` | EXIT | SCREENED | 3 | path-fleet-e06 |
| 9 | `EXIT_MTF_ATR_TRAIL` | EXIT | SCREENED | 1 | path-fleet-mtf-atr-trail |
| 10 | `EXIT_PEAK_GIVEBACK` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ENTRY | SCREENED | 1 | entry-overlay |
| 20 | `ENTRY_DC_BREAK_ENTRY_ENABLED` | ENTRY | SCREENED | 1 | dc-break-entry-agent |
| 20 | `ENTRY_DC_TIER_AUG_ENABLED` | ENTRY | SCREENED | 1 | entry-overlay |
| 20 | `ENTRY_LONG_WAIT_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_BREAKOUT_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_MOMENTUM_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_PROBE_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_PULLBACK_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_SOLID_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_TREND_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_REENTRY_TURN_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_ROTATION_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_SCALP_V3_TRADIER_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_SENTIMENT_BOOST_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_SENT_STRAT_DIVERGENCE_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_SNIPER_SHORT_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `ENTRY_WT_3M_FORCE_OPEN_ENABLED` | ENTRY | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_ALGO_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_DD_BOUNCE_STOP_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_DELTA_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_DELTA_MAX_HOLD_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_DT_TARGET_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_EMERGENCY_KILL_SIZE_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_EOD_SLIM_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_EXTREME_TP_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_GLOBAL_SENTIMENT_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_HARD_BOUNCE_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_HTF_QUICK_TP_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_IBS_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_KNM_BOUNCE_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_KNM_DC_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_MICRO_SCALP_STOCKS_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_OB_OS_TP_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_PRE_CLOSE_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_QUICK_TP_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_R1_DC_LOW4_3M_EMERGENCY_ENABLED_R1_NEWBORN_WINDOW_MIN` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_SCALP_V3_TRADIER_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_SENTIMENT_FADE_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_SENT_STRAT_TRIM_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_STRUCTURAL_RANGE_SHIFT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_STRUCT_HLNM_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_TECH_BREAKDOWN_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_WT_15M_VEL_SLOW_GAIN_BAND_PCT_WT_VEL_DECEL_RATIO` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 20 | `EXIT_WT_DC_EXIT_ENABLED` | EXIT | ADAPTER_REQUIRED | 0 | — |
| 50 | `ENTRY_DISASTER_GUARD_ENABLED` | ENTRY | QUARANTINED | 0 | — |
| 50 | `ENTRY_FH_MOMENTUM_ENABLED` | ENTRY | QUARANTINED | 0 | — |
| 50 | `ENTRY_TRADIER_PHYSICS_OPPOSITE_SIDE_BLOCK` | ENTRY | QUARANTINED | 0 | — |
| 50 | `EXIT_ATR_TRAIL_ENABLED` | EXIT | QUARANTINED | 0 | — |
| 50 | `EXIT_BROKER_SYNC_ENABLED` | EXIT | OBSERVABILITY_ONLY | 0 | — |
| 50 | `EXIT_GHOST_CLOSE_DETECTION_ENABLED` | EXIT | OBSERVABILITY_ONLY | 0 | — |
| 50 | `EXIT_HARD_STOP_LOSS_MAX_PAIN` | EXIT | QUARANTINED | 0 | — |
| 50 | `EXIT_MARKET_AGAINST_POSITION_ENABLED` | EXIT | QUARANTINED | 0 | — |
| 50 | `EXIT_STALE_DATA_HARD_STOP_STALE_DATA_GAIN_EROSION` | EXIT | QUARANTINED | 0 | — |

## Path descriptions and ranges

| path | source/config | description | settings | frozen comparison |
|---|---|---|---|---|
| `ENTRY_LADDER_GREEN` | research-only; 0 authoritative row(s) | Completed-TF bullish/bearish WT arrow requests a regression-band size; D/4h/1h requests are capacity-clipped and use a mandatory reclaim after exits. | trigger=['green']; curve_mode=['linear', 'center_plateau']; semantics=['target', 'add']; D_bottom_top=['3..8 / 1..6']; 4h_bottom_top=['2..6 / 1..4']; 1h_bottom_top=['1..4 / 0.5..2']; hard_capacity_x=[8] | entry: B&H $2k plus frozen accepted ladder curve; exit: E02 4h Donchian N=30 + zero-buffer resting reclaim |
| `ENTRY_WT_DC` | WT_DC_ENTRY_ENABLED; 2 authoritative row(s) | WaveTrend direction/cross combined with Donchian location or break. This path previously performed well; sweep aliases must reach TRA_WT_DC_ENTRY_THRESHOLD. | threshold=[35, 45, 55, 65, 75, 85]; timeframes=['15m', '1h', '4h', 'D']; min_confirming_tfs=[1, 2, 3]; dc_zone=[0.1, 0.2, 0.35, 0.5]; cross_age_bars=[1, 2, 4, 8] | entry: same frozen ladder sizing; no other entry signals; exit: E02 N=30 + resting reclaim |
| `EXIT_E02_DONCHIAN` | research-only; 0 authoritative row(s) | Exit LONG only after completed HTF close below the prior Donchian low; SHORT mirrors above prior high. Reentry is lower ladder or resting reclaim. | timeframe=['1h', '4h', 'D']; lookback=[10, 15, 20, 30, 40, 55, 80]; profit_gate_pct=[0.0, 0.25, 0.5, 1.0] | entry: frozen accepted ladder event/fill schedule; exit: E02 4h N=30 |
| `ENTRY_STOCH_HHHL` | research-only; 0 authoritative row(s) | Completed-bar HH+HL with low and rising StochRSI for LONG; mirror LH+LL with high and falling StochRSI for SHORT. Uses the band ladder only for sizing. | stoch_threshold=[15, 20, 25, 30, 35, 40]; timeframes=['1h', '4h', 'D']; min_confirming_tfs=[1, 2]; trigger=['structure', 'union-with-green'] | entry: same frozen ladder curve and capacity; exit: E02 N=30 + resting reclaim |
| `BOTTOM_A_PROTECTIVE_TRAIL` | research-only; 0 authoritative row(s) | Completed adverse structure arms an immediate diagnostic or a later monotonic ATR, rolling-stdev, or Donchian protective trail. | arm_timeframe=['1h', '4h']; trail_timeframe=['5m', '15m', '1h']; mode=['IMMEDIATE_DIAGNOSTIC', 'ATR', 'STDEV', 'DC']; break_buffer_atr=[0.0, 0.25]; atr_mult=[1.5, 2.0, 3.0, 4.0]; stdev_mult=[1.5, 2.0, 2.5, 3.0]; lookback=[4, 10, 20, 40] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `BOTTOM_B_DELAYED_LOWER_TOP` | research-only; 0 authoritative row(s) | Arm on a completed lower-low/adverse break, do not sell at the break, then exit at the next confirmed lower price/WT1 top (SHORT mirrored). | arm_timeframe=['1h', '4h']; confirm_timeframe=['5m', '15m', '1h']; confirmation_mode=['PRICE_ONLY', 'WT_ONLY', 'AND', 'OR']; confirmation_bars=[1, 2]; rebound_atr=[0.25, 0.5, 1.0]; prebreak_lookback=[4, 6]; max_wait_hours=[12, 24, 48] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `BOTTOM_C_DELAYED_EMERGENCY` | research-only; 0 authoritative row(s) | Use the delayed lower-top state, but add a separately counted rare emergency close when recovery never arrives. | arm_timeframe=['1h', '4h']; confirm_timeframe=['5m', '15m', '1h']; confirmation_mode=['AND', 'OR']; max_wait_hours=[12, 24, 48]; emergency_adverse_atr=[2.0, 3.0, 4.0]; emergency_adverse_stdev=[2.5, 3.5, 5.0]; continued_adverse_bars=[3, 5]; max_emergency_exit_share=[0.25] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | research-only; 0 authoritative row(s) | After a completed structural lower low (higher high for SHORT), wait for a lower price top and WT1 rebound/rollover top; do not exit on the first DC4 break. | arm_timeframe=['1h', '4h']; retest_timeframe=['15m', '1h']; wt_rebound_min=[0.5, 1.0, 2.0, 4.0]; pivot_lookback=[3, 4, 6, 10]; max_wait_hours=[12, 20, 30, 48]; profit_gate_pct=[0.25, 0.5, 1.0] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `ENTRY_GOLDEN_RULE` | research-only; 0 authoritative row(s) | Multi-timeframe DC/BB break/retest vote. Test it as a direct entry and as an entry filter; timeframe weights must not be collapsed into an opaque sum. | min_tfs=[1, 2, 3]; min_indicators_per_tf=[1, 2]; weights_15m_1h_4h_D=['1/1/1/1', '0.5/1/2/3', '0/1/2/4']; score=[4, 6, 8, 10, 12, 15.5]; role=['direct-entry', 'entry-filter'] | entry: same ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `EXIT_PARTIAL_RUNNER` | research-only; 0 authoritative row(s) | Take a bounded partial clip at a fast top, retain a runner for the slow exit, and give every exited clip its own lower/reclaim reentry obligation. | first_clip_fraction=[0.15, 0.25, 0.33, 0.5]; second_clip_fraction=[0.0, 0.15, 0.25, 0.33]; fast_family=['E05', 'E06', 'WT']; slow_family=['E01', 'E02']; regime_switch=[False, True] | entry: exact frozen accepted ladder schedule; exit: same-entry full E02 N=30 control |
| `EXIT_WT_MTF` | WT_HTF_EXIT_ENABLED, WT_CROSSUNDER_EXIT_ENABLED; 3 authoritative row(s) | Exit at WaveTrend exhaustion/cross against the held side, optionally requiring multiple completed timeframes and a price/structure confirmation. | timeframes=['15m', '1h', '4h', 'D', 'W']; min_against_tfs=[1, 2, 3, 4]; extreme=[45, 55, 65, 75]; velocity=[0.0, 0.25, 0.5, 1.0]; profit_gate_pct=[0.0, 0.25, 0.5, 1.0] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_GR_OPPOSITE` | research-only; 0 authoritative row(s) | Exit on opposite Golden Rule HTF votes. Timeframe votes and weights are reported separately so LONG/SHORT direction cannot be inverted or pooled. | min_tfs=[1, 2, 3]; min_indicators=[1, 2]; score=[4, 6, 8, 10, 12, 15.5]; weights_15m_1h_4h_D=['1/1/1/1', '0.5/1/2/3', '0/1/2/4'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_E01_CHANDELIER` | research-only; 0 authoritative row(s) | Monotonic ATR Chandelier stop on completed 4h or daily bars. | timeframe=['4h', 'D']; lookback=[10, 20, 30, 55]; atr_mult=[1.5, 2.0, 2.5, 3.0, 4.0]; profit_gate_pct=[0.0, 0.25, 0.5, 1.0] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `ENTRY_BB_RECOVERY` | BB_RECOVERY_ENABLED; 1 authoritative row(s) | Failed Bollinger breakout/breakdown followed by recovery into the band. | timeframes=['15m', '1h', '4h']; recovery_bars=[1, 2, 4, 8]; min_band_excursion_atr=[0.0, 0.25, 0.5, 1.0] | entry: same ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `EXIT_E05_DIVERGENCE_RETEST` | research-only; 0 authoritative row(s) | Confirmed RSI/price divergence, structural break, then rebound/rollover exit. | pivot_radius=[2, 3, 5]; divergence_min=[3, 5, 8]; break_buffer_atr=[0.0, 0.25]; rebound_atr=[0.25, 0.5]; max_wait_bars=[8, 12, 20] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `ENTRY_DELTA_MTF` | research-only; 0 authoritative row(s) | Delta/momentum entry requiring a declared number of favorable completed TFs. | min_favorable_tfs=[1, 2, 3, 4]; decay_ratio=[0.25, 0.5, 0.75]; structural_gate=[False, True] | entry: same ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `EXIT_E06_REGRESSION_RETEST` | R, e, s, e, a, r, c, h, -, o, n, l, y,  , p, r, i, o, r, -, l, o, g, -, r, e, g, r, e, s, s, i, o, n,  , p, a, t, h, .,  , e, x, i, t, _, z,  , a, r, m, s,  , w, i, t, h,  , e, x, i, t, _, z,  , <,  , a, r, m, _, z, .; 0 authoritative row(s) | Regression extreme arms an exit; reversion/retest confirms it before close. | lookback=[40, 60, 100, 150, 250]; arm_z=[1.5, 2.0, 2.5, 3.0]; exit_z=[0.75, 1.0, 1.5, 2.0]; corr_gate=[0.5, 0.7, 0.85] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_MTF_ATR_TRAIL` | research-only; 0 authoritative row(s) | Profit-aware ATR trail compounded across completed timeframes. | timeframes=['1h', '4h', 'D']; atr_mult=[1.5, 2.0, 2.5, 3.0, 4.0]; min_profit_pct=[0.0, 0.25, 0.5, 1.0]; min_confirming_tfs=[1, 2] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_PEAK_GIVEBACK` | PEAK_GIVEBACK_ENABLED; 1 authoritative row(s) | Exit or reduce after giving back a declared fraction of MFE, gated above cost. | arm_gain_pct=[0.5, 1.0, 2.0, 4.0, 8.0]; giveback_fraction=[0.2, 0.33, 0.5, 0.67]; reduce_fraction=[0.25, 0.5, 1.0] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | AUGMENT_TREND_RESUME_ENABLED; 1 authoritative row(s) | Research reconstruction of the removed stock trend-resume augment: while an existing position is profitable, add on a side-favorable 5m Donchian-basis, Stoch, and RSI continuation state. | min_gain_pct=[0.5, 1.0, 2.0, 3.0]; rsi_boundary_long_short=['60/40', '70/30', '80/20', '100/0']; add_start_position_mult=[0.25, 0.5] | entry: same frozen ladder entry schedule, sizing, and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DC_BREAK_ENTRY_ENABLED; 2 authoritative row(s) | Disconnected legacy registry path. The named DC_BREAK_ENTRY_ENABLED switch is absent. A research reconstruction combines the prior-channel break semantics from the disabled swing branch and the separately controlled StockDaytradeWing, without changing live configuration. | timeframe=['5m', '15m', '1h', '4h']; buffer_fraction=[0.0, 0.0005, 0.001, 0.002]; require_1h_expansion=[False, True]; confirmation=['none', 'not-exhausted', 'directional-stoch']; role=['direct', 'union-with-green']; live_switch_status=['DISCONNECTED'] | entry: same frozen ladder sizing and $16k capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_DC_TIER_AUG_ENABLED` | DC_TIER_AUG_ENABLED; 1 authoritative row(s) | Phantom inventory switch over an active augment block. DC_TIER_AUG_ENABLED is absent and unread, while evaluate_augment unconditionally applies profit-gated 5m/15m/1h/4h Donchian tier targets. | min_gain_pct=[1.0, 3.0, 5.0]; buffer_fraction=[0.0, 0.001, 0.002]; tier_profile=['1/2/3/5', '1/1.5/2.5/4', '1/2/4/8']; target_fill_ratio=[0.5, 0.75, 0.9]; tier4_maturity_atr=['off', 0.7, 0.5]; inventory_switch_status=['PHANTOM_ABSENT_AND_UNREAD'] | entry: same frozen ladder entry schedule, sizing, and $16k capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_LONG_WAIT_ENABLED` | LONG_WAIT_ENABLED; 1 authoritative row(s) | Low bounce wait entry. 1H dip + NH deep value + sniper confirmation. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_BREAKOUT_ENABLED` | REENTRY_BREAKOUT_ENABLED; 1 authoritative row(s) | Reentry on DC15 (15-period DC) breakout after prior exit. prev_gain = gain at last exit. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_MOMENTUM_ENABLED` | REENTRY_MOMENTUM_ENABLED; 1 authoritative row(s) | Reentry when price reclaims last-sell level (momentum reclaim). | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_PROBE_ENABLED` | REENTRY_PROBE_ENABLED; 1 authoritative row(s) | Exploratory reentry probe after exit. Small position, no prior reentry. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_PULLBACK_ENABLED` | REENTRY_PULLBACK_ENABLED; 1 authoritative row(s) | Reentry on pullback after prior profitable exit. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_SOLID_ENABLED` | REENTRY_SOLID_ENABLED; 1 authoritative row(s) | Solid reentry with K above basis or below DC NH. Used as AUGMENT not OPEN. | config_default=['True']; event_types=['AUGMENT']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_TREND_ENABLED` | REENTRY_TREND_ENABLED; 1 authoritative row(s) | Reentry in trend continuation after reduce with positive max_gain. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_REENTRY_TURN_ENABLED` | REENTRY_TURN_ENABLED; 1 authoritative row(s) | Reentry on stoch turn near DC basis with K confirmation. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_ROTATION_ENABLED` | ROTATION_ENABLED; 1 authoritative row(s) | Rotation entry: sector rotation favors symbol, enter long. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_SCALP_V3_TRADIER_ENABLED` | SCALP_V3_TRADIER_ENABLED; 1 authoritative row(s) | ScalpV3 for stocks. Long on dip + WT support; short on bounce + WT resistance. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_SENTIMENT_BOOST_ENABLED` | SENTIMENT_BOOST_ENABLED; 1 authoritative row(s) | Augment when sentiment boosts above target. loc=N = local sentiment. Dominant augment path (260+192+107 fires). | config_default=['True']; event_types=['AUGMENT']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_SENT_STRAT_DIVERGENCE_ENABLED` | SENT_STRAT_DIVERGENCE_ENABLED; 1 authoritative row(s) | Sentiment strategy divergence entry. Vel = WT velocity. RATIO_BOOST/CUT suffix = L/S ratio modifier. Top tradier entry path (69+41+39 fires). | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_SNIPER_SHORT_ENABLED` | SNIPER_SHORT_ENABLED; 1 authoritative row(s) | Sniper short: precise short entry on K extreme + structure break. | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_WT_3M_FORCE_OPEN_ENABLED` | WT_3M_FORCE_OPEN_ENABLED; 1 authoritative row(s) | Force open after close. Uses 5m WT for stocks. Rarely fires (1 event in history). | config_default=['True']; event_types=['OPEN']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `EXIT_ALGO_EXIT_ENABLED` | ALGO_EXIT_ENABLED; 1 authoritative row(s) | Algo composite exit: RSI/MFI extreme + bear penalty + stale indicators. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_DD_BOUNCE_STOP_ENABLED` | DD_BOUNCE_STOP_ENABLED; 1 authoritative row(s) | Drawdown bounce stop: price bounced off DD level, reduce. | config_default=['True']; event_types=['REDUCE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_DELTA_EXIT_ENABLED` | DELTA_EXIT_ENABLED; 2 authoritative row(s) | DELTA engine exit. TOP/BOTTOM = exhaustion state. TRANSIT = momentum change. Includes SMART_RZ_EXIT variant. / DELTA bottom-bounce exit for shorts: K extreme + DC break + vel decel. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_DELTA_MAX_HOLD_ENABLED` | DELTA_MAX_HOLD_ENABLED; 1 authoritative row(s) | Delta max hold time exceeded. Close + mandatory reentry. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_DT_TARGET_ENABLED` | DT_TARGET_ENABLED; 1 authoritative row(s) | Daytrade target/timeout/stop exits. DT_TARGET = profit target hit. DT_TIMEOUT = max hold time. DT_STOP = loss limit. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_EMERGENCY_KILL_SIZE_ENABLED` | EMERGENCY_KILL_SIZE_ENABLED; 1 authoritative row(s) | Emergency kill large position when momentum lost. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_EOD_SLIM_ENABLED` | EOD_SLIM_ENABLED; 1 authoritative row(s) | End-of-day slim: reduce position at market close based on L/S ratio. | config_default=['True']; event_types=['REDUCE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_EXTREME_TP_ENABLED` | EXTREME_TP_ENABLED; 1 authoritative row(s) | Close on extreme oversold/overbought condition (TP for short/long). | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_GLOBAL_SENTIMENT_EXIT_ENABLED` | GLOBAL_SENTIMENT_EXIT_ENABLED; 1 authoritative row(s) | Close when sentiment spikes/collapses relative to global benchmark. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_HARD_BOUNCE_ENABLED` | HARD_BOUNCE_ENABLED; 1 authoritative row(s) | Price exceeds prior extreme (hard bounce). CLIFF variant = DC drop. Exit after bounce wait limit. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_HTF_QUICK_TP_ENABLED` | HTF_QUICK_TP_ENABLED; 1 authoritative row(s) | HTF-based quick take-profit when K on 4H/D level hits extreme. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_IBS_EXIT_ENABLED` | IBS_EXIT_ENABLED; 2 authoritative row(s) | IBS (Intraday Bar Sequence) exhaustion. Long: IBS > threshold (overbought bar pattern). Short: IBS < threshold. / IBS exhaustion at Nm (multi-bar) level. Slower variant of IBS exit. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_KNM_BOUNCE_EXIT_ENABLED` | KNM_BOUNCE_EXIT_ENABLED; 1 authoritative row(s) | K bounce turn: price bouncing at DC, K turning at extreme. Short exit. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_KNM_DC_EXIT_ENABLED` | KNM_DC_EXIT_ENABLED; 1 authoritative row(s) | K NM (K at NM timeframe) real drop/rise through DC level. Confirms momentum break. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_MICRO_SCALP_STOCKS_ENABLED` | MICRO_SCALP_STOCKS_ENABLED; 1 authoritative row(s) | Micro-scalp close for stocks at target gain with prev_gain comparison. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_OB_OS_TP_ENABLED` | OB_OS_TP_ENABLED; 1 authoritative row(s) | Overbought/oversold partial take-profit (reduce not close). | config_default=['True']; event_types=['REDUCE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_PRE_CLOSE_ENABLED` | PRE_CLOSE_ENABLED; 1 authoritative row(s) | Pre-close with ratio cut: early exit before full target with L/S ratio adjustment. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_QUICK_TP_EXIT_ENABLED` | QUICK_TP_EXIT_ENABLED; 1 authoritative row(s) | Quick top exit (long at K extreme) or bottom exit (short at K extreme). | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_R1_DC_LOW4_3M_EMERGENCY_ENABLED_R1_NEWBORN_WINDOW_MIN` | R1_DC_LOW4_3M_EMERGENCY_ENABLED, R1_NEWBORN_WINDOW_MIN; 1 authoritative row(s) | ENTRY-QUALITY FAILURE DIAGNOSTIC / losing-churn evidence. Stocks use frozen dc_low4_5m/dc_high4_5m despite the legacy 3M knob name. A breach closes a failed entry at a loss; this is not a top or profit-taking recommendation. | config_default=['True / 15min']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_SCALP_V3_TRADIER_ENABLED` | SCALP_V3_TRADIER_ENABLED; 1 authoritative row(s) | Scalp V3 target close at N% gain. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_SENTIMENT_FADE_ENABLED` | SENTIMENT_FADE_ENABLED; 1 authoritative row(s) | Fade position when sentiment falls below ideal target. WTNTF = WT not favourable. Dominant reduce (3559+3437+813+735 fires). | config_default=['True']; event_types=['REDUCE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_SENT_STRAT_TRIM_ENABLED` | SENT_STRAT_TRIM_ENABLED; 1 authoritative row(s) | Sentiment strategy trim at gain level. Reduces then closes on continued fade. | config_default=['True']; event_types=['REDUCE/CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_STRUCTURAL_RANGE_SHIFT_ENABLED` | STRUCTURAL_RANGE_SHIFT_ENABLED; 1 authoritative row(s) | BB + NH structural shift: position entered at one BB extreme, now at other. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_STRUCT_HLNM_EXIT_ENABLED` | STRUCT_HLNM_EXIT_ENABLED; 1 authoritative row(s) | Higher-low / lower-high exit: structural momentum exhaustion at NH/NM level. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_TECH_BREAKDOWN_ENABLED` | TECH_BREAKDOWN_ENABLED; 1 authoritative row(s) | Technical breakdown: price breaks key technical level. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_WT_15M_VEL_SLOW_GAIN_BAND_PCT_WT_VEL_DECEL_RATIO` | WT_15M_VEL_SLOW_GAIN_BAND_PCT, WT_VEL_DECEL_RATIO; 1 authoritative row(s) | R2 for tradier. WT velocity slowdown near breakeven using R2_TF_LIST (1h/4h/D for stocks). | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_WT_DC_EXIT_ENABLED` | WT_DC_EXIT_ENABLED; 2 authoritative row(s) | DC channel structure break. NH low broken (long) or NH high broken (short). Top close path (463+263 fires). / WT+DC composite exit with velocity + TF alignment + divergence signals. | config_default=['True']; event_types=['CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `ENTRY_DISASTER_GUARD_ENABLED` | DISASTER_GUARD_ENABLED; 1 authoritative row(s) | 10-gate disaster guard added after MU SHORT $12k loss. Blocks entries when: daily gain negative, HTF misaligned, RSI extreme, etc. | config_default=['True']; event_types=['OPEN (BLOCKED)']; range_status=['QUARANTINED_NO_SWEEP'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_FH_MOMENTUM_ENABLED` | FH_MOMENTUM_ENABLED; 1 authoritative row(s) | DEAD: First-Hour Momentum strategy for stocks. Default OFF (B band missing). No history events. | config_default=['False']; event_types=['OPEN']; range_status=['QUARANTINED_NO_SWEEP'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `ENTRY_TRADIER_PHYSICS_OPPOSITE_SIDE_BLOCK` | TRADIER_PHYSICS_OPPOSITE_SIDE_BLOCK; 1 authoritative row(s) | Blocks entry when opposite side already open (Tradier can't hold LONG+SHORT simultaneously). Not a strategy — a physics gate. | config_default=['True']; event_types=['OPEN (BLOCKED)']; range_status=['QUARANTINED_NO_SWEEP'] | entry: same frozen ladder sizing and capacity; exit: E02 N=30 + resting reclaim |
| `EXIT_ATR_TRAIL_ENABLED` | ATR_TRAIL_ENABLED; 1 authoritative row(s) | DEAD: ATR trailing stop. #1 stock PnL destroyer (−2557%). Permanently OFF. | config_default=['False']; event_types=['CLOSE (DISABLED)']; range_status=['QUARANTINED_NO_SWEEP'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_BROKER_SYNC_ENABLED` | BROKER_SYNC_ENABLED; 1 authoritative row(s) | Position closed externally (broker/Tradier platform). Detected via API absence. gl=P&L. | config_default=['True']; event_types=['BROKER_CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_GHOST_CLOSE_DETECTION_ENABLED` | GHOST_CLOSE_DETECTION_ENABLED; 1 authoritative row(s) | Ghost close: Tradier API returned no position but local memory had it. N attempts without response. | config_default=['True']; event_types=['GHOST_CLOSE']; range_status=['EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_HARD_STOP_LOSS_MAX_PAIN` | HARD_STOP_LOSS_MAX_PAIN; 1 authoritative row(s) | DEAD: % stop-loss. Caused $500+ losses 2026-03-24. Shows in history = legacy data before 2026-03-24. | config_default=['DISABLED']; event_types=['CLOSE (DISABLED)']; range_status=['QUARANTINED_NO_SWEEP'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_MARKET_AGAINST_POSITION_ENABLED` | MARKET_AGAINST_POSITION_ENABLED; 1 authoritative row(s) | DISABLED: Market bias against position reduce. Disabled in current code. Shows in history = older data. | config_default=['DISABLED']; event_types=['REDUCE']; range_status=['QUARANTINED_NO_SWEEP'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |
| `EXIT_STALE_DATA_HARD_STOP_STALE_DATA_GAIN_EROSION` | STALE_DATA_HARD_STOP, STALE_DATA_GAIN_EROSION; 1 authoritative row(s) | DEAD: Stale data stops. Were killing valid positions. All disabled. | config_default=['DISABLED']; event_types=['CLOSE (DISABLED)']; range_status=['QUARANTINED_NO_SWEEP'] | entry: exact frozen accepted ladder schedule; exit: same-entry E02 N=30 control |

## Result ledger

| path | key | stage | state | strategy | B&H | same-entry control | alpha B&H | alpha control | TIM | trades |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `ENTRY_DC_TIER_AUG_ENABLED` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 128.22% | 26.20% | 1.98% | +102.03pp | +126.24pp | 74.4% | 9 |
| `ENTRY_DC_TIER_AUG_ENABLED` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -251.33% | -133.05% | -219.78% | -118.28pp | -31.55pp | 71.7% | 27 |
| `ENTRY_DC_TIER_AUG_ENABLED` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 24.32% | 22.65% | 33.66% | +1.67pp | -9.34pp | 76.6% | 6 |
| `ENTRY_DC_TIER_AUG_ENABLED` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -409.20% | -48.38% | -162.77% | -360.82pp | -246.43pp | 72.3% | 27 |
| `ENTRY_DC_TIER_AUG_ENABLED` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 280.88% | 54.33% | 280.88% | +226.55pp | +0.00pp | 86.1% | 6 |
| `ENTRY_DC_TIER_AUG_ENABLED` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1129.85% | 141.54% | 747.74% | +988.31pp | +382.11pp | 85.8% | 21 |
| `ENTRY_DC_TIER_AUG_ENABLED` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 348.21% | 37.50% | 66.62% | +310.70pp | +281.59pp | 84.3% | 5 |
| `ENTRY_DC_TIER_AUG_ENABLED` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 211.19% | 13.86% | -30.35% | +197.33pp | +241.54pp | 67.6% | 40 |
| `ENTRY_DC_TIER_AUG_ENABLED` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 397.91% | 36.98% | 80.46% | +360.93pp | +317.45pp | 62.3% | 7 |
| `ENTRY_DC_TIER_AUG_ENABLED` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 28.61% | -11.54% | -304.93% | +40.15pp | +333.53pp | 76.5% | 21 |
| `ENTRY_DC_TIER_AUG_ENABLED` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -25.59% | 21.95% | 15.04% | -47.54pp | -40.63pp | 81.0% | 9 |
| `ENTRY_DC_TIER_AUG_ENABLED` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -923.24% | -219.36% | -734.70% | -703.88pp | -188.54pp | 63.8% | 35 |
| `ENTRY_DC_TIER_AUG_ENABLED` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -104.66% | 23.03% | 4.84% | -127.69pp | -109.50pp | 72.1% | 7 |
| `ENTRY_DC_TIER_AUG_ENABLED` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -706.94% | -85.44% | -223.69% | -621.51pp | -483.26pp | 60.9% | 44 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -58.98% | 22.27% | -17.12% | -81.25pp | -41.86pp | 69.6% | 16 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -891.24% | -153.31% | -589.06% | -737.93pp | -302.18pp | 68.3% | 31 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 84.31% | 19.41% | 3.87% | +64.90pp | +80.44pp | 74.2% | 12 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -643.43% | -82.31% | -818.37% | -561.12pp | +174.94pp | 80.8% | 39 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 387.66% | 44.50% | 388.07% | +343.16pp | -0.41pp | 88.4% | 4 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 521.13% | 69.84% | 407.15% | +451.30pp | +113.99pp | 70.8% | 22 |
| `ENTRY_DC_TIER_AUG_ENABLED` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 362.29% | 84.91% | 362.29% | +277.38pp | +0.00pp | 88.9% | 4 |
| `ENTRY_DC_TIER_AUG_ENABLED` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 434.13% | 113.27% | 411.17% | +320.86pp | +22.96pp | 80.1% | 15 |
| `ENTRY_DC_TIER_AUG_ENABLED` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2267.27% | 467.09% | 2267.27% | +1800.18pp | +0.00pp | 97.4% | 5 |
| `ENTRY_DC_TIER_AUG_ENABLED` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 5414.04% | 888.47% | 5414.04% | +4525.57pp | +0.00pp | 97.0% | 8 |
| `ENTRY_DC_TIER_AUG_ENABLED` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 575.87% | 121.26% | 575.87% | +454.61pp | +0.00pp | 87.4% | 7 |
| `ENTRY_DC_TIER_AUG_ENABLED` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 736.35% | 127.67% | 642.13% | +608.68pp | +94.23pp | 73.6% | 27 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1379.58% | 204.90% | 1337.78% | +1174.67pp | +41.80pp | 86.2% | 10 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2739.89% | 381.95% | 2503.08% | +2357.94pp | +236.81pp | 85.5% | 23 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1301.80% | 123.58% | 1301.80% | +1178.22pp | +0.00pp | 73.7% | 7 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1308.05% | 104.11% | 1283.09% | +1203.95pp | +24.96pp | 82.9% | 31 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 271.68% | 88.48% | 106.89% | +183.20pp | +164.79pp | 81.8% | 6 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 361.98% | 103.59% | 163.19% | +258.39pp | +198.80pp | 79.8% | 22 |
| `ENTRY_DC_TIER_AUG_ENABLED` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 778.27% | 139.23% | 778.27% | +639.04pp | +0.00pp | 89.3% | 7 |
| `ENTRY_DC_TIER_AUG_ENABLED` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1411.21% | 214.03% | 1466.30% | +1197.18pp | -55.09pp | 83.8% | 25 |
| `ENTRY_DC_TIER_AUG_ENABLED` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 482.90% | 90.70% | 440.41% | +392.20pp | +42.50pp | 80.0% | 5 |
| `ENTRY_DC_TIER_AUG_ENABLED` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 785.39% | 118.22% | 531.31% | +667.17pp | +254.08pp | 78.8% | 17 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1036.52% | 126.60% | 1036.52% | +909.92pp | +0.00pp | 87.5% | 5 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1116.63% | 123.02% | 1150.82% | +993.61pp | -34.20pp | 78.7% | 29 |
| `ENTRY_DC_TIER_AUG_ENABLED` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1125.11% | 134.45% | 1125.11% | +990.66pp | +0.00pp | 97.1% | 6 |
| `ENTRY_DC_TIER_AUG_ENABLED` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1798.70% | 204.78% | 1683.80% | +1593.92pp | +114.90pp | 89.0% | 16 |
| `ENTRY_DC_TIER_AUG_ENABLED` | LDOS_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 37.50% | 66.62% | -37.50pp | -66.62pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ALB_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 19.41% | 3.87% | -19.41pp | -3.87pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | EGO_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 23.03% | 4.84% | -23.03pp | -4.84pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | HL_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 21.95% | 15.04% | -21.95pp | -15.04pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | TTD_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 54.33% | 280.88% | -54.33pp | -280.88pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ACN_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 44.50% | 388.07% | -44.50pp | -388.07pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | UEC_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 22.65% | 33.66% | -22.65pp | -33.66pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ASTS_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 22.27% | -17.12% | -22.27pp | +17.12pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | UUUU_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 26.20% | 1.98% | -26.20pp | -1.98pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | LAC_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 36.98% | 80.46% | -36.98pp | -80.46pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | VLO_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 84.91% | 362.29% | -84.91pp | -362.29pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | DINO_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 90.70% | 440.41% | -90.70pp | -440.41pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MPC_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 88.48% | 106.89% | -88.48pp | -106.89pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | PBF_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 121.26% | 575.87% | -121.26pp | -575.87pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | AMD_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 134.45% | 1125.11% | -134.45pp | -1125.11pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | INTC_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 139.23% | 778.27% | -139.23pp | -778.27pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MU_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 204.90% | 1337.78% | -204.90pp | -1337.78pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | ARM_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 126.60% | 1036.52% | -126.60pp | -1036.52pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | MRVL_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 123.58% | 1301.80% | -123.58pp | -1301.80pp | 0.0% | 0 |
| `ENTRY_DC_TIER_AUG_ENABLED` | SNDK_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_PHANTOM_SWITCH | 0.00% | 467.09% | 2267.27% | -467.09pp | -2267.27pp | 0.0% | 0 |
| `EXIT_MTF_ATR_TRAIL` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 315.71% | 84.91% | 387.31% | +230.81pp | -71.60pp | 57.7% | 15 |
| `EXIT_MTF_ATR_TRAIL` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 54.99% | 26.20% | 68.24% | +28.80pp | -13.24pp | 20.1% | 3 |
| `EXIT_MTF_ATR_TRAIL` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 89.32% | 22.65% | 65.05% | +66.67pp | +24.27pp | 14.3% | 11 |
| `EXIT_MTF_ATR_TRAIL` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 489.44% | 54.33% | 481.90% | +435.11pp | +7.54pp | 92.4% | 5 |
| `EXIT_MTF_ATR_TRAIL` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2459.42% | 467.09% | 2325.64% | +1992.33pp | +133.79pp | 97.5% | 3 |
| `EXIT_MTF_ATR_TRAIL` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 503.71% | 121.26% | 707.27% | +382.45pp | -203.56pp | 86.2% | 6 |
| `EXIT_MTF_ATR_TRAIL` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1169.82% | 204.90% | 1376.23% | +964.92pp | -206.41pp | 62.7% | 4 |
| `EXIT_MTF_ATR_TRAIL` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 202.64% | 123.58% | 1672.73% | +79.05pp | -1470.09pp | 61.1% | 7 |
| `EXIT_MTF_ATR_TRAIL` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 110.84% | 88.48% | 117.46% | +22.36pp | -6.62pp | 18.1% | 13 |
| `EXIT_MTF_ATR_TRAIL` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 49.12% | 37.50% | 67.76% | +11.62pp | -18.64pp | 13.3% | 15 |
| `EXIT_MTF_ATR_TRAIL` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 72.65% | 36.98% | 109.33% | +35.68pp | -36.68pp | 16.6% | 11 |
| `EXIT_MTF_ATR_TRAIL` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 732.04% | 139.23% | 964.02% | +592.81pp | -231.98pp | 72.9% | 14 |
| `EXIT_MTF_ATR_TRAIL` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 66.21% | 21.95% | 50.25% | +44.25pp | +15.95pp | 15.3% | 11 |
| `EXIT_MTF_ATR_TRAIL` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 52.83% | 23.03% | 48.02% | +29.80pp | +4.81pp | 14.0% | 14 |
| `EXIT_MTF_ATR_TRAIL` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 304.10% | 90.70% | 485.24% | +213.40pp | -181.14pp | 74.4% | 4 |
| `EXIT_MTF_ATR_TRAIL` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 50.81% | 22.27% | 66.58% | +28.54pp | -15.76pp | 20.5% | 6 |
| `EXIT_MTF_ATR_TRAIL` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1081.36% | 126.60% | 1082.44% | +954.76pp | -1.07pp | 74.2% | 13 |
| `EXIT_MTF_ATR_TRAIL` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 848.73% | 134.45% | 1180.81% | +714.28pp | -332.08pp | 96.1% | 5 |
| `EXIT_MTF_ATR_TRAIL` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 57.94% | 19.41% | 85.72% | +38.53pp | -27.77pp | 39.9% | 3 |
| `EXIT_MTF_ATR_TRAIL` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 347.41% | 44.50% | 400.98% | +302.92pp | -53.57pp | 72.8% | 9 |
| `EXIT_E05_DIVERGENCE_RETEST` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 70.06% | 37.50% | 67.76% | +32.56pp | +2.30pp | 15.8% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 164.05% | 19.41% | 85.72% | +144.64pp | +78.34pp | 46.3% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | EGO_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 46.44% | 23.03% | 48.02% | +23.42pp | -1.57pp | 21.6% | 0 |
| `EXIT_E05_DIVERGENCE_RETEST` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 73.56% | 21.95% | 50.25% | +51.61pp | +23.31pp | 44.0% | 3 |
| `EXIT_E05_DIVERGENCE_RETEST` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 580.76% | 54.33% | 481.90% | +526.44pp | +98.87pp | 97.5% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | ACN_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 429.13% | 44.50% | 400.98% | +384.63pp | +28.15pp | 92.6% | 0 |
| `EXIT_E05_DIVERGENCE_RETEST` | UEC_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 75.31% | 22.65% | 65.05% | +52.66pp | +10.26pp | 21.7% | 0 |
| `EXIT_E05_DIVERGENCE_RETEST` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 135.31% | 22.27% | 66.58% | +113.04pp | +68.73pp | 58.2% | 5 |
| `EXIT_E05_DIVERGENCE_RETEST` | UUUU_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 68.24% | 26.20% | 68.24% | +42.04pp | +0.00pp | 22.7% | 0 |
| `EXIT_E05_DIVERGENCE_RETEST` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 166.29% | 36.98% | 109.33% | +129.32pp | +56.96pp | 32.1% | 2 |
| `EXIT_E05_DIVERGENCE_RETEST` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 521.45% | 84.91% | 387.31% | +436.55pp | +134.14pp | 96.0% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 603.57% | 90.70% | 485.24% | +512.87pp | +118.33pp | 96.5% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 235.02% | 88.48% | 117.46% | +146.54pp | +117.56pp | 50.2% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 672.90% | 121.26% | 707.27% | +551.64pp | -34.37pp | 93.9% | 5 |
| `EXIT_E05_DIVERGENCE_RETEST` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1373.64% | 134.45% | 1180.81% | +1239.19pp | +192.83pp | 99.4% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | INTC_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 958.64% | 139.23% | 964.02% | +819.41pp | -5.38pp | 98.6% | 0 |
| `EXIT_E05_DIVERGENCE_RETEST` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1316.16% | 204.90% | 1376.23% | +1111.25pp | -60.07pp | 73.1% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1189.63% | 126.60% | 1082.44% | +1063.03pp | +107.19pp | 93.4% | 2 |
| `EXIT_E05_DIVERGENCE_RETEST` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1021.57% | 123.58% | 1672.73% | +897.99pp | -651.15pp | 92.4% | 1 |
| `EXIT_E05_DIVERGENCE_RETEST` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 4447.54% | 467.09% | 2325.64% | +3980.45pp | +2121.90pp | 98.8% | 1 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 67.83% | 37.50% | 66.62% | +30.33pp | +1.21pp | 18.0% | 7 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -75.35% | 13.86% | -30.35% | -89.21pp | -45.00pp | 31.2% | 39 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -25.56% | 19.41% | 3.87% | -44.98pp | -29.43pp | 35.4% | 15 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -467.79% | -82.31% | -818.37% | -385.48pp | +350.58pp | 55.4% | 26 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 31.26% | 23.03% | 4.84% | +8.24pp | +26.42pp | 28.0% | 11 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -179.46% | -85.44% | -223.69% | -94.02pp | +44.23pp | 29.0% | 49 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -44.35% | 21.95% | 15.04% | -66.30pp | -59.39pp | 22.0% | 10 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -952.31% | -219.36% | -734.70% | -732.96pp | -217.61pp | 43.8% | 22 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 334.51% | 54.33% | 280.88% | +280.18pp | +53.63pp | 91.5% | 7 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 822.60% | 141.54% | 747.74% | +681.06pp | +74.86pp | 63.0% | 16 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 309.13% | 44.50% | 388.07% | +264.63pp | -78.94pp | 88.0% | 3 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 424.51% | 69.84% | 407.15% | +354.68pp | +17.37pp | 66.6% | 22 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 52.91% | 22.65% | 33.66% | +30.26pp | +19.25pp | 24.7% | 10 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -276.20% | -48.38% | -162.77% | -227.82pp | -113.43pp | 44.1% | 32 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -32.07% | 22.27% | -17.12% | -54.34pp | -14.95pp | 26.1% | 17 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -593.44% | -153.31% | -589.06% | -440.13pp | -4.38pp | 56.3% | 27 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 2.29% | 26.20% | 1.98% | -23.91pp | +0.31pp | 23.6% | 14 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -395.42% | -133.05% | -219.78% | -262.37pp | -175.64pp | 45.4% | 39 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 119.39% | 36.98% | 80.46% | +82.42pp | +38.94pp | 22.3% | 13 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -316.58% | -11.54% | -304.93% | -305.03pp | -11.65pp | 60.3% | 25 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 395.89% | 84.91% | 362.29% | +310.99pp | +33.61pp | 96.4% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 561.56% | 113.27% | 411.17% | +448.30pp | +150.39pp | 69.8% | 15 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 525.69% | 90.70% | 440.41% | +434.99pp | +85.28pp | 82.0% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 753.92% | 118.22% | 531.31% | +635.70pp | +222.61pp | 70.0% | 18 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 167.80% | 88.48% | 106.89% | +79.32pp | +60.91pp | 39.3% | 8 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 295.15% | 103.59% | 163.19% | +191.56pp | +131.96pp | 55.0% | 21 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 766.68% | 121.26% | 575.87% | +645.42pp | +190.81pp | 86.2% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 842.54% | 127.67% | 642.13% | +714.87pp | +200.41pp | 48.3% | 30 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 932.95% | 134.45% | 1125.11% | +798.50pp | -192.16pp | 89.8% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1492.83% | 204.78% | 1683.80% | +1288.05pp | -190.97pp | 75.4% | 18 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 665.43% | 139.23% | 778.27% | +526.19pp | -112.84pp | 86.6% | 6 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1050.80% | 214.03% | 1466.30% | +836.77pp | -415.50pp | 84.7% | 23 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1373.89% | 204.90% | 1337.78% | +1168.98pp | +36.11pp | 74.0% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 2577.11% | 381.95% | 2503.08% | +2195.16pp | +74.03pp | 68.3% | 14 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 845.24% | 126.60% | 1036.52% | +718.64pp | -191.28pp | 85.0% | 3 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1036.13% | 123.02% | 1150.82% | +913.11pp | -114.69pp | 76.6% | 15 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1255.72% | 123.58% | 1301.80% | +1132.14pp | -46.08pp | 77.7% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1512.09% | 104.11% | 1283.09% | +1407.99pp | +229.00pp | 74.7% | 13 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1859.79% | 467.09% | 2267.27% | +1392.70pp | -407.48pp | 83.3% | 3 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 4983.17% | 888.47% | 5414.04% | +4094.70pp | -430.87pp | 80.8% | 5 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 183.73% | 26.20% | 1.98% | +157.53pp | +181.74pp | 76.4% | 9 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -322.04% | -133.05% | -219.78% | -188.99pp | -102.26pp | 77.9% | 27 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -31.19% | 22.65% | 33.66% | -53.83pp | -64.85pp | 78.7% | 6 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -440.19% | -48.38% | -162.77% | -391.81pp | -277.42pp | 73.1% | 27 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 291.89% | 54.33% | 280.88% | +237.57pp | +11.01pp | 89.3% | 6 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1201.09% | 141.54% | 747.74% | +1059.54pp | +453.35pp | 89.8% | 21 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 411.55% | 37.50% | 66.62% | +374.05pp | +344.94pp | 93.4% | 5 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 259.64% | 13.86% | -30.35% | +245.78pp | +289.99pp | 81.0% | 40 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 395.26% | 36.98% | 80.46% | +358.28pp | +314.80pp | 66.0% | 7 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -4.69% | -11.54% | -304.93% | +6.85pp | +300.24pp | 79.4% | 21 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -23.59% | 21.95% | 15.04% | -45.54pp | -38.63pp | 81.5% | 9 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -1000.54% | -219.36% | -734.70% | -781.19pp | -265.84pp | 65.9% | 35 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -66.80% | 23.03% | 4.84% | -89.82pp | -71.64pp | 83.4% | 7 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -947.11% | -85.44% | -223.69% | -861.67pp | -723.43pp | 75.7% | 44 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -182.67% | 22.27% | -17.12% | -204.94pp | -165.55pp | 79.0% | 16 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -1142.62% | -153.31% | -589.06% | -989.32pp | -553.57pp | 76.1% | 31 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 110.41% | 19.41% | 3.87% | +90.99pp | +106.53pp | 79.2% | 12 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -641.32% | -82.31% | -818.37% | -559.00pp | +177.06pp | 82.0% | 39 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 379.91% | 44.50% | 388.07% | +335.42pp | -8.16pp | 93.6% | 4 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 544.08% | 69.84% | 407.15% | +474.24pp | +136.93pp | 74.4% | 22 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 391.33% | 84.91% | 362.29% | +306.42pp | +29.04pp | 93.9% | 4 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 477.43% | 113.27% | 411.17% | +364.16pp | +66.25pp | 85.0% | 15 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2278.52% | 467.09% | 2267.27% | +1811.43pp | +11.25pp | 97.4% | 5 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 5424.71% | 888.47% | 5414.04% | +4536.24pp | +10.67pp | 97.1% | 8 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 588.95% | 121.26% | 575.87% | +467.69pp | +13.08pp | 88.3% | 7 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 733.62% | 127.67% | 642.13% | +605.95pp | +91.49pp | 79.4% | 27 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1337.52% | 204.90% | 1337.78% | +1132.61pp | -0.26pp | 85.0% | 10 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2808.88% | 381.95% | 2503.08% | +2426.93pp | +305.80pp | 87.7% | 23 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1297.24% | 123.58% | 1301.80% | +1173.65pp | -4.57pp | 74.0% | 7 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1289.89% | 104.11% | 1283.09% | +1185.78pp | +6.80pp | 83.0% | 31 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 305.34% | 88.48% | 106.89% | +216.86pp | +198.45pp | 85.6% | 6 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 440.91% | 103.59% | 163.19% | +337.32pp | +277.72pp | 84.5% | 22 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 776.81% | 139.23% | 778.27% | +637.58pp | -1.46pp | 89.5% | 7 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1450.49% | 214.03% | 1466.30% | +1236.46pp | -15.81pp | 85.5% | 25 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 484.86% | 90.70% | 440.41% | +394.16pp | +44.45pp | 81.3% | 5 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 732.26% | 118.22% | 531.31% | +614.04pp | +200.95pp | 82.6% | 17 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1036.52% | 126.60% | 1036.52% | +909.92pp | +0.00pp | 87.5% | 5 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1149.22% | 123.02% | 1150.82% | +1026.20pp | -1.61pp | 79.4% | 29 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1125.11% | 134.45% | 1125.11% | +990.66pp | +0.00pp | 97.1% | 6 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1865.44% | 204.78% | 1683.80% | +1660.66pp | +181.64pp | 92.5% | 16 |
| `ENTRY_DELTA_MTF` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 16.02% | 26.20% | 1.98% | -10.18pp | +14.03pp | 20.6% | 9 |
| `ENTRY_DELTA_MTF` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -250.88% | -133.05% | -219.78% | -117.83pp | -31.10pp | 41.9% | 25 |
| `ENTRY_DELTA_MTF` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 39.72% | 22.65% | 33.66% | +17.07pp | +6.06pp | 22.7% | 9 |
| `ENTRY_DELTA_MTF` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -6.37% | -48.38% | -162.77% | +42.01pp | +156.40pp | 40.4% | 26 |
| `ENTRY_DELTA_MTF` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 248.27% | 54.33% | 280.88% | +193.94pp | -32.61pp | 69.7% | 4 |
| `ENTRY_DELTA_MTF` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 730.52% | 141.54% | 747.74% | +588.98pp | -17.22pp | 58.9% | 15 |
| `ENTRY_DELTA_MTF` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 76.45% | 37.50% | 66.62% | +38.94pp | +9.83pp | 17.2% | 4 |
| `ENTRY_DELTA_MTF` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -106.85% | 13.86% | -30.35% | -120.71pp | -76.50pp | 29.1% | 26 |
| `ENTRY_DELTA_MTF` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 124.57% | 36.98% | 80.46% | +87.59pp | +44.11pp | 20.6% | 10 |
| `ENTRY_DELTA_MTF` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -124.82% | -11.54% | -304.93% | -113.28pp | +180.11pp | 60.8% | 19 |
| `ENTRY_DELTA_MTF` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -22.41% | 21.95% | 15.04% | -44.36pp | -37.45pp | 20.3% | 9 |
| `ENTRY_DELTA_MTF` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -1547.66% | -219.36% | -734.70% | -1328.31pp | -812.97pp | 57.2% | 37 |
| `ENTRY_DELTA_MTF` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 36.85% | 23.03% | 4.84% | +13.83pp | +32.01pp | 22.7% | 6 |
| `ENTRY_DELTA_MTF` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -189.66% | -85.44% | -223.69% | -104.22pp | +34.03pp | 25.0% | 34 |
| `ENTRY_DELTA_MTF` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -29.06% | 22.27% | -17.12% | -51.33pp | -11.94pp | 20.8% | 12 |
| `ENTRY_DELTA_MTF` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -679.58% | -153.31% | -589.06% | -526.28pp | -90.53pp | 54.9% | 28 |
| `ENTRY_DELTA_MTF` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -73.15% | 19.41% | 3.87% | -92.56pp | -77.02pp | 29.0% | 12 |
| `ENTRY_DELTA_MTF` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -588.52% | -82.31% | -818.37% | -506.21pp | +229.85pp | 51.1% | 28 |
| `ENTRY_DELTA_MTF` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 311.14% | 44.50% | 388.07% | +266.64pp | -76.93pp | 85.7% | 3 |
| `ENTRY_DELTA_MTF` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 417.74% | 69.84% | 407.15% | +347.90pp | +10.59pp | 66.8% | 17 |
| `ENTRY_DELTA_MTF` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 485.27% | 84.91% | 362.29% | +400.36pp | +122.98pp | 96.5% | 4 |
| `ENTRY_DELTA_MTF` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 617.04% | 113.27% | 411.17% | +503.78pp | +205.87pp | 74.3% | 17 |
| `ENTRY_DELTA_MTF` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1301.02% | 467.09% | 2267.27% | +833.93pp | -966.25pp | 87.1% | 5 |
| `ENTRY_DELTA_MTF` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 4393.12% | 888.47% | 5414.04% | +3504.65pp | -1020.92pp | 87.4% | 7 |
| `ENTRY_DELTA_MTF` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 850.51% | 121.26% | 575.87% | +729.25pp | +274.64pp | 88.7% | 5 |
| `ENTRY_DELTA_MTF` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 914.46% | 127.67% | 642.13% | +786.79pp | +272.34pp | 45.4% | 22 |
| `ENTRY_DELTA_MTF` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1221.39% | 204.90% | 1337.78% | +1016.49pp | -116.39pp | 65.3% | 5 |
| `ENTRY_DELTA_MTF` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2345.15% | 381.95% | 2503.08% | +1963.20pp | -157.93pp | 62.8% | 14 |
| `ENTRY_DELTA_MTF` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1353.15% | 123.58% | 1301.80% | +1229.57pp | +51.34pp | 73.2% | 4 |
| `ENTRY_DELTA_MTF` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1500.27% | 104.11% | 1283.09% | +1396.17pp | +217.18pp | 74.3% | 18 |
| `ENTRY_DELTA_MTF` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 176.89% | 88.48% | 106.89% | +88.41pp | +69.99pp | 39.6% | 7 |
| `ENTRY_DELTA_MTF` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 230.45% | 103.59% | 163.19% | +126.86pp | +67.27pp | 52.2% | 20 |
| `ENTRY_DELTA_MTF` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 526.23% | 139.23% | 778.27% | +386.99pp | -252.04pp | 78.0% | 6 |
| `ENTRY_DELTA_MTF` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 919.33% | 214.03% | 1466.30% | +705.29pp | -546.97pp | 75.6% | 21 |
| `ENTRY_DELTA_MTF` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 433.34% | 90.70% | 440.41% | +342.64pp | -7.06pp | 71.0% | 4 |
| `ENTRY_DELTA_MTF` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 624.76% | 118.22% | 531.31% | +506.54pp | +93.45pp | 60.4% | 16 |
| `ENTRY_DELTA_MTF` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1149.50% | 126.60% | 1036.52% | +1022.90pp | +112.98pp | 90.6% | 5 |
| `ENTRY_DELTA_MTF` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1427.26% | 123.02% | 1150.82% | +1304.24pp | +276.44pp | 76.0% | 17 |
| `ENTRY_DELTA_MTF` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1055.32% | 134.45% | 1125.11% | +920.87pp | -69.79pp | 90.0% | 5 |
| `ENTRY_DELTA_MTF` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1654.06% | 204.78% | 1683.80% | +1449.28pp | -29.74pp | 72.0% | 14 |
| `ENTRY_BB_RECOVERY` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 63.79% | 26.20% | 1.98% | +37.59pp | +61.80pp | 21.9% | 13 |
| `ENTRY_BB_RECOVERY` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -279.21% | -133.05% | -219.78% | -146.16pp | -59.43pp | 43.3% | 44 |
| `ENTRY_BB_RECOVERY` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 51.97% | 22.65% | 33.66% | +29.32pp | +18.30pp | 23.3% | 11 |
| `ENTRY_BB_RECOVERY` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -384.63% | -48.38% | -162.77% | -336.25pp | -221.86pp | 44.1% | 40 |
| `ENTRY_BB_RECOVERY` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 272.91% | 54.33% | 280.88% | +218.59pp | -7.97pp | 81.5% | 5 |
| `ENTRY_BB_RECOVERY` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 794.26% | 141.54% | 747.74% | +652.72pp | +46.52pp | 60.2% | 19 |
| `ENTRY_BB_RECOVERY` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 68.17% | 37.50% | 66.62% | +30.67pp | +1.55pp | 17.2% | 6 |
| `ENTRY_BB_RECOVERY` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -44.34% | 13.86% | -30.35% | -58.20pp | -13.99pp | 33.1% | 48 |
| `ENTRY_BB_RECOVERY` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 142.44% | 36.98% | 80.46% | +105.46pp | +61.98pp | 21.8% | 14 |
| `ENTRY_BB_RECOVERY` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -440.37% | -11.54% | -304.93% | -428.83pp | -135.44pp | 59.8% | 27 |
| `ENTRY_BB_RECOVERY` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -73.81% | 21.95% | 15.04% | -95.76pp | -88.85pp | 20.6% | 10 |
| `ENTRY_BB_RECOVERY` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -1481.60% | -219.36% | -734.70% | -1262.25pp | -746.90pp | 52.3% | 45 |
| `ENTRY_BB_RECOVERY` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 28.06% | 23.03% | 4.84% | +5.04pp | +23.22pp | 26.6% | 10 |
| `ENTRY_BB_RECOVERY` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -144.34% | -85.44% | -223.69% | -58.90pp | +79.35pp | 26.7% | 53 |
| `ENTRY_BB_RECOVERY` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -6.31% | 22.27% | -17.12% | -28.58pp | +10.81pp | 23.0% | 16 |
| `ENTRY_BB_RECOVERY` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -503.61% | -153.31% | -589.06% | -350.30pp | +85.45pp | 48.2% | 40 |
| `ENTRY_BB_RECOVERY` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -8.39% | 19.41% | 3.87% | -27.80pp | -12.26pp | 33.9% | 14 |
| `ENTRY_BB_RECOVERY` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -715.29% | -82.31% | -818.37% | -632.97pp | +103.09pp | 57.9% | 38 |
| `ENTRY_BB_RECOVERY` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 331.59% | 44.50% | 388.07% | +287.09pp | -56.48pp | 90.0% | 5 |
| `ENTRY_BB_RECOVERY` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 440.90% | 69.84% | 407.15% | +371.06pp | +33.76pp | 73.4% | 27 |
| `ENTRY_BB_RECOVERY` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 354.69% | 84.91% | 362.29% | +269.79pp | -7.60pp | 85.5% | 4 |
| `ENTRY_BB_RECOVERY` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 514.39% | 113.27% | 411.17% | +401.12pp | +103.21pp | 66.2% | 20 |
| `ENTRY_BB_RECOVERY` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1355.09% | 467.09% | 2267.27% | +888.00pp | -912.18pp | 68.0% | 6 |
| `ENTRY_BB_RECOVERY` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 4174.24% | 888.47% | 5414.04% | +3285.78pp | -1239.80pp | 68.5% | 8 |
| `ENTRY_BB_RECOVERY` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 885.38% | 121.26% | 575.87% | +764.11pp | +309.50pp | 93.3% | 6 |
| `ENTRY_BB_RECOVERY` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 957.76% | 127.67% | 642.13% | +830.09pp | +315.63pp | 50.6% | 32 |
| `ENTRY_BB_RECOVERY` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1507.10% | 204.90% | 1337.78% | +1302.19pp | +169.32pp | 70.4% | 10 |
| `ENTRY_BB_RECOVERY` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2586.52% | 381.95% | 2503.08% | +2204.57pp | +83.44pp | 65.2% | 23 |
| `ENTRY_BB_RECOVERY` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1261.47% | 123.58% | 1301.80% | +1137.89pp | -40.33pp | 83.6% | 6 |
| `ENTRY_BB_RECOVERY` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1430.34% | 104.11% | 1283.09% | +1326.23pp | +147.25pp | 58.7% | 18 |
| `ENTRY_BB_RECOVERY` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 162.59% | 88.48% | 106.89% | +74.11pp | +55.70pp | 37.7% | 7 |
| `ENTRY_BB_RECOVERY` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 201.41% | 103.59% | 163.19% | +97.81pp | +38.22pp | 54.7% | 25 |
| `ENTRY_BB_RECOVERY` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 942.18% | 139.23% | 778.27% | +802.95pp | +163.91pp | 84.0% | 7 |
| `ENTRY_BB_RECOVERY` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1657.82% | 214.03% | 1466.30% | +1443.79pp | +191.52pp | 83.1% | 28 |
| `ENTRY_BB_RECOVERY` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 459.84% | 90.70% | 440.41% | +369.13pp | +19.43pp | 75.7% | 5 |
| `ENTRY_BB_RECOVERY` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 628.03% | 118.22% | 531.31% | +509.81pp | +96.72pp | 66.4% | 21 |
| `ENTRY_BB_RECOVERY` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 774.96% | 126.60% | 1036.52% | +648.36pp | -261.56pp | 82.7% | 5 |
| `ENTRY_BB_RECOVERY` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 874.77% | 123.02% | 1150.82% | +751.75pp | -276.06pp | 75.2% | 31 |
| `ENTRY_BB_RECOVERY` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1078.14% | 134.45% | 1125.11% | +943.69pp | -46.97pp | 82.7% | 3 |
| `ENTRY_BB_RECOVERY` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1590.29% | 204.78% | 1683.80% | +1385.51pp | -93.51pp | 72.3% | 20 |
| `ENTRY_GOLDEN_RULE` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 253.68% | 84.91% | 362.29% | +168.78pp | -108.60pp | 83.5% | 4 |
| `ENTRY_GOLDEN_RULE` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 357.11% | 113.27% | 411.17% | +243.84pp | -54.07pp | 58.7% | 15 |
| `ENTRY_GOLDEN_RULE` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1695.09% | 467.09% | 2267.27% | +1228.01pp | -572.17pp | 82.9% | 3 |
| `ENTRY_GOLDEN_RULE` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 4816.57% | 888.47% | 5414.04% | +3928.10pp | -597.47pp | 85.9% | 5 |
| `ENTRY_GOLDEN_RULE` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 648.57% | 121.26% | 575.87% | +527.30pp | +72.70pp | 83.1% | 7 |
| `ENTRY_GOLDEN_RULE` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 707.82% | 127.67% | 642.13% | +580.14pp | +65.69pp | 47.6% | 36 |
| `ENTRY_GOLDEN_RULE` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1321.08% | 204.90% | 1337.78% | +1116.18pp | -16.70pp | 71.6% | 5 |
| `ENTRY_GOLDEN_RULE` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2473.80% | 381.95% | 2503.08% | +2091.85pp | -29.28pp | 67.4% | 16 |
| `ENTRY_GOLDEN_RULE` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1138.61% | 123.58% | 1301.80% | +1015.03pp | -163.19pp | 83.6% | 6 |
| `ENTRY_GOLDEN_RULE` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1015.20% | 104.11% | 1283.09% | +911.09pp | -267.89pp | 75.4% | 23 |
| `ENTRY_GOLDEN_RULE` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 160.85% | 88.48% | 106.89% | +72.37pp | +53.96pp | 37.0% | 7 |
| `ENTRY_GOLDEN_RULE` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 251.41% | 103.59% | 163.19% | +147.82pp | +88.23pp | 54.3% | 23 |
| `ENTRY_GOLDEN_RULE` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 763.89% | 139.23% | 778.27% | +624.66pp | -14.38pp | 88.8% | 6 |
| `ENTRY_GOLDEN_RULE` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1226.15% | 214.03% | 1466.30% | +1012.12pp | -240.14pp | 80.5% | 20 |
| `ENTRY_GOLDEN_RULE` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 351.22% | 90.70% | 440.41% | +260.52pp | -89.18pp | 65.5% | 6 |
| `ENTRY_GOLDEN_RULE` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 542.31% | 118.22% | 531.31% | +424.09pp | +11.00pp | 64.4% | 18 |
| `ENTRY_GOLDEN_RULE` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 997.11% | 126.60% | 1036.52% | +870.50pp | -39.42pp | 92.6% | 4 |
| `ENTRY_GOLDEN_RULE` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1129.03% | 123.02% | 1150.82% | +1006.01pp | -21.79pp | 73.0% | 21 |
| `ENTRY_GOLDEN_RULE` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1091.75% | 134.45% | 1125.11% | +957.30pp | -33.36pp | 82.4% | 4 |
| `ENTRY_GOLDEN_RULE` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1553.85% | 204.78% | 1683.80% | +1349.08pp | -129.94pp | 69.8% | 16 |
| `ENTRY_STOCH_HHHL` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 15.89% | 26.20% | 1.98% | -10.31pp | +13.90pp | 20.0% | 13 |
| `ENTRY_STOCH_HHHL` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -283.20% | -133.05% | -219.78% | -150.15pp | -63.42pp | 39.9% | 34 |
| `ENTRY_STOCH_HHHL` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 58.22% | 22.65% | 33.66% | +35.58pp | +24.56pp | 23.1% | 10 |
| `ENTRY_STOCH_HHHL` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -222.21% | -48.38% | -162.77% | -173.83pp | -59.44pp | 38.7% | 31 |
| `ENTRY_STOCH_HHHL` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 451.77% | 54.33% | 280.88% | +397.44pp | +170.89pp | 79.6% | 4 |
| `ENTRY_STOCH_HHHL` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 734.33% | 141.54% | 747.74% | +592.79pp | -13.41pp | 54.1% | 14 |
| `ENTRY_STOCH_HHHL` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 73.70% | 37.50% | 66.62% | +36.19pp | +7.08pp | 17.3% | 5 |
| `ENTRY_STOCH_HHHL` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -47.44% | 13.86% | -30.35% | -61.30pp | -17.09pp | 31.7% | 40 |
| `ENTRY_STOCH_HHHL` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 99.20% | 36.98% | 80.46% | +62.22pp | +18.74pp | 20.6% | 12 |
| `ENTRY_STOCH_HHHL` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -302.91% | -11.54% | -304.93% | -291.37pp | +2.02pp | 58.9% | 26 |
| `ENTRY_STOCH_HHHL` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -39.84% | 21.95% | 15.04% | -61.80pp | -54.88pp | 21.2% | 10 |
| `ENTRY_STOCH_HHHL` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -895.90% | -219.36% | -734.70% | -676.55pp | -161.20pp | 43.3% | 31 |
| `ENTRY_STOCH_HHHL` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 31.73% | 23.03% | 4.84% | +8.71pp | +26.89pp | 25.8% | 10 |
| `ENTRY_STOCH_HHHL` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -213.08% | -85.44% | -223.69% | -127.64pp | +10.61pp | 27.2% | 50 |
| `ENTRY_STOCH_HHHL` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -84.82% | 22.27% | -17.12% | -107.09pp | -67.70pp | 22.6% | 14 |
| `ENTRY_STOCH_HHHL` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -935.25% | -153.31% | -589.06% | -781.94pp | -346.19pp | 53.2% | 28 |
| `ENTRY_STOCH_HHHL` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -36.03% | 19.41% | 3.87% | -55.45pp | -39.91pp | 34.2% | 13 |
| `ENTRY_STOCH_HHHL` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | -468.52% | -82.31% | -818.37% | -386.21pp | +349.85pp | 55.1% | 33 |
| `ENTRY_STOCH_HHHL` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 389.53% | 44.50% | 388.07% | +345.03pp | +1.46pp | 90.0% | 3 |
| `ENTRY_STOCH_HHHL` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 480.41% | 69.84% | 407.15% | +410.57pp | +73.26pp | 68.1% | 22 |
| `ENTRY_STOCH_HHHL` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 116.50% | 84.91% | 362.29% | +31.59pp | -245.79pp | 70.1% | 3 |
| `ENTRY_STOCH_HHHL` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 223.46% | 113.27% | 411.17% | +110.19pp | -187.71pp | 61.4% | 16 |
| `ENTRY_STOCH_HHHL` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1420.22% | 467.09% | 2267.27% | +953.13pp | -847.05pp | 87.8% | 4 |
| `ENTRY_STOCH_HHHL` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 4519.42% | 888.47% | 5414.04% | +3630.96pp | -894.61pp | 91.4% | 7 |
| `ENTRY_STOCH_HHHL` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 706.04% | 121.26% | 575.87% | +584.78pp | +130.17pp | 84.2% | 6 |
| `ENTRY_STOCH_HHHL` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 793.09% | 127.67% | 642.13% | +665.42pp | +150.96pp | 46.9% | 30 |
| `ENTRY_STOCH_HHHL` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1294.86% | 204.90% | 1337.78% | +1089.96pp | -42.92pp | 64.7% | 5 |
| `ENTRY_STOCH_HHHL` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2460.36% | 381.95% | 2503.08% | +2078.41pp | -42.72pp | 64.8% | 17 |
| `ENTRY_STOCH_HHHL` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1302.31% | 123.58% | 1301.80% | +1178.73pp | +0.51pp | 77.8% | 6 |
| `ENTRY_STOCH_HHHL` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1269.84% | 104.11% | 1283.09% | +1165.73pp | -13.25pp | 70.7% | 17 |
| `ENTRY_STOCH_HHHL` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 163.69% | 88.48% | 106.89% | +75.21pp | +56.80pp | 38.6% | 6 |
| `ENTRY_STOCH_HHHL` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 211.18% | 103.59% | 163.19% | +107.59pp | +48.00pp | 54.5% | 25 |
| `ENTRY_STOCH_HHHL` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 416.88% | 139.23% | 778.27% | +277.64pp | -361.39pp | 77.1% | 5 |
| `ENTRY_STOCH_HHHL` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1016.58% | 214.03% | 1466.30% | +802.54pp | -449.72pp | 79.7% | 22 |
| `ENTRY_STOCH_HHHL` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 454.88% | 90.70% | 440.41% | +364.18pp | +14.47pp | 71.6% | 4 |
| `ENTRY_STOCH_HHHL` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 631.31% | 118.22% | 531.31% | +513.09pp | +100.00pp | 65.1% | 19 |
| `ENTRY_STOCH_HHHL` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1003.99% | 126.60% | 1036.52% | +877.38pp | -32.54pp | 87.7% | 4 |
| `ENTRY_STOCH_HHHL` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1090.24% | 123.02% | 1150.82% | +967.22pp | -60.58pp | 76.5% | 20 |
| `ENTRY_STOCH_HHHL` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 974.88% | 134.45% | 1125.11% | +840.43pp | -150.23pp | 83.8% | 4 |
| `ENTRY_STOCH_HHHL` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1460.52% | 204.78% | 1683.80% | +1255.74pp | -223.28pp | 71.6% | 15 |
| `ENTRY_WT_DC` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 278.51% | 84.91% | 362.29% | +193.61pp | -83.78pp | 86.9% | 4 |
| `ENTRY_WT_DC` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 434.01% | 113.27% | 411.17% | +320.74pp | +22.84pp | 63.8% | 16 |
| `ENTRY_WT_DC` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1293.75% | 467.09% | 2267.27% | +826.66pp | -973.52pp | 78.4% | 3 |
| `ENTRY_WT_DC` | SNDK_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 4272.77% | 888.47% | 5414.04% | +3384.31pp | -1141.26pp | 77.5% | 4 |
| `ENTRY_WT_DC` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 903.34% | 121.26% | 575.87% | +782.08pp | +327.47pp | 87.3% | 4 |
| `ENTRY_WT_DC` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 953.88% | 127.67% | 642.13% | +826.21pp | +311.75pp | 49.6% | 39 |
| `ENTRY_WT_DC` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1289.25% | 204.90% | 1337.78% | +1084.34pp | -48.53pp | 68.3% | 4 |
| `ENTRY_WT_DC` | MU_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 2345.59% | 381.95% | 2503.08% | +1963.64pp | -157.49pp | 63.2% | 17 |
| `ENTRY_WT_DC` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1310.73% | 123.58% | 1301.80% | +1187.15pp | +8.93pp | 79.6% | 4 |
| `ENTRY_WT_DC` | MRVL_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1537.37% | 104.11% | 1283.09% | +1433.26pp | +254.28pp | 65.7% | 13 |
| `ENTRY_WT_DC` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 178.34% | 88.48% | 106.89% | +89.86pp | +71.45pp | 40.4% | 9 |
| `ENTRY_WT_DC` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 287.13% | 103.59% | 163.19% | +183.54pp | +123.95pp | 56.0% | 24 |
| `ENTRY_WT_DC` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 549.58% | 139.23% | 778.27% | +410.35pp | -228.69pp | 83.7% | 7 |
| `ENTRY_WT_DC` | INTC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1254.51% | 214.03% | 1466.30% | +1040.48pp | -211.79pp | 81.2% | 18 |
| `ENTRY_WT_DC` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 606.05% | 90.70% | 440.41% | +515.35pp | +165.64pp | 87.1% | 4 |
| `ENTRY_WT_DC` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 778.97% | 118.22% | 531.31% | +660.75pp | +247.66pp | 72.1% | 19 |
| `ENTRY_WT_DC` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1043.14% | 126.60% | 1036.52% | +916.54pp | +6.62pp | 84.7% | 4 |
| `ENTRY_WT_DC` | ARM_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1243.42% | 123.02% | 1150.82% | +1120.40pp | +92.60pp | 79.5% | 19 |
| `ENTRY_WT_DC` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 897.25% | 134.45% | 1125.11% | +762.80pp | -227.86pp | 81.9% | 4 |
| `ENTRY_WT_DC` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY | 1388.34% | 204.78% | 1683.80% | +1183.56pp | -295.46pp | 71.9% | 22 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 359.71% | 84.91% | 387.31% | +274.81pp | -27.60pp | 75.3% | 7 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | -0.68% | 26.20% | 68.24% | -26.88pp | -68.92pp | 7.6% | 76 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 22.73% | 22.65% | 65.05% | +0.08pp | -42.32pp | 7.4% | 73 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 324.00% | 54.33% | 481.90% | +269.67pp | -157.90pp | 86.5% | 8 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2282.59% | 467.09% | 2325.64% | +1815.50pp | -43.05pp | 99.5% | 1 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 99.49% | 121.26% | 707.27% | -21.77pp | -607.78pp | 47.2% | 81 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1264.50% | 204.90% | 1376.23% | +1059.60pp | -111.73pp | 75.1% | 1 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 242.16% | 123.58% | 1672.73% | +118.58pp | -1430.57pp | 44.8% | 19 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 89.67% | 88.48% | 117.46% | +1.19pp | -27.79pp | 20.8% | 42 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 44.94% | 37.50% | 67.76% | +7.43pp | -22.82pp | 12.5% | 40 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 87.27% | 36.98% | 109.33% | +50.29pp | -22.06pp | 19.6% | 2 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1337.65% | 139.23% | 964.02% | +1198.42pp | +373.64pp | 86.5% | 7 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 121.37% | 21.95% | 50.25% | +99.42pp | +71.12pp | 9.8% | 59 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 8.37% | 23.03% | 48.02% | -14.65pp | -39.65pp | 7.5% | 90 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 488.02% | 90.70% | 485.24% | +397.32pp | +2.78pp | 96.1% | 1 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 37.34% | 22.27% | 66.58% | +15.07pp | -29.24pp | 9.6% | 125 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1145.12% | 126.60% | 1082.44% | +1018.51pp | +62.68pp | 74.9% | 16 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 678.73% | 134.45% | 1180.81% | +544.27pp | -502.08pp | 90.0% | 21 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 103.17% | 19.41% | 85.72% | +83.76pp | +17.45pp | 20.6% | 38 |
| `BOTTOM_A_PROTECTIVE_TRAIL` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 86.37% | 44.50% | 400.98% | +41.87pp | -314.61pp | 38.4% | 135 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -75.35% | 13.86% | -30.35% | -89.21pp | -45.00pp | 31.2% | 39 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LDOS_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 13.86% | -30.35% | -13.86pp | +30.35pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -467.79% | -82.31% | -818.37% | -385.48pp | +350.58pp | 55.4% | 26 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ALB_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -82.31% | -818.37% | +82.31pp | +818.37pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -179.46% | -85.44% | -223.69% | -94.02pp | +44.23pp | 29.0% | 49 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | EGO_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -85.44% | -223.69% | +85.44pp | +223.69pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -952.31% | -219.36% | -734.70% | -732.96pp | -217.61pp | 43.8% | 22 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | HL_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -219.36% | -734.70% | +219.36pp | +734.70pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 822.60% | 141.54% | 747.74% | +681.06pp | +74.86pp | 63.0% | 16 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | TTD_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 141.54% | 747.74% | -141.54pp | -747.74pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 424.51% | 69.84% | 407.15% | +354.68pp | +17.37pp | 66.6% | 22 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ACN_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 69.84% | 407.15% | -69.84pp | -407.15pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -276.20% | -48.38% | -162.77% | -227.82pp | -113.43pp | 44.1% | 32 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UEC_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -48.38% | -162.77% | +48.38pp | +162.77pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -593.44% | -153.31% | -589.06% | -440.13pp | -4.38pp | 56.3% | 27 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ASTS_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -153.31% | -589.06% | +153.31pp | +589.06pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -395.42% | -133.05% | -219.78% | -262.37pp | -175.64pp | 45.4% | 39 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UUUU_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -133.05% | -219.78% | +133.05pp | +219.78pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -316.58% | -11.54% | -304.93% | -305.03pp | -11.65pp | 60.3% | 25 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LAC_SHORT | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | -11.54% | -304.93% | +11.54pp | +304.93pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 561.56% | 113.27% | 411.17% | +448.30pp | +150.39pp | 69.8% | 15 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | VLO_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 113.27% | 411.17% | -113.27pp | -411.17pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 753.92% | 118.22% | 531.31% | +635.70pp | +222.61pp | 70.0% | 18 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DINO_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 118.22% | 531.31% | -118.22pp | -531.31pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 295.15% | 103.59% | 163.19% | +191.56pp | +131.96pp | 55.0% | 21 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MPC_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 103.59% | 163.19% | -103.59pp | -163.19pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 842.54% | 127.67% | 642.13% | +714.87pp | +200.41pp | 48.3% | 30 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | PBF_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 127.67% | 642.13% | -127.67pp | -642.13pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1492.83% | 204.78% | 1683.80% | +1288.05pp | -190.97pp | 75.4% | 18 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 301.34% | 84.91% | 387.31% | +216.44pp | -85.97pp | 65.9% | 15 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 19.89% | 26.20% | 68.24% | -6.30pp | -48.34pp | 9.4% | 65 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 19.75% | 22.65% | 65.05% | -2.90pp | -45.30pp | 10.3% | 34 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 445.44% | 54.33% | 481.90% | +391.11pp | -36.46pp | 84.0% | 14 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1206.80% | 467.09% | 2325.64% | +739.71pp | -1118.83pp | 83.8% | 27 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 369.71% | 121.26% | 707.27% | +248.45pp | -337.56pp | 82.5% | 37 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 616.96% | 204.90% | 1376.23% | +412.06pp | -759.27pp | 50.5% | 8 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 373.85% | 123.58% | 1672.73% | +250.27pp | -1298.87pp | 35.5% | 23 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 121.51% | 88.48% | 117.46% | +33.03pp | +4.05pp | 18.9% | 33 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 32.30% | 37.50% | 67.76% | -5.20pp | -35.45pp | 9.9% | 72 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 42.93% | 36.98% | 109.33% | +5.95pp | -66.40pp | 15.6% | 12 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 587.61% | 139.23% | 964.02% | +448.38pp | -376.40pp | 83.0% | 14 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 13.18% | 21.95% | 50.25% | -8.78pp | -37.08pp | 13.9% | 36 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 29.24% | 23.03% | 48.02% | +6.21pp | -18.78pp | 9.2% | 54 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 354.67% | 90.70% | 485.24% | +263.97pp | -130.57pp | 61.3% | 16 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 23.81% | 22.27% | 66.58% | +1.54pp | -42.77pp | 11.7% | 94 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 802.77% | 126.60% | 1082.44% | +676.16pp | -279.67pp | 62.8% | 35 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 961.67% | 134.45% | 1180.81% | +827.22pp | -219.14pp | 84.5% | 34 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 37.47% | 19.41% | 85.72% | +18.06pp | -48.24pp | 26.5% | 30 |
| `BOTTOM_B_DELAYED_LOWER_TOP` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 100.66% | 44.50% | 400.98% | +56.16pp | -300.32pp | 57.8% | 31 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | AMD_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 204.78% | 1683.80% | -204.78pp | -1683.80pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1050.80% | 214.03% | 1466.30% | +836.77pp | -415.50pp | 84.7% | 23 |
| `EXIT_E06_REGRESSION_RETEST` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 175.72% | 37.50% | 67.76% | +138.22pp | +107.96pp | 29.6% | 12 |
| `EXIT_E06_REGRESSION_RETEST` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 167.68% | 19.41% | 85.72% | +148.27pp | +81.97pp | 40.3% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 57.20% | 23.03% | 48.02% | +34.17pp | +9.18pp | 29.2% | 5 |
| `EXIT_E06_REGRESSION_RETEST` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 85.76% | 21.95% | 50.25% | +63.81pp | +35.51pp | 23.5% | 6 |
| `EXIT_E06_REGRESSION_RETEST` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 536.36% | 54.33% | 481.90% | +482.04pp | +54.47pp | 96.2% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 389.39% | 44.50% | 400.98% | +344.89pp | -11.59pp | 89.7% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 137.82% | 22.65% | 65.05% | +115.17pp | +72.77pp | 26.2% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 97.03% | 22.27% | 66.58% | +74.76pp | +30.45pp | 40.5% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 92.26% | 26.20% | 68.24% | +66.06pp | +24.02pp | 21.7% | 5 |
| `EXIT_E06_REGRESSION_RETEST` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 121.12% | 36.98% | 109.33% | +84.14pp | +11.79pp | 20.9% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 429.48% | 84.91% | 387.31% | +344.58pp | +42.18pp | 94.4% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 530.33% | 90.70% | 485.24% | +439.63pp | +45.09pp | 86.0% | 9 |
| `EXIT_E06_REGRESSION_RETEST` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 221.15% | 88.48% | 117.46% | +132.67pp | +103.69pp | 53.0% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 683.44% | 121.26% | 707.27% | +562.17pp | -23.83pp | 94.6% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 927.08% | 134.45% | 1180.81% | +792.63pp | -253.73pp | 99.2% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 661.27% | 139.23% | 964.02% | +522.04pp | -302.75pp | 96.7% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1072.90% | 204.90% | 1376.23% | +867.99pp | -303.33pp | 73.5% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 864.13% | 126.60% | 1082.44% | +737.53pp | -218.30pp | 93.6% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 926.17% | 123.58% | 1672.73% | +802.59pp | -746.56pp | 85.8% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2584.49% | 467.09% | 2325.64% | +2117.40pp | +258.86pp | 99.6% | 1 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | INTC_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 214.03% | 1466.30% | -214.03pp | -1466.30pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 2577.11% | 381.95% | 2503.08% | +2195.16pp | +74.03pp | 68.3% | 14 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MU_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 381.95% | 2503.08% | -381.95pp | -2503.08pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1036.13% | 123.02% | 1150.82% | +913.11pp | -114.69pp | 76.6% | 15 |
| `EXIT_E06_REGRESSION_RETEST` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 175.72% | 37.50% | 67.76% | +138.22pp | +107.96pp | 29.6% | 12 |
| `EXIT_E06_REGRESSION_RETEST` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 167.68% | 19.41% | 85.72% | +148.27pp | +81.97pp | 40.3% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 57.20% | 23.03% | 48.02% | +34.17pp | +9.18pp | 29.2% | 5 |
| `EXIT_E06_REGRESSION_RETEST` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 85.76% | 21.95% | 50.25% | +63.81pp | +35.51pp | 23.5% | 6 |
| `EXIT_E06_REGRESSION_RETEST` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 536.36% | 54.33% | 481.90% | +482.04pp | +54.47pp | 96.2% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 389.39% | 44.50% | 400.98% | +344.89pp | -11.59pp | 89.7% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 137.82% | 22.65% | 65.05% | +115.17pp | +72.77pp | 26.2% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 97.03% | 22.27% | 66.58% | +74.76pp | +30.45pp | 40.5% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 92.26% | 26.20% | 68.24% | +66.06pp | +24.02pp | 21.7% | 5 |
| `EXIT_E06_REGRESSION_RETEST` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 121.12% | 36.98% | 109.33% | +84.14pp | +11.79pp | 20.9% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 429.48% | 84.91% | 387.31% | +344.58pp | +42.18pp | 94.4% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 530.33% | 90.70% | 485.24% | +439.63pp | +45.09pp | 86.0% | 9 |
| `EXIT_E06_REGRESSION_RETEST` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 221.15% | 88.48% | 117.46% | +132.67pp | +103.69pp | 53.0% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 683.44% | 121.26% | 707.27% | +562.17pp | -23.83pp | 94.6% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 927.08% | 134.45% | 1180.81% | +792.63pp | -253.73pp | 99.2% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 661.27% | 139.23% | 964.02% | +522.04pp | -302.75pp | 96.7% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1072.90% | 204.90% | 1376.23% | +867.99pp | -303.33pp | 73.5% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 864.13% | 126.60% | 1082.44% | +737.53pp | -218.30pp | 93.6% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 926.17% | 123.58% | 1672.73% | +802.59pp | -746.56pp | 85.8% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2584.49% | 467.09% | 2325.64% | +2117.40pp | +258.86pp | 99.6% | 1 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ARM_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 123.02% | 1150.82% | -123.02pp | -1150.82pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1512.09% | 104.11% | 1283.09% | +1407.99pp | +229.00pp | 74.7% | 13 |
| `BOTTOM_C_DELAYED_EMERGENCY` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 264.83% | 84.91% | 387.31% | +179.92pp | -122.48pp | 59.8% | 24 |
| `BOTTOM_C_DELAYED_EMERGENCY` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 6.68% | 26.20% | 68.24% | -19.52pp | -61.56pp | 9.7% | 60 |
| `BOTTOM_C_DELAYED_EMERGENCY` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 49.37% | 22.65% | 65.05% | +26.72pp | -15.68pp | 12.7% | 21 |
| `BOTTOM_C_DELAYED_EMERGENCY` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 362.78% | 54.33% | 481.90% | +308.46pp | -119.11pp | 72.4% | 36 |
| `BOTTOM_C_DELAYED_EMERGENCY` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1148.07% | 467.09% | 2325.64% | +680.98pp | -1177.57pp | 78.9% | 78 |
| `BOTTOM_C_DELAYED_EMERGENCY` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 515.24% | 121.26% | 707.27% | +393.98pp | -192.03pp | 72.9% | 26 |
| `BOTTOM_C_DELAYED_EMERGENCY` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 515.11% | 204.90% | 1376.23% | +310.20pp | -861.12pp | 47.1% | 19 |
| `BOTTOM_C_DELAYED_EMERGENCY` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 596.66% | 123.58% | 1672.73% | +473.08pp | -1076.07pp | 36.1% | 27 |
| `BOTTOM_C_DELAYED_EMERGENCY` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 106.41% | 88.48% | 117.46% | +17.93pp | -11.05pp | 18.8% | 29 |
| `BOTTOM_C_DELAYED_EMERGENCY` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 30.91% | 37.50% | 67.76% | -6.59pp | -36.85pp | 11.5% | 75 |
| `BOTTOM_C_DELAYED_EMERGENCY` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 84.54% | 36.98% | 109.33% | +47.57pp | -24.79pp | 14.2% | 26 |
| `BOTTOM_C_DELAYED_EMERGENCY` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 726.93% | 139.23% | 964.02% | +587.70pp | -237.09pp | 79.3% | 19 |
| `BOTTOM_C_DELAYED_EMERGENCY` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 61.84% | 21.95% | 50.25% | +39.88pp | +11.59pp | 10.7% | 66 |
| `BOTTOM_C_DELAYED_EMERGENCY` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 38.84% | 23.03% | 48.02% | +15.81pp | -9.18pp | 9.2% | 49 |
| `BOTTOM_C_DELAYED_EMERGENCY` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 75.55% | 90.70% | 485.24% | -15.16pp | -409.69pp | 40.1% | 82 |
| `BOTTOM_C_DELAYED_EMERGENCY` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 53.22% | 22.27% | 66.58% | +30.95pp | -13.35pp | 11.5% | 82 |
| `BOTTOM_C_DELAYED_EMERGENCY` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 962.74% | 126.60% | 1082.44% | +836.14pp | -119.70pp | 76.9% | 20 |
| `BOTTOM_C_DELAYED_EMERGENCY` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 967.09% | 134.45% | 1180.81% | +832.64pp | -213.72pp | 83.2% | 36 |
| `BOTTOM_C_DELAYED_EMERGENCY` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 36.54% | 19.41% | 85.72% | +17.12pp | -49.18pp | 26.6% | 22 |
| `BOTTOM_C_DELAYED_EMERGENCY` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 131.75% | 44.50% | 400.98% | +87.25pp | -269.23pp | 56.1% | 30 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MRVL_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 104.11% | 1283.09% | -104.11pp | -1283.09pp | 0.0% | 0 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 4983.17% | 888.47% | 5414.04% | +4094.70pp | -430.87pp | 80.8% | 5 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | SNDK_LONG | WIRING_AUDIT | RED_DIAGNOSTIC_DISCONNECTED | 0.00% | 888.47% | 5414.04% | -888.47pp | -5414.04pp | 0.0% | 0 |
| `EXIT_E06_REGRESSION_RETEST` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 175.72% | 37.50% | 67.76% | +138.22pp | +107.96pp | 29.6% | 12 |
| `EXIT_E06_REGRESSION_RETEST` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 167.68% | 19.41% | 85.72% | +148.27pp | +81.97pp | 40.3% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 57.20% | 23.03% | 48.02% | +34.17pp | +9.18pp | 29.2% | 5 |
| `EXIT_E06_REGRESSION_RETEST` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 85.76% | 21.95% | 50.25% | +63.81pp | +35.51pp | 23.5% | 6 |
| `EXIT_E06_REGRESSION_RETEST` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 536.36% | 54.33% | 481.90% | +482.04pp | +54.47pp | 96.2% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 389.39% | 44.50% | 400.98% | +344.89pp | -11.59pp | 89.7% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 137.82% | 22.65% | 65.05% | +115.17pp | +72.77pp | 26.2% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 97.03% | 22.27% | 66.58% | +74.76pp | +30.45pp | 40.5% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 92.26% | 26.20% | 68.24% | +66.06pp | +24.02pp | 21.7% | 5 |
| `EXIT_E06_REGRESSION_RETEST` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 121.12% | 36.98% | 109.33% | +84.14pp | +11.79pp | 20.9% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 429.48% | 84.91% | 387.31% | +344.58pp | +42.18pp | 94.4% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 530.33% | 90.70% | 485.24% | +439.63pp | +45.09pp | 86.0% | 9 |
| `EXIT_E06_REGRESSION_RETEST` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 221.15% | 88.48% | 117.46% | +132.67pp | +103.69pp | 53.0% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 683.44% | 121.26% | 707.27% | +562.17pp | -23.83pp | 94.6% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 927.08% | 134.45% | 1180.81% | +792.63pp | -253.73pp | 99.2% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 661.27% | 139.23% | 964.02% | +522.04pp | -302.75pp | 96.7% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1072.90% | 204.90% | 1376.23% | +867.99pp | -303.33pp | 73.5% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 864.13% | 126.60% | 1082.44% | +737.53pp | -218.30pp | 93.6% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 926.17% | 123.58% | 1672.73% | +802.59pp | -746.56pp | 85.8% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2584.49% | 467.09% | 2325.64% | +2117.40pp | +258.86pp | 99.6% | 1 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -322.04% | -133.05% | -219.78% | -188.99pp | -102.26pp | 77.9% | 27 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -440.19% | -48.38% | -162.77% | -391.81pp | -277.42pp | 73.1% | 27 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1201.09% | 141.54% | 747.74% | +1059.54pp | +453.35pp | 89.8% | 21 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 259.64% | 13.86% | -30.35% | +245.78pp | +289.99pp | 81.0% | 40 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -4.69% | -11.54% | -304.93% | +6.85pp | +300.24pp | 79.4% | 21 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -1000.54% | -219.36% | -734.70% | -781.19pp | -265.84pp | 65.9% | 35 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -947.11% | -85.44% | -223.69% | -861.67pp | -723.43pp | 75.7% | 44 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -1142.62% | -153.31% | -589.06% | -989.32pp | -553.57pp | 76.1% | 31 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -641.32% | -82.31% | -818.37% | -559.00pp | +177.06pp | 82.0% | 39 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 544.08% | 69.84% | 407.15% | +474.24pp | +136.93pp | 74.4% | 22 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 477.43% | 113.27% | 411.17% | +364.16pp | +66.25pp | 85.0% | 15 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 5424.71% | 888.47% | 5414.04% | +4536.24pp | +10.67pp | 97.1% | 8 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 733.62% | 127.67% | 642.13% | +605.95pp | +91.49pp | 79.4% | 27 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2808.88% | 381.95% | 2503.08% | +2426.93pp | +305.80pp | 87.7% | 23 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1289.89% | 104.11% | 1283.09% | +1185.78pp | +6.80pp | 83.0% | 31 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 440.91% | 103.59% | 163.19% | +337.32pp | +277.72pp | 84.5% | 22 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1450.49% | 214.03% | 1466.30% | +1236.46pp | -15.81pp | 85.5% | 25 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 732.26% | 118.22% | 531.31% | +614.04pp | +200.95pp | 82.6% | 17 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1149.22% | 123.02% | 1150.82% | +1026.20pp | -1.61pp | 79.4% | 29 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1865.44% | 204.78% | 1683.80% | +1660.66pp | +181.64pp | 92.5% | 16 |
| `EXIT_PARTIAL_RUNNER` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 192.97% | 37.50% | 67.76% | +155.47pp | +125.21pp | 43.8% | 40 |
| `EXIT_PARTIAL_RUNNER` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | -34.79% | 19.41% | 85.72% | -54.21pp | -120.51pp | 43.8% | 62 |
| `EXIT_PARTIAL_RUNNER` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2.82% | 23.03% | 48.02% | -20.20pp | -45.20pp | 20.6% | 55 |
| `EXIT_PARTIAL_RUNNER` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | -391.49% | 21.95% | 50.25% | -413.44pp | -441.74pp | 34.8% | 87 |
| `EXIT_PARTIAL_RUNNER` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1.38% | 54.33% | 481.90% | -52.95pp | -480.52pp | 57.7% | 54 |
| `EXIT_PARTIAL_RUNNER` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 327.62% | 44.50% | 400.98% | +283.12pp | -73.36pp | 55.3% | 153 |
| `EXIT_PARTIAL_RUNNER` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 101.02% | 22.65% | 65.05% | +78.38pp | +35.97pp | 40.4% | 14 |
| `EXIT_PARTIAL_RUNNER` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | -156.56% | 22.27% | 66.58% | -178.83pp | -223.14pp | 41.3% | 103 |
| `EXIT_PARTIAL_RUNNER` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | -20.76% | 26.20% | 68.24% | -46.96pp | -89.00pp | 27.0% | 45 |
| `EXIT_PARTIAL_RUNNER` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 347.55% | 36.98% | 109.33% | +310.57pp | +238.22pp | 29.7% | 28 |
| `EXIT_PARTIAL_RUNNER` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 374.95% | 84.91% | 387.31% | +290.04pp | -12.36pp | 92.4% | 5 |
| `EXIT_PARTIAL_RUNNER` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 460.60% | 90.70% | 485.24% | +369.90pp | -24.64pp | 81.5% | 11 |
| `EXIT_PARTIAL_RUNNER` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 156.65% | 88.48% | 117.46% | +68.17pp | +39.19pp | 60.6% | 6 |
| `EXIT_PARTIAL_RUNNER` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 135.04% | 121.26% | 707.27% | +13.78pp | -572.23pp | 57.7% | 87 |
| `EXIT_PARTIAL_RUNNER` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1098.02% | 134.45% | 1180.81% | +963.57pp | -82.79pp | 97.8% | 7 |
| `EXIT_PARTIAL_RUNNER` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 903.10% | 139.23% | 964.02% | +763.87pp | -60.92pp | 90.4% | 20 |
| `EXIT_PARTIAL_RUNNER` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1254.16% | 204.90% | 1376.23% | +1049.26pp | -122.07pp | 73.8% | 11 |
| `EXIT_PARTIAL_RUNNER` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 970.74% | 126.60% | 1082.44% | +844.13pp | -111.70pp | 89.5% | 11 |
| `EXIT_PARTIAL_RUNNER` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1314.59% | 123.58% | 1672.73% | +1191.00pp | -358.14pp | 75.1% | 9 |
| `EXIT_PARTIAL_RUNNER` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1024.17% | 467.09% | 2325.64% | +557.08pp | -1301.47pp | 74.8% | 111 |
| `ENTRY_DELTA_MTF` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -250.88% | -133.05% | -219.78% | -117.83pp | -31.10pp | 41.9% | 25 |
| `ENTRY_DELTA_MTF` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -6.37% | -48.38% | -162.77% | +42.01pp | +156.40pp | 40.4% | 26 |
| `ENTRY_DELTA_MTF` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 730.52% | 141.54% | 747.74% | +588.98pp | -17.22pp | 58.9% | 15 |
| `ENTRY_DELTA_MTF` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -106.85% | 13.86% | -30.35% | -120.71pp | -76.50pp | 29.1% | 26 |
| `ENTRY_DELTA_MTF` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -124.82% | -11.54% | -304.93% | -113.28pp | +180.11pp | 60.8% | 19 |
| `ENTRY_DELTA_MTF` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -1547.66% | -219.36% | -734.70% | -1328.31pp | -812.97pp | 57.2% | 37 |
| `ENTRY_DELTA_MTF` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -189.66% | -85.44% | -223.69% | -104.22pp | +34.03pp | 25.0% | 34 |
| `ENTRY_DELTA_MTF` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -679.58% | -153.31% | -589.06% | -526.28pp | -90.53pp | 54.9% | 28 |
| `ENTRY_DELTA_MTF` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -588.52% | -82.31% | -818.37% | -506.21pp | +229.85pp | 51.1% | 28 |
| `ENTRY_DELTA_MTF` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 417.74% | 69.84% | 407.15% | +347.90pp | +10.59pp | 66.8% | 17 |
| `ENTRY_DELTA_MTF` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 617.04% | 113.27% | 411.17% | +503.78pp | +205.87pp | 74.3% | 17 |
| `ENTRY_DELTA_MTF` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 4393.12% | 888.47% | 5414.04% | +3504.65pp | -1020.92pp | 87.4% | 7 |
| `ENTRY_DELTA_MTF` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 914.46% | 127.67% | 642.13% | +786.79pp | +272.34pp | 45.4% | 22 |
| `ENTRY_DELTA_MTF` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2345.15% | 381.95% | 2503.08% | +1963.20pp | -157.93pp | 62.8% | 14 |
| `ENTRY_DELTA_MTF` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1500.27% | 104.11% | 1283.09% | +1396.17pp | +217.18pp | 74.3% | 18 |
| `ENTRY_DELTA_MTF` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 230.45% | 103.59% | 163.19% | +126.86pp | +67.27pp | 52.2% | 20 |
| `ENTRY_DELTA_MTF` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 919.33% | 214.03% | 1466.30% | +705.29pp | -546.97pp | 75.6% | 21 |
| `ENTRY_DELTA_MTF` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 624.76% | 118.22% | 531.31% | +506.54pp | +93.45pp | 60.4% | 16 |
| `ENTRY_DELTA_MTF` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1427.26% | 123.02% | 1150.82% | +1304.24pp | +276.44pp | 76.0% | 17 |
| `ENTRY_DELTA_MTF` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1654.06% | 204.78% | 1683.80% | +1449.28pp | -29.74pp | 72.0% | 14 |
| `EXIT_E01_CHANDELIER` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 339.74% | 84.91% | 387.31% | +254.84pp | -47.56pp | 68.6% | 10 |
| `EXIT_E01_CHANDELIER` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 70.93% | 26.20% | 68.24% | +44.74pp | +2.70pp | 18.7% | 2 |
| `EXIT_E01_CHANDELIER` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 39.39% | 22.65% | 65.05% | +16.74pp | -25.66pp | 16.2% | 7 |
| `EXIT_E01_CHANDELIER` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 487.37% | 54.33% | 481.90% | +433.04pp | +5.47pp | 79.5% | 8 |
| `EXIT_E01_CHANDELIER` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1999.51% | 467.09% | 2325.64% | +1532.43pp | -326.12pp | 97.5% | 4 |
| `EXIT_E01_CHANDELIER` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 515.22% | 121.26% | 707.27% | +393.96pp | -192.05pp | 90.3% | 7 |
| `EXIT_E01_CHANDELIER` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1370.91% | 204.90% | 1376.23% | +1166.01pp | -5.32pp | 63.2% | 5 |
| `EXIT_E01_CHANDELIER` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 292.06% | 123.58% | 1672.73% | +168.47pp | -1380.67pp | 58.8% | 9 |
| `EXIT_E01_CHANDELIER` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 104.60% | 88.48% | 117.46% | +16.12pp | -12.86pp | 21.0% | 23 |
| `EXIT_E01_CHANDELIER` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 36.62% | 37.50% | 67.76% | -0.88pp | -31.14pp | 11.7% | 13 |
| `EXIT_E01_CHANDELIER` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 67.78% | 36.98% | 109.33% | +30.80pp | -41.55pp | 14.0% | 15 |
| `EXIT_E01_CHANDELIER` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 724.79% | 139.23% | 964.02% | +585.56pp | -239.22pp | 84.2% | 10 |
| `EXIT_E01_CHANDELIER` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 48.44% | 21.95% | 50.25% | +26.49pp | -1.81pp | 15.3% | 22 |
| `EXIT_E01_CHANDELIER` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 54.85% | 23.03% | 48.02% | +31.82pp | +6.83pp | 14.5% | 8 |
| `EXIT_E01_CHANDELIER` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 294.19% | 90.70% | 485.24% | +203.49pp | -191.05pp | 64.5% | 8 |
| `EXIT_E01_CHANDELIER` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 72.58% | 22.27% | 66.58% | +50.31pp | +6.00pp | 24.7% | 3 |
| `EXIT_E01_CHANDELIER` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 723.73% | 126.60% | 1082.44% | +597.13pp | -358.71pp | 77.5% | 18 |
| `EXIT_E01_CHANDELIER` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1140.43% | 134.45% | 1180.81% | +1005.98pp | -40.38pp | 96.1% | 4 |
| `EXIT_E01_CHANDELIER` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 65.96% | 19.41% | 85.72% | +46.55pp | -19.75pp | 42.5% | 3 |
| `EXIT_E01_CHANDELIER` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 261.37% | 44.50% | 400.98% | +216.87pp | -139.61pp | 69.6% | 14 |
| `ENTRY_BB_RECOVERY` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -279.21% | -133.05% | -219.78% | -146.16pp | -59.43pp | 43.3% | 44 |
| `ENTRY_BB_RECOVERY` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -384.63% | -48.38% | -162.77% | -336.25pp | -221.86pp | 44.1% | 40 |
| `ENTRY_BB_RECOVERY` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 794.26% | 141.54% | 747.74% | +652.72pp | +46.52pp | 60.2% | 19 |
| `ENTRY_BB_RECOVERY` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -44.34% | 13.86% | -30.35% | -58.20pp | -13.99pp | 33.1% | 48 |
| `ENTRY_BB_RECOVERY` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -440.37% | -11.54% | -304.93% | -428.83pp | -135.44pp | 59.8% | 27 |
| `ENTRY_BB_RECOVERY` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -1481.60% | -219.36% | -734.70% | -1262.25pp | -746.90pp | 52.3% | 45 |
| `ENTRY_BB_RECOVERY` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -144.34% | -85.44% | -223.69% | -58.90pp | +79.35pp | 26.7% | 53 |
| `ENTRY_BB_RECOVERY` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -503.61% | -153.31% | -589.06% | -350.30pp | +85.45pp | 48.2% | 40 |
| `ENTRY_BB_RECOVERY` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -715.29% | -82.31% | -818.37% | -632.97pp | +103.09pp | 57.9% | 38 |
| `ENTRY_BB_RECOVERY` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 440.90% | 69.84% | 407.15% | +371.06pp | +33.76pp | 73.4% | 27 |
| `ENTRY_BB_RECOVERY` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 514.39% | 113.27% | 411.17% | +401.12pp | +103.21pp | 66.2% | 20 |
| `ENTRY_BB_RECOVERY` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 4174.24% | 888.47% | 5414.04% | +3285.78pp | -1239.80pp | 68.5% | 8 |
| `ENTRY_BB_RECOVERY` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 957.76% | 127.67% | 642.13% | +830.09pp | +315.63pp | 50.6% | 32 |
| `ENTRY_BB_RECOVERY` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2586.52% | 381.95% | 2503.08% | +2204.57pp | +83.44pp | 65.2% | 23 |
| `ENTRY_BB_RECOVERY` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1430.34% | 104.11% | 1283.09% | +1326.23pp | +147.25pp | 58.7% | 18 |
| `ENTRY_BB_RECOVERY` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 201.41% | 103.59% | 163.19% | +97.81pp | +38.22pp | 54.7% | 25 |
| `ENTRY_BB_RECOVERY` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1657.82% | 214.03% | 1466.30% | +1443.79pp | +191.52pp | 83.1% | 28 |
| `ENTRY_BB_RECOVERY` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 628.03% | 118.22% | 531.31% | +509.81pp | +96.72pp | 66.4% | 21 |
| `ENTRY_BB_RECOVERY` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 874.77% | 123.02% | 1150.82% | +751.75pp | -276.06pp | 75.2% | 31 |
| `ENTRY_BB_RECOVERY` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1590.29% | 204.78% | 1683.80% | +1385.51pp | -93.51pp | 72.3% | 20 |
| `EXIT_GR_OPPOSITE` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 330.47% | 84.91% | 387.31% | +245.56pp | -56.84pp | 55.6% | 87 |
| `EXIT_GR_OPPOSITE` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 62.08% | 26.20% | 68.24% | +35.88pp | -6.16pp | 19.5% | 55 |
| `EXIT_GR_OPPOSITE` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 84.39% | 22.65% | 65.05% | +61.74pp | +19.34pp | 13.6% | 120 |
| `EXIT_GR_OPPOSITE` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 573.05% | 54.33% | 481.90% | +518.72pp | +91.15pp | 66.5% | 74 |
| `EXIT_GR_OPPOSITE` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1160.41% | 467.09% | 2325.64% | +693.32pp | -1165.23pp | 79.8% | 153 |
| `EXIT_GR_OPPOSITE` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 252.69% | 121.26% | 707.27% | +131.42pp | -454.59pp | 68.3% | 168 |
| `EXIT_GR_OPPOSITE` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 419.38% | 204.90% | 1376.23% | +214.47pp | -956.85pp | 41.2% | 82 |
| `EXIT_GR_OPPOSITE` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | -52.64% | 123.58% | 1672.73% | -176.23pp | -1725.37pp | 45.6% | 125 |
| `EXIT_GR_OPPOSITE` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 115.78% | 88.48% | 117.46% | +27.30pp | -1.68pp | 20.9% | 81 |
| `EXIT_GR_OPPOSITE` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 20.90% | 37.50% | 67.76% | -16.60pp | -46.86pp | 11.6% | 187 |
| `EXIT_GR_OPPOSITE` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 80.64% | 36.98% | 109.33% | +43.66pp | -28.70pp | 15.9% | 125 |
| `EXIT_GR_OPPOSITE` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 131.45% | 139.23% | 964.02% | -7.78pp | -832.56pp | 67.4% | 130 |
| `EXIT_GR_OPPOSITE` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 51.42% | 21.95% | 50.25% | +29.46pp | +1.17pp | 17.8% | 111 |
| `EXIT_GR_OPPOSITE` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 55.89% | 23.03% | 48.02% | +32.87pp | +7.87pp | 16.0% | 51 |
| `EXIT_GR_OPPOSITE` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 465.74% | 90.70% | 485.24% | +375.04pp | -19.50pp | 70.2% | 56 |
| `EXIT_GR_OPPOSITE` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 98.09% | 22.27% | 66.58% | +75.82pp | +31.51pp | 22.0% | 44 |
| `EXIT_GR_OPPOSITE` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 654.59% | 126.60% | 1082.44% | +527.98pp | -427.85pp | 66.0% | 135 |
| `EXIT_GR_OPPOSITE` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 692.14% | 134.45% | 1180.81% | +557.69pp | -488.67pp | 84.5% | 101 |
| `EXIT_GR_OPPOSITE` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 47.68% | 19.41% | 85.72% | +28.26pp | -38.04pp | 41.0% | 36 |
| `EXIT_GR_OPPOSITE` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 196.04% | 44.50% | 400.98% | +151.55pp | -204.93pp | 49.3% | 286 |
| `EXIT_GR_OPPOSITE` | VLO_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 532.61% | 84.91% | 387.31% | +447.70pp | +145.30pp | 97.0% | 0 |
| `EXIT_GR_OPPOSITE` | UUUU_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 68.24% | 26.20% | 68.24% | +42.04pp | +0.00pp | 22.7% | 0 |
| `EXIT_GR_OPPOSITE` | UEC_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 75.31% | 22.65% | 65.05% | +52.66pp | +10.26pp | 21.7% | 0 |
| `EXIT_GR_OPPOSITE` | TTD_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 598.01% | 54.33% | 481.90% | +543.68pp | +116.11pp | 97.6% | 0 |
| `EXIT_GR_OPPOSITE` | SNDK_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 3654.72% | 467.09% | 2325.64% | +3187.63pp | +1329.08pp | 100.0% | 0 |
| `EXIT_GR_OPPOSITE` | PBF_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 774.07% | 121.26% | 707.27% | +652.81pp | +66.80pp | 95.5% | 0 |
| `EXIT_GR_OPPOSITE` | MU_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 1294.20% | 204.90% | 1376.23% | +1089.30pp | -82.03pp | 77.8% | 0 |
| `EXIT_GR_OPPOSITE` | MRVL_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 1203.64% | 123.58% | 1672.73% | +1080.06pp | -469.09pp | 92.4% | 0 |
| `EXIT_GR_OPPOSITE` | MPC_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 161.47% | 88.48% | 117.46% | +72.99pp | +44.01pp | 33.5% | 0 |
| `EXIT_GR_OPPOSITE` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 66.25% | 37.50% | 67.76% | +28.75pp | -1.51pp | 15.9% | 9 |
| `EXIT_GR_OPPOSITE` | LAC_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 93.94% | 36.98% | 109.33% | +56.96pp | -15.40pp | 19.8% | 0 |
| `EXIT_GR_OPPOSITE` | INTC_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 958.64% | 139.23% | 964.02% | +819.41pp | -5.38pp | 98.6% | 0 |
| `EXIT_GR_OPPOSITE` | HL_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 68.65% | 21.95% | 50.25% | +46.70pp | +18.40pp | 24.4% | 0 |
| `EXIT_GR_OPPOSITE` | EGO_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 46.44% | 23.03% | 48.02% | +23.42pp | -1.57pp | 21.6% | 0 |
| `EXIT_GR_OPPOSITE` | DINO_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 618.25% | 90.70% | 485.24% | +527.55pp | +133.02pp | 96.6% | 0 |
| `EXIT_GR_OPPOSITE` | ASTS_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 66.04% | 22.27% | 66.58% | +43.77pp | -0.54pp | 27.5% | 0 |
| `EXIT_GR_OPPOSITE` | ARM_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 1127.82% | 126.60% | 1082.44% | +1001.22pp | +45.38pp | 94.9% | 0 |
| `EXIT_GR_OPPOSITE` | AMD_LONG | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 1258.65% | 134.45% | 1180.81% | +1124.20pp | +77.84pp | 99.9% | 0 |
| `EXIT_GR_OPPOSITE` | ALB_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 85.72% | 19.41% | 85.72% | +66.30pp | +0.00pp | 44.2% | 0 |
| `EXIT_GR_OPPOSITE` | ACN_SHORT | VEC_UNTOUCHED_OOS | RED_DIAGNOSTIC_INERT | 429.13% | 44.50% | 400.98% | +384.63pp | +28.15pp | 92.6% | 0 |
| `ENTRY_STOCH_HHHL` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -283.20% | -133.05% | -219.78% | -150.15pp | -63.42pp | 39.9% | 34 |
| `ENTRY_STOCH_HHHL` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -222.21% | -48.38% | -162.77% | -173.83pp | -59.44pp | 38.7% | 31 |
| `ENTRY_STOCH_HHHL` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 734.33% | 141.54% | 747.74% | +592.79pp | -13.41pp | 54.1% | 14 |
| `ENTRY_STOCH_HHHL` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -47.44% | 13.86% | -30.35% | -61.30pp | -17.09pp | 31.7% | 40 |
| `ENTRY_STOCH_HHHL` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -302.91% | -11.54% | -304.93% | -291.37pp | +2.02pp | 58.9% | 26 |
| `ENTRY_STOCH_HHHL` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -895.90% | -219.36% | -734.70% | -676.55pp | -161.20pp | 43.3% | 31 |
| `ENTRY_STOCH_HHHL` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -213.08% | -85.44% | -223.69% | -127.64pp | +10.61pp | 27.2% | 50 |
| `ENTRY_STOCH_HHHL` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -935.25% | -153.31% | -589.06% | -781.94pp | -346.19pp | 53.2% | 28 |
| `ENTRY_STOCH_HHHL` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -468.52% | -82.31% | -818.37% | -386.21pp | +349.85pp | 55.1% | 33 |
| `ENTRY_STOCH_HHHL` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 480.41% | 69.84% | 407.15% | +410.57pp | +73.26pp | 68.1% | 22 |
| `ENTRY_STOCH_HHHL` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 223.46% | 113.27% | 411.17% | +110.19pp | -187.71pp | 61.4% | 16 |
| `ENTRY_STOCH_HHHL` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 4519.42% | 888.47% | 5414.04% | +3630.96pp | -894.61pp | 91.4% | 7 |
| `ENTRY_STOCH_HHHL` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 793.09% | 127.67% | 642.13% | +665.42pp | +150.96pp | 46.9% | 30 |
| `ENTRY_STOCH_HHHL` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2460.36% | 381.95% | 2503.08% | +2078.41pp | -42.72pp | 64.8% | 17 |
| `ENTRY_STOCH_HHHL` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1269.84% | 104.11% | 1283.09% | +1165.73pp | -13.25pp | 70.7% | 17 |
| `ENTRY_STOCH_HHHL` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 211.18% | 103.59% | 163.19% | +107.59pp | +48.00pp | 54.5% | 25 |
| `ENTRY_STOCH_HHHL` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1016.58% | 214.03% | 1466.30% | +802.54pp | -449.72pp | 79.7% | 22 |
| `ENTRY_STOCH_HHHL` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 631.31% | 118.22% | 531.31% | +513.09pp | +100.00pp | 65.1% | 19 |
| `ENTRY_STOCH_HHHL` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1090.24% | 123.02% | 1150.82% | +967.22pp | -60.58pp | 76.5% | 20 |
| `ENTRY_STOCH_HHHL` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1460.52% | 204.78% | 1683.80% | +1255.74pp | -223.28pp | 71.6% | 15 |
| `EXIT_WT_MTF` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 407.16% | 84.91% | 387.31% | +322.26pp | +19.86pp | 70.6% | 44 |
| `EXIT_WT_MTF` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 47.91% | 26.20% | 68.24% | +21.72pp | -20.32pp | 21.6% | 6 |
| `EXIT_WT_MTF` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 72.61% | 22.65% | 65.05% | +49.96pp | +7.56pp | 21.8% | 6 |
| `EXIT_WT_MTF` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 532.42% | 54.33% | 481.90% | +478.10pp | +50.53pp | 93.7% | 18 |
| `EXIT_WT_MTF` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 2855.99% | 467.09% | 2325.64% | +2388.90pp | +530.35pp | 99.7% | 2 |
| `EXIT_WT_MTF` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 555.69% | 121.26% | 707.27% | +434.43pp | -151.58pp | 81.4% | 25 |
| `EXIT_WT_MTF` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1294.20% | 204.90% | 1376.23% | +1089.30pp | -82.03pp | 77.8% | 0 |
| `EXIT_WT_MTF` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 695.39% | 123.58% | 1672.73% | +571.81pp | -977.33pp | 76.8% | 25 |
| `EXIT_WT_MTF` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 156.61% | 88.48% | 117.46% | +68.13pp | +39.15pp | 23.8% | 32 |
| `EXIT_WT_MTF` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 60.03% | 37.50% | 67.76% | +22.53pp | -7.73pp | 13.2% | 45 |
| `EXIT_WT_MTF` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 95.30% | 36.98% | 109.33% | +58.33pp | -14.03pp | 19.3% | 5 |
| `EXIT_WT_MTF` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 744.17% | 139.23% | 964.02% | +604.94pp | -219.84pp | 89.7% | 13 |
| `EXIT_WT_MTF` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 51.53% | 21.95% | 50.25% | +29.57pp | +1.27pp | 17.3% | 14 |
| `EXIT_WT_MTF` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 54.06% | 23.03% | 48.02% | +31.04pp | +6.05pp | 19.0% | 13 |
| `EXIT_WT_MTF` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 476.46% | 90.70% | 485.24% | +385.76pp | -8.78pp | 84.0% | 27 |
| `EXIT_WT_MTF` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 89.47% | 22.27% | 66.58% | +67.20pp | +22.89pp | 21.2% | 16 |
| `EXIT_WT_MTF` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 788.35% | 126.60% | 1082.44% | +661.74pp | -294.09pp | 87.3% | 28 |
| `EXIT_WT_MTF` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 710.33% | 134.45% | 1180.81% | +575.88pp | -470.48pp | 98.2% | 20 |
| `EXIT_WT_MTF` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 45.46% | 19.41% | 85.72% | +26.05pp | -40.26pp | 42.6% | 10 |
| `EXIT_WT_MTF` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 367.29% | 44.50% | 400.98% | +322.80pp | -33.68pp | 88.0% | 20 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 395.30% | 84.91% | 387.31% | +310.39pp | +7.99pp | 75.8% | 6 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 80.13% | 26.20% | 68.24% | +53.94pp | +11.90pp | 19.1% | 5 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 64.19% | 22.65% | 65.05% | +41.55pp | -0.85pp | 17.7% | 5 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 452.52% | 54.33% | 481.90% | +398.20pp | -29.37pp | 90.5% | 8 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 5558.27% | 467.09% | 2325.64% | +5091.19pp | +3232.64pp | 98.7% | 1 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 586.22% | 121.26% | 707.27% | +464.96pp | -121.05pp | 89.5% | 8 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1331.90% | 204.90% | 1376.23% | +1127.00pp | -44.32pp | 65.2% | 2 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 950.70% | 123.58% | 1672.73% | +827.12pp | -722.03pp | 79.0% | 3 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 137.29% | 88.48% | 117.46% | +48.81pp | +19.83pp | 20.8% | 14 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 46.73% | 37.50% | 67.76% | +9.23pp | -21.02pp | 13.0% | 17 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 75.59% | 36.98% | 109.33% | +38.61pp | -33.74pp | 17.9% | 10 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1079.59% | 139.23% | 964.02% | +940.36pp | +115.57pp | 94.6% | 3 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 43.51% | 21.95% | 50.25% | +21.56pp | -6.74pp | 19.3% | 6 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 60.92% | 23.03% | 48.02% | +37.89pp | +12.90pp | 20.2% | 3 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 526.29% | 90.70% | 485.24% | +435.59pp | +41.05pp | 89.5% | 4 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 67.13% | 22.27% | 66.58% | +44.86pp | +0.55pp | 22.9% | 8 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1340.22% | 126.60% | 1082.44% | +1213.62pp | +257.79pp | 81.1% | 9 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 729.15% | 134.45% | 1180.81% | +594.70pp | -451.66pp | 96.5% | 6 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 60.20% | 19.41% | 85.72% | +40.79pp | -25.51pp | 42.5% | 3 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 261.34% | 44.50% | 400.98% | +216.84pp | -139.64pp | 60.5% | 15 |
| `ENTRY_WT_DC` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 434.01% | 113.27% | 411.17% | +320.74pp | +22.84pp | 63.8% | 16 |
| `ENTRY_WT_DC` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 4272.77% | 888.47% | 5414.04% | +3384.31pp | -1141.26pp | 77.5% | 4 |
| `ENTRY_WT_DC` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 953.88% | 127.67% | 642.13% | +826.21pp | +311.75pp | 49.6% | 39 |
| `ENTRY_WT_DC` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2345.59% | 381.95% | 2503.08% | +1963.64pp | -157.49pp | 63.2% | 17 |
| `ENTRY_WT_DC` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1537.37% | 104.11% | 1283.09% | +1433.26pp | +254.28pp | 65.7% | 13 |
| `ENTRY_WT_DC` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 287.13% | 103.59% | 163.19% | +183.54pp | +123.95pp | 56.0% | 24 |
| `ENTRY_WT_DC` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1254.51% | 214.03% | 1466.30% | +1040.48pp | -211.79pp | 81.2% | 18 |
| `ENTRY_WT_DC` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 778.97% | 118.22% | 531.31% | +660.75pp | +247.66pp | 72.1% | 19 |
| `ENTRY_WT_DC` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1243.42% | 123.02% | 1150.82% | +1120.40pp | +92.60pp | 79.5% | 19 |
| `ENTRY_WT_DC` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1388.34% | 204.78% | 1683.80% | +1183.56pp | -295.46pp | 71.9% | 22 |
| `ENTRY_GOLDEN_RULE` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 357.11% | 113.27% | 411.17% | +243.84pp | -54.07pp | 58.7% | 15 |
| `ENTRY_GOLDEN_RULE` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 4816.57% | 888.47% | 5414.04% | +3928.10pp | -597.47pp | 85.9% | 5 |
| `ENTRY_GOLDEN_RULE` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 707.82% | 127.67% | 642.13% | +580.14pp | +65.69pp | 47.6% | 36 |
| `ENTRY_GOLDEN_RULE` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2473.80% | 381.95% | 2503.08% | +2091.85pp | -29.28pp | 67.4% | 16 |
| `ENTRY_GOLDEN_RULE` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1015.20% | 104.11% | 1283.09% | +911.09pp | -267.89pp | 75.4% | 23 |
| `ENTRY_GOLDEN_RULE` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 251.41% | 103.59% | 163.19% | +147.82pp | +88.23pp | 54.3% | 23 |
| `ENTRY_GOLDEN_RULE` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1226.15% | 214.03% | 1466.30% | +1012.12pp | -240.14pp | 80.5% | 20 |
| `ENTRY_GOLDEN_RULE` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 542.31% | 118.22% | 531.31% | +424.09pp | +11.00pp | 64.4% | 18 |
| `ENTRY_GOLDEN_RULE` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1129.03% | 123.02% | 1150.82% | +1006.01pp | -21.79pp | 73.0% | 21 |
| `ENTRY_GOLDEN_RULE` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1553.85% | 204.78% | 1683.80% | +1349.08pp | -129.94pp | 69.8% | 16 |
| `EXIT_E02_DONCHIAN` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 276.47% | 84.91% | 387.31% | +191.57pp | -110.83pp | 68.2% | 10 |
| `EXIT_E02_DONCHIAN` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 45.45% | 26.20% | 68.24% | +19.25pp | -22.79pp | 21.4% | 1 |
| `EXIT_E02_DONCHIAN` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 63.64% | 22.65% | 65.05% | +40.99pp | -1.41pp | 16.8% | 6 |
| `EXIT_E02_DONCHIAN` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 555.43% | 54.33% | 481.90% | +501.11pp | +73.54pp | 93.9% | 2 |
| `EXIT_E02_DONCHIAN` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 3831.22% | 467.09% | 2325.64% | +3364.13pp | +1505.58pp | 99.2% | 2 |
| `EXIT_E02_DONCHIAN` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 699.96% | 121.26% | 707.27% | +578.70pp | -7.31pp | 94.4% | 1 |
| `EXIT_E02_DONCHIAN` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1294.20% | 204.90% | 1376.23% | +1089.30pp | -82.03pp | 77.8% | 0 |
| `EXIT_E02_DONCHIAN` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 82.49% | 123.58% | 1672.73% | -41.09pp | -1590.23pp | 59.1% | 8 |
| `EXIT_E02_DONCHIAN` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 134.87% | 88.48% | 117.46% | +46.39pp | +17.41pp | 24.3% | 6 |
| `EXIT_E02_DONCHIAN` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 60.19% | 37.50% | 67.76% | +22.69pp | -7.57pp | 13.8% | 9 |
| `EXIT_E02_DONCHIAN` | LAC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 77.35% | 36.98% | 109.33% | +40.37pp | -31.99pp | 19.5% | 4 |
| `EXIT_E02_DONCHIAN` | INTC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1232.42% | 139.23% | 964.02% | +1093.19pp | +268.41pp | 92.1% | 4 |
| `EXIT_E02_DONCHIAN` | HL_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 67.83% | 21.95% | 50.25% | +45.88pp | +17.58pp | 20.3% | 5 |
| `EXIT_E02_DONCHIAN` | EGO_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 48.02% | 23.03% | 48.02% | +24.99pp | +0.00pp | 20.7% | 1 |
| `EXIT_E02_DONCHIAN` | DINO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 618.25% | 90.70% | 485.24% | +527.55pp | +133.02pp | 96.6% | 0 |
| `EXIT_E02_DONCHIAN` | ASTS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 93.63% | 22.27% | 66.58% | +71.36pp | +27.05pp | 22.3% | 5 |
| `EXIT_E02_DONCHIAN` | ARM_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1026.36% | 126.60% | 1082.44% | +899.75pp | -56.08pp | 93.1% | 2 |
| `EXIT_E02_DONCHIAN` | AMD_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 622.98% | 134.45% | 1180.81% | +488.53pp | -557.83pp | 94.2% | 9 |
| `EXIT_E02_DONCHIAN` | ALB_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 67.74% | 19.41% | 85.72% | +48.33pp | -17.98pp | 42.9% | 3 |
| `EXIT_E02_DONCHIAN` | ACN_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 417.03% | 44.50% | 400.98% | +372.54pp | +16.06pp | 90.4% | 1 |
| `ENTRY_LADDER_GREEN` | TTD_SHORT | V8_EXACT_REPLAY | RESEARCH_ONLY | 280.88% | 54.33% | 280.88% | +226.55pp | +0.00pp | 86.1% | 6 |
| `ENTRY_LADDER_GREEN` | ACN_SHORT | V8_EXACT_REPLAY | RESEARCH_ONLY | 388.07% | 44.50% | 388.07% | +343.57pp | +0.00pp | 88.3% | 4 |
| `ENTRY_LADDER_GREEN` | LAC_SHORT | V8_EXACT_REPLAY | RESEARCH_ONLY | 80.46% | 36.98% | 80.46% | +43.48pp | +0.00pp | 13.1% | 7 |
| `ENTRY_LADDER_GREEN` | LDOS_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -30.35% | 13.86% | -30.35% | -44.21pp | +0.00pp | 26.0% | 40 |
| `ENTRY_LADDER_GREEN` | ALB_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -818.37% | -82.31% | -818.37% | -736.06pp | +0.00pp | 61.2% | 39 |
| `ENTRY_LADDER_GREEN` | EGO_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -223.69% | -85.44% | -223.69% | -138.25pp | +0.00pp | 19.6% | 44 |
| `ENTRY_LADDER_GREEN` | HL_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -734.70% | -219.36% | -734.70% | -515.34pp | +0.00pp | 37.6% | 35 |
| `ENTRY_LADDER_GREEN` | TTD_SHORT | VEC_UNTOUCHED_OOS | CONTROL_ROW | 747.74% | 141.54% | 747.74% | +606.20pp | +0.00pp | 66.0% | 21 |
| `ENTRY_LADDER_GREEN` | ACN_SHORT | VEC_UNTOUCHED_OOS | CONTROL_ROW | 407.15% | 69.84% | 407.15% | +337.31pp | +0.00pp | 55.6% | 22 |
| `ENTRY_LADDER_GREEN` | UEC_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -162.77% | -48.38% | -162.77% | -114.39pp | +0.00pp | 38.1% | 27 |
| `ENTRY_LADDER_GREEN` | ASTS_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -589.06% | -153.31% | -589.06% | -435.75pp | +0.00pp | 40.7% | 31 |
| `ENTRY_LADDER_GREEN` | UUUU_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -219.78% | -133.05% | -219.78% | -86.73pp | +0.00pp | 37.5% | 27 |
| `ENTRY_LADDER_GREEN` | LAC_SHORT | VEC_UNTOUCHED_OOS | CONTROL_FAILURE | -304.93% | -11.54% | -304.93% | -293.39pp | +0.00pp | 58.1% | 21 |
| `ENTRY_LADDER_GREEN` | VLO_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 411.17% | 113.27% | 411.17% | +297.91pp | +0.00pp | 57.6% | 15 |
| `ENTRY_LADDER_GREEN` | DINO_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 531.31% | 118.22% | 531.31% | +413.09pp | +0.00pp | 52.0% | 17 |
| `ENTRY_LADDER_GREEN` | MPC_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 163.19% | 103.59% | 163.19% | +59.59pp | +0.00pp | 39.9% | 22 |
| `ENTRY_LADDER_GREEN` | PBF_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 642.13% | 127.67% | 642.13% | +514.45pp | +0.00pp | 44.0% | 27 |
| `ENTRY_LADDER_GREEN` | AMD_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 1683.80% | 204.78% | 1683.80% | +1479.02pp | +0.00pp | 72.4% | 16 |
| `ENTRY_LADDER_GREEN` | INTC_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 1466.30% | 214.03% | 1466.30% | +1252.27pp | +0.00pp | 82.3% | 25 |
| `ENTRY_LADDER_GREEN` | MU_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 2503.08% | 381.95% | 2503.08% | +2121.13pp | +0.00pp | 65.3% | 23 |
| `ENTRY_LADDER_GREEN` | ARM_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 1150.82% | 123.02% | 1150.82% | +1027.80pp | +0.00pp | 77.0% | 29 |
| `ENTRY_LADDER_GREEN` | MRVL_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 1283.09% | 104.11% | 1283.09% | +1178.98pp | +0.00pp | 82.6% | 31 |
| `ENTRY_LADDER_GREEN` | SNDK_LONG | VEC_UNTOUCHED_OOS | CONTROL_ROW | 5414.04% | 888.47% | 5414.04% | +4525.57pp | +0.00pp | 97.0% | 8 |

## Promotion gate

A result remains research-only until: vector discovery is frozen; an untouched chronological OOS fold beats both side-specific B&H and the same-entry control; source HTF future count is zero; every exit owns a lower/reclaim reentry; and backtest_v8 exact replay agrees.
