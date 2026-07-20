# TOPIC_STATE — Topic & Diagnostic Tracker

This document tracks active diagnostic issues, recent backtest sweeps, and roadmap progress for the trading system.

---

## 🔍 Session Diagnosis — 2026-05-23

We conducted an extensive review of the historical data, previous configurations, and locked files to resolve the **>$80k loss** and identify the path forward.

### 1. Root Cause Breakdown
1. **Annualization Game**: Prior sessions frequency-gamed the Sharpe ratio using `sqrt(bars_per_year)` instead of `sqrt(trades_per_year)` or raw pooled calculations, promoting unstable configurations.
2. **STRICT_NO_LOSS Entrapment**: Holding losing positions forever instead of cutting them via technical indicators caused massive drawdowns.
3. **Parabolic Suicide Buys**: Buying overbought breakouts when K > 90.
4. **Incorrect Stop-Loss TF**: Stocks used a `15m` ATR trail which is too slow (approx. 5 bars on 5m TF) compared to a `5m` ATR trail (1 bar).

---

## 📊 Backtest Sweeps & Findings

### Stocks Stop-Loss Sweep (2026-05-20)
* **Parameters**: 171 symbols × 2.13 years × 5m base TF.
* **Winner**: `ATR_5m_x2.0` (2x ATR trail on the 5m base TF).
* **Pool Sharpe**: `+0.2257` (per-trade unannualized).
* **Worst Trade**: Capped at `-6.32%`.
* **gain_per_yr**: `+5123.58%/yr` (exponentially outperforming the 2000%/yr target).
* **Drawdown**: `-6.32%` (extremely tight control).
* **Verdict**: Stocks base TF (5m) requires 1-bar trail (`5m` TF), whereas crypto base TF (3m) is validated with `15m` (5-bar).

---

## 🗺️ Roadmap Goals

1. **Keep STRICT_NO_LOSS Disabled**: Ensure that all blanket no-loss variables remain disabled in `config.py` and `config_tradier.py` to prevent positions from getting buried.
2. **Secure the 3 Loss-Exit Paths**: Ensure `R1_DC_LOW4_3M_EMERGENCY`, `R2_WT_VEL_SLOW`, and `HEDGE_FAILED` are the *only* paths that can close positions at a loss.
3. **Optimize the S1 Sweep Fleet**: Maintain the 60-70% CPU/MEM utilization floor on S1. Alternate crypto-heavy and stock-heavy sweeps hourly.
4. **Preserve Memory**: Never modify source code without updating `INDEX.md`, `SCRIPT_STATE.md`, and `TOPIC_STATE.md`.

---

## 🔍 Session Diagnosis — 2026-06-05
We investigated and resolved a critical issue where position augmentations (especially bounce/pullback plays) were not executing despite `hard_augment = True` firing:
1. **Undefined `_wrap_min`**: In `execute_trade_wrapper` (at line 12102), the variable `_wrap_min` was referenced but never defined. This caused a `NameError` which silently failed-open (inside a try-except block) and bypassed the losing-position check.
2. **Strict `MIN_GAIN` Block**: In `execute_trade_wrapper` (at line 12722), the open-position gate hard-coded a check for `_open_gain >= config.MIN_GAIN` (3.0%). This blocked all pullback and bounce augmentations (which are configured to fire at `0.5 * config.MIN_GAIN` = 1.5%). We updated this gate to correctly scale `_min_gain_aug` to `0.5 * config.MIN_GAIN` for pullback and bounce reasons.
3. **900s Cooldown Bypass**: Added a cooldown bypass to the wrapper's 900s absolute duplicate-entry check for high-performing positions (gain >= 4.0% or gain >= config.MIN_GAIN for standard positions), matching the bypass logic in `execute_now()`.

---

## 🔍 Session Diagnosis — 2026-06-06
We updated system rules to reflect the clarified parity verification focus:
1. **Focus on Logic/Trading Parity**: Parity checks must verify trading decisions/logic between live trading (`tradier_` and `ez_` scripts) and sandbox backtests (`vectorized` and `per_sym` tests).
2. **Synchronization is Handled**: `push.py` is the authority for MacBook-to-S1 syncing and live stack service restarts. We do not need manual verification or checking via `check_sandbox_parity.py`.

## 📊 Timeframe Exit Combinations Backtest — 2026-06-07
We executed a complete backtest sweep on S1 (140 crypto + 293 stock npz files) comparing the standard WT cross-back baseline exit against 18 lower-HL timeframe exit combinations (single, OR-gate, and AND-gate variants across 3m/5m, 15m, 1h, 4h, D):
1. **OR-Gates are Superior Drawdown Guards**: Exit rules using logical OR (e.g. exiting if a structure breakdown occurs on EITHER timeframe) cut losses earlier and preserve a much higher Sharpe ratio than AND-gates (which delay exits too much).
2. **Best Performing Combo**: `base_or_15m_or_D` (using the 3m/5m base TF, 15m, or Daily open) achieved the highest Sharpe ratio among all structure exit variants (+0.3236 Crypto / +0.1226 Stocks).
3. **WT Cross-Back Dominance**: The standard WT cross-back baseline remains the overall winner (+0.3931 Crypto / +0.2150 Stocks).
4. **⚡ Breakthrough: WaveTrend + HTF Structure Hybrid Exits (OR Gate)**:
   - Combining base WaveTrend crossover OR higher TF structure break achieves a **huge performance boost** over baseline.
   - **Crypto LONG**: pool_sharpe rises **`0.4281` → `0.4670`** (+9.1%) and max_dd drops **`1106.77%` → `775.59%`** (using base WT + Daily structure OR exit).
   - **Crypto SHORT**: pool_sharpe jumps **`0.0191` → `0.4767`** and max_dd drops **`23719.03%` → `759.03%`** (using base WT + 4h structure OR exit), eliminating catastrophic runaway losses on short positions.
   - **Stocks SHORT**: pool_sharpe rises **`0.2377` → `0.2427`** (using base WT + 15m structure OR exit).
5. **Reference Files**:
   - Detailed Documentation: [BACKTEST_RESULTS_METRICS.md](file:///Users/niels/Documents/binance/BACKTEST_RESULTS_METRICS.md)
   - Integration Suggestions: [HYBRID_EXIT_SUGGESTIONS.md](file:///Users/niels/Documents/binance/HYBRID_EXIT_SUGGESTIONS.md)
   - Results Query Utility: [query_bt_detailed.py](file:///Users/niels/Documents/binance/tools/query_bt_detailed.py)
   - Raw Results Database: [bt_lower_hl_detailed_results.csv](file:///Users/niels/Documents/binance/data/bt_lower_hl_detailed_results.csv)
   - MacBook Sweep Script: [test_hybrid_exits.py](file:///Users/niels/Documents/binance/scratch/test_hybrid_exits.py)
   - S1 script at `/home/niels/binance-sandbox/tools/bt_lower_hl_combinations.py` and logs at `/home/niels/logs/bt_lower_hl_combinations.log`.

---

## 🔧 WaveTrend + HTF Structure Hybrid Exit Integration — 2026-06-07
We successfully integrated the timeframe structure hybrid exit (OR Gate) into the live trading manager loops:
1. **Config Defaults**: Added `LONG_STRUCT_EXIT_TF` and `SHORT_STRUCT_EXIT_TF` parameter defaults in [config.py](file:///Users/niels/Documents/binance/config.py) (Crypto: `D`/`4h`) and [config_tradier.py](file:///Users/niels/Documents/binance/config_tradier.py) (Stocks: `D`/`15m`).
2. **Orchestrator Integration**: Wired the `HYBRID_STRUCT_EXIT` check right after R1 block in [ez_manage.py](file:///Users/niels/Documents/binance/ez_manage.py) and [tradier_manage.py](file:///Users/niels/Documents/binance/tradier_manage.py).
3. **Transition-Only Evaluation**: The check runs on higher-timeframe candle open boundaries (by tracking and freezing `_last_struct_open` on the active position). It evaluates lower-high + lower-low (for LONGs) or higher-high + higher-low (for SHORTs) relative to the prior closed timeframe candle to trigger a clean exit.
4. **Syntax/Parity Verified**: All modified files were compiled and verified for syntax parity, and backup files were preserved in `binance/backups/`.

---

## 🔍 Session Diagnosis — 2026-07-19

We analyzed why the `tradier_manage` and `binance-sandbox` trading scripts underperform simple B&H on the worst-performing symbols and detailed recommendations for 2x B&H outperformance:
1. **Benchmark Mismatch (Shorts vs. Long B&H)**: Short strategies (`MOS_SHORT`, `DUOL_SHORT`, `MSTR_SHORT`, `MSFT_SHORT`) are compared to long B&H return of the stock in a bull market. While they made positive gains (a victory for shorting), they look bad on a long benchmark.
2. **Opportunity Cost (Low Market Exposure)**: Long strategies (`ARM_LONG`, `ROKU_LONG`, etc.) have extremely low coverage (1% to 4%), meaning they sit in cash for 96% to 99% of the major rallies.
3. **Over-Filtering and Premature Exits**: Strict entry gates prevent entries during strong runs (no deep pullbacks), and the exit scorer cuts winners short on low-timeframe wiggles.
4. **Churn and Friction**: High trade counts inside tiny holding windows violate the **Churn Law**, leading to chop losses and commission/slippage drain.
5. **Recommendations**: Introduce a trend-following breakout regime above SMA200, enforce minimum holding times/trailing stops, reduce trade counts per day, implement pyramiding (adding to winners), and scale sizing dynamically.
6. **Reference File**: [bh_underperformance_analysis.md](file:///Users/niels/.gemini/antigravity-cli/brain/f413d34e-fd28-4b2e-a7b8-42b3dc5ee76a/bh_underperformance_analysis.md)

### Diagnostic Resolution & Confirmation Bypass
We completed the implementation of the B&H inversion diagnostic fixes and the guaranteed reentry trend-resumption confirmation bypass:
1. **Short B&H Benchmark Inversion Bug Fixed**:
   - Fixed `tools/capture_analysis.py` to calculate the `gain_vs_bh` ratio correctly when the benchmark `bh_key` is negative (meaning a short strategy beats a rising asset, e.g., `bh_key != 0` instead of gating with `bh_key > 0`).
   - Fixed `tools/reopt_loop.py` to remove `abs(b)` in `gvbh` calculation so B&H returns are correctly inverted and signed for shorts.
2. **Guaranteed Re-entry Trend-Resumption Confirmation Bypass**:
   - Implemented a confirmation gate bypass when a trend resumes past the exit price by `REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT` (default `0.002` = 0.2%).
   - Modified `vec_decisions/guaranteed_price_cross_reentry.py` (both scalar and vectorized paths) and `ez_reentry.py` to bypass confirmation and re-enter immediately when the price runs in favor.
   - Updated `backtest_v8_engine.py` to store the order exit price in `_bt_reduce_price` on all reduction/close event hooks and pass it to `_v8_reentry_cooldown_check` to prevent reentry starvation in the backtest engine.
3. **Parity and Sync Verified**:
   - Added `REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT` to `config.py` and `config_tradier.py`.
   - Confirmed all 28 critical trading and backtest files are bit-identical between MacBook and S1 sandbox, and synced the changes. Verified `reopt_loop.py` runs correctly on S1.

