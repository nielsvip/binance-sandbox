# Exit-engine parity monitor

generated: 2026-10-06T12:59:59.498860+00:00 · lookback: 48.0h

**exit_engine rows in window: 5155** · last: 2026-10-06T12:59:47.701046+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 2388
- **QUICK_OPEN_STRONG**: 2386
- **WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False)**: 2

top leak reasons:
  -  2235  QUICK_OPEN_STRONG_BUY
  -   151  QUICK_OPEN_STRONG_SELL
  -     2  WT_DC_ENTRY

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 1651
- GOLDEN_RULE: 1049
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 310
- BB_RECOVERY_EXIT (BB_RECOVERY_EXIT_ENABLED_TRADIER): 118
- QUICK_REDUCE_STRONG (ABLATION_DISABLE_QUICK_EXIT): 33
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 32
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 29
- QUICK_REDUCE_OTHER (ABLATION_DISABLE_QUICK_EXIT): 27
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 19
- QUICK_REDUCE_NO_PROFIT (ABLATION_DISABLE_QUICK_EXIT): 19
- DC_BREACH_REDUCE (ABLATION_DISABLE_DC_BREACH_REDUCE): 5
- VEC_DRIVEN_BRIDGE (VEC_DRIVEN_ENABLED): 4
- HARDCODED_RALLY_REENTRY (HARDCODED_RALLY_REENTRY_ENABLED): 4
- DELTA_EXIT (DELTA_EXIT_ENABLED): 2

top leaking keys:
  -    80  QUICK_OPEN_STRONG ang:DOTUSDT_LONG
  -    78  QUICK_OPEN_STRONG inf:XTZUSDT_LONG
  -    70  QUICK_OPEN_STRONG flz:ETHUSDC_LONG
  -    64  QUICK_OPEN_STRONG inf:AAVEUSDC_LONG
  -    63  QUICK_OPEN_STRONG inf:ARUSDT_LONG
  -    62  QUICK_OPEN_STRONG flz:ZECUSDC_LONG
  -    60  QUICK_OPEN_STRONG flz:HYPEUSDT_LONG
  -    54  QUICK_OPEN_STRONG ang:YFIUSDT_LONG
  -    52  QUICK_OPEN_STRONG inf:1000BONKUSDC_LONG
  -    49  QUICK_OPEN_STRONG fin:DOTUSDT_LONG
  -    48  QUICK_OPEN_STRONG inf:COTIUSDT_LONG
  -    48  QUICK_OPEN_STRONG ang:EDUUSDT_LONG
  -    45  QUICK_OPEN_STRONG fin:ATOMUSDT_LONG
  -    45  QUICK_OPEN_STRONG flz:BNBUSDC_LONG
  -    44  QUICK_OPEN_STRONG ang:ADAUSDC_LONG

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 1035
-  1007  RANKING_DIRECT_ALL_GREEN
-    28  RANKING_DIRECT_ALL_RED

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 77
-    27  QUICK_OPEN_GOOD (None=None)
-    16  WEBHOOK_HANDLE_SIGNAL (None=None)
-    14  RATIO_REBALANCE (ABLATION_DISABLE_RATIO_REBALANCE=False)
-    14  GUARANTEED_REENTRY (None=None)
-     5  QUICK_REDUCE_SCALP (ABLATION_DISABLE_SCALP_GUARD=False)
-     1  GAIN_EROSION_STOP (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 4
-     2  SAFETY_LIQUIDATION
-     2  SAFETY_MARGIN

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 0

## actions in window: {'OPEN': 1097, 'CLOSE': 1398, 'REDUCE': 177, 'AUGMENT': 13, 'QUICK_OPEN': 2413, 'STRONG_REDUCE': 33, 'NO_PROFIT': 19, 'SCALP_REDUCE': 5}
