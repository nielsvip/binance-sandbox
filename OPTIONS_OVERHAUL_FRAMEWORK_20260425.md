# Options Trading Overhaul Framework — 2026-04-25

> **Trigger**: -20% portfolio week, primarily options-driven. Owner: nielsvip.
> **Status**: Framework draft. **No code changes yet.** Decisions required from owner before any wiring.
> **Scope**: Tradier options only (`trb`, `trc`). Crypto execution is out of scope here.

---

## 0. The headline

The single biggest finding from the audit is not a missing feature — **it is a guardrail that was switched off three days before the bad week**:

```python
# config_tradier.py:218
OPTIONS_MAX_LOSS_GUARD_ENABLED: bool = False  # 2026-04-22 disabled per user — bottom-seller
```

When this was on, it auto-closed positions at:
- DTE > 30: -80% premium loss
- 14 < DTE ≤ 30: -60%
- DTE ≤ 14: -40%

Disabling it removed the *only* unconditional per-position kill switch on long premium. Combined with the second finding — **exit signals from `tradier_options_analyzer.py:1705+` are computed but treated as advisory by `tradier_manage.py`, not enforced** — there was no automated mechanism left to stop a single bad trade from compounding into the account.

That is the disaster shape. Everything else in this framework is built around making sure that shape is *structurally impossible* going forward, not just discouraged.

> ⚠️ **Open question for you**: confirm where the actual losses came from. The `data/decisions/decisions_trc_2026042*.jsonl` logs from this week show only stock positions (SLV, GLD), no option entries/exits. Either (a) options trades aren't logged to JSONL, (b) the bleed was on `trb` not `trc`, or (c) the worst trades happened earlier and just hit P&L this week. **I need the actual losing OCCs and entry/exit prices** before locking the diagnosis in.

---

## 1. Audit summary — what exists today

### Strengths (don't break)
- **Hard portfolio caps** (`config_tradier.py:201–215`): tiered base/hedged/fully-diversified caps ($5k / $10k / $15k), per-sector 40%, per-group 60%, per-symbol 25%, call/put ratio bounds 25–75%, max 3 contracts/order, max $800/order, max $9/share single-contract rule.
- **Greeks computed per-position** (`tradier_options_analyzer.py:82–97`): Black-Scholes delta/gamma/theta/vega stored on `OptionPosition`.
- **Multi-signal exit analyzer** (`tradier_options_analyzer.py:1705+`): 50% profit target, theta-urgent (DTE ≤ 3), max 21-day hold, WT-D reversal, WT slowdown, support/resistance breach, IV crush, deep-OTM, loss guards. Signals scored and sorted.
- **Entry filters** (`config_tradier.py:262–266`): min |delta| 0.35 (no lottery tickets), max 3% OTM, min 60 DTE (no theta traps), 90 DTE preferred, requires WT-D alignment.
- **CSP monitor daemon** (`tradier_options_csp_monitor.py`): non-skippable, 60s polling, 6-layer guards, full audit log. **But CSPs are disabled** (`OPTIONS_CSP_ENABLED=False`), so the monitor is currently watching nothing.
- **Diversification gates** (`tradier_options_agent.py:1129+`): pre-trade sector/group/symbol violation check.
- **Cron supervisor scaffold**: `options_watchdog_runner.sh` already runs weekdays 13:30–20:00 UTC via launchd; `tradier_watchdog_cron.sh` restarts dead processes at 13:20 UTC.

### Gaps that turn this into a disaster machine

| # | Gap | File | Why it matters |
|---|-----|------|----------------|
| G1 | Per-position loss guard **OFF** | `config_tradier.py:218` | No per-trade stop. One bad trade can theoretically lose 100% of premium. |
| G2 | Exit signals **advisory not enforced** | `tradier_options_analyzer.py:1705+` → `tradier_manage.py` | Analyzer says "exit"; manage may or may not execute. Fragile linkage. |
| G3 | **No portfolio-level Greeks** | nowhere | Per-position Δ exists, but no aggregate Δ/Γ/Θ/V budget. A "diversified" book can still be net +$50k delta with no awareness. |
| G4 | **No daily PnL kill switch** | nowhere | Account can lose 5%, 10%, 20% in a day with no automated circuit breaker. |
| G5 | **No real hedging on long options** | `OPTIONS_EQUITY_HEDGE_ENABLED=True` config exists, code is mostly stub | Long calls/puts are naked directional bets. No collars, no spreads enforced, no portfolio insurance. |
| G6 | **Spreads enabled in config but not used in entry path** | `OPTIONS_SPREAD_ENABLED=True` | The cheaper, defined-risk structure is one flag flip + entry-path rewrite away. |
| G7 | **No IV-rank gate on entry** | `tradier_options_analyzer.py` entry path | We buy premium when it's expensive. IV rank is computed for CSPs, not used for buys. |
| G8 | **Watchdog only protects CSPs (disabled)** | `tradier_options_csp_monitor.py` | The non-skippable daemon watches a strategy that isn't running. Long-option positions have nothing watching them in real time. |
| G9 | **No regime gate** (VIX, market trend) | nowhere | We open long calls into VIX>30 gap-down regimes. |
| G10 | **No dollar-loss-per-trade ceiling expressed in account terms** | config caps are in $ amounts, not % of account | A $700 trade is 1% of $70k but 7% of $10k. Cap should scale. |

---

## 2. How real options traders avoid ruin — the deep analysis

I've distilled this from how the major systematic and discretionary options programs actually run risk. The names are shorthand — what matters is the principles, which converge across all of them.

### 2.1 The TastyTrade / Sosnoff playbook (premium sellers, ~30y track record)
- **Sell premium, don't buy it, when IV rank ≥ 50.** High IV = options priced rich; sellers have edge.
- **Buy premium only when IV rank < 25 and you have strong directional view.** Otherwise theta eats you.
- **30–45 DTE entry, manage at 21 DTE.** Avoids the gamma-cliff inside the last 3 weeks.
- **Take 50% of max profit on credit trades.** Don't squeeze the last dollar out — risk-adjusted return collapses near expiry.
- **Defined risk only.** Spreads, iron condors, never naked.
- **Position size: ≤1–3% buying power per trade. Never >35% deployed total.**
- **Beta-weighted SPY-delta neutral at portfolio level.** Hedge directional exposure mechanically.

### 2.2 Mark Sebastian / OptionPit (volatility traders)
- Track **VIX term structure** (front month vs back month). Backwardation = stress regime, reduce size.
- **Skew-aware entries**: don't sell puts when skew is elevated; don't buy calls when call-side skew has popped.
- **Greeks at portfolio level matter more than per-position.** A book of 30 individually-fine trades can still be aggregate ruin.
- Periodic **gamma rebalances** when portfolio Γ exceeds threshold.

### 2.3 Karen "Supertrader" — the cautionary tale
- Lost ~$194M selling naked puts. Sized too large. **No single trade should be capable of materially harming the account.**
- Translated to your context: max realized loss per trade ≤ 1% of account = $700 on $70k.

### 2.4 Williams / Hadady / discretionary trend-followers buying long premium
- Use 60–120 DTE so theta is slow.
- **Hard stop at -50% of premium paid.** Time-and-price stops, not just price.
- **Take 100% gain (premium doubles) and then trail** — most options never come back.
- Concentration limit: **no more than 5–10 directional bets open at once.**

### 2.5 Modern systematic put-write / collared programs (CBOE PUT, BXM, XYLD)
- Sell monthly premium against equity holdings.
- Buy SPX/SPY long-dated puts at ~5% OTM as **catastrophe insurance** (~1–2% of NAV/year).
- Position-sizing scaled by **realized vol of the underlying** (lower target vol → smaller size).
- Daily mark-to-market, **monthly drawdown circuit breakers** that pause new opens.

### 2.6 Convergent principles (this is the real distillation)
Every framework above, despite different strategies, agrees on these eight:

1. **Defined risk on every trade.** No naked long premium with no stop. No naked short premium ever (you don't have margin for it anyway).
2. **Per-trade loss capped ≤ 1–2% of account.** Mechanically. Not a guideline.
3. **Daily loss circuit breaker.** -2% to -3% intra-day = pause new opens, evaluate. -5% = flatten everything.
4. **Time stop is non-negotiable.** Close at 21 DTE (sellers) / -50% premium (buyers) regardless of "feel".
5. **Volatility regime gate.** IV rank tells you which side of premium to be on. VIX tells you whether to be in at all.
6. **Portfolio Greeks budget.** Δ band, Γ ceiling, Θ floor, V band — recomputed on every position change.
7. **Concentration limits.** Per-symbol, per-sector, per-direction. You already have most of these.
8. **Catastrophe hedge.** A small persistent long-dated tail hedge (puts on SPY) so a -10% gap-down day doesn't end you.

These are what the framework below operationalizes.

---

## 3. Honest pushback on your specific ideas

You wrote: *"probably always hedge the trade (for every call a same value put on a weaker symbol in the same sector?)."*

I want to be direct rather than just nod along, because mis-naming this could cost you again:

- **That is a pairs trade, not a hedge.** Long call on strong-name + long put on weak-name is a *long volatility / long relative-strength* bet. You make money if the spread widens. You lose money if both stocks drift sideways — **theta eats both legs simultaneously**. In a low-vol grind market, this construction can lose more than just the long call would have.
- **Real hedges for a long call are**:
  - **Vertical debit spread**: buy your call, sell a higher-strike call. Caps profit, reduces cost, defines max loss. *This is what `OPTIONS_SPREAD_ENABLED=True` is for.* Almost certainly the single best change you can make.
  - **Collar (if you also own the stock)**: long stock + long put + short call. Cheap or free downside protection.
  - **Portfolio-level SPY put**: cheap tail insurance, doesn't try to micro-hedge each trade.
- **The pairs-trade idea CAN make sense as a strategy** if you're explicitly betting on relative strength — but then it's an alpha source, not a risk control, and you size it as a separate book.

**Recommendation**: replace "hedge each long with a put on a weaker name" with **(a) every long call/put becomes a vertical debit spread** + **(b) one persistent SPY/SPX put as portfolio tail hedge**. Keeps your directional view, caps your loss, costs less, no theta double-billing.

---

## 4. The proposed framework

Eight layers, ranked by disaster-prevention value. Each layer should fail safely if the layer below it breaks.

### Layer 1 — KILL SWITCHES (non-negotiable, ship first)
The thing that should have prevented this week.

- **L1.1 — Re-enable `OPTIONS_MAX_LOSS_GUARD_ENABLED`.** Per-position hard stop: -50% premium for DTE >30, -40% for 14<DTE≤30, -30% for DTE≤14. Stricter than the previous values because the previous values let trades go too far before triggering.
- **L1.2 — Daily account loss circuit breaker.**
  - Soft (`-1.5%` of account / day, ≈ -$1,050): block new option opens, log warning, email.
  - Hard (`-3%` of account / day, ≈ -$2,100): close all open options at market, halt trading until manual reset.
  - Reset at 13:30 UTC daily.
- **L1.3 — Per-trade dollar ceiling = 1% of equity.** Right now caps are absolute ($800/order). Make it `min($800, 1% × equity)`. On a $70k account that's $700 — same number, but it scales when the account grows or shrinks.
- **L1.4 — Max concurrent open option positions = 8** (down from 15). Empirically you cannot supervise 15 long-premium positions in a fast tape; reducing concurrency is the simplest gamma-risk control there is.

### Layer 2 — MAKE EXITS MANDATORY
Convert the existing analyzer signals from advisory to enforced.

- **L2.1 — Hard execution path**: when `tradier_options_analyzer.analyze_option_position()` returns a signal with `score >= HARD_EXIT_SCORE_THRESHOLD` (proposed: 80), `tradier_manage.py` must place a closing order this tick. Not next loop. Not "if convenient". This tick.
- **L2.2 — Mandatory exits (bypass any other gate)**:
  - DTE ≤ 5 — close, regardless of P&L. No exceptions.
  - 21-day hold reached — close.
  - Premium loss exceeds L1.1 thresholds — close.
- **L2.3 — Soft exits (require confirmation tick)**:
  - WT-D reversal + position in profit — close on next 3m bar that confirms.
  - IV crush + position in profit — close.
  - Support/resistance breach — close.
- **L2.4 — Exit-signal logging**: every fired exit must write to `data/options_exits/<date>.jsonl` with the full signal stack, score, and price. This is your post-mortem evidence trail.

### Layer 3 — CONVERT NAKED LONGS TO DEFINED-RISK SPREADS
Defines maximum loss per trade *structurally*, before any kill switch is needed.

- **L3.1** — Default entry construction = vertical debit spread (long ATM-ish, short 1-2 strikes higher for calls / lower for puts). Width $5–$10. `OPTIONS_SPREAD_ENABLED=True` is already set; the entry path needs to actually use it.
- **L3.2** — Naked long premium allowed only if (a) IV rank < 20 AND (b) signal score ≥ 90 AND (c) explicit override flag.
- **L3.3** — Spread max-loss = debit paid. This is your hard floor per trade. Combined with L1.3, single-trade ruin is mathematically capped.

### Layer 4 — PORTFOLIO GREEKS BUDGET
Treat the book as one position, not a list of positions.

- **L4.1 — Aggregate computation** every time a position changes or every 5 min, whichever sooner. Use beta-adjusted SPY delta (each name's delta × beta-vs-SPY, summed).
- **L4.2 — Bands**:
  - Beta-weighted Δ: ±(equity × 0.0002) — i.e. ±$14 per $1 SPY move on $70k. Tight by design.
  - Γ ceiling: scaled to equity. Initial value to be calibrated; start with whatever current portfolio Γ runs at and tighten 25%.
  - Θ floor (negative — daily decay you accept): ≥ -0.3% of equity / day.
  - Vega band: ±0.5% of equity per 1 IV-point move.
- **L4.3 — Breach action**: hard breach blocks new opens of the offending side and queues a rebalance suggestion. Two-band breach forces a rebalance at next open.
- **L4.4 — Daily Greeks email** (extend existing morning email): yesterday's close Greeks, today's open, projected theta-decay-by-EOD.

### Layer 5 — VOLATILITY REGIME GATE
Don't fight the vol regime.

- **L5.1 — IV rank gate on entries**:
  - Buying premium (long call/put or debit spread): require IV rank ≤ 40.
  - Selling premium (CSP, credit spreads, when re-enabled): require IV rank ≥ 50.
  - Neither: stay flat in options that day.
- **L5.2 — VIX gate**:
  - VIX > 35: no new option opens. Existing positions managed normally.
  - VIX < 11: no new option *buys* (IV is too cheap for sellers but too lottery-ticket for buyers — sit out).
- **L5.3 — Term-structure check**: if VIX9D > VIX > VIX3M (full backwardation) — pause new long-call opens. Stress regime.

### Layer 6 — CONCENTRATION + DIRECTIONAL SKEW (refinement of what exists)
Most of this exists. Tighten and enforce.

- **L6.1** — Per-symbol max **20%** (down from 25%).
- **L6.2** — Per-sector max **35%** (down from 40%).
- **L6.3** — Directional skew bound: |calls$ − puts$| ≤ 30% of total options $. Tighter than current 25–75% ratio. Forces a balanced book.
- **L6.4** — `OPTIONS_CONTINUOUS_SECTOR_GATE` (already exists, set True) — verify it's actually checked on every entry path, not just `daily_find_opportunities`.

### Layer 7 — CATASTROPHE HEDGE (portfolio insurance)
Cheap insurance against the gap-down day that ends accounts.

- **L7.1** — Persistent long SPY puts at ~5–8% OTM, 90–180 DTE. Roll quarterly.
- **L7.2** — Sized at **1–1.5% of equity per quarter** (~$700–$1k on $70k). Expected to lose money in calm quarters and pay 5–20× in a crash.
- **L7.3** — This sits *outside* the regular options budget. It's insurance, not an alpha bet.

### Layer 8 — SUPERVISOR AGENT (the cron loop you asked for)
This is the active intervention layer. Section 5 covers it in detail.

---

## 5. The supervisor agent — `tradier_options_supervisor.py`

Replaces the current "watchdog runs but only watches CSPs" gap.

### 5.1 Schedule
- Active **13:30–20:00 UTC weekdays** (US regular hours).
- Pre-market dry run at **12:55 UTC** (read-only health check, no orders).
- Post-close report at **20:05 UTC** (summary email).
- Driven by launchd plist (replace/extend `options_watchdog_runner.sh`).

### 5.2 Loop cadence
- Tick every **30s** (vs current 60s for CSP monitor). Faster intervention is the entire point.
- Each tick is bounded — if it can't complete in 25s, it logs and skips.

### 5.3 Tick checklist (in order)
Each item is a **gate that can fire orders**, not an advisory check.

1. **Refresh broker positions** (Tradier API). Reconcile vs internal state. Mismatch → log + alert, do not act on stale state.
2. **Compute per-position Greeks + P&L** for every open option.
3. **Compute portfolio Greeks** (beta-weighted Δ, total Γ, Θ, V).
4. **Daily P&L vs L1.2 thresholds**:
   - Below soft? Set `BLOCK_NEW_OPENS=True` for the day.
   - Below hard? Trigger `flatten_all_options()`. Halt.
5. **Per-position kill checks** (any one fires → close that position):
   - L1.1 max-loss-guard breach.
   - L2.2 hard exits (DTE ≤ 5, 21 days held, etc.).
   - Strike breach (existing CSP monitor logic, generalized to longs).
6. **Per-position soft exit checks** (L2.3): queue, require 1 confirming tick before firing.
7. **Portfolio Greeks band breach** (L4.3): block new opens of the breaching side; queue rebalance.
8. **Vol regime check** (L5): update day-state regime flags.
9. **Heartbeat write**: `data/options_supervisor/heartbeat.json` with timestamp + tick latency.
10. **Tick log**: append summary line to `data/options_supervisor/<date>.jsonl`.

### 5.4 Failure modes
- **Tradier API down**: skip the tick, log, retry next tick. Do **not** trade on stale data. Three consecutive failed ticks → email alert.
- **Internal exception**: catch + log + email + continue. Never crash the loop. Existing `options_watchdog_runner.sh` already restarts on exit-1, but the loop should self-recover without exiting.
- **Heartbeat gap > 90s** (detected by separate cron at 13:25, 14:25, …): email alert, attempt restart via launchd.

### 5.5 What it does NOT do
- It does **not** open new positions. Entry decisions stay with `tradier_options_agent.run_daily_cycle` and any explicit user-triggered path. The supervisor's mandate is **defense only.**
- It does **not** override locked files or violate `LOCKED_FILES.md`.
- It does **not** ignore `STRICT_NO_LOSS` rules — it adds *enforced exits* on options, which are explicitly allowed by `Options Hold Strategy` doctrine in memory.

### 5.6 Audit trail
Every action emits a JSONL record:
```json
{"ts": "...", "action": "force_close", "occ": "AAPL250117C00200000",
 "reason": "MAX_LOSS_GUARD", "score": 95, "pnl_pct": -0.52, "dte": 47,
 "greeks": {...}, "fill_price": 1.23, "order_id": "..."}
```
This becomes your post-mortem corpus. Every Friday, one cron job summarizes the week's interventions.

---

## 6. Phased implementation

Don't ship this all at once. Each phase is independently valuable and reversible.

### Phase 0 — Diagnosis (today, before any code)
- You confirm where this week's losses actually came from. Specific OCCs, entry prices, exit prices, account.
- I (or you) write a `LOSS_POSTMORTEM_2026-04-25.md` modeled on `TRADE_LOSS_ANALYSIS.md`. Without this, every fix below is fighting yesterday's war.

### Phase 1 — Stop the bleed (1 day, deploy same day after backtest)
- Re-enable `OPTIONS_MAX_LOSS_GUARD_ENABLED` (L1.1) with stricter thresholds.
- Add daily PnL circuit breaker (L1.2). Soft + hard.
- Make `tradier_manage.py` *enforce* analyzer exit signals at score ≥ 80 (L2.1, L2.2).
- These four changes alone would have prevented most of this week. Backtest on `backtest_v8_engine.py` against last 30 days before flipping live.

### Phase 2 — Convert to defined-risk (2–4 days)
- Wire `OPTIONS_SPREAD_ENABLED=True` through the entry path so default = vertical debit spread (L3).
- Add IV-rank gate on entries (L5.1).
- Add VIX regime gate (L5.2).

### Phase 3 — Supervisor agent (3–5 days)
- Implement `tradier_options_supervisor.py` per Section 5.
- Replace `options_watchdog_runner.sh` schedule to launch it.
- Run alongside existing watchdog for one week, dry-run only (logs decisions without firing). Compare against actual outcomes. Then enable live actions.

### Phase 4 — Portfolio Greeks budget (1 week)
- Aggregate Greeks computation (L4.1).
- Bands + breach actions (L4.2, L4.3).
- Greeks added to morning + evening email (L4.4).

### Phase 5 — Catastrophe hedge (slow, deliberate)
- Buy first SPY put tranche after Phase 1+2 are stable (L7).
- Quarterly roll cadence.

### Phase 6 — Concentration tighten (any time after Phase 1)
- Drop per-symbol cap to 20%, per-sector to 35% (L6).
- Tighten directional skew bound to 30%.

---

## 7. Decisions I need from you before writing any code

These are not rhetorical — each one changes the implementation materially.

1. **Loss confirmation**: where did the -20% actually come from? OCCs + dollar amounts + which account. *Without this Phase 0 can't close.*
2. **Daily loss circuit breaker thresholds**: am I right that -1.5% soft / -3% hard is the right pain point? Or are you willing to tolerate more for upside? Note: *every* extra percent you allow on the breaker is potentially extra weeks like this one.
3. **Naked long premium — eliminate or restrict?** My recommendation is "default to spreads, naked only with explicit override." Are you willing to give up the unbounded upside of a naked long call in exchange for a structurally bounded loss?
4. **Pairs-trade vs spread hedge**: do you accept the analysis in §3 — that "long call + long put on weaker name" is a pairs trade and not a hedge — and pivot to vertical spreads + portfolio-level SPY puts? Or do you want to also pursue the pairs construction as a separate alpha book?
5. **Catastrophe hedge budget**: 1–1.5% of equity / quarter is the standard. Are you OK with that drag, or do you want a smaller (or zero) tail hedge?
6. **Concurrency**: drop max-open option positions from 15 to 8? You can supervise 8 in your head. 15 you cannot.
7. **`OPTIONS_CSP_ENABLED`**: stays off? It's been off since inception. The CSP infrastructure (monitor, sweep, backtest) is complete but unused. Either commit to it or deprecate it — leaving it half-ready is dead code that confuses future debugging.
8. **Enforcement hardness for exit signals**: are you OK with the supervisor *force-closing* a position the analyzer flags at score ≥ 80, even if the position is at a small loss? This is the violation of `STRICT_NO_LOSS` you've already accepted in spirit for options ("Options Hold Strategy: Exit ONLY on D reversal" — but D reversal often *is* the exit at a loss, just framed as technical not P&L based). I want explicit confirmation before wiring it.

---

## 8. What this framework deliberately does NOT include

- **Predictive ML / model-based entry signals.** Out of scope. This framework is about not losing — alpha generation is a separate concern.
- **Crypto.** Different market, different doc, different risk model.
- **Account migration** to a different broker. Tradier only.
- **Options strategies beyond verticals + collars + tail hedge.** No iron condors, no calendars, no diagonals in v1. Once verticals work and supervisor is reliable, can revisit.

---

## 9. Success criteria

This overhaul is "done" when *all* of these are true for ≥ 30 consecutive trading days:

1. Zero per-position losses exceeding 1% of account ($700 on $70k).
2. Zero daily losses exceeding 3% of account.
3. 100% of analyzer-emitted hard exits (score ≥ 80) executed within 1 supervisor tick.
4. Portfolio beta-weighted Δ inside ±$14/$1-SPY band ≥ 90% of trading minutes.
5. Supervisor heartbeat gaps < 90s, 100% of trading hours.
6. Every executed close has an audit-trail entry in `data/options_exits/`.

If any one fails, the framework isn't done.

---

*End of framework draft. Awaiting decisions on §7 before any code is written or any config flipped.*
