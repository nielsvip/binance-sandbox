# NONE-CELLS PRIORITY — NEXT ROUND WORK ORDER (2026-10-07)

Every filter cell in every cat_side grid is now in exactly one of 3 states.
Source of truth: `SPREADSHEETS/V15_CAT_AVG/AVG_{cat}.xlsx` (built from finalized
`*_bh*_gain*_t*_30d_matrix.xlsx` only, 1 per sym_side) + machine-readable
`data/cat_avg/priority_evidence_{cat}.json` (`rows`/`cells` = tested keys only,
each `[n_sym, pos_sym, neg_sym]`).

## 1. The 3 states (legend — also on each workbook's COVER)

| State | Meaning | Workbook mark |
|---|---|---|
| POS | >=1 sym_side with delta > 1e-9 | count, GREEN fill |
| NEG0 | tested, never positive, >=1 negative | 0, RED fill |
| ALLZERO | tested, every value exactly 0.0 | 0, GREY fill |
| NONE | never numeric in any finalized sheet | BLANK — priority for next round |

POS counts by size drive template paint: 1-3 light yellow (FFF2CC), >3 bright
yellow (FFFF00). NEG0/ALLZERO/NONE are never yellow.

## 2. Inventory (2026-10-07 build, 87 finalized sym_sides)

Cells (template grid = rows x `FILTER=opt` headers, 13 tabs):

| cat_side | syms | POS | NEG0 | ALLZERO | NONE | grid |
|---|---|---|---|---|---|---|
| CRYPTO_LONG | 22 | 36121 | 56018 | 73624 | 514179 | 679942 |
| CRYPTO_SHORT | 15 | 7447 | 37571 | 102147 | 515339 | 662504 |
| STOCKS_LONG | 35 | 11849 | 76528 | 78652 | 564849 | 731878 |
| STOCKS_SHORT | 15 | 4358 | 51260 | 82831 | 547971 | 686420 |

Rows (VECTOR_DELTA):

| cat_side | POS | NEG0 | ALLZERO | NONE | grid |
|---|---|---|---|---|---|
| CRYPTO_LONG | 636 | 371 | 2208 | 78 | 3311 |
| CRYPTO_SHORT | 284 | 196 | 2578 | 149 | 3229 |
| STOCKS_LONG | 384 | 688 | 2391 | 78 | 3581 |
| STOCKS_SHORT | 188 | 644 | 2467 | 80 | 3425 |

Caveat: NONE counts cover the full grid incl. cells outside the pilot's yellow
candidate space (token rule). Only name-yellow NONE cells are attempted per
round (below) — the grid count is the theoretical max, not the per-round load.

## 3. Mechanism — how NONE cells get calculated next round

`v15_pilot._row_static` loads `data/cat_avg/priority_evidence_{cat}.json`
(fail-open: missing file = old behavior) and applies, inside every computed row:

1. Cell key `TAB!SWITCH=cand@HEADER` NOT in evidence (NONE) + name-yellow
   (token/ever/mandatory rule) → ALWAYS evaluated. Never sampled out, never
   zero-skipped. First real value settles it into POS/NEG0/ALLZERO.
2. Cell in evidence with pos > 0 → evaluated (yellow box).
3. Cell in evidence with pos = 0 → existing rules (pos_sym sampling by the
   filter's orange-row proxy; zero-skip condemns n >= 10).
4. Row sampling (by row POS_SYM) is unchanged and still gates the row:
   None/0 → 1/20, 1 → 1/10, 2 → 1/6, 3 → 1/2, >=4 → always. Sample-free
   REDO heal passes evaluate every hole incl. all NONE cells of healed rows.

Effect: every computed row drains its NONE cells first; across sym_sides and
rounds the NONE inventory only shrinks. Nothing about this fabricates values:
unevaluated stays blank, never 0 (§19).

## 4. Wiring triage — "switches connected correctly so an actual value comes out"

Measured 2026-10-07 (template grid vs finalized evidence): ~58% of NONE cells
sit under UNWIRED filters (pilot skips them: `excluded_unwired`, never
evaluated) — pilot compute alone can NEVER drain those. Split per cat_side:

| cat_side | NONE under wired filters (drain via compute) | NONE under UNWIRED filters (need wiring) |
|---|---|---|
| CRYPTO_LONG | 210513 | 290035 |
| CRYPTO_SHORT | 215067 | 286678 |
| STOCKS_LONG | 220293 | 299119 |
| STOCKS_SHORT | 203300 | 298286 |

Top UNWIRED NONE owners (all 4 cats): `OPEN_INTENT_SIZE_GATES_FILTER_TF`,
`NEWBORN_PROTECT_FILTER_TF`, `LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF`,
`FIRST_OPEN_THROTTLE_FILTER_TF` (~2600-2740 NONE cells each per TF value).
Top wired-but-thin (drain via compute): `WT_15M_BOUNCE_REL_VOL_GT_1`,
`WT_15M_BOUNCE_BB_MAX/HIGH_1H/LOW_1H`, `MOM3_FILTER_TF`, `DC_BREAK_FILTER_TF`,
`BREAKOUT_RETEST_FILTER_TF`, `MOMENTUM_BREAKOUT_FILTER_TF`.

Anomaly: 111 UNWIRED headers (47 on STOCKS_SHORT) carry pos > 0 evidence —
impossible under the current skip, so that evidence predates the UNWIRED
verdict (stale) or the verdict is wrong. Per-filter call before rewiring.

A NONE cell that keeps producing no value after being attempted is a wiring
case, not a sampling case. Per filter with a NONE-heavy column:

1. Check `data/wiring/vec_function_registry.json` + `verify_switch_bible.py`
   for the filter: vec predicate + exactly one `v12_quick_engine` call site?
2. If unwired: wire it (predicate → call site → behavioral test proving a
   changed ledger, §39), add rows via `tools/v15_switch_add.py` only.
3. If wired but ALLZERO-dominant: §18 protocol (parity → cand==baseline →
   binding → gap). Never re-stub reads (§19).
4. Re-run: the next AVG build moves settled cells out of NONE automatically.

## 5. Verification (after the next round)

1. Re-run `python3 tools/v15_cat_avg_matrix.py --workers 10` (count→build).
2. `data/cat_avg/build_stats.json`: NONE counts per cat/tab must drop;
   POS+NEG0+ALLZERO must rise by the same amount. A NONE count that does not
   move on a tab with finished sym_sides = the pilot hook is not active on
   that host (check the JSONs + pilot md5 on S1/S2/S5).
3. Spot-recount any cell: open the listed finalized files, count positives.

## 6. Refresh cadence + lifecycle notes

- Rebuild AVG + evidence after new finalized sheets land (the paint/N-copy
  refresh: `tools/v15_yellow_paint_pos.py --apply` — see §7).
- Template POS_SYM (N) values copied from this build are refreshed DAILY by
  the chain writer from its own aggregate (progress-JSON based) — the copy is
  a now-correct snapshot, not a pinned value. Yellow fills ride whole-row
  moves through the rewrite and persist.
- Evidence JSONs + pilot ship together: a pilot newer than its JSONs still
  works (fail-open), but priority forcing needs the JSONs present in
  `data/cat_avg/` on every pilot host (S1/S2/S5 + Mac).

## 7b. Wiring batch 1 — DONE + PROVEN (2026-10-07 ~23:30Z)

Three UNWIRED filters carried the ez batch1 live entry veto
(`_batch1_template_live_gate`: TF-gated wt1>wt2 long / wt1<wt2 short, OFF inert)
but had no live vec path (same-name inline legs sit in v12
`_batch1_template_wiring`, DISABLED/passthrough since the 2026-09-28 purge —
audit body only). Wired via the live generic path
(`vec_decisions.generic_filter_tf.FILTER_TF_MAP`, consumer in
`simulate_one`): `("entry", "wt_cross_side")` × 3, no live change needed
(crypto live already vetoes; stocks live matches all other mapped FILTER_TFs:
no batch1 gate — family-wide standing, not batch-1 gap).

| filter | NONE cells | proof (A/B ledger moved) |
|---|---|---|
| DELTA_ENGINE_FILTER_TF | 53055 | S1 ETH LONG, s2 DOGE LONG, s5 ETH SHORT |
| DC_MOMENTUM_BOTA_SCORER_FILTER_TF | 51640 | same 3 hosts |
| CIRCUIT_SHARPE_GATES_FILTER_TF | 44475 | same 3 hosts |

Cut: MAP + v12 static-tuple line + `data/vec_unwired.json` trim (switches
184→182, filters 23→20) + twin verdicts, deployed sandbox+live × S1/s2/s5
md5-verified, tests `tests/test_v15_filter_tf_batch1.py` 5/5. Fresh pilots
calculate these headers immediately (UNWIRED skip gone) + priority-forcing
covers their NONE cells. BASE moved vec-side toward live (vetoes now apply at
defaults, as live always did) — directionally parity-improving.

Remaining queue (20 names, ~1.02M NONE): 2 DEAD_VEC by design
(`PARTIAL_PROFIT_LOCK_V2`, `NOLOSS_BYPASS_WT5OF5` — operator call: accept
permanent NONE or remove headers); FROZEN_STOP next (position-state stop
machinery — needs vec design, TF helper exists); 17 more need per-filter
live-semantics archaeology (several look live-only-concept:
LIVE_ONLY_SIGNALS_BATCH5, LIVE_ENTRY_ENGINE, MTF_ARMED_ENTRIES,
OPEN_INTENT_SIZE_GATES, NEWBORN_PROTECT, FIRST_OPEN_THROTTLE,
EMERGENCY_BRAKE, HAIKU_WINNER, GR_V5_STATE, GR_FILTER_VEC,
GOLDEN_RULE_*×2, EXIT_*×3, MANDATORY_REENTRY_WT_*). Rule: no live predicate
found → no invention (BLOCKED for operator semantics, GR_V5/PPL_V2
precedent).

## 7. Template paint (this order, 2026-10-07)

`tools/v15_yellow_paint_pos.py --apply` painted all 8 template files (main +
FINAL_NORM x 4 cat_sides, user-unlocked): pos 1-3 light, pos >3 bright, pos 0
/ NONE cleared to no-fill (data cells only, row 2 headers untouched), and
copied row POS counts into each template's POS_SYM column by (switch, setting)
key. Backups: `backups/before_yellow_paint_pos_*`.
