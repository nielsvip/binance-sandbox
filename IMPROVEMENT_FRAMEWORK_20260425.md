# ez_ Improvement Framework — 2026-04-25

**Purpose:** Crash-resumable roadmap for closing the gap between ez_'s extensive feature surface and its real-world gains. Built from convergent research across (a) top public TradingView Pine strategies, (b) crypto YouTubers publishing rule-based systems, (c) a CryptoGodJohn investigation (closed), and (d) full audit of ez_'s current strategy surface.

**Anchor baseline (do not lose track of this):** pool_sharpe **2.6365** on 50-sym crypto multi-year, winner snapshot `snapshots_local/c08_crypto_winner_20260421_034137/overrides_c08_crypto_winner.json`. Anything below this on minimum sample (≥48 sym × >1yr, pool-averaged) is noise per CLAUDE.md rule 4b.

**Convergence signal:** TV-strategy and YouTube agents independently surfaced the same 7 missing primitives. That's the strongest signal — not "look at strategy X", but "you're systematically blind to these data inputs."

**CryptoGodJohn — closed.** Verified real (John Walsh, Walsh Wealth Group, ~700k X followers) but he's a paid Discord operator with ZachXBT-documented shill/dump pattern. His tradeable universe (sub-$50M altcoins, pre-listings, Solana micros) doesn't intersect the NPZ symbol set. No timestamped public trade log. Do not engineer.

---

## A. The 7 Convergent Missing Primitives

Ranked by ROI-per-engineering-hour. Tag every one `needs Tier 2 sweep before live` per NEW STRATEGY PROHIBITION.

### A1. Funding Rate (perps) — [data pipeline]
- **Status:** not fetched, not in NPZ, not scored.
- **Source:** Binance Futures `/fapi/v1/fundingRate`, free, 8h cadence, backfillable.
- **Two distinct uses:**
  1. Direction filter: veto longs when 8h funding > +0.05%; veto shorts when < −0.05%.
  2. Carry alpha (delta-neutral): long spot + short perp when funding > threshold. ~19% APY documented, MaxDD ~2%. Orthogonal to directional system.
- **Confirm:** which crypto accounts (ang/inf/men/fin/flz) have perp access vs. spot only.
- **Cost:** low. Likely top-3 contributor by Sharpe lift.

### A2. Open Interest (OI) — [data pipeline]
- **Status:** not fetched.
- **Source:** Binance `/fapi/v1/openInterestHist`, 15m granularity.
- **Rule (CryptoCred + multiple TV strategies):**
  - price ↑ + OI ↑ = real trend (continue)
  - price ↑ + OI ↓ = short-cover rally (fragile)
  - inverse for shorts.
- **Plug-in:** new score component in `check_entry_candidates_for_account()`, +10/−10 modifier.
- **Cost:** low. NPZ extension `oi_15m`, `oi_change_15m`, `oi_change_1h`.

### A3. Squeeze fire (BB-inside-KC compression release) — [new entry pathway]
- **Status:** BB width + ATR rank computed (BC_150 = sizing multiplier, NOT entry trigger). Keltner Channel not computed. Squeeze binary fire absent.
- **Mechanism:** when BB(20, 2.0) is inside KC(20, 1.5) → squeezed → entry on BB break out of KC + momentum sign.
- **Cost:** low (KC = EMA(20) ± ATR(20) × 1.5). Indicator-only.

### A4. Explicit WT + MFI divergence detector — [new score input]
- **Status:** WT divergence flag exists in some scoring paths (+50 score at extremes), but no first-class price-vs-WT and price-vs-MFI divergence event with regular/hidden + bull/bear classification (the LazyBear / VuManChu Cipher B contribution).
- **Mechanism:** lower-low in price + higher-low in WT (regular bullish), inverse for bearish; same for MFI; "hidden" variants for trend continuation.
- **Cost:** medium. Pivot detection on closed bars only (no repaint), 4 flags per TF.
- **Risk:** overfit if scored aggressively — start +5/−5 nudge.

### A5. CVD (spot vs perp) divergence — [new data + signal]
- **Status:** not available; klines lack delta. Requires aggTrades reconstruction.
- **Mechanism:** Trading Riot — spot CVD higher-low + perp price lower-low = spot accumulation under perp pressure (long bias).
- **Cost:** **high.** Trade-tick ingestion, hundreds of MB/day per symbol.
- **Decision:** parking lot until A1–A4 exhausted.

### A6. Liquidation cascade fade — [new data + signal]
- **Status:** not available.
- **Source:** Coinalyze / CoinGlass.
- **Mechanism:** when 24h liq volume > 3σ above mean and price at HTF support → counter-trade with tight SL.
- **Decision:** parking lot.

### A7. ICT/SMC primitives (FVG, OB, BOS, CHoCH, sweeps) — [new structure]
- **Status:** none. DC channel is the only structural-level concept.
- **Reference:** open-source `joshyattridge/smart-money-concepts` Python package.
- **Honest take:** discretionary pattern-matchers translated to code. Published backtests vary 30%+ across forks. **Lowest priority of the 7** despite popularity. Parking lot until A1–A5.

---

## B. Wiring/Code Bugs Costing Sharpe Today

Already-paid-for features that aren't firing. **Fix these before any new feature.**

### B1. `DELTA_EXIT_OVERRIDE_NOLOSS` declared but never read in `v8_quick_engine.py`
- Per memory `project_sandbox_bypass_wiring_gap` (2026-04-22): switch is sweep-able but engine ignores it. Sandbox over-holds losers → 12-sym Sharpe lies.
- **Action:** verify in current code (memory may be stale), wire if still gapped, re-run any sweep that varied this flag.

### B2. `HEDGE_EXIT_BYPASS_NOLOSS` + `STALL_SUB` half-wired in vec engine
- Same memory entry. If sandbox doesn't simulate hedge exits faithfully, hedge-on-hedge studies are noise.
- **Action:** v8_quick_engine should mark hedge positions and route through bypass paths.

### B3. MACD and LinReg slope computed but never scored
- Audit: "MACD computed on 1h+4h only; never scored." Same for LinReg slope. Wasted compute.
- **Action:** decide — score them, or delete the compute. Don't leave indicators floating.

### B4. Reentry block conflict: `BC_156`/`BC_157` permanently disabled
- Mandatory-reentry caused runaway flood (500+/hr). 7 active reentry blocks (B02/B04/B10/B11/B12/B14/B16) replaced them.
- **Action:** grep `BC_155`/`BC_156`/`BC_157` across `ez_*` and `v8_*`, delete dead branches.

### B5. SCALP_V3 paper vs live divergence
- Memory: paper uses 1M_ONLY, live uses 3M_ONLY. Paper-A/B testing different SCALP_V3 than what fires live.
- **Action:** unify on one TF set; rerun paper.

### B6. Hedge close gates fixed today (2026-04-25) — preserve fix
- `BLOCKED_LOW_GAIN_DRAIN_PROTECTION` + Finandy routing both lacked `is_hedge` exemption.
- **Action:** confirm rsync to S1 + S2 happened (CLAUDE.md sandbox-parity rule).

---

## C. Sweep Candidates from EXISTING Knobs

Feature importance dominated by 3 flags: `WRONG_SIDE_ABS_KILL` (+0.219), `PARTIAL_PROFIT_LOCK` (+0.150), `RZ_CASCADE` (+0.090). All have unswept parameter sub-dimensions.

### C1. WRONG_SIDE_ABS_KILL parameter sweep (highest-EV existing knob)
- Currently binary. Sweep:
  - `min_tfs_against` ∈ {3,4,5} (currently 5)
  - `min_stoch_k_against` ∈ {1,2,3} (currently 3)
  - `min_age_min` ∈ {15, 30, 60, 120} (currently 30)
- Hypothesis: 4-of-5 + age 60 may push lift further.

### C2. PARTIAL_PROFIT_LOCK_v2 step ladder
- Currently 2 of 3 steps wired. Add Step 1.5 = 25% close at +1.0%, leave 25% to ride.
- Sweep `GAIN_PCT` ∈ {0.3, 0.4, 0.5, 0.7, 1.0}, `FRAC` ∈ {0.3, 0.5, 0.7}.

### C3. ADX_4h × K3M_FLOOR cross-section
- BC_155 ADX_4h ≤ 16 alone gates 31% of entries (+11.5pp WR). Cross with K3M_FLOOR may reveal regime alignment.

### C4. AUGMENT_WT_4H_BOUNCE / WT_D_BOUNCE multipliers
- Currently 1.5× / 2.0×. Sweep {1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0} with `AUGMENT_ONLY_PROFITABLE=True` locked.

### C5. NOLOSS_BYPASS_WT_5OF5 alongside WRONG_SIDE_ABS_KILL
- Two paths likely overlap and may cannibalize. Co-sweep to find non-redundant config.

---

## D. Backtest-vs-Live Divergence Diagnosis

The "extensive system but bad real gains" complaint. Three orthogonal causes:

### D1. Sandbox-vs-live drift (recurring incident)
- 2026-04-14 wipeout, 2026-04-16 D4 mismatch, current half-wired BYPASS switches (B1/B2).
- **Action:** run `check_sandbox_parity.py` weekly minimum.

### D2. Fee/slippage modeling in backtest
- v8_quick vectorized; v8_engine fills at mark.
- Spot maker/taker 0.1%/0.2%, perp similar. Round-trip 0.2–0.4% per trade.
- 14,769 trades × 0.3% = ingest fees alone could be ~30–60% of `accumulated_gain_pct` 13,144%.
- **Action:** verify v8_engine `FEE_PCT` setting. If 0 or 0.05%, baseline 2.6365 is inflated.

### D3. PARTIAL_PROFIT_LOCK webhook latency
- Live fills slip; backtest models instant.
- **Action:** sample 50 PPL fires from `data/decisions/` JSONL, compute realized close vs spec.

### D4. 12-sym → 50-sym collapse pattern
- Crypto v38: 3.1177 → 0.4190 (7.4×). Tradier v61: 4.9674 → 0.1059 (47×).
- 2.6365 is on 50 sym; **may collapse further on 100 sym.**
- **Action:** re-run c08_crypto_winner against fresh 100-sym pool.

---

## E. Skip / Dead-End List

- **Lorentzian k-NN classifier** — 12 params, parameter-sensitive, low-EV vs A1–A4.
- **Hull Suite as standalone entry** — pure trend-follower, 30%+ DD in chop. T_UP_15m approximates.
- **Ichimoku Cloud** — underperforms WT on crypto futures.
- **Connors RSI** — built for stocks; fails on 24/7 perps. RSI banned.
- **Repainting Nadaraya-Watson** — fictitious backtests. Only Julien_Eche non-repaint fork is honest, and lift over BB is small.
- **Closed-source "AI/ML" TV indicators** (BigBeluga, AlgoAlpha) — can't audit, can't replicate.
- **CryptoGodJohn / WWG signals** — paid-Discord shill pattern, no labeled trade set.
- **MMcrypto, Coin Bureau, Pizzino** — directional commentary, no rule-based system.
- **Sheldon the Sniper free content** — paywalled mechanics.
- **Cyatophilum-style single-symbol single-TF strategies** — classic 60–80% OOS collapse.

---

## F. Recommended Priority Order

1. **Fix wiring (B1–B6).** This week. May shift 2.6365 baseline up or down before any new feature.
2. **Verify 2.6365 holds at 100-sym crypto.** If it collapses to ~1.0, every ranking decision since 2026-04-21 is suspect.
3. **Sweep existing knobs (C1–C5).** WRONG_SIDE_ABS_KILL params → PPL ladder → ADX×K3M cross → augment multipliers → NOLOSS_BYPASS co-sweep.
4. **Add A1 (funding rate) + A2 (OI).** Pure data-pipeline additions, default OFF, swept Tier 2 on top of verified baseline.
5. **Add A3 (Squeeze fire) + A4 (divergence detector).** Indicator-only.
6. **Park A5/A6/A7.** Revisit only after A1–A4 are validated.
7. **Diagnose D1–D4 in parallel.** "Bad real gains" may collapse to "backtest fee assumption is too generous."

---

## G. Sample-Size Sanity (CLAUDE.md rule 4b)

Every recommendation gated on:
- ≥48 symbols crypto, >1 year, pool-averaged Sharpe (not chained).
- All 5 standard metrics: `pool_sharpe / sym_sharpe / avg_gain_trade / gain_per_yr / gain_sym_yr`.
- Anything <48 sym or <1yr is internal debug only.

Candidate doesn't ship unless it dominates or ties 2.6365 across all 5 metrics on ≥48 sym × >1yr.

---

## Status Tracker (update as work progresses)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| B1 | Verify DELTA_EXIT_OVERRIDE_NOLOSS wiring in v8_quick_engine | not started | grep first; memory is 18 days old |
| B2 | Verify HEDGE_EXIT_BYPASS_NOLOSS + STALL_SUB wiring | not started | |
| B3 | Decide: score MACD/LinReg or delete compute | not started | |
| B4 | Grep + delete dead BC_155/156/157 branches | not started | |
| B5 | Unify SCALP_V3 paper/live TF set | not started | paper=1M, live=3M |
| B6 | Confirm hedge close gate fix synced to S1+S2 | not started | fixed 2026-04-25 |
| 2.6365-verify | Re-test c08_crypto_winner on 100 syms | not started | gates everything below |
| C1 | WRONG_SIDE_ABS_KILL param sweep | blocked on 2.6365-verify | |
| C2 | PPL step-ladder sweep | blocked on 2.6365-verify | |
| C3 | ADX_4h × K3M_FLOOR cross-section | blocked on 2.6365-verify | |
| C4 | AUGMENT multiplier sweep | blocked on 2.6365-verify | |
| C5 | NOLOSS_BYPASS_WT_5OF5 + WRONG_SIDE_ABS_KILL co-sweep | blocked on 2.6365-verify | |
| A1 | Funding rate ingest + ENGINE WIRING | **WIRED 2026-04-26** | Fetcher + cache + NPZ injection complete. Backfill done on S1 (196 funding caches). Engine wired in v8_quick_engine.compute_entry_signals: `FUNDING_GATE_ENABLED` filters longs when funding > LONG_MAX, shorts when < SHORT_MIN. autonomous_search auto-discovers via fields(QuickConfig). |
| A2 | Open Interest ingest + ENGINE WIRING | **WIRED 2026-04-26** | Fetcher + cache + NPZ injection complete. 191 OI caches on S1 (~30d each per Binance limit). Engine wired: `OI_CONFIRM_ENABLED` requires oi_change_1h ≥ MIN_PCT for longs, ≤ -MIN_PCT for shorts. |
| A3 | KC + Squeeze fire ENGINE WIRING | **WIRED 2026-04-26** | NPZ fields complete. v8_quick_engine wired: `SQUEEZE_FIRE_ENTRY_ENABLED` adds entry signal when squeeze_fire_{tf} matches direction (additive). Plus live helpers in ez_indicators.py for future live use. |
| A4 | WT/MFI divergence ENGINE WIRING | **WIRED 2026-04-26** | NPZ fields complete (40 fields per symbol). v8_quick_engine wired: `DIVERGENCE_ENTRY_ENABLED` + `DIVERGENCE_INDICATOR={wt,mfi,either}` adds entry signal when div_reg_bull(long)/div_reg_bear(short) active. |
| NPZ-REGEN | Regenerate all 48 crypto NPZs with new fields | **DONE 2026-04-26 00:39** | DONE: 50/50. 70 new fields per NPZ verified populated. Sentiment injection ran post-pass. |
| AUTONOMOUS-RESTART | Restart with extended parameter space | **LIVE 2026-04-26 00:40** | autonomous_search w26104 running (PID 329183 on S1). Extended 9-switch space active. Output: `data/autonomous/crypto_2p6365_50sym/w26104/autonomous_crypto.csv`. Log: `/home/niels/logs/autonomous_crypto_w26104_postregen.log`. Each iteration up to 120s. |
| MONITORING | Watch for first winning configs | **TODO** | Check `data/autonomous/crypto_2p6365_50sym/w26104/autonomous_crypto.csv` periodically. Look for any iteration with `pool_sharpe > 2.6365` AND uses one of the new switches. |
| A5 | CVD spot-vs-perp | parking lot | |
| A6 | Liquidation cascade fade | parking lot | |
| A7 | ICT/SMC primitives | parking lot | |
| D1 | Weekly check_sandbox_parity.py cadence | not started | |
| D2 | Audit v8_engine FEE_PCT setting | not started | likely-high-impact diagnostic |
| D3 | PPL slippage sample (50 fires from JSONL) | not started | |
| D4 | c08_crypto_winner on 100-sym (overlaps with 2.6365-verify) | not started | |

---

## Provenance

- **Research date:** 2026-04-25.
- **Source agents:** 4 background research agents (TV strategies, YouTube traders, CryptoGodJohn investigation, ez_ architecture audit).
- **Audit findings reflect codebase state at session start; some memory citations are 18+ days old — verify file:line claims before acting.**
- **Anchor baseline winner config:** `snapshots_local/c08_crypto_winner_20260421_034137/overrides_c08_crypto_winner.json`.
