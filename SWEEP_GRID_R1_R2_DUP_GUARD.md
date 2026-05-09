# Knob-Sweep Grid — R1 / R2 / DUP_GUARD (2026-05-09)

Defines the parameter space to sweep for the new exit-rule knobs shipped with the 2026-05-09 rewrite. Run on **S1** when crypto and tradier baselines are stable. Each variant goes through `backtest_v8_engine.py` (the real engine, not v8_quick) per user mandate "v8_quick IS A LIE — backtest_v8_engine is the ONLY real test".

## Universal sample

- **Crypto**: 12 syms × 4 months (start=2026-01-01, end=NPZ tail). Symbols: `BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,LINKUSDC,LTCUSDC,UNIUSDC,DOGEUSDT,MATICUSDT`.
- **Tradier**: 12 stocks × 4 months (start=2026-01-01). Symbols: `AAPL,AMZN,AVGO,AMD,ADBE,NVDA,MSFT,GOOG,META,TSLA,JPM,BAC`.

These are sample-floor *DIAGNOSTIC* (12 < 48 floor) per CLAUDE.md — every CSV row tagged accordingly. Used for **directional** signal, not promotion.

## R1 grid — DC4 emergency close

| Knob | Values | Default |
|---|---|---|
| `R1_DC_LOW4_3M_EMERGENCY_ENABLED` | True, False | True |
| `R1_NEWBORN_WINDOW_MIN` | 5, 15, 30, 60 | 15 |
| `R1_USE_DC_4BAR` | True (dc_low4_3m / dc_low4_5m), False (dc_low_3m / dc_low_5m) | True |
| `R1_TF` (tradier only) | '5m', '15m' | '5m' |

**Crypto subset**: 2 × 4 × 2 = **16 variants**.
**Tradier subset**: 2 × 4 × 2 × 2 = **32 variants**.

## R2 grid — WT velocity slowdown near breakeven

| Knob | Values | Default |
|---|---|---|
| `WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED` | True, False | True |
| `WT_15M_VEL_SLOW_GAIN_BAND_PCT` | 0.30, 0.50, 1.00 | 0.50 |
| `WT_15M_VEL_SLOW_GAIN_FLOOR_PCT` | 0.0, 0.01, 0.05 | 0.01 |
| `WT_VEL_DECEL_RATIO` | 0.3, 0.5, 0.7 | 0.5 |
| `WT_VEL_USE_DECEL_RATIO_ONLY` | True, False | True |
| `R2_TF_LIST` (crypto) | `('15m',)`, `('15m','1h')`, `('1h',)` | `('15m',)` |
| `R2_TF_LIST` (tradier) | `('1h','4h','D')`, `('4h','D')`, `('15m','1h','4h','D')` | `('1h','4h','D')` |

**Crypto subset**: 2 × 3 × 3 × 3 × 2 × 3 = **324 variants** → cap at first-pass: hold ENABLED=True, FLOOR=0.01, USE_DECEL_ONLY=True; sweep only BAND × DECEL_RATIO × TF_LIST = 3×3×3 = **27 variants**.

**Tradier subset**: same logic — first-pass 3×3×3 = **27 variants**.

## DUP_GUARD grid — gain-based augment gate

| Knob | Values | Default |
|---|---|---|
| `DUP_GUARD_USE_GAIN_GATE` | True, False (revert to 900s time) | True |
| `DUP_GUARD_GAIN_MULTIPLIER` | 0.3, 0.5, 0.7, 1.0, 1.5 | 0.5 (=1.5% with MIN_GAIN=3.0%) |

5 variants, both modes (crypto + tradier).

## First-pass sweep size

- **Crypto**: 16 (R1) × 27 (R2 first-pass) × 5 (DUP_GUARD) = **2,160 variants** — too many.
- **Tradier**: 32 × 27 × 5 = **4,320 variants** — too many.

### Coordinate-descent first pass (recommended)

Sweep one rule at a time, hold others at default:

1. **R1 alone** (R2/DUP_GUARD = default): 16 crypto + 32 tradier = 48 variants.
2. **R2 alone** (R1/DUP_GUARD = default): 27 crypto + 27 tradier = 54 variants.
3. **DUP_GUARD alone** (R1/R2 = default): 5 crypto + 5 tradier = 10 variants.

**Total first pass**: **112 variants**. At ~18.5min/variant on S1 with 1 worker = 34.5h serial. Realistic with alternation: split crypto/tradier each ~17h compute → fits in ~24h wall-clock with the existing `*/5` + `*/7` watchdogs.

After first pass identifies winners, run a second pass cross-product over the top-3 in each rule (2nd-pass = 27 variants) to find best combination.

## Implementation

Need a new sweep tier `r1_r2_dup_guard_grid` registered in the existing `backtest_v8_sweep.py` tier dispatcher. Each variant emits a CSV row via `metrics_guard.write_sharpe_row()` with the canonical 9 fields + the variant ID. Tag DIAGNOSTIC for 12-sym samples.

Tier registration happens in `backtest_v8_sweep.py` — find existing tier defs (`system_combo`, `tradier_param_hunt`, `tradier_grtf7_hunt`) and add the new one with the variant generator.

Watchdog launchers `start_crypto_sweeps.sh` / `watchdog_sweep_s1_tradier.sh` will need an additional cron entry to launch this tier alongside the existing system_combo / tradier_grtf7_hunt — OR the existing watchdogs add `r1_r2_dup_guard_grid` as a phase after their current tier finishes.

## Pass criteria (per variant)

A variant **promotes** to live override consideration only if ALL of:
- `pool_sharpe ≥ 0.6` (Best-of-current tier per CLAUDE.md)
- `trades / (n_syms × years) ≥ 200` (rate ≥ 200 trades/sym/yr → ~0.5/day/sym)
- `max_dd_pct ≤ 10%` (per `feedback_revised_tier_trade_count_weighted_20260501`)
- Sample floor met (or DIAGNOSTIC tag if 12-sym pass)

R1/R2 fires logged to `data/sweep_alerts/bad_exits.jsonl` per CLAUDE.md memory `feedback_2day_profitability_deadline_20260507`. Count of fires per variant included in CSV.

## Status

- Code knobs: shipped 2026-05-09 (md5s in `MEMORY.md` `project_exit_rewrite_r1_r2_dup_guard_20260509`).
- Tier dispatcher entry: **NOT YET ADDED** to `backtest_v8_sweep.py`.
- Watchdog cron: existing watchdogs run `system_combo` (crypto) and `tradier_grtf7_hunt` — these tiers do NOT touch the new knobs, so the current sweep results will use defaults set by the rewrite.
- Smoke test: BTCUSDC × 30d running on S1 as of 2026-05-09 19:37 UTC, log `/tmp/smoke_r1_r2_btc.log`.

## Next session — to launch this grid

1. Add `r1_r2_dup_guard_grid` tier to `backtest_v8_sweep.py` (variant generator).
2. Add a 3rd cron entry (e.g. `*/11`) to launch the new tier when the others are idle, OR fold it into one of the existing watchdog rotations.
3. Run first-pass coordinate descent (112 variants), inspect CSVs, choose top configs.
4. Cross-product the winners.
5. Promote into `data/baselines_from_s2_archive/` style override file via `metrics_guard.write_sharpe_row()`.
