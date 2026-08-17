# 10x B&H path-combination queue — matrix-only research

Generated 2026-08-04 UTC from the completed S1 vector campaign and existing
causal lifecycle receipts. This is a research queue only: it does not write
the canonical exact matrix, V8 results, or live configuration.

## Selection contract

- Benchmark: side-aware `$2,000` B&H; target is `strategy/B&H >= 10.0x` where
  the B&H denominator is finite and positive.
- Rank first by multiple, then positive monthly delta, then observed closes;
  retain lower-multiple near misses when the recipe is causal and materially
  active so the next replay can confirm or reject the frontier.
- Require finite result, positive observed closes, zero future-HTF reads, no
  capacity clamps, and a stored-level reclaim on full exit/re-entry.
- Vector lifecycle rows are provisional evidence. Duplicate behavior recipes
  are collapsed; no value is made unique by copying or perturbation.

## Confirmed 10x-capable combinations

| rank | key | entry -> augment -> exit -> re-enter | strategy/mo | B&H/mo | multiple | delta/mo | DD | TIM | closes | evidence |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | USAR_LONG | `DC_5m_N10_B100_R40` -> `WT_IN_15m_D8_P0` -> `WT_FULL_EXIT_1h_D16_P30` -> `WT_IN_15m_D8_P0` | 145.475% | 1.822% | 79.850x | +143.653pp | 35.02% | 95.75% | 104 | VECTOR_LIFECYCLE |
| 2 | PSX_LONG | `DC_5m_N5_B100_R0` -> same DC -> `E02_4h_N30` -> `DC_15m_N5_B40_R10` | 16.258% | 1.076% | 15.108x | +15.182pp | 42.54% | 98.64% | 423 | VECTOR_LIFECYCLE |
| 3 | ACN_SHORT | `DC_5m_N20_B10_R40` -> `WT_IN_15m_D3_P30` -> `E02_4h_N30` + 25% `WT_OUT_4h_D0_P10` -> `WT_IN_15m_D8_P30` | 19.965% | 1.839% | 10.855x | +18.125pp | 46.99% | 98.78% | 98 | VECTOR_LIFECYCLE |
| 4 | TTD_SHORT | `DC_5m_N5_B10_R10` -> `WT_IN_5m_D0_P10` -> `WT_FULL_EXIT_15m_D16_P75` -> mandatory reclaim | 30.293% | 2.839% | 10.670x | +27.454pp | 70.23% | 63.44% | 148 | VECTOR_LIFECYCLE |
| 5 | EOG_LONG | `DC_15m_N5_B100_R10` -> `WT_IN_15m_D3_P0` -> `WTDC_X45_N5_K75_DC0.8` -> mandatory reclaim | 6.606% | 0.568% | 11.627x | +6.038pp | 37.65% | 99.99% | 42 | COHORT3_VECTOR |
| 6 | BG_LONG | `DC_D_N5_B10_R40` -> same DC -> `WTDC_X40_N5_K85_DC0.8` -> `DC_5m_N20_B10_R0` | 2.737% | 0.122% | 22.397x | +2.615pp | 61.35% | 98.46% | 510 | COHORT3_VECTOR |
| 7 | DAR_LONG | `DC_5m_N40_B250_R0` -> same DC -> `WTDC_X60_N4_K85_DC0.85` -> `WT_IN_5m_D8_P75` | 15.457% | 1.127% | 13.718x | +14.330pp | 85.68% | 96.21% | 220 | COHORT3_VECTOR |

The strongest lower-risk/activity choices for replay are `USAR_LONG`,
`PSX_LONG`, and `ACN_SHORT`; `TTD_SHORT` clears 10x while carrying 70.23%
drawdown. `DAR_LONG` and `BG_LONG` clear the multiple but have high drawdown or
low absolute B&H, so they remain research candidates rather than defaults.

## Near-10x frontier retained

| key | multiple | strategy/mo | B&H/mo | DD | TIM | closes | exact next action |
|---|---:|---:|---:|---:|---:|---:|---|
| NVDA_LONG | 9.030x | 38.274% | 4.238% | 40.56% | 99.84% | 15 | replay D WT full-exit recipe |
| VT_LONG | 9.344x | 13.923% | 1.490% | 19.90% | 99.74% | 7 | replay 4h/D exit alternatives; low-close warning |
| LAC_SHORT | 9.779x | 20.602% | 2.107% | 40.02% | 37.69% | 95 | replay WT/DC X60 recipe; high-DD check |
| MU_LONG | 8.738x | 178.196% | 20.394% | 87.90% | 99.39% | 10 | replay only as high-risk comparison |
| CIBR_LONG | 8.436x | 79.319% | 9.402% | 18.66% | 99.38% | 2 | replay; two-close result is weak evidence |
| A_LONG | 8.337x | 44.520% | 5.340% | 18.32% | 98.83% | 39 | replay WT/DC X60 recipe |

## Exact V8 replay queue

Dispatch on S1 only, in this order:

1. `USAR_LONG`, `PSX_LONG`, `ACN_SHORT`, `TTD_SHORT` (10x winners with
   meaningful closes/activity).
2. `EOG_LONG`, `BG_LONG`, `DAR_LONG` (10x winners with source/DD caveats).
3. `NVDA_LONG`, `VT_LONG`, `LAC_SHORT` (near-10x confirmation).
4. `MU_LONG`, `CIBR_LONG`, `A_LONG` (high-return/low-close or high-DD
   diagnostics).

Each replay must preserve first-strictly-later execution, completed-parent
availability, 5 bps one-way stock slippage, `$2,000` B&H unit, `$16,000`
capacity ceiling, and mandatory stored-level reclaim. Record NPZ hash,
future-HTF count, capacity clamps, adapter status, and real closes in the
receipt. Do not promote from this queue without an exact V8 receipt.

## Missing-data / evidence notes

All cohort-3 NPZs report zero future-HTF observations and zero capacity clamps.
Several symbols use disclosed bounded 15m-derived 5m bridging (PSX 94.9%,
USAR 87.5%, EOG 94.9%, BG 88.7%, DAR 88.0%, ACN/TTD/LAC in the pilot bundle),
so those rows remain `VECTOR_LIFECYCLE` until exact replay. `QRVO_LONG` is an
honest unresolved loss and is excluded. Existing hotlist rows marked
`NOT_DISPATCHED_ADAPTER_UNVERIFIED` are not treated as exact evidence.

Source receipts: `PATH_COMBINATION_RUNDOWN_20260802.md`,
`VECTOR_LIFECYCLE_PRIORITY_RESULTS_20260801.md`,
`COHORT3_VECTOR_LIFECYCLE_20260802.md`, and the S1
`vector_full_matrix_20260804/campaign_receipt.json`.
