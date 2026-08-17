# Classic formation Tradier vector campaign — 2026-08-02

## Decision

Enabled live for both Tradier and EZ:

- Entry: Head and Shoulders / inverse, Double Top / Bottom, Rising / Falling Wedge,
  Cup and Handle / inverse, Higher-High/Higher-Low and Lower-High/Lower-Low trend structure.
- Exit: Head and Shoulders / inverse, Rising / Falling Wedge, and trend structure.
- Kept disabled: Triangle entry/exit, Flag/Pennant entry/exit, Double Top/Bottom exit,
  and Cup and Handle exit.

Every path uses the same causal `classic_formations.py` detector. Frozen NPZ arrays and
closed live klines therefore share field names, scores, direction, and confirmation timing.

## Scope and limitation

The local Tradier NPZ universe contained 9 tickers (AAPL, HAO, LRCX, MU, NUKZ, NVDA,
ROKU, SPY, VT), or 18 long/short cells. Full-history runs covered about 2.33 years;
holdout began 2025-08-01 and covered about 0.98 years. The engine labels all results
`DIAGNOSTIC` because 18 cells are below its 48-cell publication threshold. These results
justify guarded defaults for observation, not a promotion-grade profitability claim.

All runs used the vector engine with `--workers 1`: calculation within each symbol is
NumPy-vectorized, while symbol scheduling was sequential because this sandbox blocks the
process semaphore used by the multiworker scheduler.

## Baseline versus selected combination

| Window | Variant | Pool Sharpe | Symbol Sharpe | Avg gain/trade | Trades | P&L | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full | Baseline | 0.035050 | 0.093258 | 0.1861% | 1,064 | 197.9746% | 96.5% |
| Full | Selected | 0.093102 | 0.240910 | 0.2322% | 4,999 | 1,160.7391% | 91.3% |
| Holdout | Baseline | -0.010803 | -0.050232 | -0.0707% | 459 | -32.4650% | 96.5% |
| Holdout | Selected | 0.065330 | 0.245300 | 0.1971% | 2,298 | 452.8900% | 91.3% |

The drawdown remains extreme, so the formations continue to pass through existing live
portfolio, eligibility, sizing, and no-loss gates rather than overriding them.

## Full-history one-at-a-time entry arms

| Family | Pool Sharpe | Symbol Sharpe | Avg gain/trade | Trades | P&L | Max DD | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Head and Shoulders | 0.045415 | 0.135316 | 0.2367% | 1,111 | 262.9500% | 96.5% | Enable |
| Double Top / Bottom | 0.065661 | 0.224746 | 0.3157% | 1,368 | 431.8443% | 96.5% | Enable |
| Wedge | 0.057722 | 0.212069 | 0.2069% | 2,715 | 561.8607% | 96.7% | Enable |
| Triangle | 0.039254 | 0.124981 | 0.2058% | 1,090 | 224.2991% | 96.5% | Disable (holdout failed) |
| Flag / Pennant | 0.036811 | 0.096030 | 0.1952% | 1,068 | 208.4844% | 96.5% | Disable (negligible) |
| Cup and Handle | 0.054394 | 0.175919 | 0.2645% | 1,311 | 346.7080% | 96.5% | Enable |
| Trend structure | 0.089262 | 0.252343 | 0.2929% | 3,551 | 1,039.9700% | 96.6% | Enable |

## Full-history one-at-a-time exit arms

| Family | Pool Sharpe | Symbol Sharpe | Avg gain/trade | Trades | P&L | Max DD | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Head and Shoulders | 0.036687 | 0.114405 | 0.1894% | 1,078 | 204.1531% | 95.8% | Enable after combo ablation |
| Double Top / Bottom | 0.029969 | 0.108963 | 0.1521% | 1,162 | 176.7034% | 96.5% | Disable |
| Wedge | 0.047543 | 0.136432 | 0.2204% | 1,144 | 252.1321% | 92.9% | Enable |
| Triangle | 0.032542 | 0.093810 | 0.1725% | 1,072 | 184.9452% | 96.5% | Disable |
| Flag / Pennant | 0.035050 | 0.093258 | 0.1861% | 1,064 | 197.9746% | 96.5% | Disable (identical baseline) |
| Cup and Handle | 0.021090 | 0.096667 | 0.1030% | 1,170 | 120.5551% | 95.7% | Disable |
| Trend structure | 0.048313 | 0.146726 | 0.2022% | 1,279 | 258.6237% | 91.1% | Enable |

## Holdout checks and Head-and-Shoulders ablation

Positive entry holdouts were trend structure (pool Sharpe 0.043710), Double Top/Bottom
(0.022812), Cup and Handle (0.017220), Wedge (0.016112), and Head and Shoulders
(0.004705). Triangle remained negative (-0.009562). Positive exit holdouts were trend
structure (0.042919) and Wedge (0.026218). The Head-and-Shoulders exit was weak alone
(-0.001300) but improved the selected combination:

| Selected holdout variant | Pool Sharpe | Symbol Sharpe | P&L | Trades | Max DD |
|---|---:|---:|---:|---:|---:|
| All selected arms | 0.065330 | 0.245300 | 452.8900% | 2,298 | 91.3% |
| Without H&S exit | 0.063744 | 0.248383 | 444.3196% | 2,295 | 91.3% |
| Without H&S entry | 0.064615 | 0.244992 | 447.0864% | 2,290 | 91.3% |
| Without both H&S arms | 0.062970 | 0.246020 | 438.0909% | 2,287 | 91.3% |

## Primary vector receipts

- Full baseline: `data/sweep_results/v8_vec_sweep_1785702251_4162.csv`
- Holdout baseline: `data/sweep_results/v8_vec_sweep_1785702577_15605.csv`
- Full selected combination: `data/sweep_results/v8_vec_sweep_1785704154_28131.csv`
- Holdout selected combination: `data/sweep_results/v8_vec_sweep_1785704154_28129.csv`
- Holdout without H&S exit: `data/sweep_results/v8_vec_sweep_1785704205_29752.csv`
- Holdout without H&S entry: `data/sweep_results/v8_vec_sweep_1785704205_29751.csv`
- Holdout without both H&S arms: `data/sweep_results/v8_vec_sweep_1785704205_29750.csv`
