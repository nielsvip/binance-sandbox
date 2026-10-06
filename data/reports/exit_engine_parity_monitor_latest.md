# Exit-engine parity monitor

generated: 2026-10-06T04:30:17.996032+00:00 · lookback: 48.0h

**exit_engine rows in window: 2429** · last: 2026-10-06T04:30:09.195933+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 111
- **QUICK_OPEN_STRONG**: 109
- **WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False)**: 2

top leak reasons:
  -    96  QUICK_OPEN_STRONG_BUY
  -    13  QUICK_OPEN_STRONG_SELL
  -     2  WT_DC_ENTRY

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 1131
- GOLDEN_RULE: 750
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 226
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 32
- BB_RECOVERY_EXIT (BB_RECOVERY_EXIT_ENABLED_TRADIER): 30
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 29
- QUICK_REDUCE_STRONG (ABLATION_DISABLE_QUICK_EXIT): 25
- QUICK_REDUCE_NO_PROFIT (ABLATION_DISABLE_QUICK_EXIT): 13
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 12
- VEC_DRIVEN_BRIDGE (VEC_DRIVEN_ENABLED): 4
- QUICK_REDUCE_OTHER (ABLATION_DISABLE_QUICK_EXIT): 4
- DC_BREACH_REDUCE (ABLATION_DISABLE_DC_BREACH_REDUCE): 2
- HARDCODED_RALLY_REENTRY (HARDCODED_RALLY_REENTRY_ENABLED): 2
- DELTA_EXIT (DELTA_EXIT_ENABLED): 2

top leaking keys:
  -    12  QUICK_OPEN_STRONG inf:AAVEUSDC_LONG
  -     8  QUICK_OPEN_STRONG inf:COTIUSDT_LONG
  -     8  QUICK_OPEN_STRONG fin:ALGOUSDT_LONG
  -     8  QUICK_OPEN_STRONG ang:DOTUSDT_LONG
  -     5  QUICK_OPEN_STRONG flz:HYPEUSDT_LONG
  -     5  QUICK_OPEN_STRONG ang:AAVEUSDC_LONG
  -     5  QUICK_OPEN_STRONG men:DOTUSDT_LONG
  -     4  QUICK_OPEN_STRONG inf:WLDUSDC_LONG
  -     4  QUICK_OPEN_STRONG ang:ATOMUSDT_LONG
  -     4  QUICK_OPEN_STRONG men:ATOMUSDT_LONG
  -     3  QUICK_OPEN_STRONG inf:APEUSDT_SHORT
  -     3  QUICK_OPEN_STRONG flz:WLDUSDC_LONG
  -     3  QUICK_OPEN_STRONG men:COTIUSDT_LONG
  -     3  QUICK_OPEN_STRONG flz:ZECUSDC_LONG
  -     2  WT_DC_ENTRY men:BTCUSDT_LONG

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 1151
-  1118  RANKING_DIRECT_ALL_GREEN
-    33  RANKING_DIRECT_ALL_RED

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 32
-    11  WEBHOOK_HANDLE_SIGNAL (None=None)
-     9  RATIO_REBALANCE (ABLATION_DISABLE_RATIO_REBALANCE=False)
-     5  QUICK_REDUCE_SCALP (ABLATION_DISABLE_SCALP_GUARD=False)
-     3  GUARANTEED_REENTRY (None=None)
-     3  QUICK_OPEN_GOOD (None=None)
-     1  GAIN_EROSION_STOP (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 4
-     2  SAFETY_LIQUIDATION
-     2  SAFETY_MARGIN

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 0

## actions in window: {'CLOSE': 1416, 'OPEN': 800, 'REDUCE': 56, 'AUGMENT': 2, 'QUICK_OPEN': 112, 'STRONG_REDUCE': 25, 'NO_PROFIT': 13, 'SCALP_REDUCE': 5}
