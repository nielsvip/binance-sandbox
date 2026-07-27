# SHORT-native phase-2 result — 2026-07-27

## Outcome

The new vector screen keeps two independent SHORT books and does not invert a
LONG mask:

1. **bull-regime correction:** a completed 4h/D overbought top arms a brief
   short, but no position opens until a completed 1h lower-high/lower-low
   break, bearish WaveTrend rollover, or the preregistered combination;
2. **bear continuation:** D and 4h must already be bearish, price must be below
   completed moving averages, and a failed reclaim or persistent downside
   break must confirm the entry.

Across 192 data-valid candidate pairs (384 discovery-fold simulations), there
were no three-fold survivors. MRVL, LRCX and PLTR had discovery-passing
correction settings, but the settings failed the untouched 2026 FINAL fold.
IBIT's bear book was profitable in all three folds but failed the side-specific
short-B&H opportunity floor in D2 and FINAL. Exact replay was therefore
correctly empty. No live setting, canonical NPZ or matrix cell changed.

## Immutable protocol

`tools/vec_short_native_phase2.py` preregisters eight entry profiles and three
cover profiles per book. The entry profiles explicitly exercise downside
velocity and acceleration, ATR expansion, relative-volume expansion, failed
reclaims, one- or two-bar lower-high/lower-low persistence, distance from the
rolling peak/SMA, upper-wick rejection and D/4h regime. Covers are SHORT-native:
downside exhaustion, bullish structural reclaim, ATR-volatility trailing
cover, bounded max hold and a rare adverse ATR emergency. Adds require
continued bearish 1h WT and stop at $16,000.

The account contract is:

- fixed $2,000 capital unit for strategy and side-specific short B&H;
- $10,000 account ledger for solvency and drawdown;
- $16,000 hard notional capacity;
- 5 bp one-way commission and 2 bp one-way adverse slippage;
- SHORT open/add at `raw*(1-slip)` and cover at `raw*(1+slip)`;
- synthetic rows observed at parent close, completed HTF sources only, and the
  first strictly later availability batch for fills.

D1 and D2 each had to be solvent, remain under capacity, close at least two
trades, and beat `max(fixed-notional short B&H, cash=0)`. The full discovery
grid was written before selection. One setting per key was then frozen in
`DISCOVERY_FREEZE.json`; only after that receipt existed was FINAL simulated.
FINAL metrics never participated in selection.

Canonical artifact:
`data/reports/vec_research/short_native_phase2_20260727T031824Z/`.
The tracked compact receipt is
`data/reports/vec_research/SHORT_NATIVE_PHASE2_RECEIPT_20260727.json`.
Its source hash is
`b2ee40b1fc041d53e289b0bff5da7060a21537f68f7b8ee4a86d2410fb59456f`.
The embedded preregistration, discovery-grid and freeze contract hashes are,
respectively,
`e75dd83b613ea54e01c3ed3e81d49e047c14bc578fe056446e686ba8552b5fb2`,
`40dafec2eecf191826c89f4c9b5f594f3909035957850128fc5719b21e7a69dc`
and
`73d8a219f97271b12638aa6502701f8812eed6e0e1ee88902cf1619288cb068a`.

## Frozen per-key results

Returns below are fixed-$2,000 capital returns; “floor” is
`max(short B&H, 0% cash)`.

| book | key | discovery pass count | frozen setting | D1 return / floor | D2 return / floor | FINAL return / floor | FINAL account | DD | binary TIM | exits | verdict |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| correction | NVDA_SHORT | 0/24 | `C07_PERSISTENT_LHLL__COV_C_RIDE` | +13.94 / 0.00% | -3.15 / 0.00% | -7.62 / 0.00% | -1.52% | 1.64% | 2.31% | 5 | gray |
| correction | MU_SHORT | 0/24 | `C02_STRUCT_IMPULSE__COV_C_RIDE` | +38.27 / 0.00% | -14.11 / 0.00% | -57.65 / 0.00% | -11.53% | 12.28% | 6.04% | 14 | gray |
| correction | SNDK_SHORT | 0/24 | `C07_PERSISTENT_LHLL__COV_C_RIDE` | 0.00 / 11.53% | -2.28 / 0.00% | -46.47 / 0.00% | -9.29% | 12.46% | 5.78% | 12 | gray |
| correction | ARM_SHORT | 0/24 | `C03_WT_ROLLOVER__COV_C_RIDE` | +34.96 / 0.00% | +4.23 / 31.95% | -25.12 / 0.00% | -5.02% | 5.49% | 7.97% | 19 | gray |
| correction | MRVL_SHORT | 5/24 | `C08_ATR_ACCEL_SHOCK__COV_C_RIDE` | +3.68 / 0.00% | +13.23 / 0.00% | -6.89 / 0.00% | -1.38% | 1.80% | 2.01% | 5 | gray: FINAL failed |
| correction | LRCX_SHORT | 2/24 | `C01_STRUCT_BAL__COV_C_FAST` | +2.34 / 1.25% | +8.95 / 0.00% | -45.60 / 0.00% | -9.12% | 10.29% | 6.32% | 20 | gray: FINAL failed |
| correction | PLTR_SHORT | 2/24 | `C03_WT_ROLLOVER__COV_C_BAL` | +64.38 / 0.00% | +6.43 / 0.00% | +6.73 / 31.04% | +1.35% | 0.64% | 1.60% | 2 | gray: FINAL below B&H |
| bear | IBIT_SHORT | 0/24 | `B06_PERSISTENT_LHLL__COV_B_BAL` | +2.25 / 0.00% | +1.56 / 17.78% | +8.74 / 28.32% | +1.75% | 2.66% | 5.82% | 8 | gray |

All available folds were solvent and stayed within capacity. `future_htf_sources`
was zero for every selected row.

MSTR remains fail-closed because its archive has unusable/missing Stoch and LR
fields. COIN remains fail-closed because its 15m/1h/4h/D timestamps predate the
closed-bar fix. They are data errors, not zero-trade results.

## What the result says about poor SHORT performance

The problem is not simply that every metric was pointed upward. The screen did
find profitable downside windows in discovery and produced three
discovery-valid correction settings. Their failure was temporal:

- The selected NVDA/MU/SNDK/ARM settings lost in at least one discovery fold,
  so the requested overbought trigger plus first rollover is not stable enough.
- MRVL/LRCX/PLTR passed both discovery folds, then failed FINAL. In the 2026
  rebound, a completed first rollover often became a pause before the next
  advance. Most FINAL covers were bullish structural reclaims; LRCX also
  required one emergency cover. This is a real regime/generalization failure,
  not an inverted-fill artifact.
- IBIT was positive, but 5.82% FINAL time in market captured only +8.74% of
  fixed-unit return against +28.32% for remaining continuously short. The bear
  continuation book is too selective once the bear state is confirmed; it
  should test persistence/add exposure after failed reclaims without weakening
  the regime or emergency gates.

The next bounded SHORT iteration should therefore work from these regime
errors, not tune FINAL: distinguish a mere 1h rollover from a completed
multi-hour failed reclaim for correction shorts, and test longer confirmed-bear
occupancy for IBIT/crypto equities. The untouched failures stay gray so these
same settings are not rediscovered.

## Fleet, exact and matrix status

Eight attributable gray rows were appended to the path fleet:
correction entries under `ENTRY_DELTA_MTF`, and IBIT under `ENTRY_WT_DC`.
The pre-mutation backup is
`queue.db.bak_short_native_phase2_20260727T031637Z`. An idempotence rerun
appended zero and skipped all eight. The fleet rows are explicitly vector
research, `matrix_eligible=false`, `promotion_allowed=false`, and use no
fabricated same-entry control. There was no exact-eligible finalist, so no
phase-2 exact replay or matrix write occurred.

The earlier reconciled LRCX G14 exact row from §15.25 was also repaired
metadata-only. Its numeric +9.653406% fixed-$2,000 FINAL return, 4.214247%
binary TIM and 0.763120% weighted TIM did not change. The current
`EXACT_PARITY_ONLY` payload and digest now say
`FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD`,
`FIXED_2000_USD_CAPITAL_RETURN_PCT`, and display both TIM measures. Backup:
`queue.db.bak_short_exact_scope_20260727T032124Z`; the second repair run
updated zero rows.

## Rollback

Code rollback is removal/reversion of the phase-2 campaign, its tests, the
exact-scope metadata helper/migration and digest formatting. Data rollback is
restoring either named fleet backup and deleting only the phase-2 research
artifact/ingest receipt. No canonical indicator file, live configuration,
symbol list or matrix evidence was modified.
