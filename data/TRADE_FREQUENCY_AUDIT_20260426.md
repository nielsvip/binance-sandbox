# Trade Frequency Audit — 2026-04-26

**Goal**: identify choke points in `ez_manage.py` and `tradier_manage.py` that are throttling trade frequency. User wants 2000× more trades per account. PROPOSAL ONLY — nothing applied. Per CLAUDE.md: no live changes without backtest proof or explicit approval.

---

## 1. Per-Account Funnel (decisions JSONL, today UTC)

Decision JSONL only logs **accepted** actions (executed trades). It does NOT log most rejections — those land only in `~/logs/ez_manage___account_<acct>_cron.log` and `~/logs/tradier_manage_<acct>.log`.

| acct | total decisions | OPEN | AUG | CLOSE | REDUCE | other |
|------|---:|---:|---:|---:|---:|---:|
| ang  |  979 | 265 | 1 | 347 | 366 | 0 |
| fin  |  467 | 100 | 0 | 208 | 159 | 0 |
| flz  |   88 |   0 | 0 |  50 |  38 | 0 |
| inf  | 3231 | 362 | 1 |1625 |1244 | 0 |
| men  | 1877 | 103 | 1 | 909 | 864 | 0 |
| tra  |   12 |   0 | 0 |   0 |   0 | 12 (WAIT) |
| trb  |  626 |   0 | 0 | 421 |   0 | 148 (BUDGET) + 57 WAIT |
| trc  |   74 |   0 | 0 |  74 |   0 | 0 |

**Reality check**: today's "OPEN" count is dominated by **hedge opens** (most ez_manage OPENs are `QUICK_HEDGE_OPEN`). Real new directional opens are far fewer. Tradier accounts trb/trc/tra logged **zero opens today** — every entry attempt died at the gates above.

### Top blockers (live cron logs, today UTC, raw counts)

**inf (cron log)**

| count | gate token |
|---:|---|
| 5,667 | BLOCKED (generic — most are entry-side veto) |
| 2,071 | GATE_STRICT (HTF/LTF entry requirement chain) |
| 1,578 | VETO_EC (`HEDGE_DISCOVERY_TK_VETO_EC` — false alarm, hedge-cleanup noise; **not an entry block**) |
| 725  | REJECTED |
| 377  | BLOCKED_UNCALCULATED_STOCH (data not ready for the symbol on this scan) |
| 197  | GATE_ELEVATED (AGE_GATE elevated reentry confirmation) |
| 68   | BLOCKED_NON_TRADEABLE_POSITION_KEY |
| 37   | BLOCKED_BY_UNIVERSAL_NOLOSS_GATE (exits, not entries) |
| 18   | BLOCKED_HARD_REDUCE_LOCK (15 s reduce cooldown) |

**ang (cron log)**

| count | gate token |
|---:|---|
| 4,910 | BLOCKED |
| 1,499 | VETO_EC (noise) |
| 1,305 | BLOCK |
| 1,157 | REJECTED |
| 308  | BLOCKED_NON_TRADEABLE_POSITION_KEY |
| 177  | BLOCKED_HEDGE_WT3M_ (hedge against WT 3m direction — correct) |
| 172  | BLOCKED_NOT_ALLOWED_LOSS_AUGMENT_ |
| 154  | BLOCK_EARLY |

**fin (cron log)**

| count | gate token |
|---:|---|
| 49,971 | BLOCKED (overwhelmingly NOLOSS exits — STRICT_NO_LOSS by design, do NOT loosen) |
| 1,944 | VETO_EC (noise) |
| 350  | BLOCKED_BY_UNIVERSAL_NOLOSS_GATE |
| 192  | BLOCKED_UNCALCULATED_STOCH |
| 152  | BLOCKED_NON_TRADEABLE_POSITION_KEY |
| ~180 | BLOCKED_HARD_REDUCE_LOCK_* (reduce cooldown chain) |
| 21   | BLOCKED_LOW_GAIN_DRAIN_PROTECTION |

**men (cron log)**

| count | gate token |
|---:|---|
| 7,073 | BLOCKED |
| 1,628 | VETO_EC (noise) |
| 614  | REJECTED |
| 479  | BLOCKED_NOLOSS |
| 135  | BLOCKED_UNCALCULATED_STOCH |
| 25   | BLOCKED_ENTRY_VET_NO_TRIGGER |
| ~100 | BLOCKED_HEDGE_WT3M_* |

**trb (live log, today)**

| count | gate token |
|---:|---|
| 710  | "💰 SWING BUDGET BLOCKED" — **#1 throttle, capital-side, not signal-side** |
| 986  | BLOCK (subset of above + REBAL_*_BLOCK) |
| 89   | SKIP |
| 0    | OPEN today |

**trc (live log, today)**

| count | gate token |
|---:|---|
| 2,106 | "Not in approved list" (`is_symbol_tradeable` filter — symbols not in `symbols_trc_long.json`/`_short.json`) |
| ~100 | REBAL_NOLOSS_BLOCK |
| 0    | OPEN today |

---

## 2. Top 10 Throttle Parameters

Ranked by estimated impact on net entry rate. **Do not flip any of these without backtest proof.**

| # | Param (file:line) | Current | Proposed for sweep | Expected entry-rate multiplier | Risk |
|---|---|---|---|---:|---|
| 1 | `SWING_LONG_BUDGET` / `SWING_SHORT_BUDGET` (config_tradier.py:50–51) | 100,000 base, ratio-shrunk to ~$2.4–24k effective in current bearish regime → **100% of trb opens are starving** | Floor effective at 25 % of `account_value` regardless of ratio (clamp `effective = max(effective, account_value*0.25)`); OR raise base to 200,000 | **5–20× trb opens** (currently 0/day) | More LONG exposure during bear; mitigated by per-symbol % cap and STRICT_NO_LOSS already active |
| 2 | `is_symbol_tradeable` allowlist for trc — `symbols_trc_long.json` (53 sym) + `symbols_trc_short.json` (51 sym) | ~104 names but 2,106 daily filter rejections → ranking surfaces lots of names not on the list | Refresh trc lists from current 5 m / 15 m breakout scanner output; consider mirroring trb's broader 116-name pool | **2–5× trc opens** | New names need spread/liquidity check; risk of chasing illiquid stocks |
| 3 | `TRADIER_ENTRY_SCORE_THRESHOLD` (config_tradier.py:1216) | **30** (raised from 24 on 2026-04-23 emergency) | Sweep `{20, 22, 24, 26, 28, 30}` × {pool_sharpe, accumulated_gain, dd_pct} on 114 sym × 1 yr (Tier 2) | **2–4× tradier opens** (24→30 cut entries by ~50 % per the 2026-04-23 commit note) | Lower score → noisier entries; STRICT_NO_LOSS+LS ratio absorbs |
| 4 | `symbols_inf_long.json` (4 names) and `symbols_fin.json` (84) — universe asymmetry | inf-long: BNB/BTC/ETH/SOL only (4); inf-short: 28; fin: 84 | Add top 10 USDC perps by 24 h volume not currently in fin: `1000PEPEUSDC`, `WIFUSDC`, `JUPUSDC`, `LDOUSDC`, `OPUSDC`, `INJUSDC`, `LINKUSDC`, `MATICUSDC`, `RENDERUSDC`, `TIAUSDC` (verify on Binance API). For inf-long, copy at least 6 mid-cap USDC perps to balance the 28 short names | **+15–30 inf entries/day**, **+5–10 fin/day** | Symbols.json count must match positions exactly per CLAUDE.md — add via add_new_symbols.py only |
| 5 | `_DUPLICATE_REDUCE_COOLDOWN` (ez_manage.py:275) | **15 s** | 5 s for hedge ops (already partly bypassed for V3); keep 15 s for non-hedge | **+3–10 % exit/reentry chains** completed | Risk of double-fire; existing v3 urgent bypass shows 5 s safe |
| 6 | `BLOCKED_NEWBORN_PROTECT` (ez_manage.py:13338) | 900 s (15 min) blanket lockout on closes | Tier by side: 300 s for SHORT in bull regime + LONG in bear; keep 900 s otherwise. Already DC-3m-break override exists | **+5–15 % effective close→reopen turnover** | Newborn protect was added to stop premature exits — needs sweep validation |
| 7 | `MITIGATOR_REENTRY_COOLDOWN` (config_tradier.py:1596) — flagged DEAD_CONFIRMED | 180 s, **never read** | WIRE this constant into the reentry skip-filter to enable a measurable cooldown sweep | Indeterminate until wired | Adding wiring is a code change — not config-only |
| 8 | `SCALP_V3_MAX_CONCURRENT` (config.py:119) and `SCALP_V3_POSITION_CAP_USD` (config.py:120) | 8 × $20 = $160 max V3 capital on inf | Raise to 15 × $20 if SHORT_ONLY paper PnL stays > 0 over the next 7 d (already SHORT_ONLY per memory note) | **+50–100 % V3 fires/day on inf** | $160→$300 risk per account — bound it |
| 9 | `HEDGE_STRICT_WT_ALL_TFS_ENABLED=True` + `HEDGE_STRICT_WT_MIN_TFS_AGAINST=4` (config.py:499–500) | 4-of-5 WT TFs required to close hedge | Sweep `{3,4,5}` — earlier hedge close = capital recycled to new entries | **+10–20 % hedge turnover → +5–10 % new entries** | Memory says the 4-TF strict gate is INTENTIONAL after losing-hedge incident — DO NOT relax without sweep evidence |
| 10 | Loop cadence — `SLEEP_TIME_PROC_ACCT=5 s` per account (config.py:2047), `OPEN_INTERVAL=30 s`, `CANDIDATE_INTERVAL=90 s` (tradier_manage.py:1965–66) | crypto: ~12×/min/symbol; tradier open: 2×/min, candidates: 0.67×/min | crypto: leave at 5 s (already aggressive). Tradier: drop OPEN_INTERVAL to 15 s **only after** signal-side throttles 1–4 are addressed (otherwise faster scan = more "BLOCKED" log spam, no extra trades) | crypto: 0× (cadence isn't the bottleneck); tradier: +1.5–2× IF signal pipeline is fed | Faster scans add CPU/API load; current tradier API fanout is already at 30 s × 114 syms |

---

## 3. Symbol Universe Audit

| Account | symbols.json file | n | USDC | USDT | Notes |
|---|---|---:|---:|---:|---|
| ang | symbols_ang_long/short.json | 20 + 20 | 3+7 | 17+13 | Modestly diversified |
| fin | symbols_fin.json | 84 | 25 | 59 | Largest crypto pool |
| flz | symbols_flz.json | **5** | 4 | 1 | **Tiny** — by design (low-balance scalp) |
| inf | symbols_inf_long/short.json | **4** + 28 | 0+8 | 4+20 | **inf-long is 4 names** — major bottleneck for long-side fires |
| men | symbols_men.json | 77 | 20 | 57 | Healthy |
| trb | symbols_trb_long/short.json | 63 + 53 = 116 | n/a | n/a | Healthy stock universe |
| trc | symbols_trc_long/short.json | 53 + 51 = 104 | n/a | n/a | 2,106 daily "Not in approved list" rejects → universe smaller than what scanner surfaces |
| tra | symbols_tra_long/short.json + satoshit | 56 + 51 | n/a | n/a | 0 trades today (paper / cash account) |

**Sibling agent E note (referenced in prompt)**: 25 USDC perps "missing". From symbols.json (196 syms, 31 USDC), if Binance USDC perp universe is 38, the missing set is ~7. Concrete add proposal for **fin** (top-10 by 24 h volume — verify on Binance API before adding):
`1000PEPEUSDC, WIFUSDC, JUPUSDC, LDOUSDC, OPUSDC, INJUSDC, LINKUSDC, MATICUSDC, RENDERUSDC, TIAUSDC`. Estimated incremental opens/day, scaled from inf's hit rate per symbol (362 hedge-opens / 28 short syms ≈ 13/day/sym, real entries ~2–3/day/sym): **+10–20 fin opens/day** if added.

For **inf-long** (4 names): mirror its short universe to long. Adding 6 names that already trade as `_SHORT` would balance LS ratio capacity and create immediate long-opening capacity.

---

## 4. Cadence Audit

**Crypto** (`ez_manage.process_symbols_periodically`, line 21408–21421)
- Loop runs `process_all_symbols_for_account` every `SLEEP_TIME_PROC_ACCT = 5 s`.
- Inside the call, every position in the account is processed once per loop.
- Effective rate: **~12 scans/min/symbol** for held positions.
- For **non-held candidates** (entry path): scanned via `LEADERBOARD_ENTRY` ranking on `MARKET_DATA_REFRESH_INTERVAL_SECONDS = 45 s` cadence — **~1.3×/min/candidate**.

**Tradier** (`tradier_manage.py:1965–2017`)
- `OPEN_INTERVAL = 30 s` for held positions.
- `CANDIDATE_INTERVAL = 90 s` for non-held (informational; the actual loop is 30 s but only re-fetches candidates every 90 s).
- Effective rate for entries: **~0.67 scans/min/candidate** for stocks.

**Honest verdict**: cadence is NOT the binding constraint for crypto (12/min already). For tradier, 0.67/min is slow but **the immediate problem is that the scans are returning capacity-blocked or filter-blocked candidates 99 % of the time** — speeding up the loop without first widening the budget/allowlist will just generate more BLOCKED log lines, not more trades.

To safely hit `6×/min` (10 s candidate scan) on tradier:
1. Confirm Tradier API quota headroom (current 114 syms × 1/30 s = 3.8 req/s sustained on the account; 6×/min = 11.4 req/s — likely OK but verify against Tradier rate limits).
2. Move candidate-rank computation off the request thread (it's already async).
3. Pre-filter candidates inside the loop so SWING BUDGET BLOCKED cases skip the score recompute.

---

## 5. Honest Max Sustainable Trade Rate Per Account

Given the **current** architecture (no code changes, only proposed config tunings), realistic **per-day** ceilings:

| Account | Today | Realistic with proposals 1–4 (no engine changes) | Hard ceiling (engine + capital + spread) |
|---|---:|---:|---:|
| inf | ~360 hedge-opens, ~30 real new opens | 60–100 real opens | ~200/day (V3 latency + Binance order quota) |
| ang | ~265 (mostly hedge) / ~40 real | 50–80 real | ~150/day |
| fin | ~100 (mostly hedge) / ~15 real | 30–60 real | ~120/day |
| men | ~100 / ~15 real | 30–50 real | ~120/day |
| flz | 0 today | 5–15 (after symbol expansion) | ~30/day |
| trb | **0 today** | 20–40 | ~80/day (constrained by Tradier order-rate + spread) |
| trc | **0 today** | 15–30 | ~60/day |
| tra | 0 (cash/paper) | unchanged unless funded | n/a |

**2000× target is not reachable** with the current architecture: that would imply ~20 k/day per crypto account, which exceeds Binance per-account order-rate caps (`MAX_CONCURRENT_ORDERS = 186`, plus exchange-side weight limits). The realistic ceiling is **5–20× current**, not 2000×, without:
- A new high-frequency scalp variant (V3-class but with maker-only execution + multi-symbol parallelism)
- Per-symbol capital splitting to raise concurrent count without per-trade-size bloat
- Co-located order routing to remove latency tax

---

## 6. Top 3 Throttles by Impact (TL;DR for caller)

1. **`SWING_LONG_BUDGET` / `SWING_SHORT_BUDGET` ratio-shrink → trb has $0 entry capacity in bearish regime** (710 BLOCKED today, 0 opens). Floor at `account_value × 0.25` per side or raise base to 200k. Likely 5–20× trb impact.
2. **`TRADIER_ENTRY_SCORE_THRESHOLD = 30`** is the post-emergency setting; the 2.8155 winner used 30 but a sweep across `{20…30}` on 114 sym × 1 yr will quantify the tradeoff. Likely 2–4× tradier opens.
3. **`symbols_inf_long.json` has only 4 names**, and trc allowlist drops 2,106 candidates/day. Both are universe-side, fixable by adding ~6–10 high-volume names per asymmetric account. Likely +20–40 opens/day combined.

## Top 3 Risks if Loosened Without Care

1. **Lower `TRADIER_ENTRY_SCORE_THRESHOLD`** floods entries with low-edge setups → STRICT_NO_LOSS forces holding losers → drawdown balloons (this is exactly what the 2026-04-23 emergency raise was protecting against). Sweep first.
2. **Adding USDC pairs without volume/spread vetting** opens illiquid traps on inf/fin. Use Binance 24 h `quoteVolume > $50M` floor and verify spread < 0.05 % at each add.
3. **Floor SWING_BUDGET without per-symbol % cap** could concentrate trb in 2–3 mega-cap names during a drawdown. Pair with `OPTIONS_MAX_PER_SYMBOL` style cap for stocks.

---

## File Path

`/Users/niels/Documents/binance/data/TRADE_FREQUENCY_AUDIT_20260426.md`

## NEXT STEPS (none auto-applied)

1. Run a Tier-2 sweep (`backtest_v8_engine.py` on 114 syms × 1 yr) over `TRADIER_ENTRY_SCORE_THRESHOLD ∈ {20, 22, 24, 26, 28, 30}` × `SWING_LONG_BUDGET ∈ {50k, 100k, 200k}` × `SWING_SHORT_BUDGET ∈ {50k, 100k, 200k}`. Report the standard 5-metric set (`pool_sharpe / sym_sharpe / avg_gain_trade / gain_per_yr / gain_sym_yr`) with `max_dd_pct` per CLAUDE.md §5.
2. Audit the Tradier `is_symbol_tradeable` filter — produce a list of candidates that would have passed today's ranking but failed the allowlist; user picks which to admit.
3. Wire `MITIGATOR_REENTRY_COOLDOWN` (currently DEAD per config_tradier.py:1596) so it can be swept rather than silently bypassed.
4. (Bug) Fix `[ENTRY_CHECK_FAIL] ... name 'indicators' is not defined` in `ez_manage.py` (multiple symbols today) — this is a real Python NameError silently dropping entry attempts on `GRIFFAINUSDT`, `IOUSDT`, `CRVUSDC` and others.
