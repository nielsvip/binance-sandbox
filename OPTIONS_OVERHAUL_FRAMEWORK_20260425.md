# Options Trading Overhaul Framework — 2026-04-25 (v2)

> **Trigger**: -20% portfolio week, primarily options-driven. Owner: nielsvip.
> **Status**: Framework v2 — owner decisions applied 2026-04-26. **No code changes yet.**
> **Scope**: Tradier options only (`trb`, `trc`). Crypto execution is out of scope here.

---

## v2 ADDENDUM — Owner decisions + new findings (2026-04-26)

### Decisions locked in
| # | Decision | Effect on framework |
|---|----------|---------------------|
| Q1 | Loss source = `/Users/niels/Downloads/activity.csv` (longer export coming) | See §0.1 below — confirmed losers |
| Q2 | **Kill switches use TECHNICALS, not %.** "Fall through DC bottom = kill no matter what." | Layer 1 rewritten — see §4 below |
| Q3 | YES to spreads + **re-purchase logic** when better price re-appears | Layer 3 + new §3a (re-entry policy) |
| Q4 | P/C bias derived from `tradier_manage.py:4902` equity ratio, exaggerated K=1.5, clamps 15/85. No separate per-sector P/C table. | Phase 2 in §6 |
| Q5 | SPY catastrophe hedge — go | Layer 7 stays |
| Q6 | Tighten concentration | Layer 6 stays (per-symbol 25→20%, sector 40→35%) |
| Q7 | CSP — accept deprecation | Mark dead code, archive monitor |
| Q8 | Force-close on analyzer score ≥ 80 | Layer 2 stays |
| Daily P&L breaker | **Prefer technicals over hard %.** | Account-level breaker downgraded to last-resort backstop. Per-position technical exits do the work. |
| **D5 (was open)** | **Single allowlist is sole authority. NO trade in any symbol not in `symbols_trb_long.json` (calls) or `symbols_trb_short.json` (puts). Period.** | Phase 0.7 rewritten — see §6 |
| **C (was open)** | **ABT, JNJ permanently forbidden** in addition to the allowlist enforcement | New §0.2 forbidden-symbol list; remove from any allowlist where present; assert in tests |
| **Entry quality** (new) | Options entries MUST be tied to a recognized technical setup (pullback, breakout, red-zone, DC, WT, BB, etc.). **Not just "underpriced relative to fair value."** | New §3b — entry-setup taxonomy, gates analyzer recommendations |
| **E (augment)** | If a call is in loss: **no more calls UNLESS confirmed technical bottom**. **"Way out" for losing call = buy protective put (collar) OR sell higher-strike call (convert to vertical spread)** — not adding to the loser. | L1.A1 modified — see Layer 1 below; new L3.W (way-out conversions) |

### New critical findings from 2026-04-26 audit

1. **BLACKLIST not enforced** (`config_tradier.py:95`). `BLACKLIST = ["ABT","JNJ","MSTR"]` exists but the options agent uses `_load_allowed_symbols()` against `symbols_trb_long.json` / `symbols_trb_short.json` instead. Result: **ABT and JNJ were bought as call options on 2026-04-22 despite both being blacklisted AND not present in `symbols_trb_long.json`**. There is a bypass path that needs to be found and closed. **Two parallel symbol-gate systems that disagree = guaranteed leak.**

2. **No options trade logging exists.** No `data/options_*` directory. No JSONL writers in `tradier_options_agent.py` / `tradier_options_analyzer.py` / `tradier_options_csp_monitor.py` (the CSP monitor logs ticks, but CSPs are disabled, so it logs nothing useful). **You cannot manage what you cannot see.** This becomes Phase 0 — gating everything else.

3. **PLTR averaging-up disaster** (Apr 21–23). PLTR Jul17 $150C was bought 6 times: $17.45, $17.30, $14.65, $12.52, $12.45, $12.35. That's adding contracts as the option fell ~28% over 3 days. No stop fired through any of those adds. **The framework must explicitly forbid augmenting an option position that is below its first-fill price** (or equivalently, must use technicals on the underlying to gate further buys).

4. **Per-sector P/C does NOT exist** (your assumption wrong). Only global `OPTIONS_MARKET_RATIO_MIN/MAX` (25–75% calls overall) and `OPTIONS_MAX_PER_SECTOR` ($ cap, 40%). There is no `HEALTH: 60% calls / 40% puts` style mapping. Building it is part of Phase 2 — see §6.

### 0.2 — Forbidden symbols (PERMANENT)

**Hard-forbidden symbols — NO option trade in these, ever:**
- `ABT` (rogue 2026-04-22, -$240)
- `JNJ` (rogue 2026-04-22, -$345)

These are removed from `symbols_trb_long.json` / `symbols_trb_short.json` and added to a permanent denylist that is checked even before the allowlist. Asserted in unit tests.

**Allowlist is sole authority** (owner directive 2026-04-26): no option may be traded in any symbol not present in `symbols_trb_long.json` (for calls) or `symbols_trb_short.json` (for puts). The `BLACKLIST` constant in `config_tradier.py:95` is dead code and is being removed in Phase 0.7 — single source of truth, no parallel systems.

### 0.3 — The "first loss" rule (PERMANENT, owner 2026-04-26)

> *"As soon as a call/put starts losing money you either close it or cover it with a contrary."*

This is the **meta-rule** that governs all Layer 1 / Layer 3 logic. When any option position crosses from gain to loss:

1. **Decision required this tick** — not "monitor", not "wait for confirmation". Either:
   - **CLOSE** — exit the position (preferred when technicals against, see L1.T*).
   - **COVER** — open a contrary leg (Layer 3.W "way out"): for a losing long call, either buy a protective put on the same underlying (collar) **or** sell a higher-strike call (convert to vertical debit spread, recoups some basis and caps remaining loss). Mirror for puts.
2. **Forbidden actions when losing**:
   - Hold and hope without coverage.
   - Buy more of the same direction (no averaging down) — *unless* a confirmed technical bottom hit per L1.A1 exception.
   - Wait for a generic time-based "let it work."
3. **Cover-or-close picker** (default selection logic, configurable):
   - Technicals against AND no defined-risk leg available → CLOSE.
   - Technicals neutral and contrary leg liquid (bid–ask < 10% of mid) → COVER.
   - DTE ≤ 14 → CLOSE (cover is too expensive on short-DTE).
   - Already covered (collar in place, or already a spread) → CLOSE.

This rule is enforced by the supervisor every tick. It is **not** a soft guideline.

### 0.1 — Confirmed losers from activity.csv (Apr 21–24 only — full week needs longer export)

| OCC | Description | Action | Net | Note |
|-----|-------------|--------|-----|------|
| `PYPL260618P00050000` | PYPL Jun18 $50P | 3 buys @ ~$3.53 = $1059, 3 sells @ $3.75 = $1125, then re-bought @ $3.45+$3.55+$3.58 = $1058 spent again | -$106 (current) | ATM put with chop, churning fees |
| `GLD260630C00440000` | GLD Jun30 $440C | Bought 1 @ $16.75 = $1675 | -$235 (current) | Underlying $433–435, $5–7 OTM |
| `PLTR260717C00150000` | PLTR Jul17 $150C | 6 buys avg ~$14.45 ≈ $8672 spent, 4 sells netted ~$4884 | **-$566 current on remainder** | Averaged up *and* down, no stop |
| `ABT260618C00097500` | ABT Jun18 $97.50C | 4 @ $2.15 = $860, sold same day @ $1.55 = $620 | **-$240 ROGUE — blacklisted** | Symbol on BLACKLIST, gate didn't fire |
| `JNJ260515C00240000` | JNJ May15 $240C | 8 @ $1.10 = $880, sold same day @ $0.67 = $535 | **-$345 ROGUE — blacklisted** | Symbol on BLACKLIST, gate didn't fire |
| `NEM260618C00105000` | NEM Jun18 $105C | Sold 2 @ $11.85 = $2370 | (closing leg) | NEM is in gold spread group |
| `UNG260515C00011000` | UNG May15 $11C | 1 @ $0.55, sold @ $0.32 | -$23 | < 30 DTE, lottery ticket — should not have been opened |
| `BOIL260515C00014000` | BOIL May15 $14C | 1 @ $1.11, sold @ $1.03 | -$8 | < 30 DTE, lottery ticket |

**Visible options-attributable loss in 4-day snapshot ≈ -$1.5k.** The remaining ~$12.5k of the -20% week must be either (a) earlier-week options (longer CSV export needed), (b) realized stock losses on the heavy MSTR/PLTR/NEM churn visible in the same CSV, or (c) marked-to-market losses on positions still open. **You should export the full 30-day activity range so we can complete the post-mortem.**

---

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

## 3a. Re-purchase / re-entry policy (new — owner-requested)

> *"Re-purchase of closed calls and puts if a better opportunity arrives at a good price."*

After a position closes (whether by L1 kill, L2 analyzer exit, or profit-take), the same OCC may be re-bought **only if all of**:

1. **Cooldown ≥ 2 hours** since the prior close. Prevents whipsaw / chasing.
2. **Underlying technicals favorable**: for a call, underlying close > `dc_low_D` AND `wt_cross_D` ∉ {BEAR}. For a put, underlying close < `dc_high_D` AND `wt_cross_D` ∉ {BULL}. (i.e. the technicals that *would have killed it* must not currently be flagged.)
3. **Better price**: current ask ≤ 70% of the average exit price from the prior round. ("Significantly better" — not 1% cheaper.)
4. **DTE still passes the entry gate**: ≥ 60 DTE (existing rule, unchanged).
5. **Concurrency budget allows** (L1.C1–C3 still apply).
6. **Re-entry counter ≤ 3** for this OCC across its lifetime. After 3 round-trips the OCC is permanently retired — repeated re-entries on the same strike are a sign the original thesis is broken.

State stored in `data/options_repurchase_state.json`:
```json
{"PLTR260717C00150000": {
  "round_trips": [
    {"in_ts": "...", "in_px": 12.45, "out_ts": "...", "out_px": 9.80, "out_reason": "L1.T3"},
    ...
  ],
  "last_close_ts": "...",
  "permanently_retired": false
}}
```

Re-entries log to the same `data/options_trades/<occ>.jsonl` lifecycle file as the original (Phase 0). Audit-traceable.

---

## 3b. Entry quality — required technical setup (owner 2026-04-26)

> *"The analyzer should not just find over/underpriced options but investigate which ones are actually trades we want to make (pullback, breakout, red zone, DC, WT, BB, etc.) instead of recklessly buying something nobody wants for good reasons."*

The current `tradier_options_analyzer.py` entry logic ranks candidates primarily on edge / pricing fit (delta, OTM%, DTE, score). Going forward, **a recognized technical setup on the underlying is a hard prerequisite**, regardless of how cheap the option looks. "Cheap because the market correctly priced it as decaying garbage" is exactly the JNJ/ABT pattern.

**Recognized setups** (call entry — mirror for puts):

| Setup ID | Trigger on underlying | What it expresses |
|----------|------------------------|-------------------|
| `PULLBACK_TO_SUPPORT` | Price within 1.5% of `dc_low_D` AND `wt1_D` rising AND stoch_D K < 30 turning up | Mean-reversion bounce off daily channel low |
| `BREAKOUT_DC_HIGH` | Close > `dc_high_4h` (prior 20-bar) on volume > 1.2× avg, with `dc_high_D` not yet broken | Momentum breakout, room to run |
| `RED_ZONE_BOUNCE` | Touched `dc_low_D` last 1–3 bars AND closed back inside | Confirmed support hold |
| `WT_BULL_CROSS_HTF` | wt1_D crosses up over wt2_D AND wt1_4h aligned bullish | Daily WT trigger with 4h confirmation |
| `BB_SQUEEZE_RELEASE` | BB width hit 6-month low AND price breaks BB upper | Volatility expansion in bull direction |
| `STOCH_OVERSOLD_REV` | Stoch_D K cross up from < 20 AND volume > 1.1× avg | Oversold reversal |

**Gate**: every option-buy candidate from the analyzer must be **paired with at least one recognized setup**. Setup is logged with the entry (`data/options_trades/<occ>.jsonl` action=OPEN includes `"setup": "PULLBACK_TO_SUPPORT"`). No setup match → no buy, regardless of edge.

**Why this matters for the disaster pattern**: ABT and JNJ on 2026-04-22 were bought because the analyzer found them in some "good edge" calculation. They had no recognized setup — JNJ was actually in a bearish trend. With this gate, they would have been skipped *even if the symbol-allowlist bypass had let them through*. Defense in depth.

**Implementation note**: setups are computed in `tradier_indicators.py` (data already there) and surfaced by a new helper `tradier_options_setup.detect_setups(symbol, side) -> List[SetupID]` that the analyzer calls before scoring. The analyzer's own scoring stays — it just gets a hard prerequisite gate added.

**Sweep knob**: which setups are enabled is configurable per side (calls/puts) per sector — once Phase 0 logging gives 30+ days of data, sweep which setups have positive expectancy and disable the dead ones.

---

## 4. The proposed framework

Eight layers, ranked by disaster-prevention value. Each layer should fail safely if the layer below it breaks.

### Layer 1 — KILL SWITCHES — TECHNICAL FIRST (rewritten v2)
Owner directive: kill on technicals, not arbitrary %. % is a backstop only.

**Per-position technical exits — primary** (any one fires → close immediately, regardless of P&L):

| ID | Trigger (underlying) | Long call | Long put |
|----|----------------------|-----------|----------|
| L1.T1 | Close < `dc_low_4h` | KILL | (skip) |
| L1.T2 | Close > `dc_high_4h` | (skip) | KILL |
| L1.T3 | Close < `dc_low_D` (red zone break) | **KILL — no exception** | (skip) |
| L1.T4 | Close > `dc_high_D` (red zone break) | (skip) | **KILL — no exception** |
| L1.T5 | Daily WT bear cross (wt1_D < wt2_D, was wt1>wt2) | KILL on next 3m confirm | (skip) |
| L1.T6 | Daily WT bull cross | (skip) | KILL on next 3m confirm |
| L1.T7 | Stoch_D K crosses < 80 from above (on call) | KILL if also in loss | (skip) |
| L1.T8 | Stoch_D K crosses > 20 from below (on put) | (skip) | KILL if also in loss |
| L1.T9 | DTE ≤ 5 | KILL (theta cliff, no exception) | KILL |
| L1.T10 | 21 calendar days held | KILL | KILL |

These are computed from the underlying's existing indicator pipeline (`tradier_indicators.py`) — no new data needed.

**% backstops — last resort, NOT primary** (catches the case where indicators lag a gap):
- L1.B1 — Premium loss > -75% of debit paid → emergency close. **One number, one threshold, no DTE-tier complexity.** Triggers only if technicals already failed to fire.
- L1.B2 — Single-position $-loss > 1.5% of account ($1,050 on $70k) → emergency close.

**Augment / averaging-down forbidden** (the PLTR pattern, refined per owner 2026-04-26):
- L1.A1 — **No buys to add to an existing losing call (or put) UNLESS a "definite bottom" signal fires on the underlying.** A definite bottom = ALL of:
  - Underlying touched and bounced from `dc_low_D` (calls) / `dc_high_D` (puts) — wick rejection or close back inside the channel
  - Stoch_D K crossed up from < 20 (calls) / down from > 80 (puts)
  - WT 3m bull cross (calls) / bear cross (puts) confirming on most recent bar
  - Position not already augmented in last 4 hours (no rapid stacking even on bottoms)
  
  Without all four → no add. The PLTR pattern (6 adds with no bottom signal) would have been stopped after add #1.
- L1.A2 — No buys to add if current option mid < first-fill price × 0.85 AND no L1.A1 bottom signal. (15%+ drawdown without a bottom = thesis broken, don't dollar-cost into broken theses.)
- L1.A3 — Adds to existing OCC require either L1.A1 bottom signal, the §3a re-entry policy after a clean exit, or an explicit user override flag. No silent override paths.

**Concurrency**:
- L1.C1 — Max 8 open option positions at once (down from 15).
- L1.C2 — Max 3 open positions per sector group.
- L1.C3 — Max 2 open positions per single underlying.

**Daily account loss breaker — backstop only** (per owner directive, technicals are primary):
- L1.D1 — Soft trigger: realized + MTM intra-day loss < -2.5% of equity → **block new opens** for the rest of the day, no force-flatten. Reset 13:30 UTC.
- L1.D2 — Hard trigger: < -5% of equity intra-day → **flatten all options at market**, halt new entries until manual reset. This is the "the building is on fire" switch — should rarely fire if L1.T* are working.

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

#### Layer 3.W — "Way-out" conversions for losing naked positions (owner 2026-04-26)

Implements the §0.3 "first loss = close or cover" rule for legacy naked longs that exist before Phase 2 spread-by-default ships.

When a naked long call goes negative AND the picker (§0.3 step 3) selects COVER:

| Conversion | Mechanic | When to prefer |
|------------|----------|----------------|
| **L3.W.A — Buy protective put (collar)** | Buy a put on the same underlying, ~5% OTM, same-or-later expiry | Underlying near a major support; expect bounce but want downside cap |
| **L3.W.B — Sell higher-strike call (convert to vertical debit spread)** | Sell a call 1–2 strikes above your long call, same expiry | Recoups premium; caps both upside and downside; cheapest cover |
| **L3.W.C — Roll down + out** | Close existing call, open a longer-DTE call at lower strike, smaller size | Thesis intact but timing was wrong; only with explicit override |

Mirror logic for losing puts (buy protective call OR sell lower-strike put OR roll up + out).

**Rules**:
- Cover legs count toward the same OCC position group for concurrency (L1.C1).
- Once covered, the position cannot be uncovered until close — the cover stays for the life of the original.
- Cover cost limit: ≤ 50% of the original debit. If a cover costs more than half what you paid, just close.
- L3.W is preferred to CLOSE only when DTE > 14 AND contrary leg is liquid. Otherwise CLOSE wins (per §0.3 picker).

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

Each phase is independently valuable and reversible. **Phase 0 gates everything else** — without trade logging, no later phase can be validated, debugged, or post-mortemed.

### Phase 0 — OPTIONS ADMIN / LOGGING (BLOCKER — ship before anything else)

> *"Make sure we finally get an admin of options trades like what we have for equity. If you don't know what we own how can you even know to close it."* — owner

**Today's reality**: zero structured logs. No `data/options_*` dir. No JSONL writer in the agent or analyzer. Every trade through Tradier vanishes from our records the moment it fires. This is why the post-mortem of this week is so hard.

**What to build**:

#### P0.1 — Single source of truth: `tradier_options_state.py`
A new module that owns "what options do we own right now and what is their state?" Mirrors what `tradier_positions.py` does for equity. Responsibilities:
- Poll Tradier `/v1/accounts/{id}/positions` every 30s (configurable).
- Reconcile against last-known internal state. Detect new fills (no entry seen) → log as `RECONCILE_NEW`. Detect missing positions (had it, gone) → log as `RECONCILE_CLOSED`.
- Compute and stamp Greeks (delta/gamma/theta/vega via existing BS in `tradier_options_analyzer.py:82`).
- Compute and stamp underlying technicals (dc_high_D/dc_low_D/wt_cross_D/stoch_D from `tradier_indicators.py`).
- Publish current snapshot to `data/options_state/current.json` atomically (write-tmp + rename).
- Single Redis key `tradier:options:state` with TTL 90s as fast-read mirror.

#### P0.2 — Per-tick snapshot log: `data/options_state/<YYYYMMDD>.jsonl`
Every poll tick appends one line:
```json
{"ts":"2026-04-26T13:35:00Z","positions":[{"occ":"PYPL260618P00050000","qty":3,"avg_cost":3.53,"mid":3.45,"pnl":-24,"pnl_pct":-2.3,"dte":53,"delta":-0.42,"theta":-0.04,"underlying":49.88,"dc_low_D":48.5,"dc_high_D":52.1,"wt_cross_D":"NEUTRAL","stoch_D_K":52,"flags":[]}],"portfolio":{"net_delta":12.3,"net_theta":-15.4,"total_premium":4830,"daily_pnl":-186}}
```
This is what the supervisor reads. This is what the post-mortem reads. This is what the morning email reads. **Single source of truth for all downstream consumers.**

#### P0.3 — Per-OCC lifecycle log: `data/options_trades/<occ>.jsonl`
One file per OCC, append-only, full life cycle:
```json
{"ts":"...","action":"OPEN","qty":3,"price":3.53,"reason":"daily_cycle_recommendation","analyzer_score":82}
{"ts":"...","action":"AUGMENT","qty":1,"price":3.45,"reason":"daily_cycle_recommendation"}
{"ts":"...","action":"CLOSE","qty":-3,"price":3.75,"reason":"L2.3_WT_D_REVERSAL","pnl":66}
{"ts":"...","action":"REOPEN","qty":1,"price":2.40,"reason":"3a_repurchase_round_2"}
```
Powered by hooks in `tradier_options_agent.execute_decisions` and `tradier_manage.py`'s closing paths. No order can fire without writing this entry first (write-then-fire pattern).

#### P0.4 — Daily roll-up: `data/options_daily/<YYYYMMDD>.json`
End-of-day summary:
- Open positions count, total premium, net Greeks, daily MTM P&L.
- Trades-today list (opens, closes, reopens), realized P&L per OCC.
- Sector breakdown: $ deployed, P/C ratio per sector.
- Kill-switch fires (count by L1.T1–T10, L1.B*, L1.A*, L1.D*) — proves the safety net works.
- Re-entries (count, success rate).
- Appended to morning email next day.

#### P0.5 — Backfill from `activity.csv`
Build a one-shot importer `backfill_options_history.py` that ingests `/Users/niels/Downloads/activity.csv` (or a longer Tradier history export) and produces:
- One `data/options_trades/<occ>.jsonl` file per option seen.
- One `data/options_daily/<date>.json` per trading day.
- A starting `data/options_state/current.json` matching today's broker positions.

This gives us a populated history from day 1 instead of waiting weeks for organic data.

#### P0.6 — UI (read-only) — extends existing `:5050` analytics feed
- New `/options` route showing current positions table (OCC, qty, cost, mid, P&L, DTE, Greeks, underlying technicals, kill-switch flags armed).
- New `/options/<occ>` showing the lifecycle log for one OCC.
- New `/options/daily` showing daily roll-ups.
- Same Flask app as equity — minimal new infra.

#### P0.7 — Single symbol gate (owner directive 2026-04-26: "extremely simple")

**Rule, no exceptions, no tiers**:
> **`symbols_trb_long.json` is the SOLE allowlist for call buys. `symbols_trb_short.json` is the SOLE allowlist for put buys. No symbol outside these JSONs may have an option order placed against it, ever, by any code path.**

Implementation:
1. **Add a permanent denylist** `OPTIONS_DENYLIST = {"ABT", "JNJ"}` in `config_tradier.py` — checked *before* the allowlist as belt-and-suspenders. Even if a symbol gets re-added to the JSON by mistake, the denylist still blocks it.
2. **Centralize the gate** in one helper: `tradier_options_gate.is_allowed(symbol, side) -> (bool, reason)`. Returns False with a reason string for any of: in denylist, not in long JSON (for calls), not in short JSON (for puts).
3. **Audit every call site** that can place an option order. Replace any local allowlist check with the central helper. Greps to run:
   - `grep -nE "place_order|create_order|submit.*option" tradier_*.py`
   - `grep -nE "_load_allowed_symbols|symbols_trb_" tradier_*.py`
   - `grep -nE "BUY|SELL.*option" tradier_options_agent.py tradier_options_analyzer.py tradier_manage.py`
4. **Delete `BLACKLIST = ["ABT","JNJ","MSTR"]`** from `config_tradier.py:95`. It's dead. Two systems = the bug.
5. **Refusal log**: every gate refusal writes to `data/options_gate_refusals.jsonl` with `{ts, symbol, side, source_path, reason}`. If we ever see a refused symbol in `data/options_trades/`, we have proof a bypass exists.
6. **Remove ABT and JNJ from `symbols_trb_long.json` / `symbols_trb_short.json`** if present (verified: ABT/JNJ NOT currently in long JSON, but removing from any list confirms safety).
7. **Test**: a unit test attempts to call the entry path with `symbol="ABT"` and `symbol="RANDOMTICKER"` — both must return blocked. Test runs in CI / pre-commit.

**The bypass investigation (was Q-C) is now folded into step 3** — auditing every call site IS finding the bypass. Both the *fix* and the *forensic discovery* happen in the same pass.

**Phase 0 acceptance criteria**:
- Every existing open option position appears in `current.json` within 60s of the supervisor starting.
- Every close fired by any path produces a `data/options_trades/<occ>.jsonl` line within 5s.
- One full week of `data/options_daily/` files exists.
- ABT and JNJ can no longer be bought via any code path (verified by intentional test calls in dry-run).

**No subsequent phase ships until Phase 0 is green.**

### Phase 1 — Stop the bleed (1 day after Phase 0)
- Wire L1.T1–T10 (technical kill switches) into the supervisor — ride on the data the supervisor already polls.
- Wire L1.B1–B2 (% backstops) into the same loop.
- Wire L1.A1–A3 (no-augment-into-loss) into `tradier_options_agent.execute_decisions` and any other entry path.
- Wire L1.C1–C3 (concurrency caps).
- Re-enable `OPTIONS_MAX_LOSS_GUARD_ENABLED` as the L1.B1 backstop (single -75% threshold, not the old DTE tiers).
- Make `tradier_manage.py` *enforce* analyzer exit signals at score ≥ 80 (L2.1, L2.2).
- Backtest on `backtest_v8_engine.py` against the past 30 days of Tradier history (use Phase 0 backfilled data) before flipping live.

### Phase 2 — Defined-risk + sector P/C budgets (2–4 days)
- Wire `OPTIONS_SPREAD_ENABLED=True` through the entry path so default = vertical debit spread (L3).
- **P/C bias derived from existing equity sentiment ratio, with exaggeration** (owner directive 2026-04-26):
  - Source: `tradier_manage.py:4902` already computes `_target_long_pct = max(10, min(90, 50 + composite × 0.9))` where composite = `(universe_score + qqq_momentum + A/D)/3`. This is the global equity long-bias target the system uses for stocks.
  - Options derivation: `options_call_pct = clamp(15, 85, 50 + (equity_target_long_pct − 50) × K)` with **K = 1.5 default**.
  - Examples (with K=1.5):

    | composite | equity target | options call% | put% |
    |-----------|---------------|---------------|------|
    | 0 (neutral) | 50% | 50% | 50% |
    | +20 (mod bull) | 68% | 77% | 23% |
    | +40 (strong bull) | 86% | 85% (clamped) | 15% |
    | -20 (mod bear) | 32% | 23% | 77% |
    | -50 (strong bear) | 5% (clamp 10) | 10% (after 15 floor → 15%) | 85% |

  - **Why exaggerated** (owner rationale): options come in discrete contract chunks ($500–$1.5k each), and we only enter at oversold/overbought price points → fewer entry windows than equity. Each entry must express the directional view more strongly, because we can't fine-tune like with single shares of stock.
  - **Why clamps at 15/85, not 0/100**: never fully naked-directional — always keep at least one hedge contract on the book. Catches the "regime flips overnight" case.
  - **Per-sector P/C is NOT separately maintained**. Only per-sector $ caps (already exist: `OPTIONS_MAX_PER_SECTOR=0.40` etc.) + this single global P/C target. Sectors absorb the global bias proportionally to their own $ allocation. Simpler, fewer knobs to drift.
  - **Granularity reality check**: with ~$6k options budget and ~$700/contract average, that's 8–9 contracts max. 75/25 = 6C/2P. 85/15 = 7C/1P. Rarely will the math need finer than that.
  - **K is a sweep knob**, not a permanent constant. Default 1.5; backtest 1.0/1.25/1.5/2.0 once Phase 0 + Phase 1 logging gives us enough data to evaluate it (~30–60 days post-Phase-1).
- IV-rank gate on entries (L5.1).
- VIX regime gate (L5.2).
- Re-purchase / re-entry policy (§3a) — this is conceptually part of Phase 1 + Phase 2 — wire after spreads are live.

### Phase 3 — Supervisor agent (3–5 days)
- `tradier_options_supervisor.py` reads from Phase 0's `current.json` and fires the kill switches from Phase 1.
- Replace `options_watchdog_runner.sh` schedule to launch it (13:30–20:00 UTC weekdays).
- Run alongside existing watchdog for one week, dry-run only (logs decisions without firing). Compare against actual outcomes. Then enable live actions.

### Phase 4 — Portfolio Greeks budget (1 week)
- Aggregate Greeks (already computed and logged in Phase 0) get bands + breach actions (L4.2, L4.3).
- Greeks added to morning + evening email (L4.4).

### Phase 5 — Catastrophe hedge (slow, deliberate)
- Buy first SPY put tranche after Phase 1+2 are stable (L7).
- Quarterly roll cadence.

### Phase 6 — Concentration tighten (any time after Phase 1)
- Drop per-symbol cap to 20%, per-sector to 35% (L6).
- Tighten directional skew bound to 30%.

---

## 7. Decisions status (updated 2026-04-26)

**Locked in** (will be implemented as specified):
1. ✅ Loss source — `activity.csv` (4-day snapshot only; longer export still wanted to complete post-mortem).
2. ✅ Kill switches use **technicals** (DC/D-zone breaks, WT crosses, stoch reversals). % is backstop only at -75% premium / -1.5% account.
3. ✅ Default to vertical debit spreads. Naked longs only with explicit override.
4. ✅ Pivot from "weaker-sector pairs" to vertical spreads + SPY tail puts.
5. ✅ SPY catastrophe hedge — go.
6. ✅ Concurrency: 8 max positions, 3/sector, 2/symbol.
7. ✅ CSPs deprecated — monitor will be archived, code marked dead.
8. ✅ Force-close on analyzer score ≥ 80 — confirmed.
9. ✅ Account-level breaker downgraded to backstop (-2.5% soft / -5% hard). Per-position technical exits do the work.

**All resolved 2026-04-26**:
- ✅ A. Longer activity export — owner will provide. Phase 0.5 backfill runs against it when it lands.
- ✅ B. P/C bias source — derive from `tradier_manage.py:4902` equity target with exaggeration K=1.5 (clamps 15/85). No separate per-sector P/C table. See Phase 2.
- ✅ C. ABT/JNJ bypass — folded into D5/Phase 0.7 audit (auditing every call site IS finding the bypass). ABT and JNJ now permanently in `OPTIONS_DENYLIST`.
- ✅ D5 (sole authority): `symbols_trb_long.json` / `symbols_trb_short.json` are the only allowlists. `BLACKLIST` deleted. Centralized gate helper. Refusal log. Unit test in CI.
- ✅ E. L1.A1 — no augment to losing call/put **unless** all four "definite bottom" signals fire (DC zone bounce, stoch reversal, WT confirm, 4h cooldown). When losing AND no bottom: choice is CLOSE or COVER (Layer 3.W) — never hold-and-hope (per §0.3 first-loss rule).
- ✅ Entry quality (new): every option buy must match a recognized technical setup (PULLBACK, BREAKOUT, RED_ZONE_BOUNCE, WT_BULL_CROSS_HTF, BB_SQUEEZE_RELEASE, STOCH_OVERSOLD_REV). No setup → no buy. See §3b.
- ✅ "First loss" meta-rule: position goes negative → decide CLOSE or COVER this tick. No third option. See §0.3.

**Phase 0 substep order (locked)**:
1. **P0.7 / D5 first** — JSON-allowlist sole authority + denylist + central gate + refusal log. Closes the rogue-trade hole. ~1 day.
2. **P0.1 + P0.2 in parallel** — state module + per-tick snapshot log. Gives "what do we own". ~1 day.
3. **P0.3** — per-OCC lifecycle log. Hooks into agent + manage close paths. ~0.5 day.
4. **P0.5** — backfill from activity export, *after* you provide the longer CSV.
5. **P0.6** — read-only UI route on existing :5050. ~0.5 day.

Then Phase 1 starts (kill switches + analyzer-as-enforced + L3.W way-out conversions).

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
