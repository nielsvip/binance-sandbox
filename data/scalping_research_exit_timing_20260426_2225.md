# Scalp Exit Timing Research — 2026-04-26

Research-only, read-only on repo. Sources cited inline. **[INF]** = inference, not source claim.

## TL;DR — Top recommendation

To catch the top better, V3 should **replace its 1-of-3 OR fan with a 2-stage trail**:
(1) **Arming stage** — once gain ≥ N×ATR(3m), arm an aggressive ATR/structure trail; (2) **Confirm-and-go stage** — fire the close only when the trail is hit AND ≥1 microstructure signal (CVD divergence, or volume drop on a reversal candle, or VWAP touch from above). The current OR logic (any one of 3m reversal / WT flip / Stoch K cross) bleeds because each signal alone is noisy on a 3m bar; pros couple a *trail* (which structurally guarantees the top is in) with a *micro-confirmation* (which guarantees it isn't a wick). KJTradingSystems' 567,000-backtest study found Stop-and-Reverse > Dollar Target > Breakeven, with 5-bar quick exits underperforming 45-bar exits — i.e. let trail + structure cut you out, don't time-cap it.

## 7 actionable techniques (each maps to a concrete code change)

### 1. ATR Chandelier trail (arms after first push)
**Rule:** Once `unrealized_gain >= ATR_ARM_K * atr_3m` (try K=1.5), set `trail_stop = max(trail_stop, high_since_entry - ATR_TRAIL_K * atr_3m)` (try K=2.5–3.0; crypto Le Beau preset = 22-bar/3 ATR but pros bump to 4–5 ATR for crypto). Close when 3m close < trail_stop.
**Code change:** add `_v3_trail_stop` to position state; recompute every bar; replace one of the OR exits with `if 3m_close < _v3_trail_stop: close`.
**Source:** [Chandelier Exit – StockCharts](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit) — canonical Le Beau/Elder formula. Credibility: highest, original definition.

### 2. CVD bearish divergence at a reference level (the "real top" tell)
**Rule:** track per-3m-bar `cvd_3m = sum(taker_buy_vol - taker_sell_vol)` over the position. Exit a LONG when **price makes new HH** but **cvd_3m fails to make a new HH** AND price is at/near a reference level (prior session high, VWAP, or DC_high_15m). Wait one bar for "reaction" (next bar prints lower close + CVD turns down) before firing.
**Code change:** new helper `cvd_divergence_at_level(position)` returning bool; add to V3 close fan as a *required confirm* alongside trail-hit, not as standalone OR.
**Source:** [Bookmap – CVD Divergence](https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy) — explicitly recommends "wait for reaction" before exiting on divergence. Credibility: Bookmap is an order-flow vendor; concept is industry-standard.

### 3. VWAP-extension fade exit (price > VWAP + 2σ)
**Rule:** if `price > vwap_session + 2 * vwap_stdev` AND a 3m bar prints with `close < open` (red) AND volume on the red bar < avg(prior 5 bars), exit. Stop trailing further — institutions sell into VWAP+nσ extensions.
**Code change:** ingest session VWAP + rolling stdev (already in `ez_indicators`? **[INF]** check); add gate to V3 close fan.
**Source:** [Mean-Reversion VWAP fading – Tradewink](https://www.tradewink.com/learn/mean-reversion-strategy) and [VWAP CCI Reversal Scalp – TradingView Ohyashima](https://www.tradingview.com/script/OIv5BdZ7/) — both note ±2σ + reversal candle as the canonical fade entry; mirror logic = momentum exit.

### 4. Heikin-Ashi color flip with body+wick filter (NOT raw HA flip)
**Rule:** raw HA flip is too slow for scalp (multiple sources warn). Use HA flip **plus** "small body + upper wick > body" as exhaustion. Exit only when HA prints red AND `upper_wick > body_size * 1.5` on the flip bar.
**Code change:** compute HA from 3m OHLC; gate addition to fan.
**Source:** [Opofinance – 5 Pro Heikin Ashi setups](https://blog.opofinance.com/en/heikin-ashi-strategy/) and [Forex Tester](https://forextester.com/blog/heiken-ashi/) note that "wick on both ends" = the actual reversal tell, not just color change.

### 5. One-bar aggressive trail (after arm)
**Rule:** once gain ≥ M×ATR (try M=2.0), set stop to **prior bar's low − 1 tick** every bar (LONG). Aggressive — gives back ≤1 bar's range. Designed for "exiting at the top, not at fixed %".
**Code change:** state machine: `arm` → `trail_prev_low`; close when 3m_low < prev_3m_low.
**Source:** [TradeThatSwing – Aggressive 1-bar trailing stop](https://tradethatswing.com/the-aggressive-one-bar-trailing-stop-loss-for-quick-trades/) (URL was 403 to fetcher but indexed on multiple aggregators). Credibility: Cory Mitchell, ex-prop trader; concept widely used in tape-reading.

### 6. Funding-rate spike + OI drop = momentum exhaustion (crypto-only)
**Rule:** if `funding_rate_8h > 0.05%` (extreme one-sided) AND `open_interest_15m_change < -1%`, scale 50% out immediately, trail rest tightly. Funding spike + OI bleed = leverage flushing; cascades follow within 12–24h per Gate research.
**Code change:** subscribe to funding + OI streams (Binance has both); add a `crypto_derivatives_exit_signal()` gate.
**Source:** [Gate Wiki – Funding/OI/Liquidation signals](https://web3.gate.com/crypto-wiki/article/how-do-futures-open-interest-and-funding-rates-signal-crypto-derivatives-market-trends-in-2026-20260202) and [Amberdata – $31B Deleveraging](https://blog.amberdata.io/leverage-liquidations-the-31b-deleveraging). Credibility: Amberdata is an institutional data vendor; Gate is the exchange.

### 7. Climax-volume reversal candle (the "blow-off" exit)
**Rule:** exit if a 3m bar prints with `volume > 3 * avg(prior 20 bars)` AND `(high - close) > (close - low) * 2` (large upper wick rejection, LONG). This is the "last-buyers-in" candle.
**Code change:** add `is_climax_reversal_3m()`; required confirm alongside trail.
**Source:** [LuxAlgo – 5 Best Volume Indicators for Scalping](https://www.luxalgo.com/blog/5-best-volume-indicators-for-scalping/) and [Traders.MBA – Volume Spike Scalping](https://traders.mba/support/volume-spike-scalping/). Credibility: LuxAlgo is a TradingView top-3 indicator publisher.

## Cross-cutting findings from the 567k-backtest study

[KJ Trading Systems](https://kjtradingsystems.com/algo-trading-exits.html) tested 14 exit families across 5 entry types:
- **Combination exits underperformed single exits** — adding more OR-conditions to the fan is the wrong direction. **[INF]** Implication for V3: do NOT just add a 4th OR; restructure to AND-of-(trail + 1 micro-signal).
- **Stop-and-Reverse won outright** — symmetric, opinionated exits beat asymmetric stops. **[INF]** This argues for a *trail* (which is a moving stop-and-reverse-equivalent) over fixed % or signal-only exits.
- **Quick (5-bar) time exits >> 45-bar time exits underperformed** — don't add a max-hold time cap to V3.

## Pro reconciliation: "exit early vs give back"
Three converging answers from the sources:
1. **Pros scale, V3 does not** — Mind Math Money's 9-exit playbook and Trader Mayne's Structure+OTE both show partial exits (33%/33%/33% or 50%/50%) at structural levels, then trail the runner. V3 currently full-closes; consider a 50% close on first signal, trail rest.
2. **Tighten the trail as the move ages** — chandelier K shrinks over time (3.0 → 2.0 → 1.5) as probability of reversal compounds. **[INF]** age-based K tightening is a low-risk addition.
3. **Volatility regime modifier** — in low-ATR regimes (compression), give the trade more room (K=4); in high-ATR (post-news), use tighter (K=2). [Volatility Box](https://volatilitybox.com/docs/vwap-mean-reversion-strategies/) confirms regime-adaptive trails.

## Sources (consolidated)
- [KJ Trading – 567k backtests on exits](https://kjtradingsystems.com/algo-trading-exits.html)
- [StockCharts – Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit)
- [Bookmap – CVD Divergence Strategy](https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy)
- [Tradewink – Mean Reversion VWAP](https://www.tradewink.com/learn/mean-reversion-strategy)
- [TradingView – VWAP CCI Reversal Scalp](https://www.tradingview.com/script/OIv5BdZ7/)
- [Opofinance – Heikin Ashi setups](https://blog.opofinance.com/en/heikin-ashi-strategy/)
- [TradeThatSwing – 1-bar trailing](https://tradethatswing.com/the-aggressive-one-bar-trailing-stop-loss-for-quick-trades/)
- [Gate Wiki – Funding/OI/Liquidation](https://web3.gate.com/crypto-wiki/article/how-do-futures-open-interest-and-funding-rates-signal-crypto-derivatives-market-trends-in-2026-20260202)
- [Amberdata – $31B Deleveraging](https://blog.amberdata.io/leverage-liquidations-the-31b-deleveraging)
- [LuxAlgo – Volume Indicators for Scalping](https://www.luxalgo.com/blog/5-best-volume-indicators-for-scalping/)
- [Mind Math Money – 9 Exit Strategies](https://www.mindmathmoney.com/articles/when-to-take-profits-in-crypto-stocks-amp-forex-the-complete-exit-trading-strategy-guide)
- [TradeZella – Trader Mayne Structure+OTE](https://www.tradezella.com/strategies/structure-ote)
- [Volatility Box – Regime-Adaptive VWAP](https://volatilitybox.com/docs/vwap-mean-reversion-strategies/)
- [Traders.MBA – Volume Spike Scalping](https://traders.mba/support/volume-spike-scalping/)
