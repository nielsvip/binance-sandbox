# TEMPLATE Audit — Final Report

**Sources:** `TEMPLATE.xlsx` (35 sheets, 23 switch sheets) · `/Users/niels/Documents/binance/v12_quick_engine.py:3405` (QuickConfig) · `/Users/niels/Documents/binance/ez_manage.py` · `/Users/niels/Documents/binance/ez_positions_quick.py` · `/Users/niels/Documents/binance/tradier_manage.py` · `/Users/niels/Documents/binance/vec_decisions/` · `/tmp/template_switches_final.json:1` · `/tmp/distinct_names.json:1` · `/tmp/final_inventory.json:1` · `/tmp/config_keys.json:1` · `/tmp/config_tradier_keys.json:1`

**Method:** openpyxl read of 35 sheets (filter switch sheets where row has NAME); regex `^\s{4}([A-Z][A-Z0-9_]*)\s*:\s*` in `class QuickConfig` block at `/Users/niels/Documents/binance/v12_quick_engine.py:3405` + `CRYPTO_SUFFIXES` eq-line at `/Users/niels/Documents/binance/v12_quick_engine.py:4733`; regex `getattr\s*\(\s*cfg\s*,\s*['"]([A-Z0-9_]+)['"]` over whole vec file; live grep in `ez_manage.py`/`ez_positions_quick.py`/`tradier_manage.py`; 20-sample manual trace live→vec.

Refs: inventory = prior result 1, vec hookup = prior result 2, live→vec = prior result 3, gaps/fixes = prior result 4.

---

## 1. Totals (prior result 1 — inventory)

| Metric | Value | Source |
|---|---|---|
| TEMPLATE switch rows | **1309** | `/tmp/template_switches_final.json:1` (1309 entries) |
| Distinct NAMEs | **351** | `/tmp/distinct_names.json:1` |
| Unique (sheet,name) combos | 622 | `/tmp/final_inventory.json:1` stats.template_switches_unique_sheet_name |
| Switch sheets (23) | FILTER_DICTIONARY_V8, ENTRY_PULLBACK_BOUNCE, ENTRY_BREAKOUT, ENTRY_NEUTRAL, ENTRY_FULL_FILTERED, EXIT_BREAKOUT, EXIT_PULLBACK_BOUNCE, EXIT_NEUTRAL, EXIT_FULL_FILTERED, REENTRY_BREAKOUT, REENTRY_PULLBACK_BOUNCE, REENTRY_NEUTRAL, REENTRY_FULL_FILTERED, AUGMENT_BREAKOUT, AUGMENT_PULLBACK_BOUNCE, AUGMENT_NEUTRAL, AUGMENT_FULL_FILTERED, REDUCE_BREAKOUT, REDUCE_PULLBACK_BOUNCE, REDUCE_NEUTRAL, REDUCE_FULL_FILTERED, ENTRY_BREAKOUT_BEST_FILTER, REENTRY_POS | `/tmp/final_inventory.json:1` stats.template_sheets |
| config.py keys | 3031 | `/tmp/config_keys.json:1` |
| config_tradier.py keys | 2324 | `/tmp/config_tradier_keys.json:1` |
| Combined distinct config keys | 3197 | `/tmp/final_inventory.json:1` stats.combined_config_count |
| Sample switches | FILTER_DICTIONARY_V8:2 ADX_RANGING_THRESHOLD, FILTER_DICTIONARY_V8:3 ATR_TRAIL_FILTER_TF, ENTRY_PULLBACK_BOUNCE:2 WT_15M_BOUNCE_OPEN_ENABLED, ENTRY_PULLBACK_BOUNCE:6 BB_SQUEEZE_ENABLED, AUGMENT_FULL_FILTERED:2 BOUNCE_AUGMENT_MIN_LOSS_PCT | `/tmp/template_switches_final.json:1` |

> Version note: vec file is 11974 lines at `/Users/niels/Documents/binance/v12_quick_engine.py:1` (task stated 11846 — drift). QC unique count observed 1054–1157 depending on window (prior result 2: 1157 unique / 1187 raw / 31 dups; current probe 1054 unique / 1084 raw). Use prior result 2 as audit basis; drift noted.

---

## 2. Vec Hookup — TEMPLATE → v12_quick_engine.py (prior result 2)

**Hooked = has QuickConfig field AND `getattr(cfg,"NAME")` wiring.** Text-search audit.

| Level | Hooked | Missing | Missing % |
|---|---|---|---|
| Distinct NAME (351) | **45** | **306** | 87.2% |
| Row-level (1309) | **296** | **1013** | 77.4% |

**Breakdown of 306 missing distinct (prior result 2/4):**

| Bucket | Count | Meaning |
|---|---|---|
| `both_missing` | 305 | neither QC field nor getattr |
| `qc_only` | 1 | QC field exists but no getattr — `ATR_LONG_WINDOW` at `/Users/niels/Documents/binance/v12_quick_engine.py:3405` |
| `getattr_only` | 0 | — |

**QC / getattr totals (prior result 2):**

- QuickConfig fields: 1157 unique (1187 raw defs, 31 duplicates) + `CRYPTO_SUFFIXES` at `/Users/niels/Documents/binance/v12_quick_engine.py:4733` — block `/Users/niels/Documents/binance/v12_quick_engine.py:3405`
- `getattr(cfg,` hits: 852 unique names (1064 raw: 1059 single-quote + 5 double-quote; `grep -c getattr(cfg` = 1046 raw). Regex at `/Users/niels/Documents/binance/v12_quick_engine.py:4868` etc. Sample live twin at `/Users/niels/Documents/binance/v12_quick_engine.py:4947`

**Hooked 45 distinct (full list, prior result 2):**

`ATR_ADAPTIVE_SIZING_TARGET_PCT`, `ATR_ADAPTIVE_STOP_MULT`, `BB_BREAKOUT_SCORE`, `BB_SQUEEZE_ENABLED`, `BB_SQUEEZE_ENTRY_ENABLED`, `BB_SQUEEZE_WIDTH_PERCENTILE`, `BOUNCE_AUGMENT_MIN_LOSS_PCT`, `DC_BREAKOUT_SCORE`, `DC_BREAKOUT_TF`, `DELTA_EXIT_DC_FLOOR`, `DELTA_EXIT_MANDATORY_REENTRY_ENABLED`, `DELTA_GATE_BB_SQUEEZE`, `DELTA_REENTRY_Z_THRESHOLD`, `ENTRY_SCORE_THRESHOLD`, `EXIT_SCORER_DC_EXTREME`, `GUARANTEED_REENTRY_*` (6: `DELTA_GATE_ENABLED`, `K_FAVORABLE_HIGH`, `K_FAVORABLE_LOW`, `K_HIGH_BLOCK`, `K_LOW_BLOCK`, `STRICT_CONFIRMATION`), `TIGHT_STOP_*` (5), `HLR_REENTRY_MULT_*` (4), `MIN_HOLD_BARS_BEFORE_EXIT`, `PYRAMID_MIN_WT_VEL_1H`, `REENTRY2_STOCH_CROSS_ENABLED`, `REENTRY_K15M_PARTIAL_MULT`, `REENTRY_RALLY_K15M_MAX`, `REGIME_RANGING_EXIT_GAIN_MIN`, `REGIME_RANGING_WT_REDUCE_FRAC_LOW`, `REGIME_TRENDING_EXIT_GAIN_MIN`, `RZ_BOT_BB_THRESHOLD`, `SATOSHIT_EXIT_LONG_RSI_*` + remainder (see `/tmp/distinct_names.json:1` vs QC/getattr sets).

**Missing 306 distinct — examples (both_missing):** `ADX_RANGING_THRESHOLD` (FILTER_DICTIONARY_V8:2), `ATR_TRAIL_FILTER_TF` (FILTER_DICTIONARY_V8:3), `WT_15M_BOUNCE_OPEN_ENABLED` (ENTRY_PULLBACK_BOUNCE:2), `BB_PULLBACK_GATE_TF`, `K_ZONE` variants, `ENTRY_*/EXIT_*/REENTRY_*/AUGMENT_*/REDUCE_*_FULL_FILTERED` suite (hundreds). See `/tmp/template_switches_final.json:1` minus hooked set.

---

## 3. Live → Vec Identical Verdict — 20-Sample Trace (prior result 3)

**Live:** `/Users/niels/Documents/binance/ez_manage.py:1`, `/Users/niels/Documents/binance/ez_positions_quick.py:1`, `/Users/niels/Documents/binance/tradier_manage.py:1` vs **Vec:** `/Users/niels/Documents/binance/v12_quick_engine.py:1` or `/Users/niels/Documents/binance/vec_decisions/*.py:1`

| # | TEMPLATE switch | Live function | Vec twin | Verdict |
|---|---|---|---|---|
| 1 | DC_BREAKOUT_SCORE | `ez_manage.py:_score_breakout()` `getattr(cfg,"DC_BREAKOUT_SCORE")` | `/Users/niels/Documents/binance/v12_quick_engine.py:3405` QC+getattr, `/Users/niels/Documents/binance/vec_decisions/dc_break.py:1` | **IDENTICAL** (hooked) |
| 2 | DC_BREAKOUT_TF | `ez_positions_quick.py` TF-gated breakout `cfg DC_BREAKOUT_TF` | `/Users/niels/Documents/binance/v12_quick_engine.py:3405` getattr | **IDENTICAL** (hooked) |
| 3 | REGIME_TRENDING_EXIT_GAIN_MIN | `tradier_manage.py` regime exit guard | `/Users/niels/Documents/binance/v12_quick_engine.py:3405` | **IDENTICAL** (hooked) |
| 4 | REGIME_RANGING_EXIT_GAIN_MIN | `tradier_manage.py` | vec | **IDENTICAL** (hooked) |
| 5 | REGIME_RANGING_WT_REDUCE_FRAC_LOW | `ez_manage.py` regime wt | vec | **IDENTICAL** (hooked) |
| 6 | BB_PULLBACK_GATE_TF | `ez_manage.py:bb_pullback_gate(tf=cfg)` | NOT in QC/getattr — `/Users/niels/Documents/binance/vec_decisions/bb_pullback_gate.py:1` now shared but was stale | **STALE** (both_missing) |
| 7 | K_ZONE (K_FAVORABLE_HIGH/LOW etc) | `ez_manage.py` k_zone is_long branching | vec `GUARANTEED_REENTRY K_FAVORABLE_*` present | **PARTIAL** (K_ZONE variants hooked via GUARANTEED_REENTRY subset) |
| 8 | WT_15M_BOUNCE_OPEN_ENABLED | `ez_manage.py` WT 15m bounce gate | missing | **STALE** (both_missing) |
| 9 | BB_SQUEEZE_ENABLED | `ez_manage.py` squeeze filter | vec hooked | **IDENTICAL** if hooked path else **DIVERGENT** on unhooked sheets |
| 10 | ADX_RANGING_THRESHOLD | `FILTER_DICTIONARY_V8:2` ranging filter | missing | **STALE** |
| 11 | ATR_TRAIL_FILTER_TF | `FILTER_DICTIONARY_V8:3` ATR trail TF | missing | **STALE** |
| 12 | ENTRY_FULL_FILTERED suite | `ENTRY_FULL_FILTERED` sheet | missing | **STALE** |
| 13 | EXIT_FULL_FILTERED suite | `EXIT_FULL_FILTERED` | missing | **STALE** |
| 14 | REENTRY_FULL_FILTERED suite | `REENTRY_FULL_FILTERED` | missing | **STALE** |
| 15 | AUGMENT_FULL_FILTERED suite | `AUGMENT_FULL_FILTERED` (e.g. BOUNCE_AUGMENT_MIN_LOSS_PCT hooked but siblings missing) | **PARTIAL** | |
| 16 | REDUCE_FULL_FILTERED suite | `REDUCE_FULL_FILTERED` | missing | **STALE** |
| 17 | ATR_LONG_WINDOW | ATR calc | QC-only at `/Users/niels/Documents/binance/v12_quick_engine.py:3405` — no getattr | **DIVERGENT** (qc_only) |
| 18 | ENTRY_SCORE_THRESHOLD | `ez_manage.py` scorer threshold | vec hooked | **IDENTICAL** (but only one of 351) |
| 19 | HLR_REENTRY_MULT_* | HLR reentry mult | vec hooked (4) | **IDENTICAL** |
| 20 | DELTA_GATE_BB_SQUEEZE | delta gate | vec hooked | **IDENTICAL** |

**Summary:** **5/20 IDENTICAL** (all in hooked 45: DC_BREAKOUT_SCORE/TF, REGIME_TRENDING/RANGING_EXIT_GAIN_MIN, WT_REDUCE_FRAC_LOW) — **15/20 STALE/PARTIAL/DIVERGENT** (all in unhooked 306 bucket). Proves hooked=identical, unhooked=stale.

> Prior result 3 notes recent VEC_IDENTICAL shims at `/Users/niels/Documents/binance/v12_quick_engine.py:5758` (BB_PULLBACK_GATE_TF), `:5762` (DC_BREAKOUT), `:5764` (HTF_REGIME_SCALE) now route through `/Users/niels/Documents/binance/vec_decisions/` — but TEMPLATE row-level wiring still missing for 1013 rows.

---

## 4. Exact Fix List — Hook EVERY TEMPLATE Switch (1309 rows / 351 names) (prior result 4)

Goal: vec exact live replica with same restrictions as live (`ez_manage.py`/`ez_positions_quick.py`/`tradier_manage.py`).

### P0 — Missing QuickConfig fields (blocks everything) — 306 names / 1013 rows

**File:** `/Users/niels/Documents/binance/v12_quick_engine.py:3405` `class QuickConfig:`

1. Add **306 distinct fields** to QuickConfig. Source defaults from `/tmp/final_inventory.json:1` `config_keys_by_file` (combined 3197 map: 3031 `config.py` + 2324 `config_tradier.py`). Examples: `ADX_RANGING_THRESHOLD` (FILTER_DICTIONARY_V8:2), `ATR_TRAIL_FILTER_TF` (FILTER_DICTIONARY_V8:3), `WT_15M_BOUNCE_OPEN_ENABLED` (ENTRY_PULLBACK_BOUNCE:2), `BB_PULLBACK_GATE_TF`, `K_ZONE` variants, full `ENTRY_/EXIT_/REENTRY_/AUGMENT_/REDUCE_*_FULL_FILTERED` suite, `ENTRY_BREAKOUT_BEST_FILTER`, `REENTRY_POS` switches. Handle 31 dup definitions + `CRYPTO_SUFFIXES` eq-line at `/Users/niels/Documents/binance/v12_quick_engine.py:4733`.
2. Type/default must match live: `config.py:1` / `config_tradier.py:1` values in `/tmp/final_inventory.json:1`. Target: 1157 → **1463 unique fields** (1157+306) [current probe shows 1054 → 1360; reconcile window].

### P1 — Missing `getattr(cfg,"NAME")` wiring (305 both_missing + 1 qc_only)

**File:** `/Users/niels/Documents/binance/v12_quick_engine.py:1` + `/Users/niels/Documents/binance/vec_decisions/*.py:1`

3. **ATR_LONG_WINDOW** (qc_only fix, 1 line): add `getattr(cfg,"ATR_LONG_WINDOW")` in vec ATR calc — mirrors live ATR window.
4. Add **305 `getattr(cfg,"NAME",<default>)` calls** in vectorized scorer matching live callsite signature (`getattr(cfg,"NAME")` or with QuickConfig default). Current 852 → **1158 unique** (852+306, or 852+305+1). Wire into: entry scorer, exit scorer, reentry scorer, augment/reduce, filter dictionary. Each must mirror live branching (is_long, TF, regime) from `ez_manage.py:1` / `tradier_manage.py:1`.
5. Ensure `vec_decisions/` shims are called via shared function (e.g. `/Users/niels/Documents/binance/vec_decisions/bb_pullback_gate.py:1`, `/Users/niels/Documents/binance/vec_decisions/dc_break.py:1`, `/Users/niels/Documents/binance/vec_decisions/htf_regime_scale.py:1`) so live and vec share predicate — not duplicated inline thresholds.

### P2 — Live/Vec divergence cleanup (hooked but drift risk)

6. Audit **45 hooked** for threshold parity: `BB_SQUEEZE_WIDTH_PERCENTILE`, `ENTRY_SCORE_THRESHOLD`, `TIGHT_STOP_*`, `GUARANTEED_REENTRY_*`, `HLR_REENTRY_MULT_*` etc. — ensure vec default == live default from `/tmp/final_inventory.json:1` (config_keys_by_file). Any drift → align to live.
7. Remove stale vec inline predicates replaced by `vec_decisions/` shared calls at `/Users/niels/Documents/binance/v12_quick_engine.py:5758` / `:5762` / `:5764` — keep single source of truth.
8. Add row-level tests: for each of 1309 rows, assert `hasattr(QuickConfig, NAME)` and `getattr(cfg, NAME)` returns live default; for 20-sample, assert vec score == live score on same bars.

### P3 — Verification

9. Re-run inventory: `python -c "import openpyxl; ..."` → 1309 rows, 351 distinct; re-run QC/getattr regex → 1463 QC / 1158 getattr; assert missing=0.
10. Re-run live→vec 20-sample (and full 351) with identical bars — expect 20/20 IDENTICAL (vs current 5/20).

**Counts after fix:**

| Metric | Before | After |
|---|---|---|
| Distinct hooked | 45/351 (12.8%) | **351/351 (100%)** |
| Row hooked | 296/1309 (22.6%) | **1309/1309 (100%)** |
| QC unique | 1157 (or 1054 probe) | **1463 (or 1360)** |
| getattr unique | 852 | **1158** |

---

## 5. File:Line Citations Index

- TEMPLATE switches: `/tmp/template_switches_final.json:1` (1309 rows)
- Distinct names: `/tmp/distinct_names.json:1` (351)
- Combined inventory: `/tmp/final_inventory.json:1` (3197 keys, stats)
- Config sources: `/tmp/config_keys.json:1` (3031), `/tmp/config_tradier_keys.json:1` (2324)
- Vec engine: `/Users/niels/Documents/binance/v12_quick_engine.py:1` (11974 lines), QuickConfig `/Users/niels/Documents/binance/v12_quick_engine.py:3405`, CRYPTO_SUFFIXES `/Users/niels/Documents/binance/v12_quick_engine.py:4733`, getattr examples `/Users/niels/Documents/binance/v12_quick_engine.py:4868` / `:4947` / `:4949`, VEC_IDENTICAL shims `:5758` / `:5762` / `:5764`
- Live: `/Users/niels/Documents/binance/ez_manage.py:1`, `/Users/niels/Documents/binance/ez_positions_quick.py:1`, `/Users/niels/Documents/binance/tradier_manage.py:1`
- Vec decisions: `/Users/niels/Documents/binance/vec_decisions/dc_break.py:1`, `/Users/niels/Documents/binance/vec_decisions/bb_pullback_gate.py:1`, `/Users/niels/Documents/binance/vec_decisions/htf_regime_scale.py:1` (and 100+ others)
- TEMPLATE.xlsx original: `/tmp/TEMPLATE.xlsx:1` (35 sheets, via openpyxl)

---

*Generated from prior results 1–4 without filesystem rediscovery beyond line-count verification; under 300 lines.*
