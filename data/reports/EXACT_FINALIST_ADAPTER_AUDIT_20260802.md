# Exact finalist adapter audit — 2026-08-02

## Outcome

**Do not dispatch exact V8 yet.** Under `BACKTEST_BIBLE.md` §16.22B-C, the
two strongest behavior-unique complete lifecycle representatives are
`USAR_LONG` and `ROKU_LONG`, but both currently fail closed as
`ADAPTER_REQUIRED`. The ordinary D/4h/1h ladder and mandatory stored-exit-price
reclaim are live/V8 shared contracts. The finalist-specific DC
break/bounce/reclaim entry, WT value/price augment, partial reduction, full
exit, optional early re-entry, and unit sizing are research-only constructs in
`tools/run_lifecycle_combo_beam.py`; there is no live recipe reader or action
site for those exact families and numeric settings in `tradier_manage.py`, and
therefore no same-path call from `backtest_v8_engine.py`.

The discovery manifests also define only one full search/evaluation window.
They contain no frozen train/untouched-validation split. The reported deltas
below are causal full-window discovery deltas, **not untouched OOS deltas**.
That missing validation receipt is a second fail-closed condition before exact
dispatch.

No V8 sweep or engine was run for this audit. No live configuration, database,
matrix, worker manifest, queue, or process was changed.

## Why these two were selected

Selection ranked the current cohort/pilot receipts by positive side-aware B&H
delta, real activity, account drawdown, zero clamps, behavior uniqueness, and
current TRB relevance. Both symbols are present in `symbols_trb_long.json`.
Their behavior fingerprints differ, so they are not duplicate recipes.

| priority | key / recipe | gain/mo | B&H/mo | delta | multiple | DD | TIM | closes | clamps | behavior fingerprint |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `USAR_LONG` / `lcb-03f7eb1dcfc58544c9fc` | 145.4748% | 1.8218% | +143.6530pp | 79.850x | 35.02% | 95.75% | 104 | 0 | `ee138ff316ee42fecf3ab1f74a743816ecc36da395a91aafe62ca9c90774e2e9` |
| 2 | `ROKU_LONG` / `lcb-31cb52e31ac1fdfb5638` | 59.8692% | 4.3603% | +55.5089pp | 13.731x | 32.19% | 93.20% | 396 | 0 | `6a0c153d1059d83322c9ed85b7d7fd0b410e5d54c683f5e2e898bf30dfb6e0e4` |

Both clear the monthly activity floor; ROKU also clears the weekly target.
Their TIM is above the preferred 50–80% top-rank band, so exact ranking should
also retain the already-discovered target-TIM alternatives after those rows
receive complete portable receipts. This audit chose the fully portable
selected recipes above because their complete machine recipes are locally
hash-bound; it did not invent missing parameters from the compact prose about
the alternatives.

## Finalist 1 — USAR_LONG complete recipe

- Window: `2024-04-01` through `2026-08-01` exclusive.
- Costs/capital: 0 bps commission, 5 bps adverse one-way slippage; $2,000 base
  unit/B&H, $16,000 capacity, $10,000 account DD denominator.
- Ordinary ladder: target semantics; union trigger; center plateau; stochastic
  extreme 30; D bottom/top `10/6`, 4h `6/4`, 1h `4/1`.
- ENTRY: `DC_5m_N10_B100_R40` — DC break/bounce/reclaim, lookback 10,
  bounce 100 bps, reclaim 40 bps; initial target uses 8 base units or the
  stronger ladder target.
- AUGMENT: the same `DC_5m_N10_B100_R40`; add 1 base unit.
- REDUCE: `WT_OUT_4h_D3_P75`; adverse 4h WT cross whose WT value changes by
  3 and whose cross price confirms by 75 bps; reduce 75%.
- EXIT: `WT_FULL_EXIT_1h_D16_P30`; adverse 1h WT cross, WT value delta 16,
  price confirmation 30 bps; close the remaining position.
- REENTER: favorable `WT_IN_15m_D8_P0` may re-open strictly later and only at
  a side-favorable price; mandatory prior-notional exit-price reclaim remains
  armed as the fallback until a fill.
- Selected result hash: `fcea875ae0c3b3a712f3a9fdcd383d27b3fd4b98c47603f9114be9b171fbafdf`.
- NPZ: `/home/niels/binance-sandbox/data/matrix_npz/cohort3_causal_v7_20260802/USAR.npz`,
  SHA-256 `86d363918d4be654df382888c173f540eb483553990e35f31c0ae006f52f7a66`.

## Finalist 2 — ROKU_LONG complete recipe

- Window and costs/capital: same contract as USAR.
- Ordinary ladder: target/union/center-plateau/stoch-30; D `10/6`, 4h `6/4`,
  1h `4/1`.
- ENTRY: `DC_5m_N5_B40_R0`; lookback 5, bounce 40 bps, zero reclaim buffer;
  initial target uses 4 base units or the stronger ladder target.
- AUGMENT: `WT_IN_5m_D16_P0`; favorable 5m WT cross with value delta 16 and
  zero price-confirm buffer; add 2 base units.
- REDUCE: `WT_OUT_4h_D0_P10`; adverse 4h WT cross with zero WT-value delta
  and 10 bps price confirmation; reduce 25%.
- EXIT: `WT_FULL_EXIT_15m_D8_P30`; adverse 15m WT cross with value delta 8
  and 30 bps price confirmation; close the remaining position.
- REENTER: favorable `WT_IN_5m_D8_P0` may re-open strictly later and only at
  a side-favorable price; mandatory prior-notional exit-price reclaim remains
  armed as the fallback until a fill.
- Selected result hash: `f9d0af69b2dd7ef87acb16c71e1f38a6048b8754f70433abe778b2d88910e93e`.
- NPZ: `/home/niels/binance-sandbox/data/matrix_npz/cohort5_long_causal_v7_20260802/ROKU.npz`,
  SHA-256 `c7966fc880ad7a534ed59fb2f1709d9b85854acabd18ed9ea2182fa7b0378a82`.

## Live and exact-engine adapter proof

| recipe component | research definition | live read/action proof | V8 same-path proof | verdict |
|---|---|---|---|---|
| Ordinary ladder and its D/4h/1h pairs, mode, trigger, base/capacity | `run_lifecycle_combo_beam.py:430-456`; shared curve D10/6, 4h6/4, 1h4/1 | `tradier_manage.py:430-577` reads completed parents and per-key ladder settings through `_PerKeyCfgView`; flat OPEN is issued at `4033-4089`; open-position target AUGMENT is issued at `9780-9832` | V8 imports the real Tradier module at `backtest_v8_engine.py:5997-6014`, routes ladder/reclaim flat keys at `9316-9497`, and calls the real `tradier_manage.process_position` at `9752-9758` | **CONNECTED** |
| DC break/bounce/reclaim ENTRY/AUGMENT (`tf`, `lookback`, `bounce_bps`, `reclaim_bps`) | `run_lifecycle_combo_beam.py:275-307`; chronological action use at `585-618` | No `DC_BREAK_BOUNCE_RECLAIM` family, `lcb-*` recipe reader, or per-recipe lookback/bounce/reclaim action site exists in `tradier_manage.py`. The live indicators expose fixed 4-bar and configured/default DC fields (`tradier_indicators.py:1335-1359`), not this arbitrary N5/N10 state machine. Existing `DC5M_TRIGGER` and re-entry bounce paths are different predicates/gates. | V8 has no lifecycle recipe reader. Calling real `process_position` cannot activate a nonexistent live action family. | **ADAPTER_REQUIRED** |
| WT favorable AUGMENT (`tf`, `value_delta`, `price_confirm_bps`, `augment_units`) | `run_lifecycle_combo_beam.py:310-343`; action use `605-618` | Live indicator production has WT cross/value history (`tradier_indicators.py:658-697`), but no `WT_CROSS_VALUE_PRICE_LADDER_IN` action family reads the finalist's TF/delta/price-confirm/unit tuple. Existing WT force-open/re-entry logic uses materially different gates and sizing. | No same live path is callable; no lifecycle recipe keys are consumed by the V8 override loader. | **ADAPTER_REQUIRED** |
| WT partial REDUCE (`tf`, `value_delta`, `price_confirm_bps`, `reduce_fraction`) | `run_lifecycle_combo_beam.py:310-343`; reduce action `574-582` | Generic `REDUCE` execution exists, but no `WT_CROSS_VALUE_PRICE_LADDER_OUT` signal reader binds the recipe tuple and 75%/25% fraction. `PARTIAL_PROFIT_LOCK_FRAC_TRADIER` is a different profit-lock path and is not parity evidence. | V8 can execute a REDUCE emitted by live code, but the finalist-specific emitter does not exist. | **ADAPTER_REQUIRED** |
| WT full EXIT (`tf`, `value_delta`, `price_confirm_bps`) | `run_lifecycle_combo_beam.py:421-426`; full close `560-572` | `tradier_manage.py:8766-8809` has WT/DC score and other WT exits, but no `WT_CROSS_FULL_EXIT` path with the finalist's exact TF/delta/price-confirm tuple. Similar names or WT inputs are not the same action path. | V8 calls the real exit code, but cannot call an absent finalist path. | **ADAPTER_REQUIRED** |
| Optional WT early REENTER (`tf`, `value_delta`, `price_confirm_bps`) | `run_lifecycle_combo_beam.py:593-603` | Live has generic WT/stoch re-entry (`tradier_manage.py:10295-10418`), but its 2-of-4, stochastic, HTF, and sizing gates differ from the vector recipe. There is no exact `WT_CROSS_VALUE_PRICE_LADDER_IN` early-reentry reader. | V8 calls the generic live route only; it cannot reproduce the finalist predicate. | **ADAPTER_REQUIRED** |
| Mandatory prior-notional stored-exit-price reclaim | vector lifecycle `551-558` and `629-644` | Live evaluates persistent obligations at `tradier_manage.py:3907-4032`, latches only after acknowledged full close at `14049-14096`, and confirms a later fill at `14145-14165`; shared state is `ordinary_ladder_contract.py:255-335` | V8 latches/confirms the same obligation model around `backtest_v8_engine.py:6906-6947` and `7025-7035`, routes it every bar at `9316-9497`, then calls the real Tradier path | **CONNECTED**, but it cannot rescue the missing optional path adapters |
| Entry/augment unit sizing and absolute target semantics | `run_lifecycle_combo_beam.py:478-490`, `526-540`, `585-618` | Ordinary ladder absolute targets are connected, but no live reader binds lifecycle `entry_units` and `augment_units` to the DC/WT actions. Generic `START_POSITION_SIZE` is not the same per-recipe contract. | No resolved V8 override names exist for these lifecycle fields. | **ADAPTER_REQUIRED** |

The absence check searched `tradier_manage.py`, `backtest_v8_engine.py`,
`backtest_v8_sweep.py`, and `config_tradier.py` for
`TRB_LIFECYCLE`, `lcb-`, `DC_BREAK_BOUNCE_RECLAIM`,
`WT_CROSS_VALUE_PRICE_LADDER_IN`, `WT_CROSS_VALUE_PRICE_LADDER_OUT`, and
`WT_CROSS_FULL_EXIT`: none occurs. Those identifiers occur only in the vector
research runner/receipts. Raw indicator availability is not a live action
adapter.

## Minimal adapter patch required before exact confirmation

1. Add one pure shared `tradier_lifecycle_contract.py` containing a strict
   recipe schema, completed-parent token dedupe, DC break/bounce/reclaim state,
   WT cross value/price confirmation, action priority, unit/fraction sizing,
   strictly-later semantics, and the existing `ReclaimObligation` handoff.
   Do not replay a precomputed research action schedule.
2. Extend `tradier_indicators.py` and `backtest_v8_precompute.py` only where
   needed to expose identical completed-parent inputs: prior Donchian N5/N10
   levels (and the already-supported N20/N40 equivalents), source-close and
   availability timestamps, plus current/previous WT cross value and cross
   price. Hash the input schema.
3. Add a per-key, fail-closed `TRB_LIFECYCLE_RECIPE_ENABLED` plus a validated
   `TRB_LIFECYCLE_RECIPE` object read in `tradier_manage.py`. Call the shared
   contract from both flat and open-position branches, route its OPEN/AUGMENT/
   REDUCE/CLOSE actions through existing order execution, and retain the
   acknowledgement-driven mandatory reclaim lifecycle.
4. In `backtest_v8_engine.py`, make the existing Tradier simulation route a
   recipe-enabled flat key on every applicable bar and inject the exact same
   validated recipe through `V8_OVERRIDE_FILE`. The engine must continue to
   call `tradier_manage.process_position`; it must not use a research-only
   action-replay adapter.
5. Freeze each candidate recipe and evaluate it without search on a declared
   untouched vector validation window. Only a hash-bound survivor may be added
   to an `EXACT_COMPLETE_RECIPE` manifest. Then run each full recipe once in
   V8; never replay or sum component scalar cells.

## Exact queue status

- `USAR_LONG`: `NOT_QUEUED_FAIL_CLOSED_ADAPTER_REQUIRED_AND_OOS_REQUIRED`.
- `ROKU_LONG`: `NOT_QUEUED_FAIL_CLOSED_ADAPTER_REQUIRED_AND_OOS_REQUIRED`.
- `data/matrix_worker_manifest.json` has launch scope
  `EXACT_ENGINE_PARITY_AND_FINALIST_CONFIRMATION`, but its six workers are MU,
  NVDA, VT, TTD, ACN, and LAC only. Neither audited candidate is present.
- `data/MATRIX_WORKERS_PAUSED` was absent at audit time. This does not authorize
  a launch; adapter and receipt gates still fail.
- No string saying “queue exact” was treated as a queue, and no job was
  dispatched.

## Hash-bound source snapshot

| artifact | SHA-256 |
|---|---|
| `BACKTEST_BIBLE.md` | `31ba6718cbd9393d693d53f7f35a0ef8772e6efa703b417d22f61f31b0eab97a` |
| `tradier_manage.py` | `60de127e25ae53b852719415b1a485364e7857ba04f5ef2ff340b8494040ebe4` |
| `backtest_v8_engine.py` | `f1e7b3c3aedb64a21b439fdd5d3631ac2d343341e08b08b6842c70b664e6058b` |
| `backtest_v8_sweep.py` | `73053c986ad1e9ca196cc6e84e7f993e1f5f388b784c1927698689e4c3b9fdc7` |
| `ordinary_ladder_contract.py` | `4869905c54fbf68fa72ffc703ac5822f42023695ebacade0f6f0c63eefb6647b` |
| `tools/run_lifecycle_combo_beam.py` | `de20ce46dfefc4364cd170078aa51c16bc81840c38f0ba09cfc8eec7f0444a98` |
| `data/matrix_worker_manifest.json` | `32d658b7949baf1b57434ae7542752a51268296f7e730d81f8bd8cd682df061f` |
| cohort-3 report | `c6c76fe459d05b8dc19183d40900806cc642fa7f5d9e3b78c8a9a739cfd069a9` |
| cohort-5 report | `bd57f571288696d219bbf07e47c809bbc65b53935d2bdc1d7749b8efb7580070` |
| USAR local campaign manifest | `2b78b4b12699ed3d5b6013a13fe314d4509b9daa1045fd2b7808093e53d452bf` |
| USAR local result adapter | `d4ac88bb3b3ef7187529c8321966002b77036673f6aaa4e594a31753e0ba8e75` |
| USAR local envelope | `c557f4fe99dda5c036c522443d38c0badf9bf5c31337da249df28735e9cad550` |
| USAR local adapter receipt | `7a8c7c83a5bfd25d22ff89fdcf284ecd0a3506a99e989f67bfcd5701ca65ecf9` |
| USAR embedded source result receipt | `184b8375177355c8774f1bcd24bbeed14cc6b9141b8ecb6ddb1538472a24814d` |
| USAR raw-results receipt | `6ddbe4e9d516ef62e19738819c16220cf8e253e7b08109a7486f27e10ff32609` |
| ROKU local campaign manifest | `6538a8055bab54b2de714da09d6b112395e47a2e94d608995d13eff4448641a3` |
| ROKU local result adapter | `d26ff77a2287a4f61007584d9becef2a8aa90adb09e833def960aa9cda213ade` |
| ROKU local envelope | `b37b5d3361effcfd1df267854bead90ebdfa03e0df8a88abfe88fc5beb2273a2` |
| ROKU local adapter receipt | `99ba2d01de52a4f392da264469572df6b05b722211e006d5d47c87de586d3c2a` |
| ROKU embedded source result receipt | `3372dfec2bd79169e982c306f2d002ea9255b8b819201dc51562b50f93bdf68b` |
| ROKU raw-results receipt | `9b91ea49993b3defe87ccd7784e4fd01d47f28eb6739d50d381d1ee1f8370170` |
| ROKU uniqueness receipt | `69685a5dcc51b9681d8742ccfe6af36d1629b889ddc1c0e97e7c84f54e3a338f` |

The NPZ files and complete raw ledgers are S1-resident; this Mac audit binds
their receipt-declared SHA-256 values but did not re-read or modify the remote
files. The machine-readable companion receipt binds this report's file hash.
