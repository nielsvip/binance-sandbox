# Cohort 4 SHORT causal vector lifecycle — 2026-08-02

## Outcome

Ten current TRB SHORT keys were regenerated and searched with causal vector
lifecycle recipes. Four clear the operator's promotion rule (`gain/mo > 2%`
and strategy result above correctly sided B&H): `USAR_SHORT`, `CLF_SHORT`,
`MP_SHORT`, and `NXE_SHORT`. Six remain
`UNRESOLVED_USE_BH`: `AAPL_SHORT`, `AGI_SHORT`, `HII_SHORT`, `RBLX_SHORT`,
`GDX_SHORT`, and `RGLD_SHORT`. `RBLX_SHORT` beats its positive B&H by 7.85x,
but remains gray because 1.7027%/month is below the explicit 2% floor.

No exact V8 search was performed. No exact cell, database, matrix workbook,
export, or live configuration was changed. Under Bible section 16.22B, only
the top one or two already-defined complete vector recipes may later be sent
to V8 for confirmation; scalar deltas must never be summed into a recipe.

## Reproducible contract

- Lifecycle source: `tools/run_lifecycle_combo_beam.py`, SHA-256
  `de20ce46dfefc4364cd170078aa51c16bc81840c38f0ba09cfc8eec7f0444a98`.
- Frozen NPZ directory on S1:
  `data/matrix_npz/cohort4_short_causal_v7_20260802`.
- Window: `2024-04-01` through `2026-08-01` exclusive.
- Costs: zero commission and 5 bps adverse one-way stock slippage.
- Capital: $2,000 B&H/base unit, $16,000 strategy capacity, $10,000 account
  drawdown denominator.
- Every full exit keeps `prior_notional` as its mandatory zero-buffer reclaim
  target. Signal execution is first-strictly-later than completed-parent
  availability.
- Base search used the bounded default beam. Every key below the 2% operator
  floor then received the wider beam: 6 streams/TF, 20 exits, 10 reentries,
  16 reductions, beam width 48. Each wide campaign evaluated 45,122 recipes.
- The separate causal/OOS SHORT-native correction/bear screen returned 0 of
  12 survivors. Its result SHA-256 is
  `e92440eb9e623a88cd5986b45a0ecf592f00bb615cf4d6f67d4f07b5b5ecfd19`.

## Selected complete recipes

| key | gain/mo | B&H/mo | delta | positive-B&H multiple | DD | TIM | closes | classification |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| USAR_SHORT | 24.3108% | -1.8376% | +26.1483pp | n/a | 26.49% | 2.42% | 48 | VECTOR WINNER |
| CLF_SHORT | 14.0261% | 1.7679% | +12.2582pp | 7.934x | 54.00% | 99.90% | 149 | VECTOR WINNER |
| NXE_SHORT | 2.3228% | -0.5318% | +2.8546pp | n/a | 15.42% | 10.73% | 379 | VECTOR WINNER |
| MP_SHORT | 2.1281% | -6.5302% | +8.6583pp | n/a | 27.35% | 6.16% | 182 | VECTOR WINNER |
| RBLX_SHORT | 1.7027% | 0.2169% | +1.4859pp | 7.851x | 56.12% | 33.29% | 235 | UNRESOLVED_USE_BH (<2%/mo) |
| AGI_SHORT | -0.2013% | -3.1531% | +2.9518pp | n/a | 15.98% | 9.24% | 134 | UNRESOLVED_USE_BH |
| GDX_SHORT | -0.0210% | -4.6617% | +4.6406pp | n/a | 0.29% | 0.15% | 7 | UNRESOLVED_USE_BH |
| RGLD_SHORT | -1.6127% | -2.1684% | +0.5558pp | n/a | 9.95% | 5.88% | 257 | UNRESOLVED_USE_BH |
| HII_SHORT | -1.7214% | -0.4419% | -1.2795pp | n/a | 53.59% | 95.65% | 1,195 | UNRESOLVED_USE_BH |
| AAPL_SHORT | -4.0661% | -2.9043% | -1.1618pp | n/a | 47.10% | 41.84% | 646 | UNRESOLVED_USE_BH |

All selected rows have zero capacity clamps and account DD below 100%.
Negative B&H is not converted into a misleading ratio.

| key | entry | augment | reduce | full exit | reenter | sizing |
|---|---|---|---|---|---|---|
| USAR_SHORT | `DC_1h_N20_B250_R40` | same DC | `WT_OUT_5m_D16_P75`, 75% | `WTDC_X40_N4_K85_DC0.8` | mandatory exit-price reclaim | entry 6u; augment 2u |
| CLF_SHORT | `DC_5m_N20_B40_R0` | none | `WT_OUT_1h_D3_P75`, 75% | `WTDC_X45_N5_K75_DC0.8` | mandatory exit-price reclaim | entry 8u |
| NXE_SHORT | `DC_5m_N5_B250_R40` | `WT_IN_5m_D8_P30` | `WT_OUT_15m_D8_P0`, 75% | `WTDC_X60_N3_K85_DC0.8` | mandatory exit-price reclaim | entry 2u; augment 2u |
| MP_SHORT | `DC_5m_N20_B40_R0` | none | none | `WTDC_X60_N3_K75_DC0.85` | mandatory exit-price reclaim | entry 4u |
| RBLX_SHORT | `DC_15m_N10_B250_R10` | `WT_IN_5m_D0_P30` | `WT_OUT_15m_D0_P0`, 50% | `WTDC_X25_N5_K85_DC0.85` | mandatory exit-price reclaim | entry 8u; augment 2u |
| AGI_SHORT | `DC_15m_N10_B250_R10` | same DC | `WT_OUT_5m_D3_P0`, 75% | `WTDC_X40_N5_K85_DC0.85` | mandatory exit-price reclaim | entry 2u; augment 2u |
| GDX_SHORT | `DC_5m_N20_B40_R0` | `WT_IN_15m_D3_P75` | `WT_OUT_5m_D8_P10`, 75% | `WTDC_X25_N3_K85_DC0.85` | mandatory exit-price reclaim | entry 6u; augment 2u |
| RGLD_SHORT | `DC_15m_N40_B250_R40` | same DC | `WT_OUT_5m_D0_P0`, 75% | `WTDC_X25_N3_K85_DC0.8` | mandatory exit-price reclaim | entry 1u; augment 2u |
| HII_SHORT | `DC_D_N5_B40_R0` | same DC | `WT_OUT_5m_D3_P10`, 50% | `WTDC_X60_N4_K85_DC0.85` | `DC_15m_N10_B10_R40` | entry 8u; augment 2u |
| AAPL_SHORT | `DC_15m_N20_B250_R10` | same DC | `WT_OUT_5m_D3_P0`, 50% | `WT_FULL_EXIT_15m_D16_P0` | mandatory exit-price reclaim | entry 2u; augment 2u |

The complete numeric recipe objects, path metadata, schedules, and selected-row
hashes are in each portable adapter's `result.json`; the table is the compact
human view, not a substitute for those machine receipts.

## NPZ causality and lineage

| symbol | rows | bridged 5m | future HTF rows | parent clock | native parity | NPZ SHA-256 |
|---|---:|---:|---:|---|---|---|
| AAPL | 116,587 | 85.028% | 0 | PASS | PASS | `a45ea39c5128757dda10994a42f4885d3da667b2d07eae3eb9c8c4564db720bc` |
| AGI | 68,662 | 94.767% | 0 | PASS | PASS | `a4161fb81793bacc4d8b2b75bce2ac9d4071af267db589f38f3c91d6cbb9f4b5` |
| CLF | 90,697 | 95.316% | 0 | PASS | PASS | `e28c93309d587188ae4b705fa727d3ae7e75088fed40e7c97c902af7e13af378` |
| GDX | 111,891 | 87.534% | 0 | PASS | PASS | `157eeecae65370b62bf5b2589e9dfaac70f9bbf45aaf5103152c3b130270becf` |
| HII | 52,626 | 94.014% | 0 | PASS | PASS | `d3527d969ebe94d10e5bc80bba40ac15d9ca35d570ebc4d93a2ca4afb4212063` |
| MP | 94,559 | 87.077% | 0 | PASS | PASS | `05ec7d6595c6352fecbcd111a06e99045722c6d1af820ffe8411825b40ce9f47` |
| NXE | 74,034 | 88.913% | 0 | PASS | PASS | `fd742d791b4a7b7600cfb1cab21fac5a1e00ed30703695f04dbf3e1b24c44ba1` |
| RBLX | 95,995 | 23.430% | 0 | PASS | PASS | `d24abc52af25543ec05d6d7a2ef7296ef6c684332e62ae41b6481a26127bbc53` |
| RGLD | 54,497 | 93.835% | 0 | PASS | PASS | `90def4ed921e0a494fa25775b1ae93e73b1759c3d7fd29f4bc5a8c390a35e87a` |
| USAR | 65,412 | 86.593% | 0 | PASS | PASS | `86d363918d4be654df382888c173f540eb483553990e35f31c0ae006f52f7a66` |

`AAPL`, `NXE`, and `USAR` have a material gap in the limited native 5m cache.
Their continuous 15m source bridges those rows, synthetic parent timestamps
match the completed 15m close, every higher-timeframe future count is zero,
and every row declared native matches raw 5m. The older lineage tool therefore
stores the conservative label `FAIL_CLOSED_SOURCE_INCOMPLETE` for those three
because it treats any native-5m gap as fatal; the actual causal NPZ contract
used by the lifecycle runner passes. This exception is disclosed rather than
silently relabeled.

## Strict scalar lane and portable union audit

The only reviewed scalar vector adapters currently available were run for all
ten keys: 150 logical cells, 100 strict PASS and 50 quarantined. Every raw
per-key JSONL is append-only. No scalar row receives exact completion or live
promotion credit.

| key | PASS | quarantined |
|---|---:|---:|
| AAPL_SHORT | 12 | 3 |
| AGI_SHORT | 9 | 6 |
| CLF_SHORT | 8 | 7 |
| GDX_SHORT | 12 | 3 |
| HII_SHORT | 12 | 3 |
| MP_SHORT | 9 | 6 |
| NXE_SHORT | 12 | 3 |
| RBLX_SHORT | 12 | 3 |
| RGLD_SHORT | 8 | 7 |
| USAR_SHORT | 6 | 9 |

The final UNION includes the prior pilot/MSTR sources, cohort 3, and cohort 4:

- 2,039 logical rows; 189 PASS; 1,850 quarantined.
- Four accepted isolated two-value plateaus, each inside a measured axis of at
  least five values. No broader duplicate is accepted.
- Prior pilot/MSTR PASS count remains 39; no accepted cell disappeared.
- Cohort-4 snapshot reporting-loader proof: `available=True`,
  `raw_rows=2039`, `passed=189`, `quarantined=1850`, overlay rows 2,039.
- Audit JSON SHA-256:
  `4f0b79632dad90b771c909e321998dee1f8b87548ffab7a5833d2646878a8059`.
- Annotated JSONL SHA-256:
  `a99c574cd56f105abe4426fc6dfbc7dec2c79409846099f8e424c259577c3a18`.
- Causal recalculation queue SHA-256:
  `07bf05c1e941594c431d1279389bb0fe7750e7c34745f0ef07b63f3b586240d4`.

The four pilot source ledgers were mirrored to the exact Mac paths named by
the union audit, with hashes `4765d040...` (MSTR), `d5543011...` (MU),
`c9fa5375...` (NVDA), and `5902569e...` (VT). This fixes the initial
S1-only-path portability failure.

After this snapshot was sealed, the active largest-union loader correctly
advanced to cohort 5: 2,189 rows, 278 PASS, and 1,911 quarantined. That newer
hash-verified union includes all ten cohort-4 ledgers and preserves the same
100 PASS / 50 quarantined cohort-4 counts. It supersedes this snapshot for
display selection without altering its evidence.

## Portable adapter receipts

Both S1 and Mac have the same isolated staging tree:
`data/reports/cohort4_lifecycle_adapters_staging_20260802`. Every artifact
listed by every `adapter_receipt.json` was rehashed on the Mac; 10 of 10 pass.

| key | adapter signed-receipt SHA-256 | selected-row SHA-256 |
|---|---|---|
| AAPL_SHORT | `98f4e094f959adb0d788e934f130c039583fbad0e2f96656cb48f30e9a0bd451` | `abfb70697ea37df6b8f5d674a866beaf3ea18d7b655a5497b551af16cd3a9ce3` |
| AGI_SHORT | `35139b1bf45e2ba452aaa1e55361681cb501602a2f468eab22c25f02f3efdabe` | `ea145c25c58d52d0329864c8c22f08cf46f634acd588bd02902ecc19e15f5e2b` |
| CLF_SHORT | `d777ef321dc927c215d44acec4b5c87331f546565586e603dcdc8646bcbafa85` | `fad896ea885e440155ce1e9e8d944c94a7ddb401cdb16cf2f210481e14a90941` |
| GDX_SHORT | `69478e8dee8b5adbe80f7d21f3177c62c207818c38407b54c72454b932d6fc9a` | `c8cb6ec25caca42e5aca2c90173951777d3daaed33771838785b9e59f7dab02b` |
| HII_SHORT | `79e098269186704aa22f30a48d4ce9c1ca6608894d429f5a1f14fddd8b4eed00` | `0f114421299e568fdfebc6aec406ac1b5d932c1b8ca45d6198dd774b6ac43fc4` |
| MP_SHORT | `ca460948663ddcae9c72e5f07ef84f161ceacf26ad3bf3ceec8fcd44ca349183` | `a5dea149327ba4601e72335d6318b264d0ede1fad70f98fa73f737755b75928c` |
| NXE_SHORT | `dddcf1ce9ff29809824322c4e437194da7dad93ab1f21801dabb6a4e8b9f3aea` | `61174e624274a9ce76a2228c14f593a94659ea322eb8ccebe2e8c70a055fb4b2` |
| RBLX_SHORT | `be8858eb95ad0742bc63e5eb6d5d5ab288db261c1ce51af6864401dd1070f411` | `e878124a2ed16bed1a826efe2bbe15e991f6f34c16169e78ca4a0d3cd36bd795` |
| RGLD_SHORT | `2be33374b3bcc6ce85911dbca774ee85ce068c3fba6948b018282361977c3b63` | `31ca5a7b114e5d29236ccc2d252747ad816dfc4cc35bfd9575567c53bb36eaae` |
| USAR_SHORT | `a8e58dfb090977d6a238c04bc14be069b9a2b2e6476af4a9ec8cd28f3d1c61ed` | `eab9ab34b5252bc0c12bb1ec07e180d1c0d97ce16370ebd50a1e9c24176616f5` |

The staging directory is intentionally outside the current workbook discovery
path. It is ready for the next atomic publish cycle after the in-progress
export completes; it must not be partially moved into discovery.
