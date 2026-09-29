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
- **Auto-sync cron = baseline-integrity constraint (found 2026-09-29):** `tools/sync_mac_to_s1_30min.sh`
  (Mac cron, every 30 min) and `tools/template_push.sh` (every 1 min) auto-propagate Mac→s1/s2/s5. Any
  Mac edit to the engine/config/templates lands on the servers within ≤30 min — the "ghost pusher." This
  is FINE as the daily loop's DEPLOY mechanism (promotion edits Mac → auto-syncs), but it means a baseline
  CANNOT be pinned on the servers during a multi-hour compute if the Mac copy changes. THE LOOP MUST batch
  ALL engine/config/template changes at the daily boundary (promotion), let the sync propagate, verify one
  stable baseline (single engine md5 on all boxes), THEN run the sweep — and make NO Mac engine edits mid-
  round. Stamp the engine md5 into every round's outputs so a mixed-baseline round is detectable (NO-LIES).
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

## RESOLVED DECISIONS (operator, 2026-09-29) — these OVERRIDE the draft above
- **Q1 pos_sym = ROLLING WINDOW** (last N rounds), NOT cumulative-forever — so a filter that stops helping
  decays back to throttled and a resurrected one re-earns frequency. (N still to pick — proposing 20; confirm.)
- **Q2 metrics — REVISED BY DATA (2026-09-29, Stage-1 run over 378 progress JSONs):** median-over-all is
  DEGENERATE for promotion — pos_median = 0/0/0/1 across the four cat_sides, because nearly every
  switch/filter helps only a MINORITY of symbols (0 delta elsewhere) → median 0 → nothing promotes.
  DECISION: **driver = `avg` (arithmetic mean), gated by `pos_sym >= 2`** (breadth guard); this yields
  12/22/33/12 real promotable rows with genuine breadth and matches the operator's existing V15_AVG_DELTAS
  (avg_delta + pos_sym). `median` is kept in the sheet as an outlier cross-check. TUNING KNOB before a
  promotion flips a default: harden against outlier-inflated avgs (e.g. one symbol at +26) via a fractional
  breadth gate (pos_sym/n) or a trimmed mean. Tool: `tools/v15_avg_delta_rebuild.py` → SPREADSHEETS/v15_avg_delta.xlsx.
- **Q3 SOURCE OF TRUTH = the TEMPLATE sheets**, specifically the **BOLD** font of a switch/filter value:
  exactly ONE bold value is allowed/required per switch name. The configs were NOT aligned (one generic
  default for all 4 cat_sides) — that is the bug. Fixes REQUIRED:
  1. **Restore the `is_default_setting` (yes/no) column** in the templates — it fell out; defaults are essential.
  2. The **`overrides` column must ALWAYS be filled** with the override name+setting AND the filter name+setting
     that produced the positive result in that row (not just when promoted).
  3. **Add per-cat_side defaults to config.py, config_tradier.py AND QuickConfig for EVERY switch — NOW.**
     NO setting may be generic; every switch/filter default is **cat_side dependent** (4 values). On every
     template edit, sync the template's BOLD value → the config default **for that category+side only**.
  (Representation of the 4-way default in the 3 config files + how live selects it: proposal + example pending
  operator confirm — see REMAINING.)
- **Q6/Q4 go-live gate:** winner = **pos 30D gain AND pos 365D** → certify via confirmed_365d.json + write to
  live per_sym config (sanctioned path). ADDITIONS:
  - A sym_side that does NOT pass **cannot trade that day**. If it holds an open position, **wait for the local
    top to close** it — UNLESS `wt1_15m` is already against the trade, then **close immediately**.
  - Order of work: run all **necessary (passing/needed)** sym_sides first; THEN **rerun the disqualified** ones
    to try for better values from prior tests — but only apply if they then show pos 30D AND pos 365D.

## DEFAULTS & OVERRIDES REPRESENTATION — LOCKED (operator delegated choice, 2026-09-29)
- **Single source of truth = the TEMPLATE bold** (one bold value per switch/filter per cat_side).
- **Config representation = a flat per-cat_side map** in each of config.py / config_tradier.py / QuickConfig:
  `CAT_SIDE_DEFAULTS = {"CRYPTO_LONG": {key: value, ...}, "CRYPTO_SHORT": {...}, "STOCKS_LONG": {...},
  "STOCKS_SHORT": {...}}`. One entry per switch AND per filter whose cat_side default differs from the base
  field. This entry is exactly "an override for that cat_side only that overrides the base default for that
  cat_side only" (operator's phrasing). **Auto-synced from the template bold** by a sync tool
  (`tools/sync_catside_defaults.py`, to build) — NEVER hand-edited in four places, so no drift.
- **Resolver** `default_for(key, cat_side)` that QuickConfig and live (ez_manage/tradier_manage, selecting
  cat_side by venue+side) call: returns `CAT_SIDE_DEFAULTS[cat_side][key]` if present else the base field.
- **Why this collapses the complexity:** a promoted winning row is switch=value PLUS its stacked winning
  filters (filter x=y AND filter d=f AND …). Each of those — switch and every promoted filter — is just its
  own `key: value` in that cat_side's map. So the whole "many stacked overrides per switch × 4 cat_sides"
  becomes ONE flat key→value map per cat_side (no nesting). The template's **`overrides` column** keeps the
  human-readable provenance (which filters stacked under which switch + settings — always filled, per rule);
  the effective live/sweep config is the flat map.
- **Layering (precedence, live and sweep):** per_sym best (sym-specific winner) > CAT_SIDE_DEFAULTS[cat_side]
  > base config field. Promotion (Stage 4) writes into CAT_SIDE_DEFAULTS[cat_side] (= flips the template bold
  and syncs the map); per_sym best stays a separate top layer applied via confirmed_365d + per_sym config.
- **Build order (safety):** CAT_SIDE_DEFAULTS is initialized so all 4 cat_sides == the CURRENT base value
  (zero behavior change), the resolver is wired, THEN promotion fills per-cat_side divergence over rounds. The
  risky part is repointing every live read site to the resolver — that is a wave/controlled edit, not a blind
  mass change; do it under the daily-loop build with backups + parity, never rushed.

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
