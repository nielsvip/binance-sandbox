# Exit-engine parity monitor

generated: 2026-10-07T12:00:52.160181+00:00 · lookback: 48.0h

**exit_engine rows in window: 1715** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-07T12:00:27.740969+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 552, 'CLOSE': 287, 'REDUCE': 28} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 635
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 557
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 64
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 6
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 4
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 212
-    28  RSI2_EXIT_LIVE_rsi2=5.4_thr=10.0
-    22  RSI2_EXIT_LIVE_rsi2=7.0_thr=10.0
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     6  ORPHANED_HEDGE_PARENT_CLOSED
-     6  SUBSTITUTION_FOR_men:ETHUSDC_SHORT
-     5  SUBSTITUTION_FOR_men:COMPUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:RLCUSDT_LONG
-     4  SUBSTITUTION_FOR_men:ALGOUSDT_SHORT
-     4  SUBSTITUTION_FOR_men:BTCUSDC_SHORT
-     4  SUBSTITUTION_FOR_men:AXSUSDT_SHORT
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age
-     3  SUBSTITUTION_FOR_men:ATOMUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:MELANIAUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:QTUMUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:EGLDUSDT_SHORT
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.26% age
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.28% age
-     3  SUBSTITUTION_FOR_men:UNIUSDC_SHORT
-     3  SUBSTITUTION_FOR_men:NMRUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:COTIUSDT_SHORT

## actions in window: {'OPEN': 556, 'CLOSE': 1016, 'REDUCE': 35, 'QUICK_CLOSE': 108}
