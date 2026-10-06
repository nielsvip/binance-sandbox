# Exit-engine parity monitor

generated: 2026-10-06T08:59:46.708285+00:00 · lookback: 48.0h

**exit_engine rows in window: 3745** · last: 2026-10-06T08:59:45.724152+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 1168
- **QUICK_OPEN_STRONG**: 1166
- **WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False)**: 2

top leak reasons:
  -  1031  QUICK_OPEN_STRONG_BUY
  -   135  QUICK_OPEN_STRONG_SELL
  -     2  WT_DC_ENTRY

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 1462
- GOLDEN_RULE: 872
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 304
- BB_RECOVERY_EXIT (BB_RECOVERY_EXIT_ENABLED_TRADIER): 118
- QUICK_REDUCE_STRONG (ABLATION_DISABLE_QUICK_EXIT): 33
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 31
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 29
- QUICK_REDUCE_OTHER (ABLATION_DISABLE_QUICK_EXIT): 23
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 19
- QUICK_REDUCE_NO_PROFIT (ABLATION_DISABLE_QUICK_EXIT): 19
- DC_BREACH_REDUCE (ABLATION_DISABLE_DC_BREACH_REDUCE): 4
- VEC_DRIVEN_BRIDGE (VEC_DRIVEN_ENABLED): 4
- HARDCODED_RALLY_REENTRY (HARDCODED_RALLY_REENTRY_ENABLED): 4
- DELTA_EXIT (DELTA_EXIT_ENABLED): 2

top leaking keys:
  -    62  QUICK_OPEN_STRONG ang:DOTUSDT_LONG
  -    43  QUICK_OPEN_STRONG flz:ZECUSDC_LONG
  -    42  QUICK_OPEN_STRONG fin:DOTUSDT_LONG
  -    38  QUICK_OPEN_STRONG inf:AAVEUSDC_LONG
  -    37  QUICK_OPEN_STRONG flz:HYPEUSDT_LONG
  -    37  QUICK_OPEN_STRONG inf:ARUSDT_LONG
  -    36  QUICK_OPEN_STRONG inf:COTIUSDT_LONG
  -    35  QUICK_OPEN_STRONG inf:WLDUSDC_LONG
  -    33  QUICK_OPEN_STRONG ang:ADAUSDC_LONG
  -    33  QUICK_OPEN_STRONG flz:BTCUSDC_LONG
  -    32  QUICK_OPEN_STRONG ang:AXSUSDT_LONG
  -    26  QUICK_OPEN_STRONG fin:ATOMUSDT_LONG
  -    26  QUICK_OPEN_STRONG flz:ETHUSDC_LONG
  -    26  QUICK_OPEN_STRONG ang:EDUUSDT_LONG
  -    25  QUICK_OPEN_STRONG men:DOTUSDT_LONG

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 1049
-  1021  RANKING_DIRECT_ALL_GREEN
-    28  RANKING_DIRECT_ALL_RED

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 62
-    16  RATIO_REBALANCE (ABLATION_DISABLE_RATIO_REBALANCE=False)
-    16  QUICK_OPEN_GOOD (None=None)
-    14  WEBHOOK_HANDLE_SIGNAL (None=None)
-    10  GUARANTEED_REENTRY (None=None)
-     5  QUICK_REDUCE_SCALP (ABLATION_DISABLE_SCALP_GUARD=False)
-     1  GAIN_EROSION_STOP (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 4
-     2  SAFETY_LIQUIDATION
-     2  SAFETY_MARGIN

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 0

## actions in window: {'CLOSE': 1405, 'REDUCE': 173, 'OPEN': 917, 'AUGMENT': 11, 'QUICK_OPEN': 1182, 'STRONG_REDUCE': 33, 'NO_PROFIT': 19, 'SCALP_REDUCE': 5}
