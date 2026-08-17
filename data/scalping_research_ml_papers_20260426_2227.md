# Recent Algo-Trading Research: Edges Relevant to a 3m Crypto Scalper

Compiled 2026-04-26. Read-only research, internet sources only. All Sharpe / WR figures are as published — only authors' own backtests, not independently verified.

---

## 1. Order-Flow Imbalance → Returns (EFMA 2025, Anastasopoulos & Gradojevic)
- **Edge**: Orthogonalised order-flow wealth strategy on crypto.
- **Sharpe**: **1.34 annualised** (published).
- **Sample**: Multi-asset crypto, EFMA 2025 conference paper.
- **V3-portable?** YES if we can get reliable per-trade signed flow. Binance trade tape (`aggTrades` or `trades` WS) is sufficient — no L2 book depth required.
- URL: <http://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/OrderFlowpaper.pdf> [unverified — PDF cert error, only abstract from search snippet]

## 2. Crypto LOB Microstructure: Inputs > Architecture (arxiv 2506.05764, Jun 2025)
- **Edge**: Cross-asset (BTC/LTC/ETC/ENJ/ROSE) LOB features (OFI, spread, adverse selection) generalise across symbols. SHAP shows the same 3-4 features dominate for every asset.
- **Headline**: Simple gradient-boost models match deep nets when inputs are clean (Kalman / Savitzky-Golay filtering). No Sharpe disclosed — accuracy + "conservative tradability" claim only.
- **V3-portable?** PARTIAL — concept yes (denoise raw 3m features), feature set yes; full implementation needs L2 snapshots we don't currently store.
- URL: <https://arxiv.org/abs/2506.05764>

## 3. DeepScalper (arxiv 2201.09058, cited 200+ as of 2026)
- **Edge**: Risk-aware Dueling-DQN with action-branching + hindsight-bonus reward for intraday RL. Multi-modal embedding (price + macro signal).
- **Sharpe**: Beats 7 baselines on 6 futures contracts × 3yr. Paper claims "significantly outperforms" — exact Sharpe not in abstract.
- **V3-portable?** NO for live (RL training infra heavy). YES as inspiration: hindsight-bonus reward (look-ahead in training only) + dueling architecture is reusable for our future ML exit-timer experiment.
- URL: <https://arxiv.org/abs/2201.09058>

## 4. Intraday Momentum on SPY — First-Half-Hour Predicts Last (Zarattini, Aziz, Barbon 2024 SSRN 4824172)
- **Edge**: Trend-follow as soon as abnormal AM imbalance fires; flat at 16:00 ET.
- **Sharpe**: **1.33 net of costs**, 19.6%/yr 2007–early 2024, 1985% cumulative.
- **V3-portable?** Direct port to Tradier (stocks). For crypto: an hours-of-day analogue exists (US morning open, Asia open, weekend effect — see #10).
- URL: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172>

## 5. Funding-Rate Predictability — Inan SSRN 5576424 (2025)
- **Edge**: Double-Autoregressive (DAR) model predicts next-period BTC perp funding rate out-of-sample, beats no-change baseline.
- **Sharpe**: Funding-rate is itself the signal; the published cross-section paper "Fundamentals of Perpetual Futures" reports a **carry strategy Sharpe 6.45 (2020–2025)** — but degraded to 4.06 in 2024 and **negative in 2025** (decay).
- **V3-portable?** YES, low effort. We already pull funding via Binance API. Bias entries with funding direction; consider as a sizing knob, not entry trigger (decaying edge).
- URLs: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5576424>, <https://arxiv.org/abs/2212.06888>

## 6. Sentiment-Aware Mean-Variance with LLM Verification (arxiv 2508.16378, Aug 2025)
- **Edge**: News + LLM sentiment overlay on classic MV portfolio. 180d rolling train, 1d test.
- **Sharpe**: **1.11** vs BTC buy&hold **0.89**.
- **V3-portable?** PARTIAL — we have news scanner already. Adapting from portfolio weights to per-symbol scalp bias is non-trivial. Useful as a feature, not a system.
- URL: <https://arxiv.org/pdf/2508.16378>

## 7. Event-Aware Sentiment Factors from LLM-Augmented Tweets (arxiv 2508.07408)
- **Edge**: LLM extracts event types from financial tweets → per-symbol sentiment factor.
- **Sharpe**: **5.0**, 8% ann return [unverified — too high to trust without inspecting full methodology, likely small sample / equity universe / no transaction-cost realism].
- **V3-portable?** NO directly — Twitter/X scraping infra required. Idea borrowable.
- URL: <https://arxiv.org/html/2508.07408v1>

## 8. Limit Order Book Transformer "LiT" (Frontiers AI 2025)
- **Edge**: Transformer with relative-position attention on raw LOB ladders for short-horizon mid-price.
- **Sharpe**: Not the headline; outperforms DeepLOB / LSTM on directional accuracy.
- **V3-portable?** NO short-term — needs L2 book history, large GPU training.
- URL: <https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1616485/full>

## 9. WaveLSFormer — Wavelet Transformer for Long-Short Equity (arxiv 2601.13435)
- **Edge**: Learnable wavelet decomposition + transformer + ROI/Sharpe-aware loss.
- **Sharpe**: **1.024 ± 0.122** on 5yr hourly US equities (29.10.2020–29.10.2025), 6 industry groups.
- **V3-portable?** PARTIAL. Wavelet denoise of 3m candles before WT could be a free feature-engineering win without changing strategy logic.
- URL: <https://arxiv.org/html/2601.13435>

## 10. Weekend / Time-of-Day Effects in Crypto Momentum (Advances in Consumer Research, 2025)
- **Edge**: Crypto momentum strategies yield higher Sharpe and lower DD on weekends; altcoins more so than majors.
- **Sharpe**: Differential not absolute number — directional finding.
- **V3-portable?** YES, trivial — gate scalp aggressiveness by day-of-week + UTC hour. We already have hour gates; weekend toggle is one config.
- URL: <https://acr-journal.com/article/the-weekend-effect-in-crypto-momentum-...>

## 11. xLSTM Time-Series Benchmark (arxiv 2603.01820, 2026)
- **Edge**: xLSTM beats Transformer/MAMBA/standard LSTM on Sharpe-optimised futures portfolios.
- **Sharpe**: **1.79 over 2010–2025, 1.99 over 2020–2025** on daily futures.
- **V3-portable?** NO short-term — daily futures, not 3m crypto. Architecture worth evaluating if we ever build an ML exit timer.
- URL: <https://arxiv.org/html/2603.01820>

---

## TOP RECOMMENDATION

**The most replicable edge from recent literature is #1 + #5 + #10 stacked**, in that order:

1. **Order-flow imbalance from Binance aggTrades** as a per-symbol entry confirmation (Sharpe 1.34 standalone, requires only signed-trade tape we already pull).
2. **Funding-rate sign as a sizing/bias knob** (existing data, near-zero implementation cost — note 2025 decay, use as boost only).
3. **Day-of-week + UTC-hour aggressiveness gates** (one config block, costs nothing to ship).

All three avoid new infra (no L2 book storage, no GPU training, no Twitter ingestion) and align with the V3 architecture's existing "rule-based + WT score" philosophy. They can be A/B tested in our existing v8_quick_engine vectorized sweep pipeline.

Skip for now: anything requiring full LOB history (#2 inputs, #8 LiT) or RL training (#3 DeepScalper) — infra cost greatly exceeds expected Sharpe lift relative to fixing the wiring bugs already documented in `IMPROVEMENT_FRAMEWORK_20260425.md`.

---

## SOURCES (every URL cited above)

- arxiv 2506.05764 — <https://arxiv.org/abs/2506.05764>
- arxiv 2201.09058 (DeepScalper) — <https://arxiv.org/abs/2201.09058>
- arxiv 2408.03594 (Hawkes OFI) — <https://arxiv.org/html/2408.03594v1>
- arxiv 2212.06888 (Perp Futures Fundamentals) — <https://arxiv.org/abs/2212.06888>
- arxiv 2508.16378 (Sentiment MV crypto) — <https://arxiv.org/pdf/2508.16378>
- arxiv 2508.07408 (Event-aware tweet factors) — <https://arxiv.org/html/2508.07408v1>
- arxiv 2601.13435 (WaveLSFormer) — <https://arxiv.org/html/2601.13435>
- arxiv 2603.01820 (xLSTM benchmark) — <https://arxiv.org/html/2603.01820>
- SSRN 4824172 (SPY intraday momentum) — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172>
- SSRN 5576424 (Funding rate DAR) — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5576424>
- EFMA 2025 OFI paper — <http://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/OrderFlowpaper.pdf>
- Frontiers AI LiT — <https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1616485/full>
- ACR weekend-effect crypto — <https://acr-journal.com/article/the-weekend-effect-in-crypto-momentum-does-momentum-change-when-markets-never-sleep--1514/>
