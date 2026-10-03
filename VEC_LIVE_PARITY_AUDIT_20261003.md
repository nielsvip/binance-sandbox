# VEC–LIVE PARITY AUDIT 2026-10-03 — "live must trade exactly what the backtest decided"

## Verdict

The settings-parity MECHANISM is exact and complete. What is missing is SETS:
nothing is currently promotable, so live runs TEMPLATE everywhere.

- Live resolves every knob from `per_sym_store` sqlite `full_config` verbatim
  (`_psym_get`: sqlite full_config → sqlite overrides → JSON → cat_side →
  TEMPLATE xlsx → config). A promoted decided set lands on live bit-exact.
- The pilot's promoted set INCLUDES dep-forced masters (sheet C + progress
  `cumulative_overrides`, v15_pilot.py:2964-2974, same `switch_dependencies.json`
  the vec evaluator uses) — live gets the masters vec had.
- Venue band defaults are identical both sides (4h/1.8/0.7/1.0/1.0).
- Watchdog: BOTH sides off (vec `WATCHDOG_DC_VEC_ENABLED=False`; live REQ3/REQ1
  masked by MASTER 2, legacy obligatory default-off since 09-22, EMA50 trigger
  default-off). Parity holds by mutual off — until the gated twin lands on
  BOTH sides together.
- Enforcement already live: PER_SYM gate (gain>0 AND beat-BH, else TEMPLATE
  fallback only if in tradeable_keys), STRICT_VEC_PARITY_MODE LOCK (v8 route
  allowlist — proven firing: `HAIKU_WINNER_AUG` blocked), MASTER 2
  (non-vectorizable knobs forced OFF incl. both watchdog open paths).

## Divergence register (live ≠ backtest until fixed)

| # | Divergence | Fix |
|---|-----------|-----|
| D1 | `AUGMENT_MIN_GAIN_PCT`: vec sweep 0.0, live 3.0 (config.py:139). Every augment-heavy proof assumed 0.0. | Land Agent D's live helper + flip live cat_side, or re-verify proofs at 3.0. Lint M4 blocks silent promotion. |
| D2 | Live reads 1m/3m VALUES (entry alignment/vetting LTF votes, trend gate, quick 1m/3m WT). Vec guard substitutes 15m/removes. Knob-independent — MASTER 2 does not cover it. | PATCH PENDING: patches/PENDING_unlock_master2_valueguard_20261003.md (ii-strip reusing vec's own guard_npz + _1m strip + _psym_get TF clamp). M7 `--scan-sources` fails until applied. NOT parity — open. |
| D10 (CLOSED) | Sheet-vs-verify guard gap? NO — verified by body-read: sheets (`evaluate_prepared` :1036-1047), 365D (`lifecycle_pilot`), scalar engines ALL apply `min_decision_tf_guard`. NPZ holds 3m/5m series (crypto 3m, stocks 5m; 3m WT possibly fabricated-from-15m) and NO 1m. `*_prev` low-TF keys pass raw on BOTH sides (engine reads `close_5m_prev`) = parity-neutral. | None. Record only. |
| D3 | Watchdog both-off. Live will MISS entries the old proofs had (they were fake anyway — 4866 ungated fires). | Gated twin (REQ3+ema50+cd+MTF/GR, 15m+, no 3m) lands vec+live together; then re-verify. |
| D4 | PARITY LOCK allowlist is v8-era. V12 reasons with no token get BLOCKED — including `WT15M_AGAINST_FORCE_CLOSE` (the mandated blocked-side close!). | Edit 6 in patches/PENDING_unlock_wt15m_blocked_side_close_20261003.md adds `WT15M_AGAINST`. Audit every v12 emit reason vs allowlist before promotion. |
| D5 | Sizing: vec floors vs live dynamic sizing. Switch parity ≠ size parity. | Separate track; do not conflate. |
| D6 | SQLite-primary + Mac-live/S1-sweep split. The 10-03 kv flip proved JSON edits alone are silently ignored. | Promotion writes via `upsert()` (sqlite+JSON) ON MAC, then hash-verify Mac sqlite vs S1. Never hand-edit one layer. |
| D7 | All traded sides on TEMPLATE fallback → every TEMPLATE xlsx edit moves live with no promotion record. | Promoting decided sets (full_config snapshots) ends the drift. Until then, TEMPLATE edits are live changes. |
| D8 | 365D gate OFF, `data/confirmed_365d.json` absent → NOTHING is 365D-certified. Per §58 nothing 30D-only is promotable. The promotion queue is honestly EMPTY. | Repair loop on current engine (watchdog-off + recalc pilot) → both-positive → upsert → cert → per-sym gate re-enable. |
| D9 | Vec `_coerce_override` type coercion vs live verbatim reads (e.g. "OFF" strings). | Promotion probe: resolve every knob via `_psym_get` and diff against vec cfg (operator step, needs shell). |

## Promotion procedure (exact)

1. Repair loop (§58) on current engine+NPZ until 30D AND 365D both valid
   (TIM≤80, DD≤30, ≥10 trades/30D, ≥80 trades/365D, ≥330d NPZ coverage) and positive.
2. `python3 tools/parity_lint_promotion.py --sym-side SS --overrides-json <cumulative_overrides> --reasons <emit reasons>` → exit 0.
3. `per_sym_store.upsert(sym_side, overrides, meta={gain/trades/wsharpe/bh/delta...})` ON MAC (writes sqlite+JSON).
4. Probe: resolve all knobs via `_psym_get` == vec cfg (D9); md5 sqlite Mac vs S1.
5. Write 365D cert (`tools/confirm_365d.py`); re-enable gate per sym.
6. Forward parity (7D) confirms live trades the decided routes.

## Why the fleet is quiet (not a bug)

TEMPLATE settings + LIGHT_MODE market + PARITY LOCK + MASTER 2 = few vec-achievable
signals. inf is flat with consensus vetoes, not system blocks. The quiet is the
parity machinery working; volume returns with promoted decided sets, not by
loosening gates.
