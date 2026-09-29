# DAILY OPTIMIZATION PLAN — v0.1 DRAFT (pending clarifications)

**Author capture: 2026-09-29, from operator directive. STATUS: DRAFT — do NOT implement past Stage 0
until the OPEN QUESTIONS are answered. "Any mistake here will destroy the entire system" (operator).**
This is the daily self-optimization loop that is meant to lift the trading system to excellence: every
day at market open it re-derives per-cat_side settings from the prior round's real deltas, promotes the
winners to defaults everywhere (template + all 3 configs, in sync), runs the full sweep on the new
baseline, verifies 365D + live-faithful, and applies pos-gain/pos-365D winners to live per_sym before open.

## Cardinal safety rules (invariants — violating any = system damage)
- **NO-LIES**: only real engine deltas (v12_quick_engine / evaluate_sanitized); never fabricate a delta.
- **Defaults change in exactly ONE place**: Stage 4 promotion (a positive avg-delta row). Nowhere else may
  a default be touched. When promoted, the winning value becomes **bold** (new default) and the prior
  default becomes **regular** in the template, AND the same switch's default is changed in config.py,
  config_tradier.py and QuickConfig for that cat_side — all four kept in sync.
- **Row integrity**: when rows are rearranged, the ENTIRE row moves together — every yellow cell stays with
  its switch. Orange rows always stay below white (switch) rows.
- **Never delete a filter/row**: slow/inert filters are throttled by test-frequency (Stage 6), never removed.
- Back up every template/config before editing; compile; md5-verify Mac==servers; keep it reversible.

## Priority order (operator, 2026-09-29) — applies to all compute allocation
1. **Pending 30D backtests** always first.
2. Then **365D** verification of the round's winners.
3. Then the **live-faithful rerun** (backtest_v12_engine).
Idle compute anywhere backfills in this order (see the parity saturator; extend it to honor this).

## Infra note
- **s5 = hybrid** (carry ALL NPZ, both venues) — a *temporary* box until templates are optimized and tests
  get faster. Load divides across any available server (s1/s2/s5/…), venue-flexible on hybrid boxes.

## The loop — stages

### Stage 1 — Rebuild the simplified v15_avg_delta
Count **every delta** from the **latest round** of tests across **all sym_sides** (results from BOTH the old
system and the new system for this round — see Q8). Produce, per switch AND per filter, across the board:
- **pos_sym** = number of times a calculation gave a positive delta.
- **avg delta** and **mean delta** (definitions — see Q1).
Organized as **4 cat_side tabs**: CRYPTO_LONG, CRYPTO_SHORT, STOCKS_LONG, STOCKS_SHORT. Only these 4 tabs
and only {avg, mean, pos_sym} matter (simplified vs the old multi-field sheet).

### Stage 2 — Inject into TEMPLATE_{cat_side}.xlsx
Write the {avg, mean, pos_sym} per switch+filter into each `TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx`.

### Stage 3 — Rearrange rows (worst_first)
Reorder all **white (switch) rows** in **worst_first avg-delta order** (keeping each entire row + its yellow
cells together). Then all **orange rows** below, same order.

### Stage 4 — Promote positive avg-delta rows (ONLY default-change point)
For each row whose avg delta is positive: the pos-delta switch/filter value becomes **bold** (new default),
the previous default becomes **regular** (so that same row shows ~no delta next round, since it is now the
baseline). Simultaneously change that switch's default in config.py / config_tradier.py / QuickConfig **for
that cat_side** so template and all configs are in sync.

### Stage 5 — Per-cat_side config defaults (architecture change)
config.py, config_tradier.py and QuickConfig must distinguish **each switch/filter's default for each of the
4 cat_sides** (previously one default served all four). Representation + how LIVE selects the cat_side default
per position — see Q4 (the highest-risk change; touches live money).

### Stage 6 — Selective test frequency (modify v15_pilot; do NOT delete rows)
A row is tested based on its cumulative pos_sym:
| pos_sym | test cadence |
|---|---|
| 0 (never positive) | 1 in 10 tests, on a random sym_side for the cat_side |
| 1 | 1 in 5 |
| 2 | 1 in 3 |
| 3 | half the time |
| >3 | every test |
(pos_sym accumulation semantics — see Q2; this is what makes the scheme viable long-term.)

### Stage 7 — Daily run at market open
Start a **complete 30D test on the new default settings**. Each sym_side uses its **previous best settings as
baseline**, and the **newly-arranged worst_first scheme** from its `TEMPLATE_{cat_side}`. When done:
- test the newly-found settings at **365D**; if time before market open, also in **backtest_v12_engine**;
- any setting with **pos 30D gain AND pos 365D gain** is applied to **live per_sym settings before market
  open** (mechanism — see Q6/Q7).

### Stage 8 — Recompute avg_delta and roll to next day
Recompute v15_avg_delta from the latest round: **ADD** new pos_sym to existing pos_sym, but **REPLACE** avg
delta with the new value. These overwrite prior avg delta and add to pos_sym in each TEMPLATE_. Rearrange
rows again (Stage 3), promote positive avg deltas to default (Stage 4, template + all configs), and the new
template sheets run the next day.

### Stage 9 — Just-in-time NPZ regeneration
Regenerate / add bars to the NPZ **right before** each symbol is calculated: while computing symbol N, the
NPZ for symbol N+1 is updated. (Feasibility/cost + 400D target — see Q9.)

### Stage 10 — Daily symbol scan
Symbol lists are dynamically generated. Each day, **scan** and **add new** symbols, **remove** delisted/no-
longer-traded ones, so no compute is wasted on symbols we are not currently trading. (Source of truth — Q10.)

## OPEN QUESTIONS (must resolve before build)
- **Q1 avg vs mean:** exact definition of each, and which drives worst_first ordering + promotion (assume: avg
  = arithmetic mean of the deltas; mean = median?). Over all deltas, or only positive ones?
- **Q2 pos_sym accumulation:** cumulative all-time (ADD forever), rolling window (last N rounds), or per-round?
  If all-time, the frequency scheme eventually pushes everything to >3 → tested always (speedup decays). Need
  a windowing/decay rule.
- **Q3 avg-delta replace vs accumulate:** confirmed avg = latest-round value (REPLACE), pos_sym = cumulative
  (ADD). Correct?
- **Q4 per-cat_side defaults representation + LIVE selection:** how are 4 defaults per switch stored in
  config.py/config_tradier/QuickConfig, and how does live (ez_manage/tradier_manage) pick the cat_side default
  for a given position (by venue + side)? This is the riskiest, real-money piece.
- **Q5 template default vs per_sym baseline:** Stage 7 baseline = per_sym previous best, but Stage 4 promotes
  template defaults. How do promoted defaults interact with per_sym-best baselines (merge order / precedence)?
- **Q6 go-live mechanism:** how is "pos gain + pos 365D → per_sym live" applied — which per_sym files, and
  does it still route through confirmed_365d.json + the live per_sym config? Per sym_side.
- **Q7 daily schedule:** exact start time + hard deadline (market open 13:30 UTC?); crypto is 24/7 — same clock?
  If the full 30D+365D+live can't finish by open, partial-apply what completed (per the priority order)?
- **Q8 "old and new system":** which result sources feed Stage 1 (old = v15_pilot big template; new = ?).
- **Q9 NPZ JIT cost:** is per-symbol 400D NPZ regen fast enough to interleave inline, or a separate pre-pass?
- **Q10 symbol universe source of truth:** which files/generators define the daily traded universe to sync to?
- **Q11 worst_first direction:** confirm worst (most negative avg delta) first, and why (greedy biggest-lift).
- **Q12 who runs it:** hand to a dedicated agent, a cron/scheduler, or operator-run MD — and control surface.

## Handover
This doc + BACKTEST_BIBLE (pointer added) are the durable spec. A dedicated agent or a scheduled routine will
own the daily loop once the questions are answered and the design is locked. Nothing past Stage 0 is built yet.
