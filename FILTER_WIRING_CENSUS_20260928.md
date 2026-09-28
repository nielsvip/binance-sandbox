# FILTER WIRING CENSUS — 2026-09-28 (yellow + orange, all 4 templates)

USER order: every yellow/orange filter needs a dedicated vector path identical to its live
use (or added identically to ez_/tradier_ if missing live); any positive yellow delta must
add to vector delta and its name join the overrides.

Machine census: `data/reports/filter_wiring_census.json` (117 filters, per-name evidence
lines file:line). Engine state at census: the 320e5dcd-lineage cut (md5 at scan `5b4b59ff`/
`320e5dcd` family); pilot at md5 `73c2bf4f`.

## Headline

**117 distinct filters** (yellow L:BI headers + orange FFE699 blanket rows; orange = GENERAL
per-tab block rows, evaluated as one block, per `v15_pilot` `orange_rows`/`_orange_block`).

| Class | Count | Meaning |
|---|---|---|
| BOTH_WIRED | 32 | literal functional-looking reads both sides — **upper bound, verify per name before trusting** |
| LIVE_ONLY | 71 | live read found, no reachable vec read — needs vec twin (upper bound, see caveat) |
| VEC_ONLY | 10 | vec read only — per user order needs identical LIVE addition |
| NEITHER | 4 | no functional read anywhere — needs identical implementation both sides |

**CAVEAT THAT DOMINATES EVERYTHING: both codebases contain deliberate no-op stub farms that
defeat naive wiring audits.** Classes above are after stripping every farm signature we
identified, but each worklist item's cited evidence line MUST be dataflow-verified before
implementation; a cited line that only feeds `_ = ...` is a stub.

## The stub farms (verified this census)

- **Vec, dead functions (7)**: `_batch1_template_wiring` (return@174), `_wire_07_exit_stops_tranche`
  (@652), `_apply_auto_wired_params` (@~1032), `_apply_universal_distinctness_fallback` (@~1134),
  `_apply_625_entry_gates` (return@11633 — contains the hash-proxy `_ALL_FILTER_TF` dispatcher
  at ~11907: each FILTER_TF hash-mapped to an arbitrary indicator; dead but do NOT revive),
  `_apply_new_audit_causal` (@17111), `_batch3_template_wiring` (def@899 — pure `_ = cfg.X` farm,
  still CALLED at 9073/9838 but body is no-ops).
- **Vec, fake-audit tuple**: `compute_reentry_blocks` builds ~56 `_b4_*` locals from getattr and
  discards them into `_b4_all` — engine:8771 comment is verbatim **"prove vector read — audit
  counts getattr(cfg, NAME) regardless of use"**. Audit-evasion scaffolding.
- **Vec, loose discards**: ~490 bare `_ = cfg.X` lines engine-wide.
- **Live, stub regions**: ez_manage ~58380-58700+, tradier_manage ~32380-32600+: blocks shaped
  `if bool(getattr(config,"NAME",False)):` → compute a local → `_=_x  # NAME`; inline `: _ = 1`
  variants; `_ = config.NAME` discards; some annotated with FALSE comments ("real live: filter
  TF gate" above a no-op). Note `bool(getattr(config,"SOME_TF","15m"))` is truthy-nonsense —
  auto-generated.

**Practical consequence**: the `*_FILTER_TF` family (52 names, ~8-9k yellow cells EACH — the
bulk of the entire yellow map) is scaffold on BOTH sides. This is exactly why whole rows of
yellows echo one value (XRPUSDC_LONG row with 116 identical deltas). Filling books credibly
requires implementing these families for real, identically, both sides — or removing their
rows/yellows from templates.

## Top-20 worklist (by yellow-cells × tab coverage; full ranking in JSON)

MOM3_FILTER_TF, MOMENTUM_BREAKOUT_FILTER_TF, LIVE_ENTRY_ENGINE_FILTER_TF, FAST_RISER_FILTER_TF,
DELTA_ENGINE_FILTER_TF, FIRST_OPEN_THROTTLE_FILTER_TF, HAIKU_WINNER_FILTER_TF,
DC_MOMENTUM_BOTA_SCORER_FILTER_TF, OPEN_INTENT_SIZE_GATES_FILTER_TF,
PEAK_GIVEBACK_BE_EROSION_FILTER_TF, MTF_ARMED_ENTRIES_FILTER_TF, GOLDEN_RULE_ENFORCE_FILTER_TF,
FROZEN_STOP_FILTER_TF, NOLOSS_BYPASS_WT5OF5_FILTER_TF (IN_PROGRESS — engine agent),
EMA_BLANKET_FILTER_FILTER_TF, PARTIAL_PROFIT_LOCK_V2_FILTER_TF (IN_PROGRESS),
NEWBORN_PROTECT_FILTER_TF, NEWBORN_LOSS_KILL_FILTER_TF, LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF,
GR_V5_STATE_FILTER_TF. (Orange-heavy extras: HA_WICK_QUALITY_TF 240 orange rows;
MOMENTUM_BREAKOUT/FIRST_OPEN_THROTTLE/HAIKU_WINNER carry orange rows too.)

Implementation rule per user order: for each name, read its FILTERS_EXPLAINED semantics, find
or write the LIVE implementation in ez_manage/tradier_manage (venue-appropriate), then write
the identical vec_decisions module — never a proxy, never hash-mapped, ledger-visible effect
required. VEC_ONLY names (`BB_PROFIT_TAKE_TF`, `BB_EXIT_AT_LOSS_TF`, + 8 reclassified) get the
live side added identically instead.

## Pos-yellow promotion semantics — VERIFIED PRESENT (pilot md5 73c2bf4f)

- (a) Positive promotable yellows sum into the row's promotion delta: `pos_hdrs`/`sum_pos` →
  `delta_for_row` at v15_pilot.py:1795-1798, joint-recheck gate 1810-1822.
- (b) C gets `SWITCH=cand + filter=setting` (name **with** setting, comment "bare header names
  carry no setting"): ~1864-1866 via `_add_override` (def 1203, appends, never overwrites).
- (c) Promoted filters merge into `cumulative_overrides` for the greedy chain: 1813-1817
  (+ sanitize), only after the joint eval confirms the combined set beats cumulative_before.
- (d) K gets the numeric positive-yellow sum: 1855-1861, and `k_sum_pos_yellows` +
  `delta_vs_cumulative` persist in done records (1874).

No defect found in the promotion path itself; the credibility problem is upstream (filters
that cannot produce a positive delta because nothing is wired to them).
