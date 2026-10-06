# Exit-engine parity monitor

generated: 2026-10-06T21:00:25.902710+00:00 · lookback: 48.0h

**exit_engine rows in window: 215** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-06T20:58:12.933183+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 83, 'CLOSE': 17, 'REDUCE': 2} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 103
- EXIT_VELOCITY_WT (EXIT_VELOCITY_WT_ENABLED): 79
- DC_DAYTRADE_TARGET (DC_DAYTRADE_ENABLED): 20
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 1
-     1  WEBHOOK_HANDLE_SIGNAL (None=None)

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 9
-     6  WT_CROSSUNDER_FINAL_L_k=95
-     2  BB_TAKE_4h
-     1  MARGIN_FREE_FOR_ang:SNXUSDT_LONG

## actions in window: {'OPEN': 87, 'CLOSE': 124, 'REDUCE': 3, 'QUICK_CLOSE': 1}
