# Canonical Metrics — single source of truth

**Why this exists:** sweeps were producing inconsistent Sharpe numbers across scripts (9.3 here, 0.5 there) because different scripts used different formulas under the same name `sharpe`. The `Live Swarm Results — Best Sharpe: 9.3306` panel was being read alongside `wt_dc_full pool_sharpe 0.07` — apples vs pears. This document fixes the names so any future agent can read a number and know exactly what it means.

Per CLAUDE.md (`📊 BACKTEST REPORTING RULES`, `STANDARD METRIC SET — ALL FIVE MANDATORY`).

---

## Canonical names — use these and ONLY these in user-facing output

| Name | Formula | Source-of-truth implementation |
|---|---|---|
| **`pool_sharpe`** | `mean(all_trade_returns) / std(all_trade_returns)` across ALL trades of ALL symbols pooled. Each trade counts equally. | `v8_quick_engine.py:5170 _pool_sharpe()` |
| **`sym_sharpe`** | `mean(per-symbol Sharpes)`, capped ±20.0, excluding symbols with <30 trades. | `v8_quick_engine.py:5142 per_sym_sharpes_chk` (the column literally named `sharpe` in v8_quick CSVs is this) |
| **`sharpe_per_trade`** | Same as `pool_sharpe`. Synonym, kept for legacy `backtest_v8_engine.py` outputs. | `backtest_v8_engine.py:1009` |
| **`acc_gain_pct`** | Sum of all per-trade %-returns across all symbols. | every engine emits this |
| **`avg_gain_trade`** | `acc_gain_pct / trades`. | derived |
| **`gain_per_yr`** | `acc_gain_pct / n_years`. | derived (n_years from --start to today) |
| **`gain_sym_yr`** | `acc_gain_pct / n_syms / n_years`. | derived |
| **`max_dd_pct`** | Peak-to-trough equity drawdown as % of starting capital across the WHOLE test window. | `_compute_drawdown()` in each engine |
| **`avg_dd_pct`** | Mean DD across all symbols. | `v8_quick_engine.py` per-sym DD |
| **`wr`** | Win rate %. | `wins / (wins + losses) * 100` |

## Banned

| Name | Why | What to do instead |
|---|---|---|
| **`sharpe_annual`** | `sharpe_per_trade * sqrt(trades_per_year)`. Frequency-gaming — a 1.2k-trades/yr config gets 35x boost over a 1-trade/yr config that has identical per-trade quality. The "frozen 2.52 baseline" was `sharpe_annual` (0.14 per-trade × √324). | Report `pool_sharpe` (per-trade Sharpe) and `gain_per_yr` SEPARATELY — never multiply them. |
| **`sharpe_weekly`** | Same problem at weekly cadence. | Same fix. |
| **`sharpe`** as a STANDALONE column | Ambiguous between pool / sym-avg. v8_quick writes both; the column literally `sharpe` is sym-avg, but readers often assume pool. | Write `pool_sharpe` AND `sym_sharpe` as separate columns, never just `sharpe`. |

---

## Audit — current state across writers (2026-04-29)

| Script | What it emits | Status | Action |
|---|---|---|---|
| `v8_quick_engine.py` | `pool_sharpe`, `sharpe` (sym_sharpe), `sharpe_min/p25/med/p75/max`, `accumulated_gain_pct`, `max_dd_pct`, `avg_dd_pct`, `trades`, `wins`, `losses`, `wr`, `avg_pnl_pct`, `symbols_used` | ✅ canonical | none |
| `v8_quick_sweep.py` (CSV writer) | Inherits engine columns + `cfg_*` knob columns | ✅ canonical | none |
| `autonomous_search.py` | `pool_sharpe`, `acc_gain_pct`, `max_dd_pct`, `trades`, `gain_vs_bh`, `elapsed_s`, `overrides_count`, `reliable`, `overrides_json` | ⚠️ MISSING `sym_sharpe`, `wr`, `avg_pnl_pct`, `n_syms`, `n_years` — landing-page panel can only show partial picture | **Action**: add these columns to autonomous_search.csv writer. Use the existing `simulate()` return dict — fields ARE computed, just not written. |
| `backtest_v8_engine.py` | `sharpe_weekly`, `sharpe_per_trade`, `sharpe_annual` | ⚠️ Emits BANNED `sharpe_annual` to logs | **Action**: drop `sharpe_annual` from `[V8_RESULT_LIVE]` / `[V8_FINAL_PNL]` log lines. Keep computing it internally if needed for legacy analysis but don't print. |
| `analyze_wt_dc_exits.py:265` | `(mean_r / std_r) * np.sqrt(trades_per_year)` | ⛔ BANNED formula | **Action**: rename function `calc_sharpe()` → `calc_sharpe_annualized_BANNED()` and add a banner warning. Replace with `calc_pool_sharpe()` returning per-trade Sharpe. |
| `backtest_derived_strategies.py:281` | `(avg_pnl / std_pnl) * np.sqrt(252 / max(1, hold_bars))` | ⛔ BANNED formula | Same fix. |
| `_ab_*.py` (chapter scripts) | `r["sharpe"]` from underlying engine | ✅ inherits whatever engine returns | none if engine returns canonical |
| `chart_server.py` | `sharpe_annual` reads from CSV | ⚠️ display-only of banned name | **Action**: rename UI label to "Annualized Sharpe (banned per CLAUDE.md — diagnostic only)" with red warning. |
| `retest_real_sharpe_runner.py` | `sharpe_annual` | ⚠️ banned | **Action**: rename to `retest_per_trade_sharpe_runner.py`, drop annualization. |
| `update_snapshot_real_sharpe.py` | `sharpe_annual` | ⚠️ banned | Same. |
| `sweep_cockpit.py` | (this file) — DISPLAYS the banned name in /sweeps metric definitions table with a ⛔ tag | ✅ documenting it as banned, not emitting | none |

---

## Why "Best Sharpe 9.3306" was misleading on the landing page

The 9.3306 value seen in the dashboard came from an autonomous_search winners.jsonl file with:
- `pool_sharpe = 9.3306` — TRUE per-trade Sharpe but on **<10 trades** in **<6 months** on **<6 symbols**
- This is a small-sample lie: variance of `mean/std` on N=8 is enormous; one lucky trade can push pool_sharpe above 9
- Per CLAUDE.md min-sample floor: **≥48 syms (crypto) / ≥100 syms (tradier), >1yr, ≥30 trades/sym**. The 9.3 value violates ALL of these.

The 0.07-0.17 numbers in `wt_dc_full` are TRUE pool_sharpe on 48-61 crypto / 246 stocks × 4yr — sample-floor compliant. They're below 1.0, which means: that knob axis doesn't help. **Both numbers are correctly computed; only the smaller-scope one is decision-material.**

The /sweeps page metric-definitions table at the bottom (and the swarm panel's "apples-vs-pears warning") now spell this out so future agents/humans don't compare incompatible values.

---

## What "all use the same name for the same calculation across all scripts and platforms" means in practice

1. **Every CSV writer** in `data/sweep_results/` and `data/autonomous/` MUST include columns: `pool_sharpe`, `sym_sharpe` (alias `sharpe` allowed but warn), `acc_gain_pct`, `max_dd_pct`, `trades`, `wr`, `symbols_used`, `n_years`, `start_date`, `reliable`. Anything emitting fewer = ambiguous = quarantine result.
2. **Every UI** (cockpit, swarm page, params page) reads ONLY the canonical names. Old `sharpe` column shown as "sym_sharpe (diagnostic)" with a tooltip.
3. **Every log line** in real-code engines that quotes a Sharpe MUST quote `pool_sharpe` first, then optionally others with explicit names. No bare `sharpe=X.X`.
4. **Cross-script comparisons** require all 5 of: pool_sharpe, sym_sharpe, gain_sym_yr, max_dd_pct, n_syms × n_years. Any comparison missing one = invalid.

---

## Open work items

- [ ] Patch `autonomous_search.py` to emit `sym_sharpe, wr, avg_pnl_pct, symbols_used, n_years, start_date` to the CSV.
- [ ] Patch `backtest_v8_engine.py` to drop `sharpe_annual` from log lines (keep computing for legacy bug-compat but don't display).
- [ ] Rename `analyze_wt_dc_exits.py:calc_sharpe` and `backtest_derived_strategies.py` Sharpe formula to flag-and-banner the annualization.
- [ ] Rename CSVs that contain `sharpe_annual` column with a `_LEGACY_BANNED` filename suffix so downstream readers stop pulling them.

These changes preserve compute already spent — no historic results are deleted. Only the naming gets disambiguated and the banned formulas get prominently labeled so no agent/human picks them up by accident.
