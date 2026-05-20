# VEC ↔ LIVE WIRING PLAN — 2026-05-20

**Goal:** Make `tradier_manage.py` + `ez_manage.py` call the SAME `vec_paths/`
decision functions that `backtest_v8_engine.py` already uses. Closing the
0–3% live↔backtest match-rate gap is the #1 finding of the 2026-05-17 trade
+ function audit.

**Constraint (CLAUDE.md):** This is a planning artefact ONLY. No live file is
modified. All call-site swaps in `/tmp/*.diff`. Default flags = `False`.
Promotion to live requires sample-floor backtest evidence per the
"no live-script improvements until backtest proven" mandate
(2026-05-16).

---

## 1 · Vec module inventory (12)

| # | Module (file) | Primary fn | Live gate replicated | Parity test | Cases |
|---|---|---|---|---|---|
| 1 | `vec_paths/stale_mark_price.py` | `evaluate_stale_mark_block_core` / `_vec` | STALE_MARK_PRICE_BLOCK in `ez_manage.py:22832-22980` | `tools/test_stale_mark_price_parity.py` | 13/13 PASS |
| 2 | `vec_paths/emergency_brake.py` | `evaluate_emergency_brake_core` / `_vec` | EMERGENCY_BRAKE in `ez_manage.py:24023-24054` | `tools/test_emergency_brake_parity.py` | 9/9 PASS (300-event vec stream + 150-event lookup) |
| 3 | `vec_paths/quarantine_strategy_validation.py` | `evaluate_quarantine_core` / `_vec` | QUARANTINE_BLOCK in `ez_manage.py:24056-24075` | `tools/test_quarantine_strategy_parity.py` | 17/17 PASS (incl. 500-event synthetic) |
| 4 | `vec_paths/noloss_bypass_wt5of5.py` (+ noloss_obligatory_hedge re-export) | `evaluate_noloss_gate_core`, `check_wt5of5_exit` | NOLOSS / OBLIGATORY_HEDGE in `ez_manage.py:24434-24648`, `tradier_manage.py:6089-6106` | `tools/test_noloss_obligatory_hedge_parity.py` | 18/18 PASS |
| 5 | `vec_paths/hedge_scan_gates.py` | `evaluate_hedge_scan_gates_core` / `_vec` | breathing_hedge_scan + scan_and_hedge_losers in `ez_positions_quick.py:6032+, 16856+` | `tools/test_hedge_scan_gates_parity.py` | 7000/7000 (per memory) |
| 6 | `vec_paths/cooldown_locks.py` | `evaluate_cooldown_locks_core` / `_vec` | `_recent_reduces`/`debounce_exec` in `ez_manage.py:410, 23745, 24095` | `tools/test_cooldown_locks_parity.py` | Mac+S1 PASS |
| 7 | `vec_paths/newborn_protect.py` | `evaluate_newborn_protect_core` / `_vec` | newborn-window guards in `ez_manage.py` R1 path (~20709) | `tools/test_newborn_protect_parity.py` | Mac PASS |
| 8 | `vec_paths/tradeable_state_gates.py` | `evaluate_tradeable_state_gates_core` | `is_symbol_tradeable()` in `tradier_manage.py:9500+`, `_is_symbol_tradeable` in `ez_positions_quick.py:4448` | `tools/test_tradeable_state_gates_parity.py` | Mac PASS |
| 9 | `vec_paths/open_intent_size_gates.py` | `evaluate_open_intent_size_gates_core` / `_vec` | OPEN-intent sizing / OVERTRADE_GUARD in `tradier_manage.py:3201-3230` | `tools/test_open_intent_size_gates_parity.py` | Mac PASS |
| 10 | `vec_paths/protect_balance_overtrade.py` | `evaluate_protect_balance_overtrade_core` / `_vec` | BALANCE_FLOOR_HALT + OVERTRADE_GUARD in `tradier_manage.py:3184-3230` | `tools/test_protect_balance_overtrade_parity.py` | Mac PASS |
| 11 | `vec_paths/circuit_sharpe_gates.py` | `evaluate_circuit_sharpe_gates_core` / `_vec` | Per-account circuit-sharpe halts (paired with EMERGENCY_BRAKE) | `tools/test_circuit_sharpe_gates_parity.py` | Mac PASS |
| 12 | `vec_paths/golden_rule_htf_vote.py` | `evaluate_gr_htf_core` / `_vec` | `golden_rule_htf.score_entry_htf` callers in `ez_manage.py:10741+, 30700, 32422, 38661`; `tradier_manage.py:2791, 5750, 7635` | `tools/test_golden_rule_htf_vote_parity.py` | Mac PASS |

Bonus (covered by tests but not in the canonical 12):
`bar_patterns`, `ttm_squeeze`, `stdev_macro_vec`, `augment_eligibility`.

**Today (2026-05-20)**: `position_evaluator.py:1244-1248` is the SOLE live
file already re-exporting a vec module (`emergency_brake`). The four "live"
trading files (`ez_manage`, `tradier_manage`, `ez_positions_quick`, `position_evaluator`)
import ZERO `vec_paths/` functions otherwise. Backtest engine imports 19+.

---

## 2 · Risk × Impact ranking

| Module | blast_radius | parity_conf | live_lift | Notes |
|---|---|---|---|---|
| stale_mark_price | 2 | 5 | 4 | Single gate, every execute_now tick |
| **emergency_brake** | **2** | **5** | **3** | Account-level rate cap; rare fire but cheap to ship |
| **quarantine_strategy** | **1** | **5** | **3** | Reads JSON, substring match, fail-open |
| noloss/OBLIGATORY_HEDGE | 5 | 5 | 5 | Highest live↔BT delta but BIGGEST blast radius — defer |
| hedge_scan_gates | 5 | 5 | 5 | Drives every hedge open; defer until others land |
| cooldown_locks | 4 | 4 | 5 | Touches `_recent_reduces`/debounce; subtle state |
| newborn_protect | 3 | 4 | 4 | R1 path; risk of double-counting with live R1 |
| tradeable_state_gates | 4 | 4 | 4 | Symbol-list semantics (Tradier physics) |
| open_intent_size_gates | 3 | 4 | 4 | Sizing — affects qty, not just yes/no |
| protect_balance_overtrade | 3 | 4 | 4 | OVERTRADE counter; overlaps live counter dict |
| circuit_sharpe_gates | 3 | 4 | 3 | Per-account; sample-floor sensitive |
| **stale_mark_price** | **2** | **5** | **4** | ★ top pick — see above |
| golden_rule_htf_vote | 5 | 4 | 5 | Highest live-lift, also highest blast — defer |

---

## 3 · Picks (3) — lowest risk × highest near-term parity gain

### Pick A — `stale_mark_price` (TOP PICK)
Smallest live surface (single STALE check at `ez_manage.py:22860+`,
mirrored in vec module). Backtest engine already calls `backtest_should_block`
so the same code path covers both worlds. 13/13 parity PASS today.

### Pick B — `emergency_brake`
Account-level throttle, already re-exported in `position_evaluator.py:1244`
(half-wired). Fires rarely so a shadow-mode divergence log is virtually
free to run. 9/9 PASS plus a 300-event differential fuzz.

### Pick C — `quarantine_strategy_validation`
Tiny gate: reads `data/function_quarantine.json`, substring-matches against
`reason`. Live block sits at `ez_manage.py:24056-24075`. Fail-open, no
quantity manipulation, no state writes. 17/17 PASS incl. 500-event fuzz.

Picks B and C live adjacent (lines 24023–24075). Wiring both in one shadow
window doubles the divergence signal at no extra cost.

---

## 4 · Wiring diffs (in /tmp/, never applied)

- `/tmp/vec_live_wire_stale_mark_price.diff`
- `/tmp/vec_live_wire_emergency_brake.diff`
- `/tmp/vec_live_wire_quarantine_strategy.diff`

Each diff contains:

1. **Import** at top of live file.
2. **Config flag** `LIVE_VEC_<MODULE>_ENABLED = False` (default OFF) added to
   `config.py` and `config_tradier.py`.
3. **Call-site swap** `if LIVE_VEC_<MODULE>_ENABLED: vec_result = vec_fn(...); else: <existing live path>`.
4. **Shadow-mode block** that always runs the vec function in parallel, writes
   `{ts, position_key, action, scalar_result, vec_result, divergent}` to
   `data/live_vs_vec_compare.jsonl`, and fires a `LIVE_VS_VEC_DIVERGENCE`
   warning when results disagree. Shadow runs even when the flag is False so
   we can measure drift before flipping.

### 4-step rollout (identical for all 3)

| Step | Duration | Action | Pre-step parity command |
|---|---|---|---|
| 1 | 1 day | Shadow-mode ON, flag OFF, log divergences (`live_vs_vec_compare.jsonl`) on `trc` paper | `python tools/test_<name>_parity.py` |
| 2 | 1 day | Same as step 1, but tee divergences to stdout + alert channel; no live action change | same |
| 3 | 3 days | `LIVE_VEC_<NAME>_ENABLED=True` on `trc` paper account only | re-run parity + 24h shadow review |
| 4 | indef | `LIVE_VEC_<NAME>_ENABLED=True` for `trb` live (and crypto accounts) — ONLY if Steps 1-3 show <5% divergence rate | re-run parity + cumulative shadow review |

Sample-floor backtest evidence requirement (pool_sharpe >1.0, ≥48 crypto /
≥100 stocks, >1yr, ≥30 trades/sym) applies before Step 3 per
`feedback_no_more_live_improvements_until_backtest_proven_20260516`.

---

## 5 · Effort estimate

| Module | Wiring | Shadow setup | Total dev | Rollout calendar |
|---|---|---|---|---|
| stale_mark_price | 2h | 1h | 3h | 5 days |
| emergency_brake | 3h | 1h | 4h | 5 days |
| quarantine_strategy | 1h | 1h | 2h | 5 days |
| **Sum** | **6h** | **3h** | **9h** | **~2 weeks** (3 picks in parallel; rollout serial per module) |

Full inventory (12 modules) extrapolation: ~50–70 dev hours plus ~10–12
calendar weeks of staged rollout to reach live↔backtest parity across all
gates — assuming no surprises in cooldown_locks, hedge_scan_gates, or the
noloss/OBLIGATORY_HEDGE cluster (high blast radius).
