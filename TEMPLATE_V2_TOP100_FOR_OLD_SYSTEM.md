# TEMPLATE_V2 Top 100 Switches/Filters for Old per_sym System — Without Choking

**Source:** `SPREADSHEETS/TEMPLATE_V2.xlsx` (17 sheets, 963 rows, 273 unique switches + `FILTER_DICTIONARY_V2` 426 rows). **NOT** `TEMPLATE.xlsx` (old 357). **Target:** `per_sym_engine_crypto.py` / `per_sym_engine_stocks.py` (`3m`/`5m` base, `per_sym` overrides) — isolated addition, 5-10 at a time per `BACKTEST_BIBLE` `E2=E3` rule, never all 100 at once.

**How to add without choking:** One marginal sweep per switch (baseline `per_sym` 1yr `365d` + `1mo proof 30d` from same `1yr` calc, `metrics_guard` honest `pool_sharpe`), `5-15 tpd`, `dd<30`, `pool_sharpe>0.5`, `gain/mo>20%`, `Δ>0` then promote to next `E`. Batch `F` (vec) first, `G` (live) via isolated `backtest_v12` subprocess — never in parent. Reversible via `tools/per_sym_engine_*_isolated.py`.

---

## Timing: 1yr Backtest on Old System with 3/5m NPZ

| Path | 1yr (365d) | 30d proof | Bars | Note |
|---|---|---|---|---|
| **15m default** (`v12_pilot`/`per_sym` 15m resample, current) | **3-4 s** (`NVDA 1344 trades 3.99s`, `BTC 10 trades 0.6s`) | **0.4-0.6 s** (`NVDA 183 trades 0.5s`) | `365d ~53k bars` (`15m`), `30d ~5.3k` | `S1 659 NPZ`, `16 cores` parallel ~`4 syms / 15s` |
| **3/5m** (`per_sym` `3m` crypto / `5m` stocks, isolated) | **~15-20 s** (`5× bars`, `3m` `~265k` vs `15m` `53k`) | **~0.8-1.2 s** (`1mo 3/5m` slice `14k` vs `1.4k 15m` — new `indicators_1mo_3m5m/*.npz` is `5×` smaller than full `121k→5.3k`) | `457k→14k BTC` | Slowdown is real — keep `15m` default, `3/5m` only via `--use-3m-base` + `indicators_1mo_3m5m` for `1mo` test |

Measured on `S1` (`~/binance-sandbox/backtest_v8/indicators` `BTC 457k`, `NVDA 121k` bars). `1yr 3/5m` import fails on `S1` isolated `v12_wide` (`simulate_one_symbol` missing) — use original `per_sym` via `tools/run_1yr_1mo_proof.py` with `--use-3m-base` or `S1` `per_sym` direct. **Recommendation:** `1yr` stays `15m`; `1mo` with `3/5m` (`indicators_1mo_3m5m`) is the `3/5m` test (see `tools/create_1mo_npz_with_3m5m.py` — `BTC 14k` bars, proven `F filling 2/5` but `G` pending).

---

## Top 100 — Ranked by Real Delta (not hash), No Choke

Add in batches of **5-10** per `BACKTEST_BIBLE`: `E2` baseline (`per_sym` 1yr), `F3` `vec` `True/False` delta, `E3=E2+F3` only if `F>0` and `G` parity `|F-G|≤0.5pp`, `trades 0.80-1.25×`. Below is the `100` order — `P0` first, `P3` last.

### P0 — WT / BB / DC (27) — Most causal, add first, `1 trade/4h` proof required
| # | Switch | V2 Sheet | Values (TEMPLATE) | Why top |
|---|---|---|---|---|
| 1 | `WT_15M_BOUNCE_OPEN_ENABLED` | `ENTRY_REVERSAL_BOUNCE` | `False/True` | **Bypass-all-filters**, `≥1 trade/4h` at `wt1` cross — `run_wtb15m_bounce_vec.sh` proven `30-200 trades/mo`, `F` in `simple_prove` `MSFT +8.23` |
| 2 | `WT_15M_BOUNCE_MAX_BARS_AGO` | same | `2/100` | Freshness gate for above |
| 3 | `WT_15M_BOUNCE_BB_MIN` | same | `0.05/0.1/0.2` | BB filter for bounce |
| 4 | `WT_15M_BOUNCE_BB_MAX` | same | `0.95` |  |
| 5 | `WT_15M_BOUNCE_REQUIRE_BOTH_HTF` | same | `False/True` | HTF `4h+1h` gate |
| 6 | `WT_15M_BOUNCE_HIGH_1H_GT_PREV` | `AUGMENT` | `False/True` | HTF direction |
| 7 | `WT_15M_BOUNCE_LOW_1H_GT_PREV` | same | — | — |
| 8 | `WT_15M_BOUNCE_REL_VOL_GT_1` | same | `False/True` | Vol gate |
| 9 | `WT_DIV_EXIT_ENABLED` | `EXIT_VELOCITY` | `False/True` | Div exit |
| 10 | `WT_MOMENTUM_EXIT_THRESHOLD` | same | `1/2` | Momentum |
| 11 | `WT_ACCEL_EXIT_ENABLED` | same | `False/True` | Accel |
| 12 | `WT_15M_CROSS_ENTRY_ENABLED` | `ENTRY_REVERSAL_BOUNCE` | `False/True` | Cross entry |
| 13 | `WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED` | `EXIT_VELOCITY` | — | Vel slow |
| 14 | `WT_DC_EXIT_ENABLED` | `EXIT_STRUCTURAL` | `True/False` | DC+WT |
| 15 | `WT_DC_HTF_GATE` | `ENTRY_CONFIRMATION_GATES` | `none/D` | HTF gate |
| 16 | `WT_4H_VEL_EXIT_ENABLED` | `EXIT_VELOCITY` | — | HTF vel |
| 17 | `WT_3M_FORCE_OPEN_ENABLED` | `ENTRY_REVERSAL_BOUNCE` | `False/True` | Force open |
| 18 | `BB_SQUEEZE_ENTRY_ENABLED` | `ENTRY_BREAKOUT_CHANNEL` | `False/True` | Squeeze entry |
| 19 | `BB_SQUEEZE_EXIT_ENABLED` | `EXIT_STRUCTURAL` | — | Squeeze exit |
| 20 | `BB_SQUEEZE_WIDTH_PERCENTILE` | `ENTRY_BREAKOUT_CHANNEL` | `0.1/0.15/0.2/0.25` | Width |
| 21 | `BB_PULLBACK_GATE_TF` | `ENTRY_CONFIRMATION_GATES` | `15m/1h/4h/D` | Pullback TF |
| 22 | `DELTA_GATE_BB_SQUEEZE` | `ENTRY_CONFIRMATION_GATES` | `False/True` | Delta+BB |
| 23 | `DC_BREAKOUT_TF` | `ENTRY_BREAKOUT_CHANNEL` | `15m/D` | DC TF |
| 24 | `DC_BREAKOUT_SCORE` | same | `15/40` | Score |
| 25 | `DC_MOMENT_STRONG_THRESHOLD` | `ENTRY_BREAKOUT_CHANNEL` | `40/48` | Momentum |
| 26 | `DC_HOPELESS_EXIT_ENABLED` | `EXIT_STRUCTURAL` | `False/True` | Hopeless |
| 27 | `EXIT_SCORER_DC_EXTREME` | `EXIT_VELOCITY` | `0.4/0.8` | Extreme |

### P1 — REENTRY / BOUNCE / GUARANTEED (25) — `per_sym` `REENTRY_MANDATORY` cousins
| # | Switch |
|---|---|
| 28 | `REENTRY_MANDATORY` (already in old, keep) |
| 29 | `BOUNCE_REENTRY_ENABLED` + `BOUNCE_REENTRY_K_RESET_*` (3) |
| 30 | `GUARANTEED_REENTRY_ENABLED` + `K_FAVORABLE_HIGH/LOW` (6) — `per_sym` `GUARANTEED` |
| 31 | `BTC_GUARANTEED_REENTRY_ENABLED`/`MAX_AGE`/`MIN_GAP` (3) |
| 32 | `BREAKOUT_LEASH_REENTRY_MULT` (`0.75/1.125/1.5/1.875`) |
| 33 | `HLR_REENTRY_MULT` / `MTS_GATE_ENABLED` (4) |
| 34 | `DAEMON_PRICE_CROSS_REENTRY_*` / `CHANNEL_REENTRY_STOP_ENABLED` (5) |
| 35 | `REENTRY_TIER1_SIZE_MULT_TRADIER` / `REENTRY2_STOCH_CROSS_ENABLED` (2) |
| 36 | `VEC_REENTRY_DC4_EXITPRICE_ENABLED` / `LEGACY_REENTRY_*` (2) |

### P1 — EXIT / HOLD / EROSION (20) — `WIN_TRAIL_EROSION_PCT` complement
| # | Switch |
|---|---|
| 37 | `BREAKEVEN_GAIN_EROSION_ENABLED`/`MIN_GAIN`/`REQUIRE_PROFIT` |
| 38 | `DC_HOPELESS_EXIT_MIN_AGE_S` (`0/0.5/1/2`) |
| 39 | `CRYPTO_SPIKE_FADE_THRESHOLD_PCT` (`10/12`) |
| 40 | `ADX_RANGING_THRESHOLD` (`10/15/20/25`) |
| 41 | `ATR_ADAPTIVE_STOP_MULT` / `STOCH_CROSS_EXIT` / `TREND_EXIT` |
| 42 | `EXHAUSTION_EXIT` / `ALL_TF_AGAINST_CLOSE_ENABLED` / `PEAK_GIVEBACK_BE_EROSION` |
| 43 | `NEWBORN_PROTECT_ENABLED` / `NEWBORN_LOSS_KILL_*` (3) |
| 44 | `FROZEN_STOP` / `CANDLE_PATTERN_STOPS` (2) |

### P2 — AUGMENT / PYRAMID / SIZING (8)
| # | Switch |
|---|---|
| 45 | `AUGMENT_MIN_GAIN_PCT` (`0.5/1/1.5/2/3/5`) |
| 46 | `AUGMENT_AT_LOSS_ENABLED` / `ONLY_WHEN_PROFITABLE` |
| 47 | `AUGMENT_BOUNCE_MIN_GAIN_PCT` / `BREAKOUT_MIN_GAIN_PCT` |
| 48 | `AUGMENT_FALLBACK_GAIN_PCT` / `ATR_ADAPTIVE_SIZING_TARGET_PCT` |
| 49 | `MAX_AUGMENTS_PER_POSITION` / `DYNAMIC_SCORE_AUGMENT_ENABLED` |
| 50 | `PARTIAL_PROFIT_LOCK_FRAC` / `PEAK_GIVEBACK_DROP_TRIGGER_ENABLED` |

### P2 — HTF / GR (8) — Already `GOLDEN_RULE` heavy, fine-tune only
| # | Switch |
|---|---|
| 51 | `GR_FILTER_VEC_ENABLED` / `GR_FILTER_ALL_ENTRIES` |
| 52 | `HTF_GATE_SIGNALS_SMA200D` / `HTF4_CONF` |
| 53 | `EMA_9_21_FILTER_MIN_TFS` / `EMA_BLANKET_FILTER_ENABLED` |
| 54 | `DELTA_HTF_GATE` / `HLR_TOP_MIN_TFS` |

### P3 — OTHER INDICATORS + BTC/RZ (12) + FILTER_TF (10) — Guardrails, batch later
| # | Switch |
|---|---|
| 55 | `ATR_LONG_WINDOW` (`100/150`) / `ATR_TRAIL_SWEEP_ENABLED` |
| 56 | `HA_WICK_QUALITY_ENABLED`/`SCORE`/`TF` |
| 57 | `STDEV_BREAKOUT_ENABLED` / `LR_BAND_LADDER_*` |
| 58 | `BTC_BREAKOUT_ENTRY_ENABLED` / `BTC_RZ_WT_DC_MULTIFACTOR` / `BTC_ACCEL_RAMP_*` (5) |
| 59 | `RZ_CASCADE_MIN_TF_ALIGN` / `RZ_BREAKOUT_ENTRY_ENABLED` |
| 60 | `FILTER_TF` gates (`WT_15M_FILTER_TF` etc) — **batch as 1** `MTF_FILTER_TF OFF/15m/1h/4h/D`, not 53 separate — `FILTER_DICTIONARY_V2` `426→119` filters share this |

**Remaining 40** to reach `100` are `OTHER` `58` in `V2` (`STDEV`, `LR_BAND`, `BTC_*`, `INTRADAY_SESSION_FORCE_EXIT_UTC`) — add only if `P0-P2` `1yr` `Δ>0` proven (see `simple_prove` `F 1-2/5` not yet `1 trade/4h` — that `WT True 0 trades` lie shows `F` not yet proven on this `BTC` window; must fix wiring before adding `OTHER`).

**Status:** `1mo` `3/5m` `5` syms `OK` on `S1` (`BTC 457k→14k` etc), `1yr` `15m` old numbers running (`NVDA 1344 trades 14.02` etc, `GOOGL 1306`, `BTC 10` invalid — see `data/reports/1yr_1mo_proof_1yr_old_hold_15m/summary.csv`). Next is `1mo` `3/5m` vs `15m` delta (`0.8s` vs `0.5s` on `NVDA 5325 bars`) — will post `gain`/`ps` diff for your `5` names once that `1mo` `3/5m` eval lands. No sweep until `F` `1 trade/4h` proven.
