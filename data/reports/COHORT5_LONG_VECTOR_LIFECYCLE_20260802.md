# Cohort 5 LONG causal vector lifecycle and scalar results — 2026-08-02

## Controlling contract and scope

- Keys: `AAPL_LONG`, `ARM_LONG`, `BK_LONG`, `DVN_LONG`, `INTC_LONG`,
  `FANG_LONG`, `GM_LONG`, `MPC_LONG`, `ROKU_LONG`, and `RRC_LONG`.
- Discovery class: `VECTOR_DISCOVERY` under `BACKTEST_BIBLE.md` §16.22B and
  §16.24. Each key was loaded once from a newly regenerated causal V7 NPZ and
  ranked only by `tools/run_lifecycle_combo_beam.py`.
- No V8 sweep or exact-engine process was used for searching. No exact replay
  was queued here. The scalar vector lane is amber diagnostic evidence only.
- Lifecycle source SHA-256:
  `de20ce46dfefc4364cd170078aa51c16bc81840c38f0ba09cfc8eec7f0444a98`.
- Window: `2024-04-01` through `2026-08-01` exclusive, subject to each
  symbol's available causal history.
- Costs/capital: zero stock commission, 5 bps adverse one-way slippage,
  $2,000 B&H/base unit and $16,000 strategy capacity.
- Execution: completed-parent observations, first-strictly-later fill,
  mandatory zero-buffer stored-exit-price reclaim, zero selected-row capacity
  clamps, and no future HTF source observations.
- These receipts are `VECTOR_LIFECYCLE`, not exact V8 and not live
  promotions. No database, live config, or exact scalar cell was written.

## Selected behavior-unique lifecycle results

`DVN_LONG` uses the wider but still bounded causal lifecycle beam because its
standard beam reached only 1.0868%/month. The wider vector result is the
selected signed receipt.

| key | gain/mo | side B&H/mo | delta/mo | B&H multiple | DD | TIM | real closes | closes/mo | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AAPL_LONG | 23.5064% | 2.8913% | +20.6151pp | 8.130x | 23.03% | 99.77% | 163 | 5.83 | PASS; TIM above preference |
| ARM_LONG | 65.9960% | 3.1166% | +62.8794pp | 21.175x | 57.77% | 95.88% | 72 | 2.57 | PASS; target-TIM alternative retained |
| BK_LONG | 16.4280% | 4.1693% | +12.2586pp | 3.940x | 26.00% | 94.34% | 664 | 27.87 | PASS; below 5x, TIM above preference |
| DVN_LONG | 3.5732% | -0.3996% | +3.9728pp | n/a (negative B&H) | 71.04% | 95.04% | 238 | 8.51 | PASS after wider causal beam; high DD/TIM |
| INTC_LONG | 41.0688% | 3.5742% | +37.4945pp | 11.490x | 53.49% | 33.15% | 52 | 1.86 | PASS; within 20–60% non-top TIM range |
| FANG_LONG | 9.6086% | 0.0683% | +9.5403pp | 140.675x | 45.33% | 99.04% | 254 | 9.08 | PASS; TIM above preference |
| GM_LONG | 28.8699% | 3.4502% | +25.4197pp | 8.368x | 47.59% | 99.78% | 1 | 0.04 | PASS by gain rule; activity warning |
| MPC_LONG | 23.2725% | 2.0402% | +21.2323pp | 11.407x | 62.25% | 99.82% | 58 | 2.07 | PASS; target-TIM alternative retained |
| ROKU_LONG | 59.8692% | 4.3603% | +55.5089pp | 13.731x | 32.19% | 93.20% | 396 | 14.16 | PASS; target-TIM alternative retained |
| RRC_LONG | 10.3798% | 0.5572% | +9.8225pp | 18.628x | 43.88% | 99.98% | 51 | 1.82 | PASS; TIM above preference |

The selected profit winner is retained even when TIM is outside the preferred
band. Behavior-unique 50–80% TIM alternatives found by the same causal beam
are:

- `ARM_LONG` `lcb-bda057c86182aaedaec1`: 30.9720%/month,
  +27.8554pp vs B&H, 56.78% DD, 66.14% TIM, 202 closes.
- `MPC_LONG` `lcb-6ee02d4ccc14508472d9`: 8.9442%/month,
  +6.9040pp vs B&H, 55.36% DD, 61.31% TIM, 139 closes.
- `ROKU_LONG` `lcb-f512e1b99e43ac3a6f9f`: 22.8362%/month,
  +18.4759pp vs B&H, 43.95% DD, 70.84% TIM, 264 closes.

No qualifying 50–80% TIM row existed in the tested pool for the other seven
keys. `GM_LONG` also has a higher-activity alternative,
`lcb-eeb362b97d26f1cd2445`, at 24.5134%/month, +21.0632pp vs B&H,
41.04% DD, 99.78% TIM and 15 real closes. It is retained in the full S1 raw
receipt rather than replacing the higher-gain one-close row silently.

## Exact selected path recipes

The ordinary D 10/6, 4h 6/4 and 1h 4/1 ladder remains active in every recipe.
Path labels encode their numeric settings. `N` is lookback, `B` bounce bps,
`R` reclaim bps, `D` WT value delta, `P` price-confirm bps, and WT/DC labels
encode threshold/min-conditions/stochastic/DC-extreme settings.

| key | ENTRY | AUGMENT | REDUCE | EXIT | REENTER | entry/augment units; reduce fraction |
|---|---|---|---|---|---|---|
| AAPL_LONG | `DC_1h_N5_B250_R40` | `WT_IN_15m_D0_P0` | `WT_OUT_1h_D16_P0` | `WT_FULL_EXIT_D_D0_P0` | `DC_5m_N40_B10_R0` | 1/2; 50% |
| ARM_LONG | `DC_5m_N20_B40_R0` | same DC | none | `E02_4h_N20` | `WT_IN_15m_D0_P75` | 1/2; none |
| BK_LONG | `DC_5m_N5_B10_R0` | same DC | `WT_OUT_15m_D3_P0` | `WTDC_X45_N4_K85_DC0.8` | `WT_IN_15m_D0_P0` | 1/2; 25% |
| DVN_LONG | `DC_1h_N10_B100_R0` | same DC | none | `E02_1h_N20` | `DC_5m_N20_B40_R0` | 8/2; none |
| INTC_LONG | `DC_5m_N5_B40_R0` | same DC | `WT_OUT_4h_D0_P0` | `E02_4h_N10` | mandatory exit-price reclaim | 6/1; 75% |
| FANG_LONG | `DC_5m_N5_B10_R10` | same DC | `WT_OUT_15m_D8_P75` | `WT_FULL_EXIT_4h_D0_P30` | `DC_5m_N20_B40_R0` | 1/1; 75% |
| GM_LONG | `DC_5m_N10_B10_R0` | same DC | none | `WTDC_X45_N5_K75_DC0.85` | mandatory exit-price reclaim | 6/2; none |
| MPC_LONG | `DC_5m_N5_B10_R0` | `WT_IN_5m_D8_P10` | `WT_OUT_4h_D3_P75` | `E02_D_N30` | `DC_5m_N5_B10_R0` | 1/1; 75% |
| ROKU_LONG | `DC_5m_N5_B40_R0` | `WT_IN_5m_D16_P0` | `WT_OUT_4h_D0_P10` | `WT_FULL_EXIT_15m_D8_P30` | `WT_IN_5m_D8_P0` | 4/2; 25% |
| RRC_LONG | `DC_5m_N5_B10_R0` | `WT_IN_5m_D16_P0` | `WT_OUT_4h_D0_P0` | `WTDC_X45_N5_K75_DC0.8` | mandatory exit-price reclaim | 8/2; 75% |

## NPZ provenance and bounded 15m-derived 5m disclosure

All ten NPZs pass `core`, `ladder`, and `floor`; every reported
15m/1h/4h/D future-source percentage is zero. The complete hash-verified
audits are in `data/reports/cohort5_long_npz_20260802/`.

| symbol | rows | 15m-derived 5m | lineage decision | NPZ SHA-256 |
|---|---:|---:|---|---|
| AAPL | 116,587 | 86.632% | source gap disclosed; bounded 15m bridge required | `a45ea39c5128757dda10994a42f4885d3da667b2d07eae3eb9c8c4564db720bc` |
| ARM | 107,146 | 85.043% | REGENERATION_SUPPORTED | `ccded10525205c59541ecc814cc288d2a76e4f74f3fef02a4bebed9cfb81cf62` |
| BK | 47,009 | 0.000% | REGENERATION_SUPPORTED | `1992c2eb3b2ca13adbf4832f78d56495b0ca2ccaa57756fc6cb6406ee6ca6c36` |
| DVN | 85,564 | 92.627% | source gap disclosed; bounded 15m bridge required | `0cb0e3091f51a7cacb2b6502bfc5851335c4f458892d186e7cb677e1a8805d51` |
| INTC | 116,766 | 85.360% | REGENERATION_SUPPORTED | `0093b4c77e65f6d1972bd1532fa09cd1a89fb7b797910b3922dc1588f8a8c62b` |
| FANG | 60,575 | 94.797% | REGENERATION_SUPPORTED | `e17f1df88c96f66692ea5ead6a70afa9abaf09586376a3ff0102e9064119671d` |
| GM | 78,557 | 84.959% | REGENERATION_SUPPORTED | `2ea4f1452968d3699348d55ae0ce85d0ed4b416b3c6dc7704973431c91209c8e` |
| MPC | 57,055 | 87.721% | REGENERATION_SUPPORTED | `a40f544d4a0e6257ea19ea784a61d06ee9211f00cd2139f396de63cea7cbd6f2` |
| ROKU | 81,383 | 3.465% | REGENERATION_SUPPORTED | `c7966fc880ad7a534ed59fb2f1709d9b85854acabd18ed9ea2182fa7b0378a82` |
| RRC | 54,206 | 87.900% | REGENERATION_SUPPORTED | `02527aa3b3d49c84d50c991143bdc0c08cee29aa5391cbb6e06ab72da1423614` |

The authentic 5m observations for AAPL and DVN match the candidate NPZ
exactly, but each raw 5m cache contains one gap longer than seven days. The
continuous 15m source causally bridges those intervals and every bridged row is
marked synthetic; no row is represented as authentic 5m. This is permitted
bounded interpolation under the data contract, while the strict lineage tool
correctly retains `FAIL_CLOSED_SOURCE_INCOMPLETE` as a visible source caveat.

## Strict scalar amber lane and union audit

The explicit adapter lane evaluated 15 scalar cells per key. It is not the
discovery authority and cannot be summed into lifecycle deltas. Of 150 new
logical cells, 89 produced unique nonzero result/action behavior and 61 were
quarantined.

| key | strict PASS | quarantined |
|---|---:|---:|
| AAPL_LONG | 12 | 3 |
| ARM_LONG | 6 | 9 |
| BK_LONG | 10 | 5 |
| DVN_LONG | 12 | 3 |
| INTC_LONG | 12 | 3 |
| FANG_LONG | 7 | 8 |
| GM_LONG | 10 | 5 |
| MPC_LONG | 8 | 7 |
| ROKU_LONG | 8 | 7 |
| RRC_LONG | 4 | 11 |

The newest union audit is
`data/reports/TRB_ACTIVE_VECTOR_CELL_AUDIT_20260802_COHORT5_LONG_UNION.json`
(SHA-256
`71f23903b1da9ad26acd30f124b5b9b751fa21438d8730678bcae2ecf686c00f`).
It includes 34 hash-bound ledgers: prior pilot/MSTR, cohort 3, cohort 4 and
these ten. Across 2,189 logical cells it has 278 strict PASS and 1,911
quarantined cells, with seven accepted isolated two-value plateaus. No broader
duplicate was accepted. The 133-group causal recalculation queue is
`data/reports/TRB_ACTIVE_VECTOR_CELL_RECALC_QUEUE_20260802_COHORT5_LONG_UNION.json`
(SHA-256
`98bf07f234499bd826efc6a7d757edb3b6151dd8bf5d088b84d75c00929b3512`).

`campaign_must_stop=true` is intentionally retained because duplicate/inert
amber rows exist. They are quarantined rather than broadcast into the matrix;
only the 278 strict PASS rows can be considered by a later exporter.

## Signed adapter receipts

The full adapters and raw ledgers remain on S1 under
`data/reports/vec_research/cohort5_long_adapters_20260802/`. Compact signed
manifest/result/envelope/receipt copies are discoverable locally under
`data/reports/vec_research/<symbol>_long_lifecycle_causal_20260802_c5/` and
are labeled `VECTOR_LIFECYCLE_REMOTE_RECEIPT_UNVERIFIED` because raw ledgers
and NPZs are remote.

| key | adapted result receipt SHA-256 |
|---|---|
| AAPL_LONG | `638497eb198c819926d5f5f3ae4a878640b17d47b581f937028a28152d91301c` |
| ARM_LONG | `ad3452e3c10c966ee6983fd41447c56babe570de2174d00ededce7f06cd9fb6f` |
| BK_LONG | `da33b8b461cf13eb3004d77ad3f56a51360a8bf8c953b7d8cdd08ba9d65ca4b0` |
| DVN_LONG | `d88f5c764bbe1bf8dbbf53ab7f7168017619294f8e8a0d0e26063190cde6cd1b` |
| INTC_LONG | `d731517086b54d4c111383616756baa472b26b435441c0b9d3c49dc83883c38e` |
| FANG_LONG | `e27705b30ce06ae84056b7ca6fb564097968f48c986a5bbacddafb1b71635fcb` |
| GM_LONG | `a62102458f34b834d13ca5ed0247223d6c90156cbfa0e296b000d4a222f8a3dc` |
| MPC_LONG | `b2dedded8186caced6e0c96a7d972b683a4b184f37f80f672c2843a16468c02a` |
| ROKU_LONG | `08238ecda5045e058b33c74c9e42ba5329c9d5973102a3d589854d0b7b72fd2c` |
| RRC_LONG | `80160ab2c8f45000fd70f8055fcbb5ac42087a850ce6196e1b8391e38b7f83a2` |

All ten local compact adapters pass canonical manifest, result, best-row,
envelope, adapter-receipt and embedded artifact hash verification. Reporting
discovers all ten and selects the wider DVN winner. The preserved exact-V8
archive remains 7,595 rows with unchanged SHA-256
`e51b04b0b93c815c145aa8a1afa0424e699c962c7fc8b8e27a881d6e974c81c0`.

Verification suite: 38 lifecycle/reporting/scalar-contract tests pass.
