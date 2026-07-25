# Systematic exits near tops and stateful lower-price re-entry

**Date:** 2026-07-25  
**Scope:** Stocks first (`MU_LONG`, `VT_LONG`, `HAO_SHORT`), then the remaining tradeable keys and the `ez_` crypto universe  
**Purpose:** Turn reputable trend, stop, market-structure, divergence, and channel concepts into causal algorithms that can be swept quickly. This is research input, not evidence that any candidate already works in this system.

## Executive conclusion

The literature does **not** support a universal indicator that reliably identifies the exact top. It supports a more defensible sequence:

1. Let a confirmed trend run with a deliberately slow exit.
2. Arm a faster exit only after measurable exhaustion or a structural break.
3. Require a causal trigger after arming, such as a failed retest, opposite Donchian break, channel re-entry, or trailing-stop breach.
4. Store the exit state.
5. Re-enter either at a materially better price after a confirmed trend-resumption pattern, or immediately when price invalidates the exit by reclaiming the stored exit/top level.

This directly addresses the current failure mode documented in `TOPIC_STATE.md` and `REWORK_RESULTS_STORE_20260718.md`: 5-minute exits turn multi-month trends into short scalps, while missing or blocked re-entry leaves time in market near zero. A better top exit cannot rescue performance if the engine still exits on 5-minute noise or cannot re-enter.

The first diagnostic sweep should therefore compare a **single slow trend-preserving exit** against the current baseline before combining paths. If the slow exit still produces very low time in market, the problem is structural wiring, fill timing, or re-entry—not parameter choice.

## What the evidence supports

### Strongest general evidence

- Moskowitz, Ooi, and Pedersen document time-series momentum across 58 liquid futures/forward instruments, with return persistence over roughly one to twelve months and partial reversal at longer horizons. This supports preserving established trends and using reversal logic only after evidence of trend deterioration, rather than constantly scalping them. [Time Series Momentum, *Journal of Financial Economics* (2012)](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf)
- Brock, Lakonishok, and LeBaron test moving-average and trading-range-break rules on the Dow Jones Industrial Average and report evidence inconsistent with several common null models. This is support for testing objective breakout/regime rules, not proof that a specific parameter will work now or on these symbols. [Simple Technical Trading Rules and the Stochastic Properties of Stock Returns, *Journal of Finance* (1992)](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x)
- Kaminski and Lo show why stops are conditional tools: under a random walk, simple all-in/all-out stop-loss rules reduce expected return; in the presence of momentum they can add value. This argues against firing every exit path continuously and for regime-conditioned stops. [When Do Stop-Loss Rules Stop Losses?, *Journal of Financial Markets* (2014)](https://ideas.repec.org/p/hhs/sifrwp/0063.html)
- The original Turtle framework uses objective 20/55-day breakouts, opposite-channel exits, and volatility-normalized sizing. Its main transferable lesson is asymmetric timing: a relatively slow entry/trend state paired with a faster opposite breakout for exit. [Original Turtle rules overview](https://www.turtletrader.com/rules/) and [Curtis Faith rules PDF](https://c.mql5.com/3/131/Curtis_Faith_-_Original_Turtle_Rules.pdf)

### Useful mechanical definitions

- Chandelier Exit is conventionally defined for a long as a rolling high minus an ATR multiple, commonly 22 periods and 3 ATR. Its purpose is to keep a position in a trend while allowing volatility room. [StockCharts ChartSchool: Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit)
- Parabolic SAR is a monotonic trailing mechanism whose acceleration factor tightens as a move extends. Smaller acceleration settings reduce sensitivity and whipsaw. It is better tested as an armed trigger after exhaustion than as an always-on 5-minute exit. [StockCharts ChartSchool: Parabolic SAR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/parabolic-sar)
- TradingView defines a regression channel as a least-squares center line plus/minus a chosen number of standard deviations. It is a useful objective measure of “unusually far from trend,” but an excursion alone is not a reversal signal. [TradingView: Linear Regression](https://www.tradingview.com/support/solutions/43000644936-linear-regression/) and [causal rolling implementation example](https://www.tradingview.com/script/PAM5tzSw-Rolling-Linear-Regression-Channel/)
- RSI divergence has a testable definition—price makes a higher high while RSI makes a lower high for bearish divergence, mirrored for bullish divergence—but can persist while price continues trending. It must arm a structural trigger, not directly cause an exit. [StockCharts ChartSchool: RSI](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi)

### Price-action educators and books converted into rules

- Adam Grimes describes a failure test as price moving beyond a prior pivot and quickly reversing, and an “Anti” as the first pullback after an established trend suffers a sharp counter-trend shock. These descriptions map cleanly to a causal arm/retest/trigger state machine. [Adam Grimes: Fundamental Trading Patterns](https://www.adamhgrimes.com/fundamental-trading-patterns/)
- Al Brooks describes a major trend reversal as a process rather than one candle: trend break, test of the old extreme, then reversal evidence. The book is a respected publisher source; the exact algorithm below is our testable translation, not a claim that Brooks published those numeric thresholds. [Wiley: *Trading Price Action Reversals*](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119202622)
- A simple break-and-retest educator formulation supplies a directly testable continuation/re-entry template: define a prior level, require a close through it, then require a retest and renewed continuation. [TradeZella: Break & Retest strategy](https://www.tradezella.com/strategies/break-retest)

YouTube search results were too noisy to justify importing undocumented claims. Educator material is included only where the public page states a rule that can be encoded. Popularity or a successful-looking chart is not evidence.

## Non-negotiable causal backtest rules

Every candidate below must use these rules. A result that violates one is invalid and should be red in the matrix.

1. **Closed-bar decisions:** A signal calculated from bar `t` can fill no earlier than bar `t+1` open. If live logic genuinely submits a stop resting before bar `t`, model the resting order separately.
2. **Shift breakout thresholds:** `rolling_high(N)` and `rolling_low(N)` used as break levels must be calculated through `t-1`, never including the bar that is being tested.
3. **Confirmed pivots:** A pivot at index `p` with `r` right bars becomes known only at `p+r`. It can arm or trigger an action only at `p+r` or later and can fill only afterward. Never stamp an exit back onto `p`.
4. **Completed higher-timeframe bars:** Daily, 4-hour, and 1-hour features become visible only after their source bar closes. Join them to lower-timeframe rows using an as-of backward join on availability timestamp. Never forward-fill a still-forming daily/4-hour value.
5. **Conservative stops and gaps:** For a long resting stop, if next open is below the stop, fill at the open minus modeled slippage; otherwise fill at the stop minus slippage if the low touches it. Mirror for shorts.
6. **No same-bar best case:** When both stop and favorable target are touched and sub-bar sequence is unknown, use the adverse sequence or mark the bar ambiguous and report both bounds.
7. **Costs everywhere:** Charge commission, spread, SEC/TAF where relevant, borrow/locate assumptions for shorts, and slippage on every reduction and re-entry.
8. **Corporate actions and sessions:** Use split/dividend-adjusted comparison series consistently, but trade with executable prices. Do not let overnight daily indicators leak into the prior session.
9. **State survives exits:** Exit price, exit top/bottom, direction, quantity, maximum favorable excursion, reason, and exit timestamp must persist until re-entry or explicit expiry. A process restart must not erase them.
10. **No GPT discretion:** GPT, news, or an agent may propose a candidate outside the backtest, but may not label historical bars, choose exits, alter quantities, or resolve ambiguous fills inside it.
11. **Walk-forward selection:** Optimize on a discovery window, freeze parameters, then score on the next window. Do not select symbol parameters using the same period reported as final performance.
12. **Correct benchmark:** Long keys compare with long buy-and-hold. Short keys need both a short-and-hold benchmark and the underlying long B&H context; “beating B&H” must not silently reward being short during a falling sample only.

## Candidate algorithms

The expected time-in-market (TIM) ranges below are **design hypotheses**, not results. They assume a ladder or trend entry remains enabled and mandatory invalidation re-entry works. Report realized TIM and reject the candidate if it falls far outside the range.

### E01 — Monotonic Chandelier trend-preserving exit

**Purpose:** Establish whether the engine can hold a trend when fast exits are removed.

For a long on bar `t`:

```text
raw_stop[t] = highest(high[t-N:t-1]) - k * ATR_N[t-1]
stop[t] = max(stop[t-1], raw_stop[t])
exit_signal[t] = low[t] <= stop[t]
```

For a short:

```text
raw_stop[t] = lowest(low[t-N:t-1]) + k * ATR_N[t-1]
stop[t] = min(stop[t-1], raw_stop[t])
exit_signal[t] = high[t] >= stop[t]
```

Reset the monotonic stop only when flat or after direction changes. Test on `1h`, `4h`, and `D`; do not start on 5-minute data.

- Grid: `N ∈ {14, 22, 30, 44, 66}`, `k ∈ {2.0, 2.5, 3.0, 3.5, 4.0, 5.0}`.
- Expected TIM: `65–92%`.
- Vectorization: ATR and raw stops are vectorized; a short monotonic state scan is O(N) and Numba-friendly.
- Diagnostic value: **highest**. If this produces 1–5% TIM, another exit is still firing, the entry/re-entry state is broken, or the stop is mis-scaled.
- Source basis: [Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit).

### E02 — Turtle/Donchian opposite-channel exit with breakout re-entry

**Purpose:** Use a classic asymmetric trend rule that naturally exits later than oscillator crosses.

Long:

```text
exit when close[t] < lowest(low[t-N_exit:t-1])
re-enter when close[t] > highest(high[t-N_entry:t-1])
```

Short is mirrored. A more responsive variant uses intrabar touch with a resting stop; it must use the stop known at `t-1`.

- Grid: `N_exit ∈ {5, 10, 15, 20, 30}`, `N_entry ∈ {10, 20, 40, 55}` with `N_entry >= N_exit`.
- Timeframes: `1h`, `4h`, `D`.
- Expected TIM: `55–88%`.
- Vectorization: fully vectorized rolling extrema and crosses; re-entry state is a simple scan.
- Source basis: [Turtle rules](https://www.turtletrader.com/rules/).

### E03 — Confirmed LH/LL arm, failed-retest exit

**Purpose:** Encode the requested “lower high/lower low break → arm → exit at the next top/retest” without hindsight.

Long state machine:

```text
TREND:
  wait for a confirmed lower high LH1
  if close breaks below the last confirmed swing low SL0 -> ARMED

ARMED:
  track rebound high
  if a new confirmed pivot high RH is below LH1 + tolerance
     and close subsequently breaks below the rebound's confirmed swing low
     -> EXIT next bar
  if close reclaims the pre-break high + invalidation_buffer -> cancel arm
  if max_wait expires -> cancel arm or fall back to E01/E02
```

Short is the exact mirror: confirmed higher low, break above swing high, failed lower retest, then break above rebound high.

- Pivot grid: left/right bars `(l,r) ∈ {(2,2),(3,3),(5,3),(5,5)}`.
- Tolerance: `0.0–0.75 ATR`; structural break buffer `0.0–0.5 ATR`.
- Maximum armed duration: `4, 8, 12, 20` source-timeframe bars.
- Timeframes: structure on `1h` or `4h`; optional trigger on completed `15m/1h`.
- Expected TIM: `55–82%`.
- Vectorization: pivot masks and break arrays are vectorized; event sequencing is one O(N) state scan per parameter set.
- Source basis: systematic translation of the major-trend-reversal sequence in [Al Brooks/Wiley](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119202622) and the failure-test/Anti descriptions from [Adam Grimes](https://www.adamhgrimes.com/fundamental-trading-patterns/).

### E04 — Trend-break, old-extreme test, reversal trigger

**Purpose:** Test a simpler Brooks-style major trend reversal without depending on subjective trendlines.

Define the long trend as positive `EMA_slow` slope plus closes above it. Then:

1. **Arm:** close below `EMA_slow` by `break_buffer`.
2. **Test:** within `W` bars, price trades to within `test_tolerance` of the pre-break highest high, but does not close above it by `invalidation_buffer`.
3. **Trigger:** close below the test bar low, or below the lowest low of the prior `m` bars.
4. **Exit:** next bar open.
5. **Cancel:** new close above the old extreme plus invalidation buffer.

Mirror for shorts.

- Grid: `EMA_slow ∈ {20, 34, 50, 89}`, `break_buffer ∈ {0, 0.25, 0.5} ATR`, `test_tolerance ∈ {0.25, 0.5, 1.0} ATR`, `W ∈ {4, 8, 12, 20}`, `m ∈ {1, 2, 3}`.
- Timeframes: `1h/4h`; daily only for trend state.
- Expected TIM: `50–82%`.
- Vectorization: vectorized feature arrays plus small state scan.
- Source basis: [Trading Price Action Reversals](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119202622). Numeric definitions are ours and must be swept.

### E05 — Divergence arms, structure confirms

**Purpose:** Use RSI, WT, or MACD divergence as exhaustion evidence without treating it as an immediate top call.

Long:

1. Identify two **confirmed** price pivot highs `P1`, `P2`.
2. Require `price[P2] > price[P1] + price_buffer`.
3. Require oscillator at the corresponding confirmed pivot to be lower by `div_min`.
4. Arm only when higher-timeframe trend was positive before `P2`.
5. Exit only after close breaks the confirmed intervening swing low, or E03's failed-retest trigger fires.

Mirror for shorts.

- Oscillators: RSI `7/14/21`, existing WT pair, MACD histogram.
- Grid: price buffer `0–0.5 ATR`; RSI divergence `3, 5, 8, 12` points; WT/MACD divergence as `0.25, 0.5, 1.0` rolling oscillator standard deviations; confirmation window `2–16` bars.
- Expected TIM: `60–90%`.
- Vectorization: confirmed pivot arrays plus indexed oscillator values; state scan for confirmation.
- Failure control: divergence alone must never exit.
- Source basis: [StockCharts RSI divergence](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi).

### E06 — Regression-channel excursion then channel re-entry

**Purpose:** Define “top gray line” statistically and exit only when price comes back inside it.

On closed bars, fit rolling least-squares regression to `log(close)`:

```text
center[t] = rolling_OLS_prediction_using_bars_through_t-1
sigma[t] = std_or_RMSE_of_residuals_through_t-1
z[t] = (log(close[t]) - center[t]) / sigma[t]
```

Long:

1. Arm when slope is positive and `z[t] >= z_arm`.
2. Exit when a later close returns below `z_exit`, or when price breaks the prior bar low while `z` is falling.
3. Cancel only if the trend regime changes before arming; after arming, timeout reverts to the slow trail.

Mirror for shorts.

- Grid: `N ∈ {40, 60, 100, 150, 250}`, `z_arm ∈ {1.5, 2.0, 2.5, 3.0}`, `z_exit ∈ {0.75, 1.0, 1.5, 2.0}` with `z_exit < z_arm`.
- Optional quality gate: absolute rolling correlation `R >= {0.5, 0.7, 0.85}`.
- Expected TIM: `45–78%`.
- Vectorization: rolling sums provide O(N) OLS features; arming is a state scan.
- Source basis: [TradingView Linear Regression](https://www.tradingview.com/support/solutions/43000644936-linear-regression/).

### E07 — Exhaustion-armed Parabolic SAR

**Purpose:** Test a responsive trailing exit only after evidence that a top/bottom may be forming.

PSAR is **inactive** during ordinary trend continuation. Activate it only after one of:

- E05 divergence arm;
- E06 regression excursion;
- two-timeframe stochastic/WT exhaustion with deceleration;
- daily/4-hour band excursion.

Once active, exit on the first causal PSAR reversal. Deactivate if price makes a new trend extreme by more than the invalidation buffer before a flip.

- Grid: acceleration step `{0.01, 0.02, 0.03}`, maximum `{0.10, 0.20, 0.30}`, arm source `{E05, E06, MTF}`.
- Expected TIM: `55–86%`.
- Vectorization: arm arrays vectorized; PSAR requires an O(N) recurrence and is Numba-friendly.
- Source basis: [StockCharts Parabolic SAR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/parabolic-sar).

### E08 — MFE-activated volatility profit lock

**Purpose:** Avoid near-entry ATR exits while still reducing large giveback after a substantial run.

Long:

```text
MFE_ATR[t] = (highest_since_entry[t] - entry_price) / ATR_entry_or_slow[t]
if MFE_ATR < q:
    use wide stop k_wide
else:
    use monotonic Chandelier with k_tight
```

Short is mirrored.

- Grid: activation `q ∈ {2, 3, 4, 6, 8} ATR`; `k_wide ∈ {4, 5, 6}`, `k_tight ∈ {1.5, 2, 2.5, 3, 3.5}`.
- Optional graduated schedule: tighten one step at `q1`, another at `q2`.
- Expected TIM: `60–88%`.
- Vectorization: entry-segment cumulative extrema plus a state scan.
- Evidence rationale: stops can add value in momentum regimes but hurt when applied indiscriminately; see [Kaminski and Lo](https://ideas.repec.org/p/hhs/sifrwp/0063.html). Chandelier supplies the mechanical trail.

### E09 — Multi-timeframe exhaustion arm, lower-timeframe structural trigger

**Purpose:** Use daily/4-hour context correctly while preserving fast execution.

Create completed-bar votes:

```text
D votes: regression z extreme, WT/RSI divergence, slope deceleration
4h votes: regression z extreme, failed HH/LL, oscillator rollover
1h trigger: break of confirmed swing, failed retest, or channel re-entry
```

For a long, arm when at least `K` of the daily/4-hour votes indicate upside exhaustion. Exit only on a 1-hour bearish structural trigger. Mirror for shorts.

- Grid: `K ∈ {1, 2, 3}`, equal weights versus `{D:2, 4h:1}`, trigger family `{E03, E05-confirm, E06-reentry}`.
- Expected TIM: `55–84%`.
- Vectorization: all votes vectorized; one state scan.
- Anti-leak requirement: completed HTF bars only. This candidate should have a unit test with deliberately changing unfinished 4-hour data that proves no earlier lower-timeframe row changes.
- Evidence rationale: trend persistence favors slow context, while divergence/reversal evidence is more credible when confirmed structurally.

### E10 — Stateful exit-level invalidation and mandatory reclaim re-entry

**Purpose:** Ensure a good-but-imperfect exit never leaves the system flat while the original trend runs away.

On any long exit store:

```text
exit_fill
exit_bar_high
armed_structural_top
reclaim_level = max(exit_fill, exit_bar_high, armed_structural_top if present)
```

While flat:

1. If close exceeds `reclaim_level + reclaim_buffer`, force re-entry next bar.
2. This path bypasses optional entry filters and cooldowns, but not hard broker/risk validity checks.
3. Use a size schedule, not a veto: `50–100%` of the pre-exit quantity immediately, with the remainder added on the next trend-confirming break.
4. Clear the state only after confirmed fill, direction change, delisting, or explicit expiry.

Short is mirrored with `min(...)` and a downward reclaim.

- Grid: reclaim buffer `{0, 0.1, 0.25, 0.5} ATR` or `{0, 0.1%, 0.2%, 0.3%}`; immediate fraction `{0.5, 0.75, 1.0}`; expiry `{none, 20, 60, 120}` source bars.
- Expected TIM: `68–95%`, depending on exit frequency.
- Vectorization: sequential state machine; O(N), Numba-friendly.
- Diagnostic: every exit must end in exactly one of `lower_reentry`, `reclaim_reentry`, `still_flat_at_end`, or `expired_with_reason`.
- Internal basis: this formalizes the mandatory price-cross behavior already documented in `REENTRY_FIX_PLAN.md`, `BACKTEST_BIBLE.md`, and `LOCKED_FILES.md`.

### E11 — Lower-price pullback/failure-test re-entry

**Purpose:** Re-enter below the exit after the top exit has actually saved price, instead of waiting only for the reclaim.

For a long after exit:

1. Require price to reach `exit_fill - gap`, where `gap` is ATR- or percentage-scaled.
2. Require one causal bottom pattern:
   - confirmed higher low then break above the intervening swing high;
   - downside failure test: low pierces a prior confirmed low but close returns above it, then next close breaks the failure-test high;
   - completed WT/Stoch low plus higher high/higher low structure;
   - lower regression-channel excursion followed by re-entry inside the channel.
3. Fill next bar.
4. If E10 reclaim fires first, use E10 instead; do not wait for a cheaper price forever.

Short is mirrored.

- Grid: `gap ∈ {0.5, 1, 1.5, 2, 3} ATR`; pivot `(2,2),(3,3),(5,3)`; oscillator threshold quantiles `{5%, 10%, 20%}`; maximum wait `{8, 20, 40, 80}` bars.
- Expected TIM: `58–88%`.
- Vectorization: pattern masks plus exit-state scan.
- Source basis: [Adam Grimes failure tests and pullbacks](https://www.adamhgrimes.com/fundamental-trading-patterns/) and the explicit [break/retest sequence](https://www.tradezella.com/strategies/break-retest).

### E12 — Two-stage top reduction plus slow runner

**Purpose:** Reduce dependence on predicting one exact top while retaining exposure to the trend.

For a long:

1. On an E05/E06 exhaustion event, reduce `f1`.
2. On a confirmed E03/E04 structural trigger, reduce another `f2`.
3. Trail the remainder with E01 or E02.
4. Re-add the first and second clips independently using E11, with E10 reclaim as the mandatory fallback.

Short is mirrored.

- Grid: `(f1,f2) ∈ {(0.25,0.25),(0.25,0.50),(0.50,0.25),(0.50,0.50)}`; runner exit `{E01,E02}`.
- Expected weighted TIM/exposure: `50–85%`.
- Vectorization: exposure is a small finite-state machine with fractions `{0, 0.25, 0.5, 0.75, 1}`.
- Warning: report gross exposure-time as well as binary TIM; otherwise a 25% runner misleadingly appears “100% in market.”

### E13 — Regime-conditioned fast versus slow exit

**Purpose:** Avoid applying mean-reversion exits to persistent trends.

Define a higher-timeframe trend regime using only completed bars:

```text
trend_up = return_L > 0
           and EMA_fast > EMA_slow
           and slope(EMA_slow) > 0
trend_down = mirrored
```

For a long:

- In `trend_up`, only E01/E02 can close the runner; E05/E06 may reduce but not fully close.
- In `neutral/exhausted`, E03–E07 may close.
- In `trend_down`, structural exits may close immediately.

Short is mirrored.

- Grid: return lookback `{20, 60, 120, 250}` daily bars; EMA pairs `{20/50, 50/100, 50/200}`; action policy `{slow-only, partial-fast, full-fast}`.
- Expected TIM: `70–96%` in aligned trends; lower across the complete sample.
- Vectorization: fully vectorized regime arrays plus finite-state action rules.
- Source basis: trend persistence in [Moskowitz, Ooi, and Pedersen](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf), with the caveat that their horizons and instruments are not identical to this system.

## Combinations to test without a combinatorial explosion

Do not sweep the Cartesian product of every exit. Each extra exit in an `OR` array can only make the earliest exit earlier, so blindly adding paths tends to reduce TIM and can hide which path caused damage.

Use a staged, residual-driven campaign:

### Stage 0 — Wiring controls

Run on `MU_LONG`, `VT_LONG`, and `HAO_SHORT`:

- ladder entry with **no technical exit**, end-of-window liquidation only;
- E01 alone;
- E02 alone;
- current accepted `per_sym` configuration;
- B&H/short-and-hold.

Required invariants:

- exposure with no exit should be near the intended ladder baseline, not 1–5%;
- every exit has a reason and source timeframe;
- every full exit creates re-entry state;
- E10 fires whenever the exit level is causally reclaimed;
- disabling a switch changes the event count when its trigger exists.

### Stage 1 — Slow exit family

Screen E01 and E02 grids with all fast exits disabled. Keep a Pareto frontier on:

- net return versus B&H;
- weighted TIM;
- maximum drawdown;
- trend capture ratio;
- exit giveback;
- trades and turnover.

Do not choose one winner by return alone.

### Stage 2 — Top arming families

Using the top 3–5 slow baselines, add each arm/trigger family separately:

- E03;
- E04;
- E05;
- E06;
- E07 only after E05/E06;
- E08.

Measure **incremental attribution** relative to the exact parent configuration:

```text
delta_return
delta_drawdown
delta_TIM
avoided_drawdown_after_exit
missed_upside_after_exit
reentry_price_improvement
bars_flat_above_exit_level
```

### Stage 3 — Logical combinations, not all combinations

Test only these interpretable policies:

1. `SLOW_OR_STRUCT`: E01 OR E03.
2. `SLOW_OR_DIVERGENCE_CONFIRMED`: E01 OR E05-confirmed.
3. `SLOW_OR_CHANNEL_REENTRY`: E01 OR E06.
4. `SLOW_WITH_MFE_LOCK`: E08.
5. `EXHAUSTION_2_OF_3`: at least two of divergence, regression excursion, HTF rollover arm; then one structural trigger.
6. `PARTIAL_THEN_RUNNER`: E12.
7. `REGIME_SWITCHED`: E13.

An `OR` combination should be rejected if its gain is merely more favorable exposure on the discovery window and it degrades the next walk-forward window.

### Stage 4 — Re-entry policy comparison

For each retained exit:

- exit + E10 only;
- exit + E11 only;
- exit + E11, with E10 mandatory fallback;
- partial exit/re-add via E12.

This isolates whether the edge came from exit timing or from accidentally staying flat.

## Matrix fields required for every result

The switch matrix should not store only return. Each tested cell needs enough information to avoid repeatedly testing harmful variants.

```text
run_id
parent_run_id
symbol_key
direction
discovery_or_validation
date_start
date_end
data_hash
code_commit
entry_baseline
exit_policy
reentry_policy
parameter_json
net_return_pct
benchmark_return_pct
gain_vs_bh_ratio
alpha_vs_bh_pct_points
binary_time_in_market_pct
weighted_exposure_time_pct
trade_count
full_exit_count
partial_exit_count
lower_price_reentry_count
reclaim_reentry_count
still_flat_at_end_count
median_bars_to_reentry
median_reentry_improvement_pct
bars_flat_beyond_exit_level
trend_capture_ratio
median_exit_giveback_pct
max_drawdown_pct
sharpe_or_daily_return_ir
turnover
estimated_cost
exit_reason_counts_json
red_invariant_count
status
notes
```

Suggested definitions:

- `trend_capture_ratio`: realized directional return during a ground-truth-free trade episode divided by the corresponding favorable close-to-close move while the trend regime remained aligned. This is descriptive, never used as a future label.
- `exit_giveback`: for a long, `(highest executable close since entry - exit fill) / highest executable close since entry`; mirrored for shorts.
- `reentry_improvement`: for a long, `(exit fill - reentry fill) / exit fill`; mirrored for shorts.
- `bars_flat_beyond_exit_level`: flat long bars whose close is above the stored reclaim level, mirrored for shorts. Any nonzero value after the allowed next-bar fill latency is a red wiring failure.

Store losing and under-B&H configurations as gray/discarded rows, never delete them. Record their code/data hashes so the campaign does not retest them accidentally.

## Fast implementation pattern

The candidates are “vectorizable” in the practical sense: expensive indicators and event masks are calculated in arrays; only path-dependent position state uses a tight compiled scan.

### Layer A — feature cache

Build once per symbol/timeframe/data hash:

```text
ATR arrays
Donchian highs/lows shifted one bar
EMA and slopes
rolling OLS center/sigma/z/correlation
RSI/WT/MACD
confirmed pivot event arrays with availability timestamps
completed HTF features joined causally to execution bars
```

Use memory-mapped NumPy/NPZ or Parquet arrays. Parameter-independent features must never be recomputed in a sweep worker.

### Layer B — candidate event arrays

For a batch of parameters, broadcast thresholds over cached features:

```text
arm[param, bar]
trigger[param, bar]
slow_stop[param, bar]
lower_reentry_pattern[param, bar]
```

Chunk parameter batches to control memory.

### Layer C — compiled finite-state simulation

Run one Numba/Cython/Rust-style O(P×N) loop over small integer states:

```text
FLAT
LONG_FULL / SHORT_FULL
PARTIAL_1 / PARTIAL_2
EXIT_ARMED
WAIT_LOWER_REENTRY
WAIT_RECLAIM
```

The loop should only read arrays and write aggregate metrics plus compact event logs for finalists. Do not call the full production indicator functions on every parameter/bar.

### Layer D — exact-engine replay

Replay only:

- Pareto-front candidates;
- random samples of losing candidates;
- every candidate with an invariant discrepancy.

Use `backtest_v8_engine` or the approved exact path to compare event timestamps. A fast candidate is eligible only when its signals match the exact implementation within declared fill latency.

## Acceptance and falsification

The requested `2–10× B&H` can be a search objective, but it is not a credible acceptance criterion by itself over a two-month discovery window. It strongly rewards leverage, a lucky regime, and per-symbol overfit. Require all of:

- positive net return after realistic costs;
- improvement over the correct benchmark on validation data;
- no lookahead/invariant failures;
- intended TIM band;
- reasonable turnover and drawdown;
- no single trade or single day accounting for most of the edge;
- similar direction of improvement on adjacent parameter values;
- success on at least one frozen later period before considering live deployment.

Also report unlevered return and gross/average exposure. A `10× B&H` result obtained by `10×` average exposure is not exit alpha.

## Recommended first queue

For each of `MU_LONG`, `VT_LONG`, and `HAO_SHORT`:

1. Stage 0 wiring controls.
2. E01 on 4-hour and daily data.
3. E02 on 4-hour and daily data.
4. E10 mandatory reclaim attached to the best E01/E02 candidates.
5. E03 failed-retest exit.
6. E06 regression excursion/re-entry.
7. E05 divergence with structural confirmation.
8. E11 lower-price re-entry plus E10 fallback.
9. E12 partial/runner.
10. E13 regime switch.

Start with a short discovery window for speed, but immediately freeze the top variants and replay them over longer and later windows. The first output should be diagnostic, not a declaration of a winning algorithm.

## Key hypotheses to test

1. Current underperformance is primarily caused by fast exits and re-entry starvation, not lack of entry signals.
2. Slow 4-hour/daily Chandelier or Donchian exits will restore TIM and trend capture.
3. Structural exits add value only after an exhaustion arm; always-on structural/oscillator exits will recreate churn.
4. Mandatory reclaim re-entry removes most catastrophic missed-upside episodes.
5. Lower-price re-entry improves exit economics only when paired with reclaim fallback.
6. Partial exits will be more robust than full top calls.
7. Multi-timeframe logic will help only after unfinished higher-timeframe leakage is explicitly ruled out.

These are falsifiable. If Stage 0 fails, stop parameter optimization and repair the engine path first.

## Additional research cautions

- [Optimal Trading with a Trailing Stop](https://arxiv.org/abs/1701.03960) and [Bayesian drawdown stop thresholds](https://arxiv.org/abs/1609.00869) show that trailing-stop design can be framed mathematically, but their model assumptions do not identify a universal real-market parameter.
- Later work has challenged how much apparent time-series momentum exceeds simple estimators, so the 2012 evidence is not a license to assume trend rules work in every sample. [Time series momentum: Is it there?, *Journal of Financial Economics* (2020)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X19301953)
- Every extra exit creates another opportunity to leave a winning trend. An exit path should be retained only when its incremental, walk-forward attribution is positive.
- “Near the top” should be evaluated by giveback and missed-upside distributions, never by a hindsight label that awards the exact future maximum.

