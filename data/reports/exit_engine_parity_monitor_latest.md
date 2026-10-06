# Exit-engine parity monitor

generated: 2026-10-06T22:00:34.214354+00:00 · lookback: 48.0h

**exit_engine rows in window: 353** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-06T22:00:30.393293+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 142, 'CLOSE': 40, 'REDUCE': 2} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 157
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 122
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 30
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1
- CYCLE_TP (CYCLE_TP_TIERED_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 11
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     2  BB_TAKE_4h
-     1  MARGIN_FREE_FOR_ang:SNXUSDT_LONG
-     1  CB_HTF_EXHAUST_BAD_ENTRY: g=-0.66% age
-     1  SUBSTITUTION_FOR_men:SNXUSDT_LONG

## actions in window: {'OPEN': 146, 'CLOSE': 201, 'REDUCE': 4, 'QUICK_CLOSE': 2}
