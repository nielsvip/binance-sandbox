# 📋 NEW-STRATEGY PROPOSAL — Live-wire WT_DC-detailed + BB-squeeze to unlock 12 robust configs
**Date:** 2026-09-28 · **Status:** STRATEGY A **IMPLEMENTED (default OFF)** per user "unlock and go" 2026-09-28. Wiring done + smoke-tested; NOT yet flipped live for any sym (needs backtest_v12_engine parity re-run on the 11, then per-sym flip). Strategy B (BB alignment) still deferred.

### IMPLEMENTED 2026-09-28 (Strategy A — all behind `WT_DC_DETAILED_SCORER_ENABLED`, default False)
- `config.py` + `config_tradier.py`: `WT_DC_DETAILED_SCORER_ENABLED: bool = False`. `v12_quick_engine.QuickConfig`: same field.
- `tradier_indicators.py` + `ez_indicators.py`: emit `wt_cross_bull/bear_{tf}` (=1 iff cross on current bar, `bars_ago==0`) — parity-exact with NPZ `wt_cross_bull/bear_{tf}`. (The other ~20 scorer fields were already emitted live.)
- `tradier_manage.py:12417`: passes `detailed=_cfg("WT_DC_DETAILED_SCORER_ENABLED", …)` (per-sym flippable via per_sym_active_config_stocks.json).
- `backtest_v12_engine.py` verifier wrapper: honors the switch so parity stays faithful.
- v15 pilot: switch already in `data/v15_wired_switches.json` (186) + TEMPLATE_*.xlsx (both True/False rows, added by v15 agent); I added it to `data/opportune_filter_map.json` ENTRY_CONFIRMATION_GATES for all 4 venue_sides (10–11 yellow filters). v12_quick_engine already reads the switch → **sweeps now.**
- Smoke test passed: simple score 80 vs detailed 55 on same dict (real algorithm difference); string 'True'/'False' template values coerced to bool by pilot.
- **Crypto-live boundary:** ez (crypto) has NO live `wt_dc_score_entry` caller, so for crypto this switch affects **backtest/sweep only** — the 4 crypto held configs can't go live on WT_DC-detailed until an ez entry caller is added (separate change). Stocks (7 configs) are fully live-wireable now.
- Backups: `backups/before_wtdc_detailed_livewire_202609281615.*` (7 code files) + `backups/before_wtdc_detailed_template_202609281625.*` (4 templates + map).


**Governs:** NEW STRATEGY PROHIBITION (present entry/exit/data/freq/risk first) + BACKTEST_BIBLE §13 (sweep + paper + parity + stress before live).

---

## 0. Why (the payoff)
Walk-forward validation produced **34 robust configs** (generalize out-of-sample ≥4/5 windows AND 365D-positive). **22 went live** today because their edge comes from **already-live-wired exit switches** (DC stop/target, WT-lower-cross). The remaining **12 are held** because their edge depends on switches that exist only in the **vector backtest**, not the live path — so promoting them now would make **live ≠ backtest** (the NO-LIES trap). This proposal wires those two switch families into live so the 12 can be promoted safely.

**The 12 held configs and what they need:**
| relies on | configs |
|---|---|
| **WT_DC-detailed** (`WT_DC_ENABLED` + `WT_DC_DETAILED_SCORER_ENABLED` + `WT_DC_TF_ENTRY`/`WT_DC_DC_TF`) | EGLDUSDT_LONG, PTBUSDT_LONG, RLCUSDT_LONG, XLMUSDT_LONG, ALMU_SHORT, AU_SHORT, CF_LONG, CMC_LONG, CRWD_SHORT, IBM_SHORT, NVDA_SHORT (11) |
| **BB-squeeze** (`BB_SQUEEZE_ENTRY_ENABLED` + `BB_SQUEEZE_ENTRY_TF`) | ZECUSDC_SHORT (1) |

Their validated 365D deltas range +8 to +105 (ZECUSDC_SHORT +104.7, EGLD +90, others +16 to +36). Real, held-out edge — worth unlocking, but only through the proper gate.

---

## STRATEGY A — WT_DC detailed slowdown/accel scorer (live)

### Entry logic
Replace, **only when `WT_DC_DETAILED_SCORER_ENABLED`**, the simple `score_entry_multitf` (25/25/30/10/10) with the detailed `wt_dc_entry_scorer._score_long`/`_score_short` (already exists, was dead fallback):
6 weighted categories, score 0–100, entry threshold **43**:
1. **HTF trend (×0.75, cap 30):** `wt_bull/bear_alignment` (0–5 TFs), `wt_velocity_up/down_count`, `wt_structure_4h/D`, `wt_composite_bias`, `wt_wave_phase_4h/D`.
2. **LTF trigger (×1.25, cap 20):** `wt_cross_bull/bear_1h/15m/5m`, `wt_velocity_1h`, `wt_momentum_state_1h`.
3. **Momentum quality (×0.75, cap 15):** `wt_cross_value_1h`, `wt_cross_rising_1h`, `wt_cross_prev_value_1h`, `wt_divergence_1h`.
4. **DC+BB (×2.25, cap 15 — dominant):** `dc_position_1h/4h`, `bb_pct_b_4h/D`.
5. **Volume (×0.5, cap 10):** `relative_volume_1h`, `mfi_1h`, `stoch_crossover/crossunder_15m`, `stoch_k_1h`.
6. **Trend context (×0.75, cap 10):** `close_D` vs `ema_200_D`, `close_1h` vs `ema_20_1h`, `ha_4h/D`.

### Exit logic
**Unchanged.** WT_DC is an ENTRY scorer; exits stay on the existing DC/WT/structural exits (which are already live). The 11 WT_DC configs also carry those exits.

### Data pipeline (AUDITED 2026-09-28 — smaller than first written)
The detailed scorer reads **22 distinct fields**. Verified against live `ez_indicators.py`/`tradier_indicators.py`:
- **~20 are ALREADY emitted live** every few seconds (`wt_bull/bear_alignment`, `wt_velocity_up/down_count`, `wt_structure_{tf}`, `wt_wave_phase_{tf}`, `wt_composite_bias`, `wt_cross_rising_1h`, `wt_divergence_1h`, `wt_momentum_state_1h`, `stoch_crossover/crossunder_15m`, `ha_4h/D`) — all present, built via per-TF f-strings. **No pipeline work needed.**
- **1 real gap = naming mismatch, not missing data:** NPZ/backtest emits `wt_cross_bull_{tf}`/`wt_cross_bear_{tf}` (int8 booleans, `backtest_v8_precompute.py:1139-40`); live emits the same crossing info as `wt_cross_{tf}` (direction) + `wt_cross_count_bull/bear_{tf}`, but NOT those exact keys. The scorer reads `wt_cross_bull/bear_1h/15m/5m` (the ×1.25 LTF-trigger category); on a live dict lacking them `_safe_int`→0, so live scores that category lower than backtest → live≠backtest. **Fix = ~1-line adapter** deriving `wt_cross_bull/bear_{tf}` from the already-live `wt_cross_{tf}` (in the live caller or a tiny normalizer), NOT a data build.

Vector twin `wt_dc_entry_scorer_vec.score_entry_detailed_vec` is **parity-proven 0.000000** vs the scalar over 3000 bars (test_wt_dc_detailed_scorer_vec.py). **Action before flip:** add the `wt_cross_bull/bear_{tf}` adapter, then confirm a live snapshot yields the same score as the NPZ bar (spot-check on the 11 syms). This is the only real data step.

### Trade frequency
Fires when the multi-TF WT_DC score ≥ 43 on an eligible bar — moderate frequency (comparable to the current WT_DC entries, gated by the same per-sym enables).

### Risk & controls
- **Opt-in switch** `WT_DC_DETAILED_SCORER_ENABLED` (default **False**) — live behavior unchanged until flipped per-sym. Live crypto is also VPN-blacked-out currently, so only the stock configs (ALMU_SHORT, AU_SHORT, CF_LONG, CMC_LONG, CRWD_SHORT, IBM_SHORT, NVDA_SHORT) would trade at open.
- Existing kill switches (per-sym `PER_SYM_CONFIG_ENABLED`, OVERTRADE_GUARD) + the new churn/loss guard apply.

### Live wiring change (code)
`wt_dc_entry_scorer.score_entry` already has an opt-in `detailed=` param (added 2026-09-28, default off = live unchanged). To activate live, the **live callers** must pass `detailed=config.WT_DC_DETAILED_SCORER_ENABLED`:
- `tradier_manage.py:12229` `wt_dc_score_entry(_entry_ind, is_long, current_price)` → add `detailed=…`  **(LOCKED file — needs unlock)**
- any `ez_manage`/`ez_positions_quick` WT_DC entry caller  **(LOCKED)**
- `backtest_v12_engine.py` uses `score_entry` too → it will then also honor the flag, so the parity check stays honest.

---

## STRATEGY B — BB-squeeze breakout `alignment` (live)

### Entry logic
`detect_bb_squeeze_breakout` (rolling 100-bar bb-width percentile squeeze → breakout BUY/SELL) + confirmation gate `alignment ≥ BB_SQUEEZE_MIN_ALIGNMENT(10)` AND stoch_ok (`k_3m<75` long / `>25` short). **Currently DEAD in live** because the bare `alignment` key is never populated anywhere in the codebase — the gate never passes. Only **ZECUSDC_SHORT** needs this (crypto, VPN-blacked-out, so no immediate open exposure).

### Data pipeline
Wire `alignment` (0–24 composite, side-aware) into live indicators, mirroring the approved `compute_alignment_vec`: EMA9 vs EMA20/50/200 at 15m/1h/4h (+4 each), stoch K>D at 15m/1h/4h (+2), rel-vol>1 (+2), close vs SMA200 (+2), wt1 vs wt2 1h (+2). Add to `ez_indicators.compute()` / `tradier_indicators.compute()` output dict.

### Exit / freq / risk
- Exits unchanged. Fires rarely (only on squeeze release + alignment + stoch). Opt-in `BB_SQUEEZE_ENABLED`/`BB_SQUEEZE_ENTRY_ENABLED` (default off).
- **RISK:** this enables a currently-inert strategy — treat as brand-new. Given only 1 config benefits, **lowest priority**; could defer.

### Live wiring change (code)
- `ez_indicators.py` / `tradier_indicators.py`: emit `alignment`  **(LOCKED — needs unlock)**.
- Vector twin `vec_decisions/check_entry_candidates_crypto__bb_squeeze_gate.compute_alignment_vec` already exists (backtest side done).

---

## Rollout gate (BACKTEST_BIBLE §13 — do not skip)
1. **Unlock** the locked live files (`tradier_manage.py`, `ez_manage.py`, `ez_positions_quick.py`, `ez_indicators.py`, `tradier_indicators.py`) — you say `unlock <files>`.
2. Implement behind the opt-in switches (default **off** → zero live change until flipped).
3. **Live field step (AUDITED — small)**: Strategy A needs only the `wt_cross_bull/bear_{tf}` adapter (~20 of 22 fields already live); Strategy B needs the `alignment` composite emitted. Confirm a live snapshot scores identically to the NPZ bar.
4. **Re-run `backtest_v12_engine` parity** on the 12 with the switches ON → confirm live == vec (trade ratio 0.80–1.25, gain <0.5pp & <15%). Only configs that now pass parity proceed.
5. **Paper-trade forward** ≥ a few sessions (live-parity, no real orders) → confirm behavior matches.
6. **Stress** (param sensitivity 50–150%, 1.5–2× slippage) on the 12 — already walk-forward + 365D validated; add friction check.
7. **Then** flip `WT_DC_DETAILED_SCORER_ENABLED` / `BB_SQUEEZE_ENTRY_ENABLED` per-sym in `per_sym_active_config*.json` for the 12 (backed up), churn/loss guard watching.

## Revert
Every step is behind a default-off switch; per_sym writes are backed up; the guard auto-blocks misbehavers. Rollback = restore backup + flip switches off.

## Recommendation
Do **Strategy A (WT_DC-detailed)** first — 11 of the 12 configs, parity-proven vector twin, only a live-caller flag + live-field verification. Defer **Strategy B (BB alignment)** — 1 config, more net-new. On your `unlock` + go, I'll do step 3 (live-field audit) first and report before touching entry logic.

*End of proposal — nothing implemented until you approve + unlock.*
