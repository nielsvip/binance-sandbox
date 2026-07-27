# Parent-close execution clock — exact replay result (2026-07-27)

## Outcome

The research ladder vector path and exact-engine replay now share one causal
execution clock:

- native 5m rows are available at their native timestamp;
- 15m-derived 5m rows are available at
  `synthetic_5m_parent_close_ts`;
- equal availability timestamps retain stable `source_row_index` order;
- a signal fills only on the first strictly later availability;
- completed HTF source timestamps cannot exceed signal availability;
- replay spec v3 binds the ordered availability/source-row hash;
- unsafe v2 synthetic replay specs fail closed.

This route is research-only. It did not alter live configuration, the switch
matrix, symbol universes, or a canonical NPZ.

## MU_LONG

The immutable source was
`data/matrix_npz/stocks_repaired_20260725_c2/MU.npz`, SHA-256
`f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3`.
It predated the parent-close field. A versioned research copy added only
`synthetic_5m_parent_close_ts` using
`ceil(source_timestamp/900)*900`. The formula matched all 99,115 rows still
synthetic in the rolling archive with zero mismatches. Every original source
array has a hash in the S1 receipt.

Research copy:

`data/npz_recovery/mu_parent_clock_20260727/MU.npz`

SHA-256:

`82b9110fac514bc9eddfe4bc50227ab135e4d55783f7c712dc2b85e3eca77def`

The deterministic 244-curve, unchanged-seed ladder/E02 rerun produced:

| fold | return | B&H | multiple | weighted TIM | min equity | clamps |
|---|---:|---:|---:|---:|---:|---:|
| F1 | +226.6728% | +44.0878% | 5.141x | 34.3659% | $9,551.39 | 0 |
| F2 | +909.1468% | +132.9570% | 6.838x | 90.3630% | $7,758.43 | 150 |
| F3 | +1,316.0214% | +205.2519% | 6.4117x | 77.0917% | $8,210.77 | 0 |

Aggregate: +2,451.8409% versus +382.2968% B&H (6.41345x), 68.0038%
row-weighted TIM, zero insolvent folds.

The exact engine replayed untouched F3:

- 34/34 scheduled actions, no refusals;
- signal parity and completed-source recomputation passed;
- future HTF source count zero;
- exact capital return +1,316.0213778308723%;
- accounting delta `9.09e-11` bp;
- exact weighted TIM 77.09165765192097%, delta zero;
- exact binary TIM 94.16613823715916%, delta zero;
- capacity and accounting audits passed.

S1 exact receipt:

`data/reports/vec_research/parent_clock_v3_20260727/v8_exact_ladder_replay_20260727T024800Z_MU_LONG/`

F1 and F2 miss the 70–80% exposure band, and F2 has 150 clamps. This is exact
final-fold parity for a strong research control, not matrix/live promotion.
The receipt retains `matrix_written=false` and `promotion_allowed=false`.

## VT_LONG negative control

VT's causal vector aggregate remained -15.3641% versus +13.7857% B&H at
80.3196% weighted TIM. F1/F2/F3 returned -49.0286%, +95.5108%, and -61.8462%
at 92.3018%, 92.5456%, and 66.4087% weighted TIM. All folds were solvent with
zero clamps. Exact F3 still proved mechanical parity: 27/27 actions, zero
future HTF inputs, accounting delta `5.68e-12` bp, and zero TIM delta.

S1 exact receipt:

`data/reports/vec_research/parent_clock_v3_20260727/v8_exact_ladder_replay_20260727T025047Z_VT_LONG/`

VT remains rejected/gray. Its failure is evidence that the clock does not
manufacture alpha.

## HAO status

The v3 clock removes HAO's former mechanical parent-close blocker. It does
not rescue the strategy result: the preregistered HAO solvency grid found zero
strict discovery survivors. No HAO v3 exact replay was launched or claimed.

## Verification and rollback

Focused regression suite: 26 passed.

```text
python3 -m pytest -q \
  test_v8_research_ladder_adapter.py \
  test_research_availability_clock.py \
  test_vec_band_ladder_walkforward.py
```

Rollback is limited to the research clock/adapter hook and deletion of:

- `data/npz_recovery/mu_parent_clock_20260727/`;
- `data/npz_recovery/vt_parent_clock_20260727/`;
- `data/reports/vec_research/parent_clock_v3_20260727/`.

The immutable source archives and all canonical/live files remain unchanged.
