# Exit-engine parity monitor

generated: 2026-10-07T02:00:39.964250+00:00 · lookback: 48.0h

**exit_engine rows in window: 679** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-07T02:00:25.071064+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 271, 'CLOSE': 109, 'REDUCE': 5} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 273
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 229
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 34
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 6
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 20
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     2  BB_TAKE_4h
-     2  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age
-     2  WT_CROSSUNDER_FINAL_L_k=72
-     2  ORPHANED_HEDGE_PARENT_CLOSED
-     1  MARGIN_FREE_FOR_ang:SNXUSDT_LONG
-     1  SUBSTITUTION_FOR_men:SNXUSDT_LONG
-     1  SUBSTITUTION_FOR_men:BBUSDT_LONG
-     1  MARGIN_FREE_FOR_ang:UNIUSDC_SHORT
-     1  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.51% age
-     1  MARGIN_FREE_FOR_ang:GRAMUSDT_SHORT

## actions in window: {'OPEN': 275, 'CLOSE': 385, 'REDUCE': 12, 'QUICK_CLOSE': 7}
