# Exit-engine parity monitor

generated: 2026-10-06T20:00:22.441438+00:00 · lookback: 48.0h

**exit_engine rows in window: 12** · since 2026-10-06T18:48:00+00:00 · last: 2026-10-06T19:50:39.532964+00:00

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {'OPEN': 8} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 4
- GOLDEN_RULE: 3
- CRYPTO_SPIKE_FADE (CRYPTO_SPIKE_FADE_ENABLED): 1

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 0

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 0

## actions in window: {'OPEN': 12}
