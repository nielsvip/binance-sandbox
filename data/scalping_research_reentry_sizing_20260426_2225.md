# Scalper Reentry & Position Sizing — Field Research
Date: 2026-04-26 | Scope: V3 3m crypto scalp | Problem: 0/345 same-direction reentries within 1h

## Executive Summary
Pros DO re-fire the same direction repeatedly on a live trend; what they DON'T do is reuse the same hard-coded entry filter the second time. The 0/345 number means the entry gate is structurally one-shot — typical fixes are (a) a separate, looser "continuation" gate that fires off pullback/vol-expansion logic, and (b) pyramiding inside the same position rather than waiting for a fresh full-stack signal.

---

## 7 Techniques (each = a concrete V3 change)

### 1. Pullback-to-MA Continuation Reentry (replaces full-stack signal)
After a winning exit, re-arm with a *looser* gate: re-enter when price retests EMA20 or EMA50 on the trade-direction TF and prints a 3m close back through it. This is the standard professional fix for "stopped/exited too early on a live trend."
**Config:** `V3_REENTRY_PULLBACK_ENABLED=True`, `V3_REENTRY_PULLBACK_EMA=20`, `V3_REENTRY_WINDOW_MIN=60`, bypass score gate, keep direction lock.
**Source:** Quora — "Is it a good idea to have a re-entry signal..." (consensus by trend-following authors); credibility = medium-high (matches Brooks/Dalton playbooks).

### 2. Volatility-Expansion Reentry
Re-enter only if 3m ATR expands (current ATR > 1.2× ATR-at-exit). Filters out the dead-trend reentries that destroy WR.
**Config:** `V3_REENTRY_VOL_RATIO_MIN=1.20`, ATR(14) on 3m.
**Source:** Reddit/r/algotrading consensus + altrady.com; credibility = high — same rule used by ORB/breakout shops.

### 3. Pyramiding (4-unit Turtle rule, scaled to V3)
Don't exit-and-reenter — *add* to the open position every 0.5×ATR favorable, decreasing size each add (1.0R → 0.5R → 0.25R → 0.12R), max 4 units. This is what Jerry Parker's Turtles actually did and is the cleanest way to "accumulate gains on the same trend."
**Config:** `V3_PYRAMID_ENABLED=True`, `V3_PYRAMID_STEP_ATR=0.5`, `V3_PYRAMID_MAX_UNITS=4`, `V3_PYRAMID_SIZE_DECAY=0.5`. Initial $20 → adds $10, $5, $2.50.
**Source:** Original Turtle rules (Curtis Faith / Jerry Parker interviews on trendfollowing.com); credibility = highest — 35-yr track record.

### 4. Inverse-Volatility Sizing (replaces $20 fixed cap)
`size_$ = risk_$ / (ATR_3m × multiplier)`. Accounts for BTC swings 3-7%/day. Calm regime = bigger size, choppy = smaller.
**Config:** `V3_SIZE_MODE='inv_vol'`, `V3_RISK_PER_TRADE_USD=2.0` (i.e. risk $2, not cap notional), `V3_ATR_MULT=2.0`. ATR 0.3% → $33 notional; ATR 1.5% → $7. **Hard floor $5, hard cap $50** (already-set $20 was a *cap* not a *target* — keep cap, drop floor).
**Source:** Van Tharp "Definitive Guide to Position Sizing" + quantstrategy.io ATR sizing; credibility = highest, decades of literature.

### 5. Half-Kelly with Live EV (caps the upside, prevents blowup)
`f* = edge/odds`. Half- or quarter-Kelly is what hedge funds actually run — full Kelly is mathematically optimal but emotionally/operationally suicidal in crypto. Compute on rolling 100-trade window of V3.
**Config:** `V3_KELLY_FRAC=0.25`, `V3_KELLY_WINDOW=100`, `V3_KELLY_MAX_PCT=0.5%` of equity per trade.
**Source:** Wikipedia Kelly Criterion + lbank.com crypto Kelly; credibility = high. Quants converge on 10-25% Kelly; 50%+ blows up on parameter error.

### 6. Drawdown-Tiered Size Reduction (auto-defense)
Tiered cuts on equity drawdown from peak: 5% DD → 0.9× size; 10% DD → 0.75×; 15% DD → 0.5×; 20% DD → halt new entries. Auto-restores as equity recovers.
**Config:** `V3_DD_TIERS={5:0.9, 10:0.75, 15:0.5, 20:0.0}`, peak-equity rolling.
**Source:** quant.fish "Reducing Position Sizing During Drawdowns"; credibility = high — standard CTA risk overlay.

### 7. Same-Symbol Cooldown + Direction-Lock (anti-revenge / anti-flip)
After a *losing* exit, lock the symbol for N minutes. After a *winning* exit, NO cooldown but direction-locked to winner's side for 60 min (prevents bot from flipping short into a still-rising trend on a single 3m wiggle).
**Config:** `V3_LOSS_COOLDOWN_MIN=30`, `V3_WIN_DIRECTION_LOCK_MIN=60`, `V3_REVERSE_BLOCK_AFTER_WIN=True`.
**Source:** Reddit/r/algotrading + Brooks Trading Course scalping rules; credibility = medium-high (oral tradition, not papered).

### 8. Max-Concurrent via Correlation Buckets (replaces 3-8 hard cap)
Replace flat 3-8 concurrent with: max 2 per correlation cluster (e.g., L1s, memes, AI tokens), max 6 total. Fixed cap sees BTC/ETH/SOL all-long as 3 separate trades when it's really 1 BTC-beta bet.
**Config:** `V3_MAX_PER_CLUSTER=2`, `V3_MAX_TOTAL=6`, cluster file `data/symbol_clusters.json`.
**Source:** ITI dynamic position sizing + standard portfolio theory; credibility = high.

---

## TOP RECOMMENDATION — fix the 0/345 gap

**The 0/345 is a gating problem, not a signal problem.** V3's entry gate requires the full setup to print fresh; once price has moved 0.5% in the trade direction, the same gate can never fire again on the same symbol — the setup is "consumed."

**Fix in this order (cheapest to most invasive):**
1. **Add Technique #1 (Pullback Reentry)** behind a kill-switch. Sweep `V3_REENTRY_PULLBACK_ENABLED={True, False}` × `V3_REENTRY_WINDOW_MIN={30, 60, 120}` × `V3_REENTRY_PULLBACK_EMA={20, 50}`. Expected: first non-zero reentry rate.
2. **Add Technique #3 (Pyramiding)** as alternative — instead of exit/reenter, never exit a winner until trail hits; just add. Skips the gate problem entirely.
3. **Combine with #4 (Inv-Vol Sizing)** so adds aren't dumb $20 every time.

Sweep all three on Tier-1 vec engine (>48 sym × >1yr) before flipping live. Floor metric: `pool_sharpe ≥ 1.0` AND `gain_sym_yr` improved vs current V3 baseline AND `max_dd_pct ≤ baseline+2pp`.

Sources:
- [Quora — Re-entry signal for trend following](https://www.quora.com/Is-it-a-good-idea-to-have-a-re-entry-signal-for-a-trend-following-trading-strategy-to-re-enter-into-a-trade-that-got-stopped-out-prematurely)
- [LuxAlgo — Pyramiding Strategies](https://www.luxalgo.com/blog/pyramiding-strategies-scaling-into-trades-to-boost-returns/)
- [trendfollowing.com — Jerry Parker / Turtle systems](https://www.trendfollowing.com/2024/08/28/jerry-parker-completely-contradicts-this-by-saying-the-turtles-were-trading-four-different-systems/)
- [Medium — Discipline Over Prediction: Turtle Rules](https://medium.com/@trading.dude/discipline-over-prediction-why-the-turtle-trading-rules-still-matter-f0f1d400d58d)
- [Van Tharp Institute — Position Sizing](https://vantharpinstitute.com/van-tharp-teaches-position-sizing-strategies-and-risk-management/)
- [QuantStrategy.io — ATR Position Sizing](https://quantstrategy.io/blog/using-atr-to-adjust-position-size-volatility-based-risk/)
- [LBank — Kelly Criterion for Crypto](https://www.lbank.com/explore/mastering-the-kelly-criterion-for-smarter-crypto-risk-management)
- [Wikipedia — Kelly Criterion](https://en.wikipedia.org/wiki/Kelly_criterion)
- [QuantInsti — Risk-Constrained Kelly](https://blog.quantinsti.com/risk-constrained-kelly-criterion/)
- [Quant.Fish — Reducing Position Sizing During Drawdowns](https://quant.fish/wiki/reducing-position-sizing-during-drawdowns/)
- [Robuxio — Algorithmic Crypto Trading XV: Drawdowns](https://www.robuxio.com/algorithmic-crypto-trading-xv-drawdowns/)
- [Altrady — Entry Exit Signals for Crypto Trend](https://www.altrady.com/crypto-trading/technical-analysis/entry-exit-signals-trend-traders)
- [Brooks Trading Course — Rules for Scalping](https://www.brookstradingcourse.com/trading-strategies/rules-for-scalping/)
- [International Trading Institute — Dynamic Position Sizing](https://internationaltradinginstitute.com/blog/dynamic-position-sizing-and-risk-management-in-volatile-markets/)
