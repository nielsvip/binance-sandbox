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

### Done (data + engine + sweeps live)

| ID | Task | Status |
|----|------|--------|
| A1 | Funding rate: fetcher + cache + NPZ + FUNDING_GATE engine wiring | ✅ WIRED 2026-04-26 (196 caches on S1) |
| A2 | OI: fetcher + cache + NPZ + OI_CONFIRM engine wiring | ✅ WIRED 2026-04-26 (191 caches, ~30d each per Binance limit) |
| A3 | KC + Squeeze fire: NPZ fields + SQUEEZE_FIRE_ENTRY engine wiring + ez_indicators helpers | ✅ WIRED 2026-04-26 |
| A4 | WT/MFI divergence: pivot detector + 40 NPZ fields + DIVERGENCE_ENTRY engine wiring | ✅ WIRED 2026-04-26 |
| ENGINE-REVISION | HTF alignment guard for additive signals (`ADDITIVE_SIGNAL_MIN_HTF`) | ✅ 2026-04-26 04:00, md5 da106de8… |
| ENGINE-CLAMP | `_clamp_overrides()` in QuickConfig — auto-reset out-of-range MFI/RSI/K/Chop fields to defaults on load | ✅ 2026-04-26 05:55, md5 5d583f07… |
| BASELINES-SANITIZED | Both baselines cleaned of drift artifacts | ✅ crypto: 7 fields removed (`crypto_2p6365_sanitized.json`); tradier: 13 fields removed (`tradier_3p4361_sanitized.json`) |
| CRYPTO-NPZ-REGEN | 50/50 crypto NPZs regenerated with all new fields | ✅ DONE 00:39 |
| TRADIER-NPZ-REGEN | 106/114 tradier NPZs regenerated with KC/Squeeze/divergence (no funding/OI by design) | ✅ DONE 03:48 |
| CRYPTO-AUTONOMOUS | w26200 LIVE on S1 (50sym, target 2.0, sanitized baseline `crypto_2p6365_sanitized.json`, flip 0.05) — replaces w26104 (killed 05:54, 0 iter snapshot) | ✅ LIVE since 05:54 |
| TRADIER-AUTONOMOUS | w36200 LIVE on S2 (114sym, target 2.5, sanitized baseline `tradier_3p4361_sanitized.json`, flip 0.08) — replaces w36106 (killed 05:54, 47 iter pre-sanitize snapshot saved) | ✅ LIVE since 05:54 |
| CONFIG-MIRROR | SQUEEZE_FIRE switches added to both config.py and config_tradier.py | ✅ |

### Open — wiring/diagnostics (still pending from original framework)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| B1 | Verify `DELTA_EXIT_OVERRIDE_NOLOSS` wiring in v8_quick_engine | open | grep first; memory is 18 days old |
| B2 | Verify `HEDGE_EXIT_BYPASS_NOLOSS` + `STALL_SUB` wiring | open | same memory entry |
| B3 | Decide: score MACD/LinReg or delete compute | open | wasted compute |
| B4 | Grep + delete dead BC_155/156/157 branches | open | |
| B5 | Unify SCALP_V3 paper/live TF set | open | paper=1M, live=3M |
| B6 | Confirm 2026-04-25 hedge close gate fix synced to S1+S2 | open | |
| D1 | Weekly `check_sandbox_parity.py` cadence | open | |
| D2 | Audit `v8_engine` `FEE_PCT` setting | open | likely-high-impact diagnostic for "bad real gains" |
| D3 | PPL slippage sample (50 fires from JSONL) | open | |
| 100-SYM-VERIFY | Re-test c08_crypto_winner on 100 syms (also addresses D4) | open | **gates C1–C5** |
| TRADIER-CRON-RESTORE | Re-enable autonomous watchdog: `ssh s2-int "crontab -l \| sed 's/^#REGEN_PAUSE_//' \| crontab -"` | open | do once w36106 stable. Cron currently disabled on S2. Also screen `autochain_s2` killed — re-create if you want auto-respawn. |

### Open — sweeps (blocked on 100-SYM-VERIFY)

| ID | Task | Notes |
|----|------|-------|
| C1 | WRONG_SIDE_ABS_KILL param sweep | min_tfs ∈ {3,4,5}, k_tfs ∈ {1,2,3}, age ∈ {15,30,60,120} |
| C2 | PPL step-ladder sweep | GAIN_PCT ∈ {0.3,0.4,0.5,0.7,1.0}, FRAC ∈ {0.3,0.5,0.7} |
| C3 | ADX_4h × K3M_FLOOR cross-section | |
| C4 | AUGMENT multiplier sweep | {1.0,1.25,1.5,1.75,2.0,2.5,3.0} with AUGMENT_ONLY_PROFITABLE locked |
| C5 | NOLOSS_BYPASS_WT_5OF5 + WRONG_SIDE_ABS_KILL co-sweep | |

### Parking lot (revisit only after A1–A4 prove out)

| ID | Task | Why parked |
|----|------|------------|
| A5 | CVD spot-vs-perp | needs aggTrades reconstruction, hundreds of MB/day per symbol |
| A6 | Liquidation cascade fade | needs Coinalyze/CoinGlass external feed |
| A7 | ICT/SMC primitives (FVG/OB/BOS/CHoCH/sweeps) | published backtests vary 30%+ across forks; high overfit risk |

---

## Provenance

- **Research date:** 2026-04-25.
- **Source agents:** 4 background research agents (TV strategies, YouTube traders, CryptoGodJohn investigation, ez_ architecture audit).
- **Audit findings reflect codebase state at session start; some memory citations are 18+ days old — verify file:line claims before acting.**
- **Anchor baseline winner config:** `snapshots_local/c08_crypto_winner_20260421_034137/overrides_c08_crypto_winner.json`.
