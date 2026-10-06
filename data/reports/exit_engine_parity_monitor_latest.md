# Exit-engine parity monitor

generated: 2026-10-06T18:00:17.269906+00:00 · lookback: 48.0h

**exit_engine rows in window: 6207** · last: 2026-10-06T17:59:47.962162+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 3295
- **QUICK_OPEN_STRONG**: 3293
- **WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False)**: 2

top leak reasons:
  -  3063  QUICK_OPEN_STRONG_BUY
  -   230  QUICK_OPEN_STRONG_SELL
  -     2  WT_DC_ENTRY

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 1789
- GOLDEN_RULE: 1173
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 311
- BB_RECOVERY_EXIT (BB_RECOVERY_EXIT_ENABLED_TRADIER): 118
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 39
- QUICK_REDUCE_STRONG (ABLATION_DISABLE_QUICK_EXIT): 33
- QUICK_REDUCE_OTHER (ABLATION_DISABLE_QUICK_EXIT): 30
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 29
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 19
- QUICK_REDUCE_NO_PROFIT (ABLATION_DISABLE_QUICK_EXIT): 19
- DC_BREACH_REDUCE (ABLATION_DISABLE_DC_BREACH_REDUCE): 6
- HARDCODED_RALLY_REENTRY (HARDCODED_RALLY_REENTRY_ENABLED): 5
- VEC_DRIVEN_BRIDGE (VEC_DRIVEN_ENABLED): 4
- DELTA_EXIT (DELTA_EXIT_ENABLED): 3

top leaking keys:
  -   139  QUICK_OPEN_STRONG inf:XTZUSDT_LONG
  -   105  QUICK_OPEN_STRONG inf:THETAUSDT_LONG
  -    99  QUICK_OPEN_STRONG inf:AAVEUSDC_LONG
  -    97  QUICK_OPEN_STRONG inf:EGLDUSDT_LONG
  -    90  QUICK_OPEN_STRONG flz:ETHUSDC_LONG
  -    87  QUICK_OPEN_STRONG inf:1000BONKUSDC_LONG
  -    81  QUICK_OPEN_STRONG inf:IOTAUSDT_LONG
  -    80  QUICK_OPEN_STRONG ang:DOTUSDT_LONG
  -    73  QUICK_OPEN_STRONG ang:YFIUSDT_LONG
  -    71  QUICK_OPEN_STRONG inf:ARUSDT_LONG
  -    67  QUICK_OPEN_STRONG flz:BNBUSDC_LONG
  -    63  QUICK_OPEN_STRONG flz:ZECUSDC_LONG
  -    62  QUICK_OPEN_STRONG flz:HYPEUSDT_LONG
  -    57  QUICK_OPEN_STRONG fin:ATOMUSDT_LONG
  -    56  QUICK_OPEN_STRONG flz:BTCUSDC_LONG

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 1035
-  1007  RANKING_DIRECT_ALL_GREEN
-    28  RANKING_DIRECT_ALL_RED

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 78
-    28  QUICK_OPEN_GOOD (None=None)
-    16  WEBHOOK_HANDLE_SIGNAL (None=None)
-    14  RATIO_REBALANCE (ABLATION_DISABLE_RATIO_REBALANCE=False)
-    14  GUARANTEED_REENTRY (None=None)
-     5  QUICK_REDUCE_SCALP (ABLATION_DISABLE_SCALP_GUARD=False)
-     1  GAIN_EROSION_STOP (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 4
-     2  SAFETY_LIQUIDATION
-     2  SAFETY_MARGIN

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 6
-     5  MTF_GR_WT_EXIT_15m_grTFs=3
-     1  BB_BOUNCE_ENTRY_1h_S

## actions in window: {'OPEN': 1230, 'CLOSE': 1406, 'REDUCE': 180, 'AUGMENT': 13, 'QUICK_OPEN': 3321, 'STRONG_REDUCE': 33, 'NO_PROFIT': 19, 'SCALP_REDUCE': 5}
