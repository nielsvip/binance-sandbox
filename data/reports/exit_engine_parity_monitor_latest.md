# Exit-engine parity monitor

generated: 2026-10-07T05:00:44.343255+00:00 · lookback: 48.0h

**exit_engine rows in window: 1027** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-07T04:59:54.319104+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 369, 'CLOSE': 174, 'REDUCE': 7} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 377
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 322
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 45
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 6
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 99
-    22  RSI2_EXIT_LIVE_rsi2=7.0_thr=10.0
-    10  RSI2_EXIT_LIVE_rsi2=5.4_thr=10.0
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     4  ORPHANED_HEDGE_PARENT_CLOSED
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.26% age
-     3  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.28% age
-     2  BB_TAKE_4h
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age
-     2  WT_CROSSUNDER_FINAL_L_k=72
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.66% age
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-4.27% age
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.69% age
-     2  SUBSTITUTION_FOR_men:ATOMUSDT_SHORT
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.75% age
-     2  SUBSTITUTION_FOR_men:MELANIAUSDT_SHORT
-     2  SUBSTITUTION_FOR_men:QTUMUSDT_SHORT
-     2  SUBSTITUTION_FOR_men:COMPUSDT_SHORT
-     2  SUBSTITUTION_FOR_men:EGLDUSDT_SHORT
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-2.25% age
-     1  MARGIN_FREE_FOR_ang:SNXUSDT_LONG

## actions in window: {'OPEN': 373, 'CLOSE': 611, 'REDUCE': 14, 'QUICK_CLOSE': 29}
