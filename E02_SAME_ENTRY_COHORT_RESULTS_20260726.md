# Same-entry E02 Donchian cohort — 2026-07-26

## Outcome

The bounded `EXIT_E02_DONCHIAN` path job is complete over the frozen top-10
LONG and bottom-10 SHORT cohorts. It tested exactly:

- completed HTF: 1h, 4h, D;
- lookback: 10, 15, 20, 30, 40, 55, 80;
- minimum open-position profit gate: 0%, 0.25%, 0.5%, 1.0%.

There are 84 candidates per key and 1,680 side-isolated candidate rows across
the cohort. The path-fleet job is `SCREENED`; all 20 frozen winners are gray
and the exact-replay queue is empty.

No candidate passed the complete chronological contract:

1. positive discovery alpha versus side-specific B&H;
2. positive discovery alpha versus identical-entry 4h/N30 E02;
3. discovery capacity-weighted exposure within 70–80%;
4. positive untouched-validation alpha versus both benchmarks;
5. validation exposure within 70–80%;
6. zero future HTF sources, reclaim violations, insolvency and capacity breach.

No matrix, per-symbol, live-config or promotion write was made.

Authoritative S1 artifacts:

- cohort:
  `data/reports/vec_research/same_entry_e02_topbottom_nested_exp70_80_20260726T1825Z`;
- path-fleet job:
  `data/reports/path_fleet/job_35_EXIT_E02_DONCHIAN`;
- refreshed ledger:
  `data/reports/path_fleet/PROGRESS.md`;
- refreshed digest:
  `data/reports/SWITCH_MATRIX_TRB_DIGEST.md`.

## Same-entry and causality contract

Every arm consumes the curve selected before the exit screen and preserves,
fold by fold:

- the SHA-256 of every entry-request timestamp and target multiplier;
- `target` or `add` semantics;
- next-RTH entry and exit fill timing;
- $2,000 B&H comparison unit and $16,000 entry capacity;
- 5 bps commission plus 2 bps adverse slippage on every fill;
- lower/higher ladder reentry and persistent zero-buffer resting reclaim;
- LONG and SHORT cash/P&L in separate ledgers.

The channel is built from the prior N completed HTF highs/lows. LONG exits only
after a completed close below the prior low; SHORT mirrors above the prior
high. The opposite prior channel edge becomes the reclaim reference.

`dc_low4_5m`, a first-break stop, and interpolated 5m exit signals are absent.
The rolling channels are vectorized once per timeframe/lookback; the stateful
ledger then evaluates the four profit gates without rebuilding entry signals.

## Frozen top-10 LONG validation

`disc Δctl` is discovery alpha versus identical-entry 4h/N30 E02. All returns
and exposure shown in the remaining columns are from the untouched final fold.

| key | frozen E02 setting | disc Δctl | strategy | B&H | Δ control | TIM | exits | fill ratio | clamps | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SNDK_LONG | D N30, gate 0 | +603.43pp | +3,831.22% | +467.09% | +1,505.58pp | 99.20% | 2 | 1.98% | 361 | reject: exposure/cap churn |
| MRVL_LONG | 1h N10, gate 0.25 | +173.13pp | +82.49% | +123.58% | -1,590.23pp | 59.06% | 8 | 14.27% | 209 | reject: below B&H/control; insolvent fold |
| ARM_LONG | D N10, gate 0 | +136.91pp | +1,026.36% | +126.60% | -56.08pp | 93.06% | 2 | 5.65% | 307 | reject: exposure/control |
| MU_LONG | D N55, gate 0 | +273.65pp | +1,294.20% | +204.90% | -82.03pp | 77.79% | 0 | 10.97% | 180 | reject: control; zero-exit MTM |
| INTC_LONG | 4h N10, gate 1 | +101.07pp | +1,232.42% | +139.23% | +268.41pp | 92.07% | 4 | 22.14% | 289 | reject: exposure |
| AMD_LONG | 1h N10, gate 0 | +50.39pp | +622.98% | +134.45% | -557.83pp | 94.18% | 9 | 9.28% | 238 | reject: exposure/control |
| PBF_LONG | 1h N80, gate 0 | +1.92pp | +699.96% | +121.26% | -7.31pp | 94.40% | 1 | 7.80% | 78 | reject: exposure/control |
| MPC_LONG | 1h N40, gate 0 | +55.16pp | +134.87% | +88.48% | +17.41pp | 24.31% | 6 | 53.85% | 32 | reject: exposure |
| DINO_LONG | D N40, gate 0 | +14.04pp | +618.25% | +90.70% | +133.02pp | 96.57% | 0 | 18.29% | 109 | reject: exposure; zero-exit MTM |
| VLO_LONG | 1h N20, gate 0 | +46.23pp | +276.47% | +84.91% | -110.83pp | 68.19% | 10 | 78.79% | 18 | reject: exposure/control |

The high SNDK, INTC and DINO returns are not promotion evidence. Their
validation exposure is 92–99%, effectively a heavily levered hold, and their
entry requests repeatedly hit occupied capacity. SNDK filled only 1.98% of
requested entry notional.

MU is the only LONG frozen winner inside the 70–80% validation band. It had no
technical exit in that fold, so +1,294.20% is final mark-to-market, not proof
that D/N55 exited a top. It also lost 82.03pp to identical-entry E02.

## Frozen bottom-10 SHORT validation

| key | frozen E02 setting | disc Δctl | strategy | B&H | Δ control | TIM | exits | insolvent folds | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| LAC_SHORT | 1h N10, gate 1 | +47.13pp | +77.35% | +36.98% | -31.99pp | 19.54% | 4 | 1 | reject |
| UUUU_SHORT | 1h N55, gate 0 | +236.06pp | +45.45% | +26.20% | -22.79pp | 21.42% | 1 | 1 | reject |
| ASTS_SHORT | 1h N10, gate 0 | +546.31pp | +93.63% | +22.27% | +27.05pp | 22.34% | 5 | 1 | reject |
| UEC_SHORT | 1h N20, gate 0 | +34.56pp | +63.64% | +22.65% | -1.41pp | 16.76% | 6 | 0 | reject |
| ACN_SHORT | 4h N55, gate 0 | +30.95pp | +417.03% | +44.50% | +16.06pp | 90.39% | 1 | 0 | reject: exposure |
| TTD_SHORT | D N15, gate 0 | +87.77pp | +555.43% | +54.33% | +73.54pp | 93.85% | 2 | 0 | reject: exposure |
| HL_SHORT | 1h N20, gate 1 | +555.02pp | +67.83% | +21.95% | +17.58pp | 20.25% | 5 | 1 | reject |
| EGO_SHORT | 4h N30, gate 0 | 0.00pp | +48.02% | +23.03% | 0.00pp | 20.67% | 1 | 0 | reject: no control alpha |
| ALB_SHORT | 1h N15, gate 0 | +239.49pp | +67.74% | +19.41% | -17.98pp | 42.93% | 3 | 1 | reject |
| LDOS_SHORT | 1h N10, gate 1 | +12.73pp | +60.19% | +37.50% | -7.57pp | 13.82% | 9 | 0 | reject |

No SHORT winner has validation exposure in the 70–80% band. ACN and TTD beat
both benchmarks but are 90–94% exposed. LAC, UUUU, ASTS, HL and ALB have at
least one insolvent evaluated fold and are rejected independently of their
headline return.

## Why the path did not advance

- A slower Donchian exit often improves return simply by holding more exposure.
  That is not top detection and fails the exposure comparison.
- A faster 1h exit can lower exposure but often destroys identical-entry E02
  alpha or falls far below 70%.
- Profit gates did not create a stable middle region. They delay loss exits as
  well as premature profit exits.
- Entry-capacity contention remains severe in the high-exposure LONG rows:
  their 2–22% fill ratios show repeated requests against already occupied
  capacity.
- Zero-exit folds are kept, not discarded. They are explicitly labeled
  final-MTM evidence and cannot demonstrate exit-path value.

The useful boundary from this sweep is therefore:

1. `D` exits at N30–55 are hold-like and must not be credited as superior top
   exits without multiple realized exits.
2. `1h` exits at N10–20 increase churn/capacity contention and are unstable
   across symbols.
3. The next exit family must add top information rather than merely changing
   hold duration. WT/price structure or partial-runner research should retain
   this exact entry/control contract.

## Reproduction and code

- `tools/vec_same_entry_exit_adapter.py`
- `tools/run_same_entry_exit_cohort.py`
- `tools/path_fleet_e02_worker.py`
- `tools/path_fleet_campaign.py`
- `test_vec_same_entry_exit_adapter.py`
- `test_run_same_entry_exit_cohort.py`
- `test_path_fleet_e02_worker.py`

Representative S1 command:

```bash
python3 tools/path_fleet_e02_worker.py \
  --root data/reports/path_fleet \
  --npz-dir backtest_v8/indicators \
  --long-summary data/reports/path_fleet/job_33_ENTRY_LADDER_GREEN/summary.json \
  --short-summary data/reports/path_fleet/job_33_ENTRY_LADDER_GREEN/summary.short.json \
  --output-root data/reports/vec_research/same_entry_e02_topbottom_nested_exp70_80_20260726T1825Z \
  --workers 4
```

Focused local tests pass. S1's pytest installation lacks `pluggy`, so the
causal long/short Donchian, bounded-grid, accounting and ingestion tests were
also executed directly on S1 and passed.
