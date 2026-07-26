# Causal SHORT band-ladder mirror — 2026-07-26

## Scope and evidence contract

`tools/vec_band_ladder_walkforward.py` now runs a separate `--side SHORT`
ledger. It does not negate or reuse LONG returns.

- Entry arrows use completed-TF `wt_cross_bear_{D,4h,1h}`.
- The structural entry mirror is completed-bar LH+LL with high and falling
  StochRSI.
- Regression-band depth is reflected (`1 - lrL_pct_b`), so SHORT size is
  largest near the upper band and smaller near the lower band.
- E02 exits SHORT only after a completed 4h close above the prior N-bar high.
- After exit, a higher-price ladder event may re-enter only after price first
  trades above the exit fill. A downward crossing of the stored exit/bottom
  reclaim level forces the mirrored reclaim; indicator vetoes cannot forget it.
- Signals fill at the next RTH open with adverse side-aware slippage.
- SHORT sale proceeds, cover cost, commissions, mark-to-market liability, and
  final liquidation use a ledger isolated from LONG.
- The side-aware B&H benchmark deploys $2,000. Strategy entry capacity is
  $16,000/8x. A non-positive short-and-hold result is never used as a ratio
  denominator.
- Completed HTF source timestamps are audited and future-source count must be
  zero. Vector rows remain research-only until exact replay.

The exact adapter also now retains pre-validation warmup when recomputing
rolling HTF signals. Event indices are rebased only for the engine window. This
fixed a real parity defect first exposed by LAC_SHORT.

## Frozen bottom-ten vector control

The cohort is the point-in-time bottom ten stored in
`data/reports/path_fleet/universe.json`. The search used 32 random coherent
curves in addition to the deterministic curve families. Values are the sum of
three untouched outer validation folds.

| key | strategy | side-aware B&H | alpha | multiple | weighted TIM | verdict |
|---|---:|---:|---:|---:|---:|---|
| LAC_SHORT | -304.93% | -11.54% | -293.39pp | N/A | 58.13% | FAIL: insolvent; max account DD 160.68%; 56 clamps; fill ratio 0.337 |
| UUUU_SHORT | -219.78% | -133.05% | -86.73pp | N/A | 37.55% | FAIL |
| ASTS_SHORT | -589.06% | -153.31% | -435.75pp | N/A | 40.68% | FAIL |
| UEC_SHORT | -162.77% | -48.38% | -114.39pp | N/A | 38.08% | FAIL |
| ACN_SHORT | +407.15% | +69.84% | +337.31pp | 5.83x | 55.56% | SURVIVOR; latest fold exact pass |
| TTD_SHORT | +747.74% | +141.54% | +606.20pp | 5.28x | 65.97% | SURVIVOR; latest fold exact pass; aggregate fill ratio 0.201 |
| HL_SHORT | -734.70% | -219.36% | -515.34pp | N/A | 37.64% | FAIL: insolvent; max account DD 102.62% |
| EGO_SHORT | -223.69% | -85.44% | -138.25pp | N/A | 19.64% | FAIL |
| ALB_SHORT | -818.37% | -82.31% | -736.06pp | N/A | 61.19% | FAIL: insolvent; max account DD 163.06%; fill ratio 0.175 |
| LDOS_SHORT | -30.35% | +13.86% | -44.21pp | -2.19x | 25.95% | FAIL |

The result is not a general SHORT strategy. Only two of ten controls survive
the vector gate. LAC's latest validation fold happens to be positive, but its
three-fold aggregate is catastrophic; exact parity for that fold proves
implementation fidelity only and cannot override aggregate failure.

## Exact replay

LAC's latest frozen fold was replayed through the current faithful
`backtest_v8_engine` from the same corrected artifact:

`data/reports/vec_research/v8_exact_ladder_replay_20260726T174801Z_LAC_SHORT`

- schedule: 27/27 actions executed, zero refusals/mismatches;
- accounting: +80.4563549% on the $2,000 unit, delta
  `1.28e-11` bp;
- completed HTF future-source count: zero;
- next-RTH execution: pass;
- weighted TIM: 13.0755023%, exact vector match;
- capacity: $3,864.93 peak post-fill, no breach;
- promotion and matrix writes: false.

The two aggregate vector survivors also passed current-engine replay of their
latest untouched folds:

| key | strategy | B&H | multiple | weighted TIM | actions | exact verdict |
|---|---:|---:|---:|---:|---:|---|
| ACN_SHORT | +388.0709% | +44.4968% | 8.7213x | 88.3016% | 37/37 | PASS |
| TTD_SHORT | +280.8786% | +54.3252% | 5.1703x | 86.0947% | 32/32 | PASS |

Artifacts:

- `data/reports/vec_research/v8_exact_ladder_replay_20260726T174957Z_ACN_SHORT`
- `data/reports/vec_research/v8_exact_ladder_replay_20260726T175500Z_TTD_SHORT`

Both have zero future HTF sources, exact next-RTH prices/quantities, zero
refusals/mismatches, exact TIM, exact accounting (less than `2e-11` bp delta),
and no $16,000 entry-capacity breach. TTD generated 38 clamp requests; 20 were
zero-fill requests while already at capacity. Those are preserved as
`CAPACITY_NO_FILL` audit events rather than fake zero-quantity engine orders.
The first audit correctly failed until those events reconciled the full
$554,855.23 requested amount and 38 clamps.

These are exact parity results for the frozen latest folds, not authorization
to write matrix/live state. Promotion remains false pending the parent
campaign's full acceptance and per-symbol configuration decision.

## Fleet integration and rollback

`tools/path_fleet_control_worker.py` now processes the top-LONG and
bottom-SHORT cohorts independently. `--resume-job-id ... --short-only` permits
an append-only side backfill without deleting the already accepted LONG rows.
Job 33 was backfilled with ten SHORT rows; eight are `CONTROL_FAILURE` and two
are `CONTROL_ROW`. All rows remain non-promotable because `exact_replay=false`.

Rollback is limited to:

- `tools/vec_band_ladder_walkforward.py`
- `tools/v8_research_ladder_adapter.py`
- `tools/path_fleet_control_worker.py`
- the `ENTRY_LADDER_GREEN` adapter status in
  `tools/path_fleet_campaign.py`

No live config, universe membership, switch matrix, or position state was
changed by this work.
