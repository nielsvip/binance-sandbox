# Cohort 3 causal vector lifecycle and scalar results — 2026-08-02

## Scope and evidence contract

- Active keys, in canonical `symbols_trb_long.json` order after prior cohorts:
  `CIBR_LONG`, `PSX_LONG`, `USAR_LONG`, `EOG_LONG`, `QRVO_LONG`,
  `GOOGL_LONG`, `A_LONG`, `BG_LONG`, `DAR_LONG`, and `TRGP_LONG`.
- S1 NPZ directory: `data/matrix_npz/cohort3_causal_v7_20260802`.
- Lifecycle source SHA-256:
  `de20ce46dfefc4364cd170078aa51c16bc81840c38f0ba09cfc8eec7f0444a98`.
- All replays use first-strictly-later execution, completed-parent availability,
  zero stock commission, 5 bps adverse one-way slippage, a $2,000 B&H/base
  unit, and a $16,000 capacity ceiling.
- All ten NPZs pass `core`, `ladder`, and `floor`; future 15m/1h/4h/D source
  observations are zero. All selected rows have zero capacity clamps and
  account drawdown below 100%.
- These are VECTOR_LIFECYCLE receipts, not exact V8 and not live promotions.
  Exact V8 cells and live configuration were not touched.

## Selected behavior-unique lifecycle results

| key | gain/mo | side B&H/mo | delta/mo | B&H multiple | DD | TIM | closes | closes/mo | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CIBR_LONG | 79.3192% | 9.4023% | +69.9169pp | 8.436x | 18.66% | 99.38% | 2 | 0.47 | PASS, outside TIM target |
| PSX_LONG | 16.2579% | 1.0761% | +15.1818pp | 15.108x | 42.54% | 98.64% | 423 | 15.12 | PASS, outside TIM target |
| USAR_LONG | 145.4748% | 1.8218% | +143.6530pp | 79.850x | 35.02% | 95.75% | 104 | 6.28 | PASS; 50–80% TIM alternative also found |
| EOG_LONG | 6.6061% | 0.5682% | +6.0379pp | 11.627x | 37.65% | 99.99% | 42 | 1.50 | PASS, outside TIM target |
| QRVO_LONG | -29.5290% | -1.0506% | -28.4784pp | n/a | 35.83% | 70.06% | 238 | 97.54 | **UNRESOLVED — honest losing best retained** |
| GOOGL_LONG | 34.8942% | 4.7000% | +30.1942pp | 7.424x | 44.20% | 99.97% | 4 | 0.14 | PASS, outside TIM/activity target |
| A_LONG | 44.5197% | 5.3397% | +39.1800pp | 8.337x | 18.32% | 98.83% | 39 | 10.48 | PASS, outside TIM target |
| BG_LONG | 2.7370% | 0.1222% | +2.6148pp | 22.397x | 61.35% | 98.46% | 510 | 18.24 | PASS, outside TIM target |
| DAR_LONG | 15.4567% | 1.1268% | +14.3299pp | 13.718x | 85.68% | 96.21% | 220 | 7.87 | PASS, high DD/TIM |
| TRGP_LONG | 38.4681% | 5.0416% | +33.4265pp | 7.630x | 27.36% | 100.00% | 1 | 0.04 | PASS; target-TIM alternative preferred for activity |

The requested TIM band is a preference rather than a rejection gate. The
profit-ranked winner remains recorded, but these target-band alternatives are
also preserved:

- `USAR_LONG` `lcb-3cce17e675aa2ab12e0f`: 93.1192%/mo,
  +91.2974pp versus B&H, 60.86% DD, 59.63% TIM, 1,381 closes.
- `TRGP_LONG` `lcb-b6e75787ed030b9a8a2b`: 22.2748%/mo,
  +17.2332pp versus B&H, 19.04% DD, 63.56% TIM, 149 closes.

No profitable >2%/month, positive-delta 50–80% TIM representative existed in
the tested behavior-unique pool for the other eight keys.

## Exact selected path recipes

| key | entry | augment | reduce | exit | reenter | sizing |
|---|---|---|---|---|---|---|
| CIBR_LONG | `DC_5m_N40_B10_R0` | `WT_IN_15m_D16_P0` | none | `E02_4h_N30` | mandatory exit-price reclaim | entry 2u; augment 2u |
| PSX_LONG | `DC_5m_N5_B100_R0` | same DC | `WT_OUT_15m_D16_P10`, 25% | `E02_4h_N30` | `DC_15m_N5_B40_R10` | entry 4u; augment 2u |
| USAR_LONG | `DC_5m_N10_B100_R40` | same DC | `WT_OUT_4h_D3_P75`, 75% | `WT_FULL_EXIT_1h_D16_P30` | `WT_IN_15m_D8_P0` | entry 8u; augment 1u |
| EOG_LONG | `DC_15m_N5_B100_R10` | `WT_IN_15m_D3_P0` | `WT_OUT_4h_D0_P75`, 25% | `WTDC_X45_N5_K75_DC0.8` | mandatory exit-price reclaim | entry 4u; augment 2u |
| QRVO_LONG | `DC_5m_N20_B40_R0` | `WT_IN_5m_D0_P10` | `WT_OUT_5m_D0_P75`, 75% | `WTDC_X25_N3_K75_DC0.85` | `WT_IN_5m_D0_P10` | entry 8u; augment 1u |
| GOOGL_LONG | `DC_5m_N5_B10_R0` | same DC | none | `WT_FULL_EXIT_D_D16_P30` | mandatory exit-price reclaim | entry 8u; augment 2u |
| A_LONG | `DC_5m_N20_B250_R40` | `WT_IN_5m_D0_P0` | `WT_OUT_1h_D8_P10`, 25% | `WTDC_X60_N4_K85_DC0.85` | mandatory exit-price reclaim | entry 4u; augment 2u |
| BG_LONG | `DC_D_N5_B10_R40` | same DC | `WT_OUT_15m_D8_P30`, 25% | `WTDC_X40_N5_K85_DC0.8` | `DC_5m_N20_B10_R0` | entry 4u; augment 2u |
| DAR_LONG | `DC_5m_N40_B250_R0` | same DC | `WT_OUT_4h_D3_P10`, 75% | `WTDC_X60_N4_K85_DC0.85` | `WT_IN_5m_D8_P75` | entry 1u; augment 2u |
| TRGP_LONG | `DC_4h_N5_B250_R40` | `WT_IN_5m_D16_P0` | none | `WTDC_X45_N5_K75_DC0.8` | `DC_5m_N5_B10_R0` | entry 6u; augment 2u |

Every full exit retains the prior-notional exit-price reclaim target. Broadly
duplicate recipes are preserved in raw evidence but quarantined by behavior
fingerprint; they never create repeated scalar cells.

## NPZ provenance and bounded 15m-derived 5m disclosure

| symbol | rows | 15m-derived 5m | authentic 5m | future HTF | NPZ SHA-256 |
|---|---:|---:|---:|---:|---|
| CIBR | 10,817 | 20.307% | 79.693% | 0 | `c76838590b320a89e043a3dac630d5e41c58728585fed65c7603ed5025041fa6` |
| PSX | 56,140 | 94.919% | 5.081% | 0 | `743e1216ab8ba981e97bb6c9a4e1cf0485478add03b84f168ac9df9a412d1907` |
| USAR | 65,412 | 87.541% | 12.459% | 0 | `86d363918d4be654df382888c173f540eb483553990e35f31c0ae006f52f7a66` |
| EOG | 57,992 | 94.917% | 5.083% | 0 | `16bd1f469d3020443ee46e8006b33792204382a3ab70edd858b89aa345319af8` |
| QRVO | 4,452 | 12.271% | 87.729% | 0 | `19d1e56f1f9bc17173903366e5a756cada7c2ae65498e048e770592ae26fc953` |
| GOOGL | 115,470 | 84.649% | 15.351% | 0 | `6ceeb999129acb03faf956d2a74aaafefa0abfad6062aaba7f17e01571c3d47a` |
| A | 7,423 | 57.522% | 42.478% | 0 | `546895d302211af2b798509770b60a588086988e77884b41d477701a24738b35` |
| BG | 52,219 | 88.737% | 11.263% | 0 | `362370408c0fad1e853b86f6fea856c59d2911cbfb5e395ae446661d4bc37b51` |
| DAR | 51,689 | 87.954% | 12.046% | 0 | `cad8d2f9c9425c845bc9f446f502a3a4fe56a0c34adc3cdcdecb95f94131abe8` |
| TRGP | 51,114 | 88.700% | 11.300% | 0 | `49f3a4a128680f9d47932931663e0fdfb7b4ae25e6dfd5813abf3a4ae2f9615c` |

USAR and QRVO have a material gap in the authentic 5m cache. Their continuous
15m cache causally bridges that interval, with explicit synthetic provenance;
no bar is represented as authentic 5m. This is accepted bounded interpolation
under the stated data contract but remains a source caveat, especially for the
unresolved QRVO result.

## Scalar causal matrix lane

The explicit reviewed-adapter lane evaluated 15 append-only scalar cells per
key (150 new rows). `tools/audit_vector_cell_contract.py` accepted only 50
unique nonzero moved behaviors and quarantined 100 inert/broad-duplicate rows.

| key | strict PASS | quarantined |
|---|---:|---:|
| A_LONG | 1 | 14 |
| BG_LONG | 7 | 8 |
| CIBR_LONG | 4 | 11 |
| DAR_LONG | 3 | 12 |
| EOG_LONG | 6 | 9 |
| GOOGL_LONG | 12 | 3 |
| PSX_LONG | 5 | 10 |
| QRVO_LONG | 0 | 15 |
| TRGP_LONG | 12 | 3 |
| USAR_LONG | 0 | 15 |

The exporter-visible union receipt is
`data/reports/TRB_ACTIVE_VECTOR_CELL_AUDIT_20260802_COHORT3_UNION.json`.
It contains the four prior pilot/MSTR ledgers plus these ten ledgers: 1,889 raw
logical cells, 89 strict PASS cells (prior 39 + cohort 50), 1,800 quarantined,
and 97 causal recalculation groups. All 14 source paths and SHA-256 hashes are
embedded. Lifecycle combinations were not broadcast into scalar cells.

## Dashboard/workbook adapters

Portable signed adapters live under
`data/reports/vec_research/<symbol>_long_lifecycle_causal_20260802`.
`lifecycle_dashboard_rows()` and `lifecycle_workbook_rows()` discover all ten.
They are correctly labeled `VECTOR_LIFECYCLE_REMOTE_RECEIPT_UNVERIFIED`
because raw ledgers and NPZs remain on S1; exact-ledger chart markers are not
claimed. Source envelope/adapter hashes remain linked inside each portable
receipt.

Verification: 35 lifecycle/reporting tests pass. The strict scalar reporting
subset passes 12/12 tests. The union audit and its ten new ledgers were locked
after hashing for serialized workbook deployment.
