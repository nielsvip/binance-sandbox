# evaluate_reentry Condensation + Switch-Tuning Framework — Design (2026-04-16)

## Part A — evaluate_reentry current state

**Location:** `ez_manage.py:16113–17290` (1,177 lines, single async function).

Not 15 sub-functions — the code is one giant inlined pipeline with ~15 distinct **decision blocks**, each marked by `# === ... ===` or `# BC_N` comments. Blocks identified (rough map by observation of the first 200 lines + grep):

| # | Block | Line anchor | Feature / origin | Gated by switch? |
|---|---|---|---|---|
| 1 | Position-size & min-qty sanity | 16125–16130 | Basic guards | No |
| 2 | Conviction + reasons fetch | 16132–16141 | Reads precomputed z-conviction | No |
| 3 | Stoch + HA snapshot | 16143–16144 | Indicator fetch | No |
| 4 | WT 2/3-in-favor rally reentry | 16146–16177 | BC_156 variant | `REENTRY_RALLY_K15M_MAX` + `REENTRY_RALLY_HTF_MIN` (only rally sub-gate) |
| 5 | Guaranteed reentry (bottom / cross / 2WT) | 16178–16220 | BC_156 | `LEGACY_REENTRY_GUARANTEED_BOTTOM/CROSS/2WT` ✓ |
| 6 | DC high break retest bypass | 16221–16231 | check_dc_high_break_retest | No (cooldown only) |
| 7 | TradingPolicy alignment gate | 16232–16239 | Live policy layer | No |
| 8 | Trend/HA gate + bounce exception | 16241–16270 | Legacy | No |
| 9 | Indicator full-pull (~20 fields) | 16271–16286 | Setup for rest | No |
| 10 | Reentry-level/timestamp parse | 16289–16310 | Position memory | No |
| 11 | HTF Quick TP boost (1.5×) | ~16311+ | BC_N (need to verify) | No (inferred) |
| ...12–15+ | The remaining ~900 lines not yet sampled | 16313–17290 | Likely: full conviction eval, multi-TF augment sizing, manipulation flag, delta-engine reentry, hedge reentry, structural range shift reentry, SMA-200 retest, satoshi reentry, news-driven reentry, ratio shift reentry | Mostly NOT switch-gated |

**Core performance problem (per your note):** up to 3s/symbol/3m-tick. At 48 symbols × (1yr/3m = ~175k bars) = **~25 million calls × 3s = unworkable**. At 500ms it'd be 145 days. At 50ms it's 14 days. Need <5ms per call for a 4yr 48-sym sweep to finish in hours.

### Likely hot-spots (to profile, not assume)
1. `await ii(ctx['trade_manager'], symbol)` at line 16131 — Redis/compute round-trip inside loop
2. `await price(symbol, position, 3)` at 16121 — API fallback
3. `check_dc_high_break_retest(i, current_price, is_long)` at 16221 — may re-read indicators
4. `trading_policy.check_entry_alignment(i, is_long)` and `check_htf_trend_aligned(...)` — two policy layer calls each doing multi-TF gate work
5. `safe_fetch_float` called hundreds of times in one invocation (each does exception-wrapping)
6. Per-call creation of small dicts/lists (log strings in hot path even when log level suppresses them — the f-string is built regardless)

### Condensation strategy (proposed — do NOT apply yet)

**Phase 1 — Cheap wins (no behavior change):**
- Replace all `safe_fetch_float(i.get('x', 0), 0)` with a precomputed indicator struct once at top of function (via `_unpack_indicators(i)` helper). Single pass, typed.
- Short-circuit the info-level logs behind `if logger.isEnabledFor(logging.INFO)` so the f-string isn't built when logging suppressed.
- Replace `await ii(...)` with a cached-per-tick fetch (the data is the same 3m indicator snapshot — shouldn't re-await inside the function).
- Move block #1 (size sanity) + #2 (conviction) outside the per-tick path if possible — conviction lives on the indicator snapshot already.

Target: 3s → 300ms per call with zero behavior change.

**Phase 2 — Structural (behavior-preserving refactor):**
- Break the 1,177-line function into ~15 named sub-functions (one per decision block). Each sub-function: pure (takes unpacked struct, returns Signal or None). Main function = a pipeline `for stage in [rally, guaranteed, dc_retest, tp_align, htf_dir, trend_gate, htf_quick_tp, ...]: sig = stage(ctx); if sig: return sig`.
- Every sub-function gated by a `REENTRY_STAGE_<NAME>_ENABLED` switch (default True to preserve behavior).
- Sub-functions become independently sweep-testable.

Target: 300ms → 50ms with zero behavior change + full sweep control.

**Phase 3 — Vectorize for backtest:**
- In V8 backtest mode, the 3m-bar loop iterates one bar at a time per symbol. The sub-functions become numpy-vectorized pre-computations over the full bar array, then per-bar the "decision" is a cheap lookup.
- This is a V8-engine-only change (apply_patches shim). Live still uses the scalar path.

Target: 50ms → <1ms effective (the cost of vectorized setup amortizes across all bars).

### Mandatory before any of Phase 1–3

- `backtest_evaluate_functions.py` validation run — proves refactor produces identical trades to the pre-refactor path for a known day. Per CLAUDE.md this is mandatory for any reentry/exit change.
- Backup of `ez_manage.py` before each phase.
- No live change before Phase 2 lands clean on backtest.

---

## Part B — Switch-Tuning Framework

**User directive:** when a switch produces same/similar results on True vs False, underlying values must be tuned individually until True and False produce distinct (ideally: one significantly better than the other). Systematic, not ad-hoc.

### Framework stages

1. **Canonical switch inventory** — merge:
   - `data/sweep_alerts/canonical_switches.json` (33 required per CLAUDE.md)
   - `data/sweep_alerts/new_loss_exit_switches.md` (9 new this session)
   - Runtime scan: grep `getattr(config, '\\w+_ENABLED'` across ez_manage / tradier_manage / ez_positions_quick / backtest_v8_engine
   - Output: `data/sweep_alerts/switch_registry.json` — one row per switch with: name, file:line of gate, file:line of default, category (entry/exit/sizing/hedge/reentry/ratio), knobs that interact with it (e.g., `CT_WT_VELOCITY_GATE_ENABLED` → `CT_WT_VELOCITY_1H_MIN`)

2. **Dead-switch detection** — a sweep harness that, for each switch:
   - Runs config A (switch=True) and config B (switch=False) across a fixed 48-sym/4yr or 128-sym/2yr reference baseline (crypto S1, stocks S2)
   - Everything else held at current-live values
   - Compares (Sharpe, PnL, trade count, WR)
   - Dead if |delta| / |A+B| < 2% on Sharpe AND trade-count delta < 1%
   - Output: `data/sweep_alerts/dead_switches_<YYYYMMDD>.md`

3. **Underlying-value tuning loop** — for each dead switch:
   - Enumerate its companion knobs (the threshold/multiplier it reads — from step 1 interaction column)
   - Sweep those knobs while forcing switch=True: find a value where True clearly beats the current-live value
   - If none found after a reasonable grid → the switch is BROKEN (likely reads a missing or stale field). Flag for code review.
   - If found → propose the new default value + switch=True as the new live config, pending user approval and a cross-symbol confirmation sweep.

4. **Cadence** — framework runs weekly while V8 is producing trades (~hundreds/minute goal). Each run: 1–3 dead switches processed. Not all at once — CLAUDE.md says "NEVER add >1 new strategy per conversation" and similar prudence applies to flipping multiple proven-dead switches simultaneously.

5. **Integration with `binance_supervisor`** — the framework is a Python module `switch_tuner.py` that the supervisor invokes on demand (subcommand `--tune-switches`), not in the main loop. User triggers manually until proven safe.

### Current dead-switch suspects (from this session's evidence)

From sweep `v8_sweep_crypto_t30_20260416_0034.csv` ablation: all 5 CT_* gates (WT_VELOCITY, 15M_MOMENTUM, DC_CROSSOVER_SKIP, CHOP_4H, VOLUME_SURGE) currently produce status=no_result regardless of True/False — but that's the V8-engine no-result bug, not genuine switch deadness. Re-test after V8 crypto fix (agent a59a078f running).

Known live-applied BC entries that user trusts (not dead, don't re-test):
- BC_170 (`CT_WT_VELOCITY_GATE_ENABLED`): Sharpe 1.94→5.26
- BC_172 (`CT_DC_CROSSOVER_SKIP_ENABLED`): SHORT Sharpe +34%
- BC_150 (`COMPRESSION_BREAKOUT`): active
- BC_151 (`MIN_GAIN_TO_BUY_AGGRESSIVELY` 5→3%): active
- BC_152 (direction-favorable reentry 120min): active
- BC_153 (dynamic ratio dead zone 2pp/5pp): active

Candidates to put on the dead-switch probe first (not evidence they're dead, just plausible to verify):
- `CT_15M_MOMENTUM_GATE_ENABLED` — config.py:729 comment says "standalone backtest proved USELESS — same avg return, worse consistency 54.5%". Verify with tighter sweep that alternate thresholds don't rescue it.
- `CT_CHOP_4H_GATE_ENABLED` — default False; current `choppiness_*` fields MISSING from NPZ (verified this session). Either add the precompute or remove the switch.
- `CT_VOLUME_SURGE_GATE_ENABLED` — default False; `relative_volume_1h/4h` exist but the 1.3 threshold may be too loose on crypto. Sweep 1.2/1.5/2.0/3.0 with switch True.

---

## Part C — Execution order

1. **Wait for V8 crypto fix** (agent a59a078f) — without trades from sweeps, the dead-switch detector has no signal.
2. **Build `switch_registry.json`** (Part B step 1) — pure static analysis, safe to do now.
3. **Phase 1 of evaluate_reentry condensation** — cheap wins (indicator unpacking + log guards). Proof via `backtest_evaluate_functions.py`. Aim for 10x speedup.
4. Once V8 crypto fixed + Phase 1 done → kick cross-symbol sweep (48-sym 4yr crypto S1, 128-sym 2yr tradier S2).
5. Run dead-switch detector against the sweep results.
6. For each dead switch: run tuner, propose new values, user approval gate before live edit.
7. Phase 2 refactor of evaluate_reentry (named sub-functions + per-stage switch).
8. Phase 3 vectorization for V8 sweep (target seconds-per-config).

This is multi-week work. No agent should try to do it in one shot.

## Part D — Anti-regression protections (per CLAUDE.md)

- Every phase: `backtest_evaluate_functions.py` or `backtest_evaluate_functions_tradier.py` MUST match pre-change trades within tolerance.
- No silent switch removals — if a switch is proven dead, it's proposed for REMOVAL with user approval; it's not auto-deleted.
- All 33 canonical switches must remain wired in all 5 files (tradier_manage, ez_manage, config, config_tradier, backtest_v8_engine) — weekly re-verification via `verify_switches.py` (already in launchd).
- No unversioned edits — every config/strategy change gets a `BC_N` marker with sweep evidence.
