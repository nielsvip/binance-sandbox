# Exit-engine parity monitor

generated: 2026-10-06T19:00:22.014298+00:00 · lookback: 48.0h

**exit_engine rows in window: 0** · since 2026-10-06T18:48:00+00:00 · last: None

VEC_EXACT-decided orders (the vec engine decided; not gate-judged): {} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE

⚠️ No exit_engine rows yet. Either (a) ez_manage not restarted since the 2026-09-28 instrumentation edit (running procs hold old code), or (b) no execute_now calls happened. Restart ez_manage to activate; check broker-sync.

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 0

## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): 0

## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): 0

## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): 0

## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): 0

## actions in window: {}
