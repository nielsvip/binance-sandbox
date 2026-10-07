# Exit-engine parity monitor

generated: 2026-10-07T18:01:01.693364+00:00 · lookback: 48.0h

**exit_engine rows in window: 2123** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-07T17:54:48.004466+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 693, 'CLOSE': 374, 'REDUCE': 39} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 703
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 597
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 92
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 6
- WT_LOWER_CROSS_EXIT (WT_LOWER_CROSS_EXIT_TF): 4
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 313
-    28  RSI2_EXIT_LIVE_rsi2=5.4_thr=10.0
-    22  RSI2_EXIT_LIVE_rsi2=7.0_thr=10.0
-    21  RSI2_EXIT_LIVE_rsi2=3.0_thr=10.0
-     9  SUBSTITUTION_FOR_men:COMPUSDT_SHORT
-     9  SUBSTITUTION_FOR_men:BTCUSDC_SHORT
-     9  SUBSTITUTION_FOR_men:ETHUSDC_SHORT
-     8  ORPHANED_HEDGE_PARENT_CLOSED
-     7  SUBSTITUTION_FOR_men:SKYUSDT_SHORT
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     5  SUBSTITUTION_FOR_men:QTUMUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:EGLDUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:ALGOUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:NMRUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:CHRUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:EDUUSDT_LONG
-     5  SUBSTITUTION_FOR_men:RLCUSDT_LONG
-     4  SUBSTITUTION_FOR_men:MELANIAUSDT_SHORT
-     4  SUBSTITUTION_FOR_men:AXSUSDT_SHORT
-     4  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.84% age
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age

## actions in window: {'OPEN': 697, 'CLOSE': 1220, 'REDUCE': 46, 'QUICK_CLOSE': 160}
