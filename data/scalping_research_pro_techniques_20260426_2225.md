# Pro Crypto Scalper Techniques — Distillation for V3
**2026-04-26** | 3m–5m perp scalping | Sources: educational sites + practitioner essays + 1 quant blog. No paywalled / shill sources.

## Top 10 techniques

### 1. CVD divergence as PRIMARY exit, not WT cross
Require CVD-vs-price divergence (price NH + CVD LH = close LONG; mirror SHORT) before honoring WT-cross exit. WT cross alone fires constantly in trending continuations.
**Source:** [Phemex — CVD Guide](https://phemex.com/academy/what-is-cumulative-delta-cvd-indicator) — exchange research desk.
**V3 conflict:** V3 exits on WT cross alone.

### 2. VWAP entry filter / mean-reversion anchor
Require LONG entries on pullback to session-VWAP (or first reclaim from below) instead of just HH+HL. Most-cited 5m setup; institutional algos defend VWAP intraday.
**Source:** [Tradewink — VWAP Bounce](https://www.tradewink.com/learn/vwap-bounce-trading-strategy).
**V3 conflict:** no VWAP gate.

### 3. Order-book imbalance gate ≥2:1 across top-5 DOM
Require bid/ask imbalance ≥2:1 in top-5 levels in trade direction at entry tick (Binance L2 free). OBI has near-linear relationship with returns over 5–60s — exactly V3's window.
**Sources:** [QuantStrategy.io — OBI Scalping](https://quantstrategy.io/blog/how-to-spot-and-trade-order-book-imbalances-for-high/) (specifies 2:1/3:1 top-5); [Dean Markwick — OFI HFT](https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html).
**V3 conflict:** zero microstructure filter.

### 4. Funding-rate context — avoid the crowded side
Suppress LONG when funding >+0.05%/8h; suppress SHORT when funding <-0.03%/8h. 2025's $154B liquidation wave was crowded-leverage cascades; funding extremes precede them.
**Sources:** [BingX — Funding Rate](https://bingx.com/en/learn/article/what-is-funding-rate-and-how-use-it-in-crypto-trading); [MEXC — 2025 Mistakes](https://www.mexc.com/news/388471).
**V3 conflict:** ignores funding entirely.

### 5. Replace stoch-sum HTF gate with simple EMA trend filter
Require 1h close above 20-EMA for LONG (below for SHORT); drop `k_15m+k_1h≥50`. Stoch-sum on two TFs is statistically degenerate; pro consensus = HTF EMA trend.
**Sources:** [FXOpen — Scalping](https://fxopen.com/blog/en/1-minute-scalping-trading-strategies-with-examples/); [Exness — 5m Scalping](https://insights.exness.com/trading-strategy/5-minute-scalping-strategy/).
**V3 conflict:** stoch-sum-≥50 unique to V3; no pro source recommends it.

### 6. Time-stop the trade (max 5–7 bars on 3m)
If no progress > X·ATR within 5 bars → exit at market. Pros cite time-stop > tight price-stop on micro-TFs; trades that don't work in their window have lost edge.
**Source:** [Highstrike — Scalping Futures](https://highstrike.com/scalping-futures/).
**V3 conflict:** no time-stop.

### 7. Reentry on RETEST, not next-bar HH
After stop-out, require price revisit prior breakout level and close back above before re-firing same direction. "False breakout → retest → real" is the most-cited continuation re-entry.
**Source:** [Gate.com — Continuation Patterns](https://www.gate.com/crypto-wiki/article/continuation-patterns-the-ultimate-guide-to-trading-crypto-trends-20260115).
**V3 conflict:** allows next-bar reentry the moment HH+HL prints again — churn pattern.

### 8. Scale out 50% + trail the runner
Close 50% on first momentum-slowdown signal (CVD slope flip / contrary 1m bar with vol); trail rest behind 3m structure. Captures high-WR scalp edge AND occasional 5R extension. Aligns with V3's existing PARTIAL_PROFIT_LOCK v2.
**Source:** [Hyperdash — SL/TP/Trailing](https://hyperdash.com/learn/stop-losses-take-profits-trailing-stops-order-types-every-trader-should-know).
**V3 conflict:** scalp_v3 path closes 100% on exit.

### 9. Skip 03:00–06:00 UTC liquidity gap
Restrict V3 to high-volume windows (US open, London close overlap). 2025's liquidation crisis concentrated in overleveraged scalpers during thin liquidity.
**Source:** [BitcoinEthereumNews — 2025 Mistakes](https://bitcoinethereumnews.com/crypto/3-deadly-mistakes-that-cost-crypto-traders-155-billion-in-2025/).
**V3 conflict:** 24/7, no session filter [inferred].

### 10. Drop stoch-K-cross-50 as exit
Stoch is bounded; in trends K oscillates 50–80 forever, firing false exits. Replace with structure exit (close below prior 3m swing low for LONG).
**Sources:** [LuxAlgo — Scalping 101](https://www.luxalgo.com/blog/scalping-101-high-frequency-trading-tips/); [StockGro — 5m Scalping](https://www.stockgro.club/blogs/trading/5-minute-scalping-strategy/).
**V3 conflict:** k-cross-50 exit directly contradicts pro consensus.

---

## What I would change FIRST
**Add a CVD-divergence gate (technique #1) on top of the current WT-cross exit.** Biggest expected lift, smallest code surface, directly answers "exit at top not fixed %". Validate on the existing 12-sym×6mo backtest before anything else.

CryptoCred's essay confirms the philosophy (divergence between price/CVD/OI/funding) but provides no concrete thresholds — direction, not recipe.
