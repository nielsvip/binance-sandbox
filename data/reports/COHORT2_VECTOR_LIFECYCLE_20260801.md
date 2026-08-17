# Cohort 2 causal vector lifecycle results — 2026-08-01

## Scope and evidence contract

- Keys: `AXTI_SHORT`, `CIEN_SHORT`, `MNTS_SHORT`, `MP_LONG`, `AG_LONG`, `LSCC_SHORT`, `HL_LONG`, `MRVL_LONG`, `AMD_LONG`, `DELL_LONG`.
- S1 NPZ directory: `data/matrix_npz/cohort2_causal_v7_20260801`.
- Final lifecycle source SHA-256: `de20ce46dfefc4364cd170078aa51c16bc81840c38f0ba09cfc8eec7f0444a98`.
- Every accepted replay uses first-strictly-later execution, completed-parent availability, 5 bps one-way stock slippage, zero commission, a $2,000 base unit, and a $16,000 capacity ceiling.
- All ten accepted results have zero future-HTF observations, zero capacity clamps, and account drawdown below 100%.
- These are vector lifecycle receipts, not exact/V8 receipts and not live promotions. Exact/V8 rows and live config were not touched.
- The small signed envelopes are local and discoverable by the workbook and port 5077. Raw ledgers and NPZs remain on S1, so the Mac correctly labels them `VECTOR_LIFECYCLE_REMOTE_RECEIPT_UNVERIFIED` until those large artifacts are copied and rehashed locally.

## Selected best behavior-unique results

| key | window start | gain/mo | side B&H/mo | delta/mo | B&H multiple | DD | TIM | closes | closes/mo | unique / duplicate-quarantined |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AXTI_SHORT | 2024-04-01 | 233.5101% | 23.9804% | +209.5297pp | 9.7376x | 22.2998% | 99.7799% | 61 | 28.4459 | 209 / 43,825 |
| CIEN_SHORT | 2024-04-01 | 214.8574% | 13.5423% | +201.3151pp | 15.8657x | 16.1051% | 85.1527% | 35 | 14.3436 | 429 / 43,605 |
| MNTS_SHORT | 2024-04-01 | 596.0235% | 36.8841% | +559.1395pp | 16.1594x | 49.2334% | 99.1667% | 8 | 3.7306 | 334 / 42,884 |
| MP_LONG | 2026-03-01 | 4.4305% | -6.0561% | +10.4866pp | n/a: B&H negative | 35.6252% | 90.4876% | 117 | 23.5483 | 3,190 / 40,844 |
| AG_LONG | 2026-03-01 | 5.3215% | -10.4460% | +15.7674pp | n/a: B&H negative | 5.0166% | 1.1264% | 16 | 3.2203 | 1,019 / 43,015 |
| LSCC_SHORT | 2026-03-01 | 60.8014% | 7.4281% | +53.3733pp | 8.1853x | 24.7446% | 99.9169% | 1 | 0.4663 | 734 / 43,300 |
| HL_LONG | 2025-08-01 | 164.3767% | 11.9542% | +152.4225pp | 13.7506x | 45.4440% | 99.7617% | 3 | 0.2507 | 1,558 / 42,476 |
| MRVL_LONG | 2026-04-15 | 225.3320% | 11.7251% | +213.6069pp | 19.2179x | 26.4262% | 50.1186% | 30 | 8.5123 | 1,921 / 42,113 |
| AMD_LONG | 2026-03-01 | 282.9299% | 29.2343% | +253.6956pp | 9.6780x | 22.5738% | 99.9042% | 2 | 0.4025 | 1,300 / 42,734 |
| DELL_LONG | 2024-04-01 | 197.9097% | 15.8163% | +182.0934pp | 12.5130x | 22.2571% | 90.8663% | 31 | 14.4561 | 9,015 / 35,019 |

`multiple_vs_positive_bh` is intentionally absent when the side-specific B&H return is negative. MP and AG still clear the operator rule because strategy gain is above 2%/month and delta versus the correctly sided B&H is positive.

## Exact selected lifecycle recipes

| key | entry | augment | reduce | exit | reenter | sizing |
|---|---|---|---|---|---|---|
| AXTI_SHORT | `DC_D_N20_B40_R0` | same DC | `WT_OUT_5m_D16_P75`, 25% | `WT_FULL_EXIT_4h_D0_P30` | `DC_15m_N5_B250_R0` | entry 1u; augment 2u |
| CIEN_SHORT | `DC_1h_N20_B100_R10` | `WT_IN_5m_D0_P10` | `WT_OUT_1h_D3_P30`, 75% | `WT_FULL_EXIT_15m_D8_P75` | `WT_IN_5m_D0_P75` | entry 4u; augment 2u |
| MNTS_SHORT | `DC_5m_N20_B40_R0` | `WT_IN_5m_D0_P10` | none | `WT_FULL_EXIT_1h_D16_P10` | `WT_IN_15m_D0_P30` | entry 8u; augment 2u |
| MP_LONG | `DC_15m_N40_B250_R0` | `WT_IN_15m_D16_P10` | `WT_OUT_1h_D0_P30`, 75% | `WT_FULL_EXIT_15m_D0_P10` | `DC_5m_N10_B250_R40` | entry 6u; augment 1u |
| AG_LONG | `DC_5m_N20_B100_R0` | `WT_IN_15m_D0_P10` | `WT_OUT_15m_D3_P0`, 75% | `WTDC_X40_N3_K75_DC0.8` | mandatory exit-price reclaim | entry 8u; augment 2u |
| LSCC_SHORT | `DC_4h_N5_B10_R10` | same DC | none | `WT_FULL_EXIT_D_D0_P0` | mandatory exit-price reclaim | entry 1u; augment 1u |
| HL_LONG | `DC_5m_N20_B40_R0` | `WT_IN_5m_D3_P0` | none | `WT_FULL_EXIT_D_D16_P75` | `WT_IN_15m_D16_P0` | entry 8u; augment 1u |
| MRVL_LONG | `DC_5m_N40_B100_R40` | same DC | none | `WTDC_X40_N5_K85_DC0.85` | mandatory exit-price reclaim | entry 8u; augment 2u |
| AMD_LONG | `DC_5m_N5_B40_R10` | same DC | none | `WT_FULL_EXIT_D_D0_P0` | mandatory exit-price reclaim | entry 6u; augment 2u |
| DELL_LONG | `DC_5m_N5_B40_R10` | same DC | `WT_OUT_15m_D8_P10`, 75% | `WT_FULL_EXIT_15m_D16_P30` | `DC_15m_N10_B10_R10` | entry 8u; augment 2u |

Every full exit retains the runner's `prior_notional` reclaim target. Duplicate recipes that produced the same behavior fingerprint were retained in the uniqueness receipts as `QUARANTINED_DUPLICATE_BEHAVIOR`; they do not create repeated scalar cells.

## TIM-target alternatives found in the raw hash-bound ledgers

- AXTI_SHORT: 214.2441%/mo, 8.9341x B&H, 27.7922% DD, 58.0352% TIM, 247 closes (`lcb-f84fe139e7048961394d`).
- CIEN_SHORT: 164.6970%/mo, 12.1617x B&H, 21.8611% DD, 76.7151% TIM, 59 closes (`lcb-7582b3845dff75392a94`).
- HL_LONG: 59.8760%/mo, 5.0088x B&H, 24.9057% DD, 24.0606% TIM, 539 closes (`lcb-72013d0e562e4e5cfdf0`).
- MRVL_LONG selected best already has 50.1186% TIM and 19.2179x B&H.
- AMD_LONG: 134.8721%/mo, 4.6135x B&H, 18.1995% DD, 59.92% TIM, 168 closes. This is below the requested 5x multiple, so the higher-return 9.6780x result remains selected.
- DELL_LONG: 131.3670%/mo, 8.3058x B&H, 26.2186% DD, 79.24% TIM, 63 closes.
- No positive >2%/month behavior in the tested pool met the requested TIM band for MNTS_SHORT, MP_LONG, AG_LONG, or LSCC_SHORT. Their profitable best result is retained rather than fabricating a target-band cell.

## NPZ and source audit

| symbol | rows | interpolated 5m | future 15m / 1h / 4h / D | ladder contract | NPZ SHA-256 |
|---|---:|---:|---|---|---|
| AG | 102,074 | 92.491% | 0 / 0 / 0 / 0 | PASS | `77d8bd2f86136551eea7d598c3bcd96e5993e3efd01ebaa988ec4874c30ce766` |
| AMD | 116,665 | 84.700% | 0 / 0 / 0 / 0 | PASS | `ad9c4150617b9fcfa3650621e9491fe09f4004f1b450872f693e9b9265b1dba9` |
| AXTI | 10,661 | 86.957% | 0 / 0 / 0 / 0 | PASS | `e43ea853165883955270869a87fd4dcfaed2e21cd21ec526c3f5d7ba0e438e59` |
| CIEN | 9,120 | 22.083% | 0 / 0 / 0 / 0 | PASS | `da4453889f4ce56ec5cc1901bd84cf0deac08b05d5a834f5325145b0ecca7372` |
| DELL | 9,657 | 86.881% | 0 / 0 / 0 / 0 | PASS | `8916b2b2939e7f29c2caf0ba1145b06b11108b2c8fc863b607924a83f07e7980` |
| HL | 94,390 | 92.796% | 0 / 0 / 0 / 0 | PASS | `30a6168b3f54b0521526450be6243d1e7eefed0eb167276c4e8863bf5267c1e5` |
| LSCC | 4,663 | 86.877% | 0 / 0 / 0 / 0 | PASS | `33551c56fe27d498beffdcda300aa59f3c32b1d6ec9cbab02209f2096fed7acf` |
| MNTS | 8,518 | 86.861% | 0 / 0 / 0 / 0 | PASS | `c6024232a5a2a3ac4938cb20e9c09d0d9df575f3a4b8b85c7665420732721797` |
| MP | 94,559 | 85.995% | 0 / 0 / 0 / 0 | PASS | `05ec7d6595c6352fecbcd111a06e99045722c6d1af820ffe8411825b40ce9f47` |
| MRVL | 107,705 | 1.147% | 0 / 0 / 0 / 0 | PASS after 2026-04-15 | `81edda1443cee6441189e4f36b56ec5bc0ada3de5fb4f56dc0aaab4f160727ad` |

MRVL's raw 5m and 15m sources both contain a material March/April gap. The 2026-03-01 result was renamed `result.quarantined_source_gap.json` and is not discoverable. The accepted MRVL replay starts 2026-04-15, after the gap; its separate `MRVL_contract_post_gap.json` receipt passes.

## Per-key cryptographic receipts

| key | envelope SHA-256 | result receipt SHA-256 | selected-row SHA-256 | raw ledger SHA-256 |
|---|---|---|---|---|
| AG_LONG | `a798dd368edf4deb4377e0f989ff7ec289ee9a124f9b385bc12a1240a79ad6ac` | `5e90004e5b6f16cafef86ccd740f8ba69bb97a3a8a43197347d87523d5a49d8a` | `a424802c6aa0cddd3168261651e5c035f6e6695f9f14dfc7d54c6a4498ecba8f` | `73d878f807028267fbdcdbeb211755394c4a60ab0a159896128aac96fbd09605` |
| AMD_LONG | `2a476964dd8e3ee5bb79f669b3070d8bd26eb1941df584958a5cbf6f7691d7c7` | `0e5bf46dbb5e74309bb95ef928b53bcb1b3424d2088bc2dd7ef4c6542b43f1ad` | `32d990dfb84ab9eeccd4445a48c0f16e1ad2d4374a886b293f00cf73a27e19c6` | `8e50ea38eaa41816252da2c2e89dabf84634024c22157ff85e0942a6c3b8f05e` |
| AXTI_SHORT | `5c993b648f79cbac737999bbf082799efdc443fb4cb8154901ec00fdd475c146` | `8ca3abf422952fb492964a15145bfb8fc63e892db04fb8746b93c3b39bf339b1` | `e0565e22416562cb8ad86e82f8faa52a112c74d998bfc49c9c1b092c0d6f7501` | `60e45f16deb309ed3d1ebaa32a2e6a43f0c30a2fd012633ac3ff01e663f26978` |
| CIEN_SHORT | `a80a347d6ea30e452d5813e6a4f4ded88bbcfe8089cd2a6d4a2688a98ab54d36` | `90db392be7e37ba6d50ed785baca3771ce9b095fb0679cf585abec1dafdddc8a` | `c57b02dd5f3732abe93dba85c44926427481128cf614d16dda0d36fe66bdb3d4` | `40113eb1307bb54e44e51d35392708bda2081975f219014105788e0ea779829e` |
| DELL_LONG | `4b64a3210d80ff4d713a7dfc7a8dda3710da6b645821871f2ecf8a477779c238` | `5c94208e4f9ce3f51e0610c7e8eb7404343600372eb0221fc0ee488f04040f91` | `b26472420565371ccad140a0276c30a9069badf783fe80d29abdf825f19b4bf8` | `e19348e6851255495d1524663791cda82ffeaa25e7a230c8f782eae74043d9db` |
| HL_LONG | `116d4e13fa4bccfbf40adadb23041bfb2a6911d72df363c83f4eb7c3bbbcc72c` | `7f90da6aeda722abd52bba593b79d4be2f6c5fe1503533899507a6c06d936f17` | `f4157d29a342aa7b400bb992ea8af97c508722268a4187722f687564f849ff78` | `339d0e1c2111c9f220658ed591e8e206c6b22c03ad49e8ab4a453df164a35e1f` |
| LSCC_SHORT | `a401bce62b5cedecb04def276e742f6f308a165db215fc2f001f3f48812b4d26` | `d18169f3d02370fda5381592f3c18248dac668131450963a171a43454043989b` | `5e1a58da85813159d793aecfb1dee2d15fb7987bd0cdd2c6d754bf73bb8c7b48` | `f43ded6f52ec546e3f2775c005c5e9b2e9fe0b3b7ecd4e01010cc8e98386b3eb` |
| MNTS_SHORT | `e18b935d9abcbe15500fcf80375c48a12b2cacfcc1f93902fdf66ff86c1cfc46` | `10cc1a16af8ae27d3dac2f1fc1839b851dae312c1b00867b5e688e7bedbdc662` | `bc56acdf2e96a0cea21557697d0739845c323a8e2945ed18f7ae1318cb706773` | `dd2632acfeb1836508dfa552274c0486ebe1ebca902ae8cf9a9f562f44504ee1` |
| MP_LONG | `0e1c10582348a08c9d11d2e15bebaf7b33a7e1242bda272cce9d78abe51f950d` | `feb3f8cb74678da69ce83adc446067ce5b7188095933d276a80c7cd3a0a5d55f` | `c92e49da6ca3745b825b225ed72af327fe68b8b1febdf68a7eafdda30eccf5e7` | `0bcd0746bfa6f3e6b3cb96781f1b271302cd0bdbe262ab57c081100c2363fbb1` |
| MRVL_LONG | `53e54646a9ba90779438e4d039923ef451105b46e815343a41483941236f1b44` | `cce81f353f48d92ff1814e9bb299d43cab1f2013c2b9f4058aaa54bde6f235e8` | `65e1324ad06e2bd3a9c7ab89df8d0ea0b95072c5b84ff3cc8a596296b0660c85` | `620da7ad3656960b2b3666c14f809a36ada40d0bfda6e7400147bec022e3d376` |

## Dashboard and verifier checks

- Flask test client: `/current_matrix_best` returns all 10 cohort keys in `vector_lifecycle_rows`.
- Envelope fail-closed fix: `tools/build_lifecycle_receipt_envelope.py` now validates the selected row's embedded `result_sha256`, not only the outer result receipt.
- Tests: `test_lifecycle_receipt_envelope.py`, `test_current_matrix_reporting.py`, `test_lifecycle_workbook.py`, and `tools/test_build_lifecycle_receipt_envelope.py`: 23 passed.
