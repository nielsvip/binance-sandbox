# Exit-engine parity monitor

generated: 2026-10-06T15:00:06.462814+00:00 · lookback: 48.0h

**exit_engine rows in window: 5753** · last: 2026-10-06T14:59:56.966919+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 2913
- **QUICK_OPEN_STRONG**: 2911
- **WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False)**: 2

top leak reasons:
  -  2708  QUICK_OPEN_STRONG_BUY
  -   203  QUICK_OPEN_STRONG_SELL
  -     2  WT_DC_ENTRY

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 1724
- GOLDEN_RULE: 1116
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 311
- BB_RECOVERY_EXIT (BB_RECOVERY_EXIT_ENABLED_TRADIER): 118
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 33
- QUICK_REDUCE_STRONG (ABLATION_DISABLE_QUICK_EXIT): 33
- QUICK_REDUCE_OTHER (ABLATION_DISABLE_QUICK_EXIT): 30
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 29
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 19
- QUICK_REDUCE_NO_PROFIT (ABLATION_DISABLE_QUICK_EXIT): 19
- DC_BREACH_REDUCE (ABLATION_DISABLE_DC_BREACH_REDUCE): 5
- HARDCODED_RALLY_REENTRY (HARDCODED_RALLY_REENTRY_ENABLED): 5
- VEC_DRIVEN_BRIDGE (VEC_DRIVEN_ENABLED): 4
- DELTA_EXIT (DELTA_EXIT_ENABLED): 2

top leaking keys:
  -   115  QUICK_OPEN_STRONG inf:XTZUSDT_LONG
  -    89  QUICK_OPEN_STRONG flz:ETHUSDC_LONG
  -    88  QUICK_OPEN_STRONG inf:AAVEUSDC_LONG
  -    80  QUICK_OPEN_STRONG ang:DOTUSDT_LONG
  -    79  QUICK_OPEN_STRONG inf:1000BONKUSDC_LONG
  -    68  QUICK_OPEN_STRONG inf:EGLDUSDT_LONG
  -    65  QUICK_OPEN_STRONG inf:ARUSDT_LONG
  -    64  QUICK_OPEN_STRONG ang:YFIUSDT_LONG
  -    62  QUICK_OPEN_STRONG flz:ZECUSDC_LONG
  -    61  QUICK_OPEN_STRONG flz:HYPEUSDT_LONG
  -    61  QUICK_OPEN_STRONG inf:THETAUSDT_LONG
  -    58  QUICK_OPEN_STRONG flz:BNBUSDC_LONG
  -    54  QUICK_OPEN_STRONG flz:BTCUSDC_LONG
  -    51  QUICK_OPEN_STRONG fin:DOTUSDT_LONG
  -    50  QUICK_OPEN_STRONG ang:EDUUSDT_LONG

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

## actions in window: {'OPEN': 1166, 'CLOSE': 1399, 'REDUCE': 180, 'AUGMENT': 13, 'QUICK_OPEN': 2938, 'STRONG_REDUCE': 33, 'NO_PROFIT': 19, 'SCALP_REDUCE': 5}
