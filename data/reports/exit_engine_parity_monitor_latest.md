# Exit-engine parity monitor

generated: 2026-10-07T13:00:53.539099+00:00 · lookback: 48.0h

**exit_engine rows in window: 1829** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-07T13:00:48.553521+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 579, 'CLOSE': 308, 'REDUCE': 32} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 655
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 557
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 84
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 6
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 4
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 254
-    28  RSI2_EXIT_LIVE_rsi2=5.4_thr=10.0
-    22  RSI2_EXIT_LIVE_rsi2=7.0_thr=10.0
-    21  RSI2_EXIT_LIVE_rsi2=3.0_thr=10.0
-     7  SUBSTITUTION_FOR_men:COMPUSDT_SHORT
-     7  SUBSTITUTION_FOR_men:ETHUSDC_SHORT
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     6  ORPHANED_HEDGE_PARENT_CLOSED
-     5  SUBSTITUTION_FOR_men:ALGOUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:BTCUSDC_SHORT
-     5  SUBSTITUTION_FOR_men:RLCUSDT_LONG
-     4  SUBSTITUTION_FOR_men:QTUMUSDT_SHORT
-     4  SUBSTITUTION_FOR_men:AXSUSDT_SHORT
-     4  SUBSTITUTION_FOR_men:NMRUSDT_SHORT
-     4  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.84% age
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age
-     3  SUBSTITUTION_FOR_men:ATOMUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:MELANIAUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:EGLDUSDT_SHORT
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.26% age
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.28% age

## actions in window: {'OPEN': 583, 'CLOSE': 1087, 'REDUCE': 39, 'QUICK_CLOSE': 120}
