# Exit-engine parity monitor

generated: 2026-10-06T04:59:39.349376+00:00 · lookback: 48.0h

**exit_engine rows in window: 2621** · last: 2026-10-06T04:59:38.018362+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 255
- **QUICK_OPEN_STRONG**: 253
- **WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False)**: 2

top leak reasons:
  -   208  QUICK_OPEN_STRONG_BUY
  -    45  QUICK_OPEN_STRONG_SELL
  -     2  WT_DC_ENTRY

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 1229
- GOLDEN_RULE: 751
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 268
- BB_RECOVERY_EXIT (BB_RECOVERY_EXIT_ENABLED_TRADIER): 60
- QUICK_REDUCE_STRONG (ABLATION_DISABLE_QUICK_EXIT): 33
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 32
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 29
- QUICK_REDUCE_NO_PROFIT (ABLATION_DISABLE_QUICK_EXIT): 19
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 15
- QUICK_REDUCE_OTHER (ABLATION_DISABLE_QUICK_EXIT): 12
- VEC_DRIVEN_BRIDGE (VEC_DRIVEN_ENABLED): 4
- DC_BREACH_REDUCE (ABLATION_DISABLE_DC_BREACH_REDUCE): 2
- HARDCODED_RALLY_REENTRY (HARDCODED_RALLY_REENTRY_ENABLED): 2
- DELTA_EXIT (DELTA_EXIT_ENABLED): 2

top leaking keys:
  -    22  QUICK_OPEN_STRONG inf:AAVEUSDC_LONG
  -    18  QUICK_OPEN_STRONG flz:ZECUSDC_LONG
  -    13  QUICK_OPEN_STRONG flz:HYPEUSDT_LONG
  -    12  QUICK_OPEN_STRONG ang:DOTUSDT_LONG
  -    11  QUICK_OPEN_STRONG inf:WLDUSDC_LONG
  -    10  QUICK_OPEN_STRONG ang:AAVEUSDC_LONG
  -     9  QUICK_OPEN_STRONG flz:WLDUSDC_LONG
  -     9  QUICK_OPEN_STRONG fin:ALGOUSDT_LONG
  -     8  QUICK_OPEN_STRONG inf:COTIUSDT_LONG
  -     8  QUICK_OPEN_STRONG men:DOTUSDT_LONG
  -     7  QUICK_OPEN_STRONG ang:AXSUSDT_LONG
  -     6  QUICK_OPEN_STRONG inf:THETAUSDT_LONG
  -     6  QUICK_OPEN_STRONG flz:BTCDOMUSDT_LONG
  -     5  QUICK_OPEN_STRONG ang:ATOMUSDT_LONG
  -     5  QUICK_OPEN_STRONG ang:SNXUSDT_SHORT

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 1095
-  1067  RANKING_DIRECT_ALL_GREEN
-    28  RANKING_DIRECT_ALL_RED

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 38
-    13  RATIO_REBALANCE (ABLATION_DISABLE_RATIO_REBALANCE=False)
-    11  WEBHOOK_HANDLE_SIGNAL (None=None)
-     5  QUICK_REDUCE_SCALP (ABLATION_DISABLE_SCALP_GUARD=False)
-     5  QUICK_OPEN_GOOD (None=None)
-     3  GUARANTEED_REENTRY (None=None)
-     1  GAIN_EROSION_STOP (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 4
-     2  SAFETY_LIQUIDATION
-     2  SAFETY_MARGIN

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 0

## actions in window: {'OPEN': 797, 'CLOSE': 1409, 'REDUCE': 98, 'AUGMENT': 2, 'QUICK_OPEN': 258, 'STRONG_REDUCE': 33, 'NO_PROFIT': 19, 'SCALP_REDUCE': 5}
