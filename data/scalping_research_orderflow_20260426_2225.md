# Order-Flow & Microstructure Techniques for SCALP_V3

Date: 2026-04-26 | Scope: techniques V3 (3m crypto scalp) can adopt with `ez_orderbook.py` (currently OFF) + existing OHLCV/Stoch/WT/DC stack.

---

## 1. Microprice (Stoikov) as fair-value reference
- **Formula**: `microprice = mid + (spread/2) * tanh( (Qb - Qa) / (Qb + Qa) )` where Q = top-of-book size. More robust than weighted-mid; Stoikov proves it's a martingale and a strict short-horizon predictor of mid-price moves.
- **V3 use**: gate entries so `microprice` agrees with intended side (LONG: microprice > mid; SHORT: microprice < mid). Reject signals where microprice contradicts the WT/Stoch trigger — that's the book leaning the other way.
- **Threshold**: require `(microprice - mid) / mid` ≥ +0.5 bps (LONG) or ≤ -0.5 bps (SHORT).
- Source: Stoikov 2018, SSRN.

## 2. Order Book Imbalance (OBI) at top-N levels
- **Formula**: `OBI = (Σ bid_qty[1..N] − Σ ask_qty[1..N]) / (Σ bid_qty + Σ ask_qty)`. Pair the ratio with a **minimum absolute size** (e.g. ≥ $50k notional in the top 5) so a 70 % skew on dust doesn't fire.
- **Threshold**: |OBI| ≥ 0.6 with N=5 levels = strong directional pressure (Towards Data Science / hftbacktest tutorial). Use N=10 for noisier alts.
- **V3 use**: confirmation gate alongside Stoch_K cross. Skip signal if OBI is opposite sign.

## 3. Order Flow Imbalance (OFI, Cont/Kukanov/Stoikov 2010)
- Distinct from OBI: OFI tracks **changes** in best-bid/ask (additions − cancellations − executions), not snapshot levels.
- **Predicts**: 1-second to ~1-minute returns; 65–87 % R² of contemporaneous returns when stacked across levels (multi-level OFI).
- **Practical recipe** (Markwick 2022): aggregate OFI in 1-sec buckets, z-score over a 5-min rolling window, fire when |z| ≥ 2.
- **V3 use**: derive a 3-min OFI z-score; only allow entries when z agrees with side. Acts as a momentum-of-order-flow filter complementing WT velocity.

## 4. Cumulative Volume Delta (CVD) divergence
- **Definition**: cumulative (taker_buy_vol − taker_sell_vol). Already half-available via Binance aggTrades; zero new infra cost.
- **Trigger**: divergence at a known reference level (prior swing, VWAP, DC band). Bullish: price LL, CVD HL → fade short, scalp long.
- **V3 use**: secondary entry trigger when price tags `dc_low_15m` and CVD prints higher-low over the last 5 bars. Pairs naturally with the existing S/R guard.
- Source: Bookmap, Phemex Academy, CoinGlass docs.

## 5. Liquidation heatmap "magnet" levels (Coinglass)
- High-leverage liquidation clusters act as price magnets within ~24h horizon. Coinglass exposes this via API (free tier limited).
- **Threshold**: only treat clusters with intensity ≥ 0.85 (Coinglass default filter) and within ±2 % of spot as actionable magnets.
- **V3 use**: bias scalp targets toward the nearest magnet within 0.3–1.5 % of entry, not blind +0.5 % TP. Avoid opening *into* a magnet on the wrong side (likely sweep then reverse).
- Source: Coinglass Liquidation Heatmap docs.

## 6. Funding-rate spike as fade filter
- Funding > +0.05 % (per 8h) on perp = crowded longs; mean-reverts within hours. Cluster ≥ ±2σ from rolling mean = scalp-fade signal (Quant Journey / BitMEX 2025Q3 report).
- **V3 use**: when funding z-score > +2 (BTC perp) → disable LONG_ONLY entries on alts for ~30 min; allow opportunistic SHORT scalps with tight TP. Mirror for negative.

## 7. Spoof / bait-wall absorption detection
- **Real wall**: stays firm, gets filled (volume prints at the level); often an iceberg refilling.
- **Spoof**: cancels within ~100 ms of price approach; no volume prints.
- **Trigger** (Bookmap): wall absorbs aggressive flow ≥ 30 s without cracking → fade entry in opposite direction; wall pulls without prints → fade the breakout (it's hollow).
- **V3 use**: with `ez_orderbook.py` snapshots @ 100 ms, flag levels where size > 5× median for ≥ 30 s, then watch for absorption vs. cancel. High-confidence reversal scalp.

## 8. Volume Profile HVN/LVN for scalp targets
- HVN = consolidation/support-resistance; LVN = price-vacuum, fast travel. Build VPVR over rolling 24h.
- **V3 use**: take-profit at next HVN within 0.3–1.5 % of entry; expect rejection. If entry is in an LVN, expand target to the far edge of the vacuum.

---

## Top recommendation — wire `ez_orderbook.py` for **microprice + OBI + OFI z-score**
These three derive from the same L2 stream you already have plumbing for, are cheap (top-5 levels), and stack: microprice answers "where is fair value drifting NOW", OBI answers "is the book leaning my way", OFI z-score answers "is flow accelerating my way". Add as a **single confirmation gate** at SCALP_V3 entry:

```
allow_entry = signal_pass AND
              microprice_side == intended_side AND
              |OBI_top5| >= 0.6 AND OBI_sign == intended_side AND
              |OFI_zscore_3m| >= 1.5 AND OFI_sign == intended_side
```

Backtest impact estimate: papers report 0.12–0.5 Sharpe lift on top of a baseline trigger when OFI is used as overlay (Markwick, Cont). On V3's current 0.13–0.26 Sharpe, this is the single biggest leverage point before adding heavier infra (Coinglass liq feed, CVD service).

CVD divergence (#4) is the cheapest second add — derives from aggTrades you already pull.

---

## Sources

- Stoikov, *The Micro-Price* (SSRN): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694
- Markwick, *Order Flow Imbalance — A High Frequency Trading Signal*: https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html
- *Price Impact of Order Book Imbalance in Cryptocurrency Markets* (Towards Data Science): https://towardsdatascience.com/price-impact-of-order-book-imbalance-in-cryptocurrency-markets-bf39695246f6/
- hftbacktest, *Market Making with Alpha — Order Book Imbalance*: https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html
- Bookmap, *Cumulative Volume Delta Trading Strategy*: https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy
- Bookmap, *Bait Walls and Phantom Size*: https://bookmap.com/blog/how-price-reacts-around-fake-liquidity-bait-walls-and-phantom-size
- Coinglass, *How to use Liquidation Heatmaps*: https://www.coinglass.com/learn/how-to-use-liqmap-to-assist-trading-en
- Coinglass, *What is CVD*: https://www.coinglass.com/learn/what-is-cumulative-volume-delta-cvd
- Phemex Academy, *Cumulative Delta Indicator*: https://phemex.com/academy/what-is-cumulative-delta-cvd-indicator
- Quant Journey, *Funding Rates in Crypto*: https://quantjourney.substack.com/p/funding-rates-in-crypto-the-hidden
- BitMEX, *2025 Q3 Derivatives Report*: https://www.bitmex.com/blog/2025q3-derivatives-report
- QuantVPS, *Mastering Volume Profile*: https://www.quantvps.com/blog/mastering-volume-profile
- Cont, Kukanov, Stoikov 2010 (referenced via Markwick + Towards Data Science)

Credibility: Stoikov & Cont = peer-reviewed academia (Cornell / Imperial). Markwick = working quant blog. Bookmap / Coinglass = vendor docs (skew toward their product but technique-accurate). Phemex / QuantVPS = exchange/educator content (verified against academic claims). arxiv links available in raw search but not load-bearing for the recommendations above.
