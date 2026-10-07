# Exit-engine parity monitor

generated: 2026-10-07T11:00:51.296750+00:00 · lookback: 48.0h

**exit_engine rows in window: 1608** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-07T11:00:25.389020+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 514, 'CLOSE': 266, 'REDUCE': 25} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 608
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 544
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 54
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 6
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 194
-    28  RSI2_EXIT_LIVE_rsi2=5.4_thr=10.0
-    22  RSI2_EXIT_LIVE_rsi2=7.0_thr=10.0
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     6  ORPHANED_HEDGE_PARENT_CLOSED
-     5  SUBSTITUTION_FOR_men:COMPUSDT_SHORT
-     5  SUBSTITUTION_FOR_men:ETHUSDC_SHORT
-     5  SUBSTITUTION_FOR_men:RLCUSDT_LONG
-     4  SUBSTITUTION_FOR_men:ALGOUSDT_SHORT
-     4  SUBSTITUTION_FOR_men:BTCUSDC_SHORT
-     4  SUBSTITUTION_FOR_men:AXSUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:ATOMUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:MELANIAUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:QTUMUSDT_SHORT
-     3  SUBSTITUTION_FOR_men:EGLDUSDT_SHORT
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.26% age
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.28% age
-     3  SUBSTITUTION_FOR_men:UNIUSDC_SHORT
-     3  SUBSTITUTION_FOR_men:NMRUSDT_SHORT
-     2  BB_TAKE_4h
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age

## actions in window: {'OPEN': 518, 'CLOSE': 964, 'REDUCE': 32, 'QUICK_CLOSE': 94}
