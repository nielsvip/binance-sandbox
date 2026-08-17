# C5 shadow contract cutover evidence — 2026-07-30

Status: **SHADOW CONTRACT PASS; ACTIVE CUTOVER NOT PERFORMED**

This receipt covers engine/accounting integrity only. It does **not** promote
the canary strategy settings to live trading and does not assert that either
strategy beats buy-and-hold. The active c4 code, frozen c4 rows, canonical
matrix, and matrix database were not modified by the canary.

## Binding

- Contract: `tradier-matrix-exec-c5-20260730`
- Campaign: `stocks_repaired_20260730_c5`
- Window: `[2026-05-01, 2026-07-21)`
- Exact S1 receipt:
  `/home/niels/binance-sandbox/data/reports/c5_contract_canary_20260730/report.json`
- Receipt SHA-256:
  `8608e211693743081d99e0c34d3ec95d2972b2f429eb2204fe9880d8f1af0177`
- C5 database rows after the canary: `0`
- Runtime TIM policy: ranks 1–10 on each side `50–80%`; all remaining
  tradeable keys `20–60%`

The canary uses the existing green-arrow ladder, disables the legacy
`WT_3M_FORCE_OPEN` and separate `LR_BAND_ENTRY` repopulation paths, shapes
occupancy with causal `MTF_DC_REJECT_EXIT` on 1h, and applies candidate-only
STDEV rejection exits. MU additionally disables the six WT/DC scorer fallback
entries that left its otherwise valid canary 1.1615 percentage points above
its runtime TIM ceiling.

## Exact hard-gate results

| Key | Runtime rank/band | Candidate TIM | Control → candidate action fingerprint | STDEV closes | Opposite-side rows | Peak requested / executed / engine | Reentry violation / pending / reclaim | GR trace | Result |
|---|---:|---:|---|---:|---:|---:|---:|---|---|
| `MU_LONG` | 46 / 20–60% | 58.7346% | `35:exact-actions-v2:a20ad8a0356538f2ab0901af` → `50:exact-actions-v2:724977094022e316d26acb36` | 19 | 0 | $11,856.13 / $11,856.13 / $11,856.13 | 0 / 0 / 0 | valid, 1,469 candidate rows | PASS |
| `ACN_SHORT` | 47 / 20–60% | 52.1517% | `30:exact-actions-v2:c77b9d712d2ddbfef506f072` → `43:exact-actions-v2:fce78d0297637da3be093004` | 14 | 0 | $15,915.42 / $15,915.42 / $15,915.42 | 0 / 0 / 0 | valid, 1,635 candidate rows | PASS |

Contract fingerprints:

- `MU_LONG`:
  `tradier-matrix-exec-c5-20260730:86e78732b6da08cb5cad4d7e8be2a3c5e1a283fa38a815b408a1c31f58a3fd11`
- `ACN_SHORT`:
  `tradier-matrix-exec-c5-20260730:b1f91e132ff4b1214e644bee11f3c8dea50f1fe36ff90a257cf97cff7c342baa`

The contract fingerprint reads staged c5 code where present, active supporting
code otherwise, and the exact frozen NPZ. It no longer accidentally hashes the
active c4 engine in place of the shadow engine.

## Frozen data

- MU NPZ:
  `/home/niels/binance-sandbox/data/matrix_npz/stocks_repaired_20260725_c2/MU.npz`
  — `f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3`
- ACN NPZ:
  `/home/niels/binance-sandbox/data/matrix_npz/stocks_repaired_20260725_c2/ACN.npz`
  — `c38b4c82ecb1b8abb0c04693ac882f94fdfbbc6ff5ea978c5542da1d3aad5e3d`

## Repairs proved by the exact canaries

1. Forced-side execution is enforced both before pending fills and at the
   final exact-tradeable decision, so a forced LONG cannot emit SHORT P&L rows
   and vice versa.
2. Requested and executed open quantities are clamped at the last execution
   boundary and real position state is updated from actual fills. Both canaries
   remained under the $16,000 stock capacity.
3. Any actual flat-to-open fill now resolves the outstanding mandatory-reentry
   trace. The lifecycle no longer depends on the broker reason string
   containing `MANDATORY_REENTRY`.
4. Multi-timeframe opposition can delay a reclaim only for a bounded number of
   bars; it cannot silently forget it after a favorable overshoot.
5. STDEV reads the raw indicator snapshot and compares causal completed-parent
   values. Candidate STDEV exits fired and changed the exact action
   fingerprint on both LONG and SHORT canaries.
6. GR HTF scorer handoff is recorded in a fresh, per-leg trace. Stale trace
   files are deleted before each run, and symbol/side/version/stage mismatches
   fail the canary.
7. The action fingerprint covers quantities, cash flows, partial closes, exit
   reasons, exposure integrals, and ordered action events rather than only a
   coarse trade/result tuple.

## Tests and unchanged active code

- Local focused c5/contract suite: `93 passed`
- S1 shadow focused suite before the final canary: `35 passed`; subsequent
  focused reentry/runner suite: `18 passed`

Active S1 hashes remained:

- `backtest_v8_engine.py`:
  `f68b50a1460a1b31be42274b491072a203819102658244151d1ca5018a5739e8`
- `tradier_manage.py`:
  `0c3a847c62e907832b0daa2ad5e95f46f5e4b5c3874d8b27f352711c2014b3f9`
- `tools/param_results_store.py`:
  `73e92f6120b7e05f464058ccfc96bf09634d5e3ef59e34b99cc5ae2039b490fe`
- `tools/persym_baseline_campaign.py`:
  `c0dd35706e1ba0015594c638fdc0dbc8253fd8bf64aa539125ac3c8ae5d2a1bf`

## Cutover order

If the parent operator approves the contract cutover, the safe order is:

1. Atomically install the staged c5 engine/manage/helpers.
2. Bind the daemon/store/exporter to
   `tradier-matrix-exec-c5-20260730` and
   `stocks_repaired_20260730_c5`.
3. Start only isolated c5 workers and require the same exact-action, side,
   capacity, reentry, GR-trace, causal-STDEV, and runtime-TIM gates.
4. Accept new c5 rows without relabeling or mutating any c4 history.
5. Regenerate the current matrix/digest only after accepted c5 rows exist.

Strategy promotion remains a separate decision. In particular, the structural
canary PASS must not be interpreted as permission to put the ACN candidate
strategy live; its negative P&L is exactly why matrix optimization and
buy-and-hold comparison remain downstream gates.
