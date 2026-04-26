# Research Scan — 2026-04-26

**Purpose:** Extensive internet-wide scan for tradeable systems and verified-track-record traders, complementing `IMPROVEMENT_FRAMEWORK_20260425.md` and `OPTIONS_OVERHAUL_FRAMEWORK_20260425.md`. Five non-overlapping research lanes (stocks, on-chain crypto, drawdown overlays, GitHub quant code, verified leaderboards). All lanes were briefed on what the prior frameworks already covered so they pushed into NEW ground.

**Why this exists:** Despite a backtested 2.64 pool_sharpe baseline (50-sym crypto / 114-sym tradier), live performance is bleeding (-20% inf in 4 days, -$14k options in a week). The user said "we are a long way from being successful." This scan is the answer to "what's missing."

---

## 0. The headline finding (do not skip this)

**The 2.64 backtest baseline is likely a statistical artifact, not real edge.**

López de Prado's deflated Sharpe formula: under the null hypothesis of zero true edge, the *expected maximum* Sharpe across N independently-tested configurations is approximately

```
E[max SR | null] ≈ (1−γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N·e))     (γ ≈ 0.5772)
```

For the search size `autonomous_search.py` produces (N ≈ 10,000+ configs over months), the random-noise ceiling is **~3.85**. A pool_sharpe of 2.64 is **below** that ceiling — meaning a result of that magnitude is *expected* even if the true underlying edge is zero. The deflated Sharpe `DSR = (SR_obs − E[max SR_null]) / σ_SR` is likely **negative** for the c08 baseline.

**This reframes everything.** The system isn't broken in live; the backtest was overfit to historical regime by virtue of search size. -20% in 4 days is one realization of the true (much lower, possibly ~0) Sharpe distribution.

**Action:** Before adding any new feature, rerun `autonomous_search.py` with **deflated Sharpe** as the objective (penalize by config-count). Treat any reported `pool_sharpe < ~2.5×log(N_configs)/√T` as noise.

This finding alone justifies pausing all C1–C5 sweeps in the prior framework until the search machinery is fixed.

---

## 1. Convergent priority order across all 5 lanes

The 5 research agents independently produced a converging recommendation. Do these in this order — earlier items make later items more honest:

| # | Item | Lane(s) | Cost | Why first |
|---|------|---------|------|-----------|
| 1 | **mlfinlab Purged K-Fold + Combinatorial Purged CV** in `autonomous_search.py` | 3+4 | ~200 LOC | Selection bias is the #1 explanation for the 2.64-vs-live gap. CPCV is the only honest CV for time-series with overlapping labels. |
| 2 | **Yang-Zhang volatility estimator** added to NPZ precompute | 3+4 | ~30 LOC | You have OHLC, you're throwing away 60-70% of vol information by using close-to-close stdev. Drop-in. |
| 3 | **Volatility-targeting global size scalar** in front of all sizing | 3 | ~2 days | Inverse-vol exposure scaling, capped [0.25, 2.0]. Halves max DD without removing return. AQR/Harvey 2018 — most-replicated overlay in quant finance. **This single overlay is the highest-EV defense against the case where true Sharpe is 0.5 instead of 2.64.** |
| 4 | **TSMOM crisis-alpha book-level scalar** | 1+3 | trivial | `book_scalar = clip(0.5 + 0.5×sign_agreement_pct, 0.25, 1.5)` where agreement = % of positions whose 12-1m TSMOM matches their direction. Convergent with Clenow's slope×R² overlay. AQR 130yr / 67-market evidence. |
| 5 | **Drawdown-aware fractional Kelly** | 3 | ~4 hours | Half-Kelly halved again at -10% account DD, halved again at -15%. Ulcer-index targeting reduces max DD ~40% without Sharpe loss. |
| 6 | **mlfinlab triple-barrier + meta-labeling** wrap of existing entries | 4 | ~1 week | Don't replace WT/Stoch/MFI signals. Train a binary "act/skip" classifier on top of them. Published replications report 15-30% precision lift. |
| 7 | **Stablecoin flow + Exchange Netflow** as portfolio leverage scaler | 2 | ~6 hours, free | Whale Alert API + Etherscan stablecoin contract events. New mint → bullish regime, redemption → risk-off. Already converges with `0market_sentiment_score`. |
| 8 | **AQR QMJ symbol-allowlist filter** for tradier | 1 | ~5 LOC | Restrict the 114 tradable symbols to QMJ-positive names. Eliminates the junk-tail that contributes most to -15% weeks. |
| 9 | **Sector relative-strength gate** for tradier (vs SPY 63-day) | 1 | trivial | Only fire long entries on names whose sector ETF has positive 63d RS vs SPY. AI/semi/uranium thematic 2024-2025 alpha was *all* in this regime. Auto-shuts off when regime breaks. |

Items 1-5 are diagnostic + risk-control. Items 6-9 are accuracy improvements. **Do not skip to 6+ until 1-5 are in place** — otherwise you're just adding more noise to an already overfit system.

---

## 2. New strategies worth adding (after items 1-5 above)

Ranked by reconstruction-quality × fit-with-existing-infra. All need Tier 2 sweep before live per CLAUDE.md NEW STRATEGY PROHIBITION.

### 2.1 Zarattini–Barbon–Aziz 5-min ORB on stocks-in-play  ⭐ TOP PICK

- **Source:** Swiss Finance Institute Research Paper No. 24-98 (peer-reviewed, SSRN).
- **Documented:** Sharpe 2.81, 41.6% IRR, 1,637% cumulative gain Jan-2016–Dec-2023 across 7,000+ US stocks. Multiple independent QuantConnect replications confirm the edge survives.
- **Universe:** Top-20 US stocks by **opening relative volume** that day, recomputed daily 09:25–09:30 ET.
- **Entry:** After first 5-min RTH bar, buy break of bar high (long) / sell break of bar low (short). Stop = opposite end of OR.
- **Exit:** Trailing stop at 10-EMA, OR end-of-day flat (no overnight).
- **Sizing:** Risk-fixed % of equity, leverage scaled by opening-range size.
- **What we'd need:** pre-market cumulative volume per symbol (vs 14d avg same-time-of-day), top-20 RVOL ranker, intraday bar capture at 5m (you have this).
- **Why it's the top pick:** *The entire edge is in the universe filter, not the breakout.* ORB on SPX-100 dies. ORB on RVOL>5x lives. Your existing tradier 114-symbol list is the wrong universe — needs daily RVOL recomputation.
- **Honest take:** Highest-evidence stock day-trading paper in existence. Should be ported as a standalone tradier strategy, not a sweep variant.

### 2.2 Qullamaggie episodic pivots (Kullamägi)

- **Source:** Public Twitter trades 2018–present, Chat With Traders / Stansberry / Real Vision interviews. $5k → $100M+ documented.
- **Universe:** US small/mid caps, IPOs <10y old, price >$10, <$10B mcap.
- **Entry:** Gap up ≥10% on news/earnings + RVOL 5x+ first 30 min. Wait for break of opening 1m or 5m high. Skip if stock fails to take its open in 30 min.
- **Exit:** 10/20/50-day MA trailing, varied by holding TF (3-30 days). Sells half into +20% / 3-day strength.
- **Sizing:** Risk 0.25-1% per trade. Pyramids only on continuation.
- **What we'd need:** earnings/news catalyst tag, IPO age, pre-market gap%, 30-min RVOL.
- **Honest take:** 25-35% WR with 5:1 R:R structural — your engine must tolerate low WR (currently strict). Worked in 2020-2021, suffered 2022, came back 2023-2025 (NVDA, AVGO, SMCI, QBTS, TEM, RKLB).

### 2.3 Minervini SEPA Trend Template gate

- **Source:** US Investing Championship wins 1997 (155%) + 2021 (334.8%, $1M+ division), audited by Norm Zadeh's competition. Schwager *Stock Market Wizards*.
- **8-rule Trend Template** — used as a hard filter on the long-side universe:
  1. Price > 50/150/200 SMA
  2. 50 SMA > 150 SMA > 200 SMA (stacked)
  3. 200 SMA rising at least 1 month
  4. Price ≥ 30% above 52w low
  5. Price ≤ 25% below 52w high
  6. RS rank ≥ 70 (vs S&P 500)
  7. (Tighter add) 50/150 SMA rising
  8. (Tighter add) Volume confirming
- **VCP entry pattern** is harder to mechanize (40-50% of subjective labels disagree across operators). Code Trend Template as hard gate; treat VCP as soft score.
- **Why it ports cleanly:** Drops directly into existing tradier sweep harness. ~30 LOC.

### 2.4 Stockbee Episodic Pivot screens (Bonde)

- **Source:** stockbee.blogspot.com 20+ year track record, TraderLion interviews.
- **Concrete catalyst rule:** earnings/guidance gap with sales growth ≥39% Q-over-Q + EPS growth ≥39% + price $10+ + 100k+ avg vol. Buy day-of or first pullback to declining 8-EMA.
- **9M-share-volume screen** is a clean, codable proxy for institutional accumulation.
- **What we'd need:** quarterly fundamentals (sales growth, EPS growth) joined to bars — significant data lift.
- **Honest take:** Catalyst-based. Less codable than 2.1/2.3 because of fundamentals join. Park until tradier has earnings calendar wired.

### 2.5 Funding-rate mean-reversion (extension of already-wired primitive)

- **Source:** arXiv:2401.07125 (2024), "Funding-Rate as a Predictor of Crypto Perp Returns."
- **Rule:** extreme funding flips → 3-7d mean reversion. Long when 8h funding < −0.05% sustained. Short when > +0.05% sustained.
- **Why now:** Funding rate fetcher is already wired (A1, 2026-04-26). This adds a *second* use of the same data — directional fade on extremes — beyond the directional filter already implemented.

### 2.6 Cointegration pairs trading on BTC-quoted alts

- **Source:** Engle-Granger / Johansen + Kalman-filter dynamic hedge ratios (mlfinlab).
- **Method:** Run Johansen on every BTC-quoted perp pair quarterly, keep top-50 cointegrated, trade the spread Z-score.
- **Honest take:** Crypto cointegration is FRAGILE post-2022 — most pairs decohere in trending regimes. Useful as a *complementary low-correlation strategy*, not standalone. Park until directional system is honest (item 1-3 done).

---

## 3. New data feeds (the framework's "missing primitives" extension)

| Feed | Source | Cost | Update | Use |
|------|--------|------|--------|-----|
| **Stablecoin issuance/redemption** | Whale Alert free API + Etherscan contract events | $0 | Real-time | Portfolio leverage scaler |
| **Exchange netflow per asset** | CryptoQuant Free (BTC/ETH) → $29/mo Advanced (top-30 alts) | $0–$29 | 10min–hourly | 1-3d directional bias on top alts |
| **Santiment social-volume spike** | Santiment GraphQL free tier | $0 | Hourly | 4th breadth feature alongside `0market_sentiment_score` |
| **Pre-market RVOL ranker** | Tradier API + custom 14d-avg-by-time table | $0 | 09:25–09:30 ET daily | Stocks-in-play universe for 2.1 ORB |
| **Earnings calendar + EPS/sales surprise** | Finnhub free / Alpha Vantage / Yahoo scrape | $0 | Daily | Stockbee 2.4 catalyst rule |
| **Sector ETF prices for RS gate** | Tradier (SPY, SMH, IGV, XSD, XBI, XLE, etc.) | $0 | EOD | RS-vs-SPY 63-day gate (item 9) |
| **AQR factor data (QMJ for symbol allowlist)** | aqr.com working paper data downloads | $0 | Monthly | Symbol-quality filter (item 8) |
| **Hyperliquid trade history per wallet** | Hyperliquid public API `/info` | $0 | Real-time | Wallet-level reverse engineering (sec 4) |
| **GMX v2 / Vertex / dYdX v4 fills** | Arbitrum subgraph / Vertex API / dYdX Indexer | $0 | Real-time | Cross-DEX wallet overlap = highest-conviction skill signal |
| **Polymarket / Kalshi resolution outcomes** | Polymarket subgraph / Kalshi API | $0 | Real-time | Bayesian wallet scoring with ground truth |

**Skip / parking lot:** Glassnode (mostly descriptive macro, BTC/ETH-only at trading horizon), Nansen Alpha tier ($1800/mo, signals stale by the time non-Alpha sees them), CVD aggTrades reconstruction (still high-cost, parking lot), full L2 ingestion for VPIN (only worth it after items 1-5 done).

---

## 4. Reverse-engineerable trader pools (replaces mined Binance leaderboard)

Your prior "project_copy_trade_reverse_engineering" mined ~50k trades from 110+ Binance traders. The Binance/Bybit/Bitget leaderboards are now adversarial data — rented-account market is industry-wide and ZachXBT-documented. **The new untapped pools are on-chain perp DEXs and Polymarket, where trade data is trustless.**

| Pool | Why it beats Binance leaderboard |
|------|-----------------------------------|
| **Hyperliquid** (`app.hyperliquid.xyz/leaderboard` + free `/info` API) | Every fill, funding, liquidation, open-position snapshot timestamped to second. Wallet ≠ rented account (signing keys = ownership). 100k+ active addresses, top wallets have 1k-50k fills each. |
| **GMX v2** (Arbitrum subgraph, free) | OI by wallet exposed. ~30k monthly active wallets. v1 had GLP front-running bug (filter 2022 data); v2 clean. |
| **dYdX v4** (Cosmos chain, free Indexer API) | Multi-cycle data 2021-now. Filter trading-rewards farmers (wash) by net-of-rebate PnL. Volume migrated to Hyperliquid in 2024 — sample is thinner now but historical is full. |
| **Polymarket / Kalshi** | Unique: every trade has a *ground-truth* outcome. Compute exact Brier scores per wallet. Filter for ≥50 resolved markets, find wallets that consistently buy <implied prob and sell >implied. Kalshi for Fed/economic events → leading indicator for Tradier. |
| **Collective2** (broker-statement audited, paid to follow but free leaderboard) | Best non-on-chain dataset. Filter ≥3yr track record + ≥500 trades. Mostly futures/stocks/options. Cluster trend cohort and mimic for tradier. |
| **eToro Popular Investors** (free) | Stock/ETF only, broker-verified. Top PIs persisting 3+ years is genuine skill signal. Useful for tradier slow-rotation. |
| **Nansen / Arkham smart-money wallets** | Cross-reference inflow into a token vs price. Wallets that appear top on BOTH GMX AND Hyperliquid = highest-conviction skilled trader. Filter those that overlap with Nansen "smart money" labels for triple-confirmation. |

**Method:** for each platform, build per-wallet feature vectors (entry-WT-velocity, hold time, side bias, asset rotation), Bayesian-rank by out-of-sample Sharpe, then either (a) mimic top decile with N-second lag, or (b) compute crowd-divergence as a contrarian signal when top-ranks net-long while broad-ranks net-short.

**Filter all leaderboards by:**
- ≥30-day account age
- ≥100 distinct symbols (or ≥50 resolved markets for prediction markets)
- Net-of-rebate PnL (excludes maker-rebate gamers)
- ≥1y track record
- For CEX leaderboards specifically: presence of small losing periods (no losses = rented account)

---

## 5. Skip list / honest scams

Confirmed-bad sources from across the 5 lanes:

- **Ross Cameron / Warrior Trading** — FTC settled $3M (2022) for misleading earnings claims. Customer-side P&L net-negative for vast majority. Strategy is watered-down EP.
- **Tim Sykes / Profit.ly** — Self-reports on platform he owns. CXO Advisory found *some* edge in pump-detection but explicitly "not scalable beyond niche."
- **Adam Khoo** — Paid course operator, no broker statements.
- **William O'Neil's IBD CAN SLIM as marketed** — Personal record undisputed (mid-century) but FFTY ETF has *underperformed SPY since inception 2015*. Only Trend Template + RS components survive (already absorbed in Minervini 2.3).
- **Karen Supertrader** — CFTC fraud charges 2016. Permanent skip.
- **CryptoGodJohn** — Already on framework skip list. Paid Discord shill, ZachXBT-documented dump pattern.
- **Sheldon the Sniper free content** — Paywalled mechanics.
- **MMcrypto / Coin Bureau / Pizzino** — Directional commentary, no rule-based system.
- **3Commas / Bitsgap "smart trade" leaders** — DCA-into-loss, look great until they don't.
- **MQL5 / cTrader signals** — FX-skewed, martingale survivorship dominates.
- **TradingView "Top Authors"** — Opinions not fills.
- **Adam-Khoo-style FinTwit verified P&L via eToro/Kinfo** — Most "verified" statements show only %-return without trade-by-trade reconciliation. **No FinTwit account meets a quant operator's evidentiary bar without a FINRA U4-backed statement, which essentially never happens.**

**Crypto on-chain "deceptively useless" — descriptive macro that doesn't trade at 3m:**
- NUPL, Hash Ribbons, MVRV-Z, Puell Multiple, Stock-to-Flow, Realized Cap HODL Waves, Spent Output Age Bands, Coin Days Destroyed, Active Addresses (gamed by airdrop farmers since 2023).

**GitHub graveyard — high-stars, do NOT spend time:**
- QuantConnect/Lean (cloud-locked, C# core), OpenBB (Bloomberg-clone for retail, no strategy code), AI4Finance/FinRL (look-ahead bias in env reward), microsoft/qlib (A-shares-China, daily bars only OSS), mementum/backtrader (effectively abandoned), R-finance/quantstrat (R), jesse-ai/jesse (in-sample fits like freqtrade-strategies), AlpacaHQ/example-scalping (toy code).
- **Heuristic that has never failed:** if README shows equity curve up-and-to-the-right with no DD > 5%, it has look-ahead bias. Move on.

---

## 6. Recent papers worth reading (2024-2026)

- **arXiv:2401.07125** — "Funding-Rate as a Predictor of Crypto Perp Returns." Extreme funding flips → 3-7d mean reversion. Pairs with already-wired A1.
- **arXiv:2403.05593** — "Time-Series Momentum on Crypto Perps Post-2020." TSMOM works on BTC/ETH, *decays sharply on alts*. Important caveat for the trend-follow rewrite.
- **SSRN 4567112 (2024)** — "Factor Decay in Crypto." Almost every published crypto factor's IC halves within 18 months. Sanity check on any 2020-2022 backtest.
- **SSRN 5367656 (Aug 2025)** — "Evaluating a 12-1 Month Momentum Strategy 2005-2024." Standard 12-1 momentum: net annualized −2.79%, Sharpe −0.23, MDD −81%. **Classical momentum is dead post-cost.**
- **Swiss Finance Institute 24-98** — Zarattini-Barbon-Aziz ORB paper. Sharpe 2.81. Item 2.1 above.
- **Bailey & López de Prado (2014)** — "The Deflated Sharpe Ratio." The diagnostic in Section 0.
- **Hurst-Ooi-Pedersen (2017)** — "A Century of Evidence on Trend-Following Investing." TSMOM crisis-alpha. Item 4 in Section 1.
- **Harvey et al. (2018)** — "The Impact of Volatility Targeting" *J. Portfolio Management*. Item 3 in Section 1.

---

## 7. Provenance

- **Date:** 2026-04-26.
- **Method:** 5 parallel research agents on non-overlapping lanes (stocks, on-chain crypto, drawdown overlays, GitHub quant code, verified leaderboards), each briefed on what `IMPROVEMENT_FRAMEWORK_20260425.md` and `OPTIONS_OVERHAUL_FRAMEWORK_20260425.md` already covered.
- **Convergence signal:** lanes 3 + 4 independently arrived at the same fix for autonomous_search.py selection bias (deflated Sharpe + CPCV). Lanes 3 + 1 independently arrived at the same exposure-scaling overlay (vol-targeting + TSMOM crisis alpha + Clenow slope×R²). That convergence is the strongest signal in this scan.
- **Status:** raw research, no code shipped. Items 1-3 in the priority list (Section 1) should be the next session's work, gated on user approval.
