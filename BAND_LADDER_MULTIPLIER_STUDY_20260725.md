# Regression-band ladder multiplier study — 2026-07-25

## Status and safety boundary

This is an isolated `VEC_RESEARCH` study. It made no live/config/matrix writes.
`HAO_SHORT` was not tested because its ladder NPZ contract is still invalid.
The results below are a fast shortlist, not promotion evidence; an exact
`backtest_v8_engine` adapter must reproduce the same signal and fill schedule
before any matrix or live use.

The runner is `tools/vec_band_ladder_walkforward.py`. Unit tests are in
`test_vec_band_ladder_walkforward.py`.

## What the current code actually does

The remembered numbers are correctly present in the sizing pure function:

- D: 10x at the lower band to 6x at the upper band;
- 4h: 6x to 4x;
- 1h: 4x to 1x;
- below the lower band: 0x.

They are only hypotheses. A D 10x request is impossible under the declared
$16,000 capacity and $2,000 base unit, so it is clipped to 8x.

More importantly, the live path does **not** currently implement the complete
strategy the comments suggest:

1. `band_ladder_mult()` is only a sizing function.
2. The MTF-arrow caller uses a 5m price rebound from a running low, not the
   per-timeframe WT green arrows.
3. The priority LR-band caller opens on positive regression slope; it does not
   require a green arrow.
4. Both callers select one `LR_BAND_ENTRY_TF`; neither combines independent
   D, 4h and 1h ladder orders.
5. `BAND_ARROW_ENABLED`/`BAND_ARROW_ACCUMULATE` are not a proven live-parity
   implementation of the remembered D+4h+1h behavior.

Therefore this study defines the two requested entry signals explicitly:

- `green`: the existing `wt_cross_bull_<tf>` flag, sampled once when a newly
  completed D/4h/1h bar first becomes available;
- `structure`: HH+HL on that completed bar with StochRSI K low and rising;
- `union`: either event.

This wiring discrepancy should be repaired behind a backtest-only switch and
event-parity tested before any result is promoted.

## Causality and accounting contract

- NPZ fields must pass the `ladder` data contract.
- Signals use only completed HTF bars. Across MU and VT, future HTF source
  timestamps observed by the campaign: **zero**.
- A signal fills at the next RTH open.
- Costs: 5 bps commission plus 2 bps slippage per side.
- B&H deploys $2,000.
- The strategy uses a $2,000 base unit and may hold at most $16,000 of entry
  notional. Market appreciation can make later marked notional exceed $16,000,
  but the simulator never adds above the entry cap.
- Capital return is strategy P&L divided by the same $2,000 comparison unit.
  Account drawdown is measured against the $10,000 sandbox equity.
- After a full 4h Donchian-N30 exit, a lower ladder event may re-enter first.
  A zero-buffer reclaim of the stored exit/top level is mandatory.
- `exposure_weighted_tim_pct` weights every RTH bar by marked notional divided
  by the $16,000 capacity; binary TIM is also retained.

The candidate generator changes coherent six-number curves, trigger family,
interpolation shape and accumulation semantics as blocks. It does not sweep
fields one at a time. Each outer validation window freezes the winner selected
from three earlier chronological inner folds.

## MU_LONG results

Artifact on S1:

`data/reports/vec_research/band_ladder_walkforward_20260725T203411Z_MU_LONG`

The three frozen validation windows, each with an independently selected
earlier-only curve:

| validation | frozen curve | trigger / semantics | strategy capital return | B&H | x B&H | exposure-weighted TIM | account max DD | fill ratio / clamps |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 2025 H1 | D 3/1, 4h 2/1, 1h 1/0.5 linear | green / target | +226.67% | +44.09% | 5.14x | 34.37% | 12.37% | 100% / 0 |
| 2025 H2 | D 3.07/1.51, 4h 1.43/0.98, 1h 1.03/0.40 linear | union / add | +909.15% | +132.96% | 6.84x | 90.36% | 25.76% | 23.09% / 150 |
| 2026 to July 24 | D 8.56/4.98, 4h 3.96/2.23, 1h 1.95/1.36 plateau | union / target | +1,316.02% | +205.25% | 6.41x | 77.09% | 37.62% | 100% / 0 |

Frozen OOS aggregate:

- strategy capital return sum: **+2,451.84%**;
- B&H capital return sum: **+382.30%**;
- multiple: **6.413x B&H**;
- alpha: **+2,069.54 percentage points**;
- row-weighted exposure TIM: **68.00%**;
- worst account drawdown: **37.62%**;
- aggregate requested-to-filled ratio: **44.38%**;
- clamps: **150**;
- bars flat beyond mandatory reclaim: **0**.

The latest frozen curve is the most relevant current hypothesis. It lands
inside the requested 70–80% exposure band and did not clamp. It is still too
risky for promotion without exact-engine replay and independent schedule
parity.

### Remembered curve diagnostic

The remembered linear union/add curve produced +2,955.15% versus +666.39% B&H
over the full MU sample (4.435x), but this is **not** frozen selection evidence.
It spent 93.38% exposure-weighted time in market, reached 64.96% account
drawdown, filled only 10.27% of requested notional and clamped 708 requests.
Those numbers reject 10/6, 6/4, 4/1 as a sensible default even though the
leveraged return is positive.

MU trigger evidence over the available completed bars:

| TF | completed bars | existing green arrows | HH+HL + low/rising Stoch events | future-source events |
|---|---:|---:|---:|---:|
| D | 583 | 38 | 34 | 0 |
| 4h | 1,750 | 119 | 95 | 0 |
| 1h | 4,657 | 393 | 204 | 0 |

## VT_LONG results

Artifact on S1:

`data/reports/vec_research/band_ladder_walkforward_20260725T203401Z_VT_LONG`

VT's repaired NPZ passes the ladder contract, with the already documented
69.5-day source gap. Frozen validation stops before that gap.

The remembered curve looks attractive in a leaked full-sample diagnostic:
+191.22% versus +32.69% B&H (5.85x). Frozen OOS rejects that conclusion:

- strategy capital return sum: **-15.36%**;
- B&H sum: **+13.79%**;
- alpha: **-29.15 percentage points**;
- exposure-weighted TIM: **80.32%**;
- worst account drawdown: **35.84%**;
- bars flat beyond mandatory reclaim: **0**.

VT therefore has no accepted ladder multiplier from this campaign. Its result
is a useful warning that raw full-sample leverage can make a bad, unstable
entry/exit formula look excellent.

## HAO_SHORT quarantine

No HAO strategy numbers were produced. Its current NPZ fails because:

- 15m/1h/4h/D timestamp fields are base-timestamp aliases;
- D StochRSI is unusable;
- 4h and D regression-band position/slope fields are missing;
- the data contains a 65.2% one-bar discontinuity that still needs corporate
  action/source verification.

HAO must be regenerated and pass the same `ladder` contract before this runner
will accept it.

## Next promotion gate

1. Add an isolated engine entry adapter that emits the exact D/4h/1h completed
   WT-green and HH+HL-low/rising-Stoch events used here.
2. Make the adapter explicitly choose `target` or `add` semantics and enforce
   the $16,000 entry-notional cap with requested/filled/clamp telemetry.
3. Replay MU's latest frozen curve through `backtest_v8_engine`.
4. Require event timestamps, next-open fills, costs, TIM, capacity and P&L to
   match this research schedule.
5. Keep VT gray/rejected and HAO quarantined until each independently passes.
