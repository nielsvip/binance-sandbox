# 30min Progress — 2026-08-11 13:32 UTC

**Counters:** `964→964 params` `zeros 661` `has_neg 53` `trusted 152`

## Stocks (TRB) — best vectorized (exact per-sym, audited)
- **Best row** `pool_sharpe=0.2448` `sym_sharpe=0.4721407554695474` `avg_gain_trade=0.967458552512977` `gain/mo=3.35%/mo` `gain_per_yr=40.2%/yr` `gain_sym_yr=13.403044765059132` `trades=96` `n_syms=3` `years=2.31` `dd=20.5%` `tim=?` `verdict=DIAGNOSTIC` file `v8_vec_sweep_1785769670_41482.csv`
  - symbols: `trb tradier 2022-01-01`
  - **Audit:** DIAGNOSTIC (not promotable); n_syms=3<8 (floor 48 crypto/100 stocks); pool_sharpe 0.24<1.0 (Noise tier)

  **Per-sym exact:**

| Sym | Events | Closes | Avg gain | WR |
|---|---|---|---|---|
| LRCX | 55 | 18 | +0.22% | 56% |
| NVDA | 63 | 24 | +0.74% | 79% |
| ROKU | 42 | 15 | -0.54% | 67% |

## Crypto (FLZ/FIN) — best vectorized (exact per-sym, audited)
- **Best row** `pool_sharpe=0.2055` `sym_sharpe=0.21117928207142053` `avg_gain_trade=1.9041958316830647` `gain/mo=117.78%/mo` `gain_per_yr=1413.3%/yr` `gain_sym_yr=235.55233490254213` `trades=1750` `n_syms=6` `years=2.36` `dd=100.0%` `tim=?` `verdict=DIAGNOSTIC` file `v8_vec_sweep_1786334418_95712.csv`
  - symbols: `flz crypto 2024-01-01`
  - **Audit:** DIAGNOSTIC (not promotable); n_syms=6<8 (floor 48 crypto/100 stocks); DD 100.0% >25% ceiling; pool_sharpe 0.21<1.0 (Noise tier)

  **Per-sym exact:**

| Sym | Events | Closes | Avg gain | WR |
|---|---|---|---|---|
| BCHUSDC | 388 | 177 | +0.15% | 50% |

## All 32 sym_side — exact 1yr vectorized (sym_side NOT sym only, dd TIM bh delta vs bh test dates)
| Sym_Side | pool_sharpe | gain/mo | bh/mo | Δ vs bh/mo | DD | TIM | Trades | WR | Test dates (window) |
|---|---|---|---|---|---|---|---|---|---|---|
| AAPL_LONG | 0.168 | +1.76% | +7.32% | -5.48% | 3.9% | 5.0% | 458 | 67.5% | 395d 12mo |
| AGI_SHORT | -0.080 | -18.50% | ? | -16.40% | ? | 45.2% | 62 | 52.1% | ? ?mo |
| ASTS_SHORT | -0.000 | -4.72% | +0.78% | -5.50% | 22.2% | 22.2% | 110 | 50.2% | ? 12mo |
| GOOGL_LONG | 0.124 | +10.96% | +8.63% | +2.30% | 5.2% | 30.1% | 3063 | 63.2% | 397d 12mo |
| HAO_SHORT | -0.080 | -18.50% | ? | -25.30% | ? | 45.2% | 85 | 52.1% | ? ?mo |
| IBIT_SHORT | -0.080 | -18.50% | ? | -16.40% | ? | 45.2% | 62 | 52.1% | ? ?mo |
| LAC_SHORT | -0.080 | -18.50% | ? | -25.30% | ? | 45.2% | 85 | 52.1% | ? ?mo |
| MU_LONG | 0.126 | +32.27% | +47.12% | -14.63% | 6.8% | 50.3% | 6258 | 65.6% | 394d 12mo |
| NVDA_LONG | 0.000 | +0.01% | +8.59% | -8.46% | 0.0% | 0.0% | 1 | 100.0% | 397d 12mo |
| PBF_LONG | 0.080 | +18.50% | ? | +16.40% | ? | 45.2% | 62 | 52.1% | ? ?mo |
| PLTR_LONG | 0.080 | +18.50% | ? | +25.30% | ? | 45.2% | 35 | 52.1% | ? ?mo |
| RBLX_SHORT | -0.080 | -18.50% | ? | -16.40% | ? | 45.2% | 62 | 52.1% | ? ?mo |
| SLV_SHORT | -0.080 | -18.50% | ? | -25.30% | ? | 45.2% | 57 | 52.1% | ? ?mo |
| USAR_SHORT | -0.080 | -18.50% | ? | -25.30% | ? | 45.2% | 62 | 52.1% | ? ?mo |
| UUUU_SHORT | -0.080 | -18.50% | ? | -25.30% | ? | 45.2% | 62 | 52.1% | ? ?mo |

## Previously badly scoring (16 with Δ≤0) — recalculating until Δ>0 ideal 10× BH
| Sym_Side | Δ vs bh/mo | gain/mo | bh/mo | DD | TIM | Status |
|---|---|---|---|---|---|---|
| WDAY_SHORT | -81.95% | +10.44% | +92.39% | 13.4% | 35.7% | recalculating → need Δ>0, ideal 10× BH |
| HAO_SHORT | -25.30% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| LAC_SHORT | -25.30% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| SLV_SHORT | -25.30% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| USAR_SHORT | -25.30% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| UUUU_SHORT | -25.30% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| AGI_SHORT | -16.40% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| IBIT_SHORT | -16.40% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| RBLX_SHORT | -16.40% | -18.50% | ? | ? | 45.2% | recalculating → need Δ>0, ideal 10× BH |
| MU_LONG | -14.63% | +31.80% | +46.44% | 6.8% | 50.3% | recalculating → need Δ>0, ideal 10× BH |
| VLO_LONG | -14.63% | +21.65% | +36.28% | 6.3% | 39.9% | recalculating → need Δ>0, ideal 10× BH |
| MRVL_LONG | -14.61% | +0.47% | +15.07% | 17.6% | 33.3% | recalculating → need Δ>0, ideal 10× BH |
| LLY_LONG | -14.34% | -1.84% | +12.51% | 3.9% | 35.2% | recalculating → need Δ>0, ideal 10× BH |
| ASML_LONG | -14.03% | +32.98% | +47.01% | 6.0% | 37.2% | recalculating → need Δ>0, ideal 10× BH |
| AMZN_LONG | -13.55% | -6.34% | +7.21% | 7.8% | 43.0% | recalculating → need Δ>0, ideal 10× BH |
| TSM_LONG | -9.80% | -12.17% | -2.37% | 11.1% | 41.9% | recalculating → need Δ>0, ideal 10× BH |

## New good — 100 other stocks (beyond 32) + FLZ/FIN cryptos — vectorized 1yr
| Sym_Side | pool_sharpe | gain/mo | Δ vs bh/mo | DD | TIM | Test dates |
|---|---|---|---|---|---|---|
| ABT_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| ADBE_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| AEM_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| AGCO_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| AG_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| ALB_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| AMD_LONG | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| ARM_LONG | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| ARM_SHORT | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| AR_LONG | 0.245 | +3.35%/mo | ? | 20.451237352763073% | ? | 2.309823940984105yr |
| _… 161 more other stocks — see sweep_results_ | | | | | | |

**FLZ (10 majors) — vectorized 1yr:**
| Sym_Side | pool_sharpe | gain/mo | DD | TIM | Trades |
|---|---|---|---|---|---|
| BNBUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| BNBUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| BTCUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| BTCUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| ETHUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| ETHUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| SOLUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| SOLUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| XRPUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| XRPUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |

**FIN (100 other cryptos — 85 fin) — vectorized 1yr:**
| Sym_Side | pool_sharpe | gain/mo | DD | TIM | Trades |
|---|---|---|---|---|---|
| BTCDOMUSDT_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| BTCDOMUSDT_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| DOGEUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| DOGEUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| ZECUSDC_LONG | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |
| ZECUSDC_SHORT | 0.206 | +117.78% | 99.989147842373% | ? | 1750 |


## Notes
- `V8_VEC_UNKNOWN_KNOBS` = 0 (dynamic attrs now wired), `MIN_HOLD_BARS` 0/4/8/16 tested, `switch_lab` header fixed, `V8_MAX_WALL_SECS` prevents `exit 124` hang.
- Next: FIN 85 chunked 1yr + per-switch 600+600 vectorized-only, no live `backtest_v8_engine`.

*Generated 2026-08-11 13:32 UTC — `V8_VEC_SANDBOX_FALLBACK=1 V8_VEC_ALLOW_DIAGNOSTIC=1`*
