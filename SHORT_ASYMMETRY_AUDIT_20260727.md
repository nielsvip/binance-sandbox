# SHORT asymmetry audit — 2026-07-27

## Outcome

The poor SHORT book is not explained by one inverted oscillator. The central
structural defect is that two different trades are routed through one policy:

1. trend-continuation shorts in an established bear regime; and
2. short, violent corrections in overbought LONG-universe names.

The first has mostly correct side-specific metric comparisons. The second has
a real signal generator, `TOP_REJECTION_SHORT`, but the common entry guard
rejects exactly the market state that creates it. Several reporting tools also
made invalid SHORT/B&H ratios. No live switch or per-symbol setting was changed
by this audit.

## Config → signal → guard → engine inventory

| family | intended SHORT behavior | classification | evidence / consequence |
|---|---|---|---|
| WT/DC entry scorer | D+4h WT bearish, fresh 1h bear cross, high 5m Stoch, upper DC position | correctly side-specific, but bear-continuation only | `wt_dc_entry_scorer.py:107-145`; it is not a correction-short entry |
| WT/DC exit scorer | cover on 1h bull cross + bullish 4h/D + oversold Stoch/DC | correctly side-specific | `wt_dc_exit_scorer.py:75-107` |
| Delta TOP rejection | arm near overbought top, require negative 1h/5m velocity and high 5m Stoch | correctly asymmetric correction signal | `wt_dc_delta.py:1081-1111`; requests a temporary SHORT key |
| Delta breakdown / bottom cover | add on actual downside impulse; cover at an exhausted bottom/bounce | correctly SHORT-specific in concept | `wt_dc_delta.py:1118-1160` |
| guaranteed re-entry | SHORT reclaim is price at/below prior cover, with high/falling Stoch and bearish WT | correctly side-specific | `tradier_manage.py:3338-3367`, `8844-9055` |
| R3 / Rule-B / structural exits | SHORT cover mirror is higher-high+higher-low / bullish WT | correctly side-specific | `tradier_manage.py:2604-2670`, `8013-8030` |
| LONG band/arrow entry | explicitly `is_long` only; optional global LONG-only switch | not a SHORT path | `tradier_manage.py:3292`, `3435-3535`; must not be counted as SHORT coverage |
| disaster daily-return guard | refuses SHORT if the ticker is up 2.5% that day | wrong for correction book, reasonable for blind bear continuation | `config_tradier.py:2548`, `tradier_manage.py:11639-11643` |
| disaster RSI guard | refuses SHORT when 15m or 1h RSI is at least 65 | direct conflict with overbought-entry logic | `config_tradier.py:2553/2555`, `tradier_manage.py:11685-11690`; it blocks the desired RSI 65–85 arm |
| disaster D/4h candle guard | refuses SHORT during bullish D/4h unless below 15m SMA200 | direct conflict with countertrend corrections in secular winners | `tradier_manage.py:11661-11680` |
| disaster HTF confirmation | every SHORT needs bearish D or 4h, then bullish daily WT is also refused | makes TOP rejection effectively unreachable in a healthy uptrend | `tradier_manage.py:11770-11786` |
| result ratio | some tools used `abs(long B&H)`, divided by negative short B&H, or used `entry/exit-1` | proven reporting defects | fixed in this audit; details below |

The correction path therefore has this contradiction:

`overbought/rallying LONG name → TOP_REJECTION_SHORT signal → general SHORT guard sees rally/RSI/bullish HTF → reject`.

That is not a parameter-range problem. A dedicated, path-scoped correction
permission is required eventually, but only after exact evidence. It must bypass
the five bear-continuation vetoes for a named `TOP_REJECTION` setup, not weaken
the safety guard for every SHORT.

## Benchmark and ledger corrections

For a fixed initial short notional, side-specific short B&H is
`(entry_price - end_price) / entry_price`. It is not `entry/end - 1`. If this
return is zero or negative, a B&H multiple is undefined and cash at 0% is the
opportunity floor.

The following research/reporting defects were corrected:

- `tools/reopt_loop.py`: a strategy losing less than negative short B&H can no
  longer become `beats_bh=True`; it must beat cash when short B&H is nonpositive.
- `tools/capture_analysis.py`: stores long B&H, short B&H, cash and opportunity
  benchmark separately; emits no multiple for nonpositive short B&H.
- `tools/pilot_range_finder.py` and `tools/per_key_priority_sweep.py`: removed
  `abs(long B&H)` SHORT ratios.
- `tools/switch_ladder_lab.py`: no ratio for nonpositive side B&H.
- `tools/htf_baseline_2x2.py` and `tools/persym_htf_validation.py`: replaced the
  reciprocal-price pseudo-return with fixed-notional SHORT P&L.

Focused benchmark tests cover a declining stock, a bull-market SHORT loss, a
bull-market profitable correction short, and unchanged LONG behavior.

## First bounded causal campaign

`tools/vec_asymmetric_short_campaign.py` is research-only. It does not invert a
LONG position mask. It uses completed 1h/4h/D data and next-availability-batch
fills, separates correction and bear books, includes one-way commission and
slippage, and caps the strategy at $16,000 with a $2,000 base unit.

The correction book patiently arms on completed 4h/D overbought context, waits
for a completed-1h bearish WT/price rollover, starts small, scales only after
additional ATR downside with WT still bearish, takes ATR/trailing/oversold
profits quickly, and has a bounded ATR emergency cover. The bear book requires
completed D+4h bear context before a rally-failure entry.

Selection uses only D1 (2024-03-26→2025-07-01) and D2
(2025-07-01→2026-01-01). The 2026-01-01→2026-07-25 fold remains untouched
until one setting is frozen per symbol. Every report contains raw strategy
return, realized cash return, open MTM, cash, short B&H, long B&H opportunity,
drawdown, time in market, trades and correction capture. A multiple is present
only when short B&H is positive.

| book | key | untouched strategy | short B&H | long B&H | DD | TIM | exits | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|
| correction | NVDA_SHORT | -2.07% | -7.91% | +7.91% | 2.71% | 4.56% | 12 | gray |
| correction | MU_SHORT | -2.44% | -201.70% | +201.70% | 7.16% | 7.43% | 17 | gray |
| correction | SNDK_SHORT | -2.05% | -457.39% | +457.39% | 6.00% | 13.12% | 27 | gray |
| correction | ARM_SHORT | -3.61% | -125.40% | +125.40% | 11.70% | 4.96% | 15 | gray |
| correction | PLTR_SHORT | +3.44% | +31.04% | -31.04% | 0.68% | 2.75% | 2 | gray: failed discovery/activity |
| correction | MRVL_SHORT | -14.08% | -119.05% | +119.05% | 14.20% | 4.22% | 10 | gray |
| correction | LRCX_SHORT | +2.80% | -70.00% | +70.00% | 4.05% | 10.49% | 25 | gray: failed discovery |
| bear | IBIT_SHORT | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | gray: entry starvation |

There were zero immutable survivors, so exact replay is correctly empty. MSTR
is blocked by missing/unusable regression/Stoch fields and COIN by a pre-fix
HTF timestamp contract. Those are data blockers, not zero-trade evidence.
The eight completed final rows were appended idempotently to the path fleet as
`VEC_ASYMMETRIC_SHORT_UNTOUCHED_OOS / GRAY_REJECTED` after backup
`queue.db.bak_asymmetric_short_20260727T0201Z`; a second ingest appended zero
and skipped all eight. Correction rows are attributed to `ENTRY_DELTA_MTF`,
the production family that owns `TOP_REJECTION_SHORT`; IBIT is attributed to
`ENTRY_WT_DC`. Because this compound screen has no identical-entry E02 control,
control alpha is deliberately pinned to zero so the rows cannot be promoted.
The fleet receipts point to the initial `020146Z` artifact. A fee-accounting
cleanup then reran the complete campaign as `020907Z`; all headline strategy,
B&H, drawdown, TIM, trade and verdict values were unchanged. `020907Z` is the
canonical source-matched artifact.

The first screen confirms the requested direction but not a working formula:
simple overbought→1h rollover remains too noisy for the semiconductors. The next
bounded campaign should require a completed structural break (1h LH+LL close
through the prior low) before the first fill, then compare a distinct
bear-continuation profile. It should also attribute exits by profit target,
trail, correction-end, max-hold and emergency, rather than tuning on aggregate
return alone.

## Research basis for asymmetry

The design is consistent with evidence that negative returns and volatility
are asymmetric rather than a sign-flipped version of upside moves:

- Campbell and Hentschel, [No News is Good News](https://www.nber.org/papers/w3742)
- Bekaert and Wu, [Asymmetric Volatility and Risk in Equity Markets](https://www.nber.org/papers/w6022)
- SEC, [Key Points About Regulation SHO](https://www.sec.gov/investor/pubs/regsho.htm)

The SEC material also reinforces why capacity, emergency cover and solvency
must remain explicit: a plain short has theoretically unbounded adverse-price
risk and operational borrow/cover costs not present in a LONG mirror.

## Rollback

No live config, symbol allowlist or running process changed. Code rollback is
limited to the reporting fixes and the new research script. Research evidence:

- `data/reports/vec_research/asymmetric_short_20260727T020907Z/result.json`
- `data/reports/vec_research/asymmetric_short_20260727T020907Z/RESULTS.md`
- `tools/ingest_asymmetric_short_campaign.py`
