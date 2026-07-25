# Why the stocks system is not beating buy-and-hold — structural forensic

Date: 2026-07-25  
Scope: Tradier stocks backtest, with MU_LONG, VT_LONG and HAO_SHORT as the pilot keys.  
Safety: no live configuration was promoted and no live process was restarted.

## Executive finding

The present gap to buy-and-hold is not decision-grade evidence that the trading ideas are
bad. Several independent data, signal, execution and accounting defects prevent the current
campaign from measuring the proposed strategy.

The most important distinction is:

1. **MU** has enough higher-timeframe data, but its old Tier-2 inputs were corrupted by the
   harness and its 5m history is mostly synthetic. Its vector/Tier-2 disagreement is therefore
   expected.
2. **VT** does not have a usable multi-timeframe NPZ. Almost every 15m/1h/4h indicator is zero
   or absent, Daily fields are absent, and its regression ladder is constant/missing. Zero or
   near-zero trades are a data-contract failure, not a strategy result.
3. **HAO** has only about 73 Daily observations and therefore cannot produce the configured
   200-day/400-bar long regression channels. Its D/4h ladder fields are missing. Its price
   history also contains extreme discontinuities, making the nearly +100% short B&H benchmark
   especially sensitive to split/corporate-action and borrow assumptions.

Current matrix results produced under these conditions must remain red/diagnostic. They must
not be used to select entry, exit or multiplier settings.

## P0 defects

### 1. Tier-2 permanently activated event flags

`IndicatorStore` treated **zero as missing** for every numeric 1h/4h/D field, then forward-filled
the previous nonzero value. Zero is a real value for cross events, neutral direction, score and
slope fields.

Measured on the local MU NPZ before the fix:

| field | raw active | old Tier-2 active |
|---|---:|---:|
| `wt_cross_bull_1h` | 8.171% | 99.988% |
| `wt_cross_bear_1h` | 8.172% | 99.977% |
| `wt_cross_bull_4h` | 6.904% | 99.940% |
| `wt_cross_bear_4h` | 6.911% | 99.871% |
| `wt_bullish_1h` | 49.562% | 99.988% |
| `wt_bullish_4h` | 50.188% | 99.940% |
| `wt_bullish_D` | 52.937% | 99.492% |

This makes bullish and bearish cross flags simultaneously true and turns composite alignment
almost permanently bullish. `wt_dc_entry_scorer.py` consumes these fields directly. Tier-1
vector code reads raw NPZ arrays and does not apply this corrupting fill, explaining why an
apparently strong vector candidate can collapse or become inert in Tier-2.

String states had the same defect: `NONE`, `NEUTRAL` and empty divergence states were replaced
by the last active state.

**Fixed in the backtest harness:** only actual NaNs/None are now fillable; zero, `NONE`,
`NEUTRAL` and empty event states are preserved. Six integrity tests pass. Fresh S1 loads now
match the raw arrays for the audited fields. Every older result needs a provenance check or a
fresh replay.

### 2. Higher-timeframe look-ahead

The precompute resamples with opening labels and broadcasts with:

```python
np.searchsorted(tf_ts, ts_epoch, side="right") - 1
```

The resulting completed 1h/4h/D OHLC and indicators are visible from the **start** of their
interval. On MU at 13:00, the snapshot already contains the high, low and close from the rest
of that hour. Across 4,045 sampled MU hour labels, the already-visible final hourly close
differed from the contemporaneous price by 0.69% on average and up to 13.70%.

Daily data has the same problem: at an RTH open, the complete future Daily range was already
present. The sampled Daily high-low range averaged 5.78% of opening price.

This corrupts arrows, HH/HL and LH/LL patterns, StochRSI turns, regression slopes, Golden Rule,
Donchian levels and exits. It can create excellent vector results that cannot exist live.

**Required fix:** regenerate Tradier NPZs with an explicit information-availability contract.
The safe initial contract is “last fully closed HTF bar”; an exact live-parity version may
instead recompute the developing HTF bar using only base bars available at each timestamp.
No matrix result should be promoted before a prefix-invariance test proves that appending
future bars cannot change an earlier snapshot.

### 3. Base 5m history is often fabricated after the fact

`backtest_v8_precompute.py` fabricates three 5m closes from each 15m bar by linear interpolation
from the previous close to the current 15m close. It also fabricates the high/low path. This
uses the final 15m outcome to invent earlier 5m bars.

Measured exact-linear centered 5m points:

| symbol | exact-linear points |
|---|---:|
| MU | 66.16% |
| HAO | 63.35% |
| VT | 1.49% |

Green-arrow timing, low StochRSI, WT crosses, 4-bar Donchian levels and HH/HL patterns cannot
be validated on these invented paths. Short-window tests must use the authentic 5m coverage;
longer tests should operate at authentic 15m resolution unless real historical 5m bars are
backfilled.

### 4. VT is not a multi-timeframe testable key

S1 audit of `VT.npz`:

- `wt1_15m`, `wt1_1h`, `wt1_4h`: only 0.255%, 0.255%, 0.225% nonzero.
- `stoch_k_15m`: only 0.134% finite.
- `stoch_k_1h` and `stoch_k_4h`: 0% finite.
- `wt1_D`, `stoch_k_D`, `dc_position_D`, D/4h long-regression fields: missing.
- `lrL_pct_b_1h`: one constant value; `lrL_slope_1h`: constant zero.

The precompute always prefers 15m as canonical even when that source covers only a tiny tail,
while VT has substantially more 5m history. This choice zero-fills its HTFs.

**Required fix:** choose the authoritative source by coverage and authenticity per symbol.
For VT, resample the real 5m source to 15m/1h/4h/D, or backfill the canonical 15m file, then
require coverage thresholds before admitting the key to a matrix campaign.

### 5. The advertised `BAND_ARROW` switch is disconnected

The current `tradier_manage.py` has no consumer of `BAND_ARROW_ENABLED`. The entry and exit
blocks existed in commit history and were removed in commit `9f78580b`. Therefore
`tools/band_ladder_sweep.py` currently enables a dead switch.

The removed implementation should not simply be restored: it defined “green arrow” as
`lrL_slope > deadband`, so it fired on every evaluation bar while the slope stayed positive,
not on a discrete arrow/turn event. With accumulation enabled it rapidly added every 5m cycle
until a cap.

**Required fix:** implement a tested, stateful arrow event definition:

- transition/cross of the relevant slope state, or
- HH+HL with low/rising StochRSI for long; LH+LL with high/falling StochRSI for short;
- only information available at that timestamp;
- one event per HTF bar, with an explicit developing-versus-closed-bar policy.

The ladder must remain a pure sizing function applied after this trigger.

### 6. Ladder sizes above roughly 5× are not actually being tested

With a $10,000 test account:

- `START_POSITION_SIZE = $500`;
- `SWING_LONG_BUDGET = SWING_SHORT_BUDGET = $2,500`;
- `MAX_ORDER_VALUE = $2,500`.

A 10× ladder order requests $5,000. The entry cascade first rejects it against the $2,500
swing budget; downstream queue code also clamps ordinary entries to $2,500. Values 10×, 15×
and 25× are consequently blocked or collapse to the same effective order.

The B&H floor invests the full $10,000. A strategy limited to $2,500 is compared with a
100%-invested benchmark, making it structurally difficult to match B&H even with correct
timing.

**Required fix:** report requested multiplier, filled quantity, effective notional/capital,
and every size clamp. Either normalize both strategy and benchmark to identical deployed
capital, or define a controlled leverage/capital policy for the ladder campaign. Reject a
cell as red if its requested multiplier is not distinguishable in filled notional.

### 7. Side attribution in `band_ladder_sweep.py` is invalid

The script sets `V8_SIDE_GATE_DISABLED=1`, does not set `V8_LADDER_ONLY_SIDE`, reads the
aggregate `V8_RESULT`, and stores that combined long+short P&L under the requested side.
MU_LONG can therefore contain MU_SHORT trades and HAO_SHORT can contain HAO_LONG trades.

**Required fix:** isolate the requested side and assert that raw events contain no opposite
side before storing a cell.

## P1 defects

### 8. Matrix and ladder metrics do not share one capital-return definition

`persym_baseline_campaign.key_metrics()` defines accumulated gain as the sum of per-round
percentage returns. This ignores filled quantity and capital. A 1× and 10× ladder with the
same timestamps therefore has the same `acc_gain_pct` in that path.

`_reconstruct_chart_trades()` also emits only the final reduction's price/quantity for a
round, losing the P&L of earlier partial exits. In contrast, `V8_RESULT.pnl` uses dollar P&L
divided by capital. The matrix can therefore compare incompatible numbers.

Normal `_compute_trade_pnl()` reconstruction currently computes gross P&L and can overwrite
the net fields pre-attached to final MTM events. Transaction costs are not consistently
reflected in the canonical result.

**Required fix:** one event-sourced equity ledger must be canonical:

- filled quantity and price for every open/add/reduce/close;
- realized dollar P&L for every partial;
- final MTM;
- costs/slippage per fill;
- capital return, drawdown and exposure;
- per-side totals.

Percentage-per-trade remains a diagnostic for pool Sharpe, not the strategy gain used against
B&H.

### 9. The “never miss the move” re-entry is not guaranteed

`PRICE_CROSS_BACK_REENTRY` is earlier than ordinary entries, but it is still subject to later
UVE, local-extremes, circuit-breaker, swing-budget, catalyst and queue/size gates. The
guaranteed dispatcher only retries queue insertion; it does not bypass those earlier vetoes.

Additionally, `last_exit_prices` and `last_exit_times` are keyed by `symbol`, not
`position_key`/side, and are updated on every partial reduction. In a both-side simulation,
a short reduction can overwrite the long re-entry reference and vice versa.

The persisted re-entry candidate path is side-aware, but applies many WT/Stoch/age gates, so
it is not the unconditional “price crossed the exit/top, reopen immediately” contract.

**Required fix:** use `(account, symbol, side)` state, record full-close and partial-exit
references separately, and create an explicit mandatory-reentry path whose only permissible
blocks are hard execution impossibilities. Emit a violation whenever price crosses the
contract level while the position remains flat for more than one executable bar.

### 10. Stock regular-hours handling was wrong for half the year

Tier-2 used 13:30–20:00 UTC year-round. In winter, NYSE regular hours are 14:30–21:00 UTC, so
the engine traded an hour of premarket and skipped the closing hour.

**Fixed in the backtest engine:** RTH is now evaluated in `America/New_York`; summer and winter
unit tests pass. Early-close calendars remain a follow-up.

### 11. HTF timestamps and freshness are fabricated

The precompute aliases every `timestamp_<tf>` to the base timestamp, and
`build_indicator_dict()`/the Tradier loop report every HTF age as zero. Stale/missing HTFs
therefore appear perfectly fresh and gates using age cannot behave like live.

The regenerated data contract must carry each source bar's actual availability timestamp and
age. A missing/stale field must fail explicitly rather than silently become zero/current.

### 12. HAO's benchmark needs a corporate-action and shortability audit

HAO spans only 2026-04-09 onward and its NPZ price ranges from about $151 to $0.17, with 42
single-base-bar moves over 10% and one over 50%. Its near +100% short B&H is dominated by that
collapse. Before using it as a strategy floor, validate:

- split-adjusted OHLC consistency;
- ticker continuity;
- executable short availability and borrow cost;
- locate/recall assumptions;
- whether the entry price was realistically shortable.

## Why 2–10× B&H is currently an inconsistent target

For MU, B&H over the long window is about +729%. A 10× B&H target means roughly +7,290%.
That cannot come merely from being flat during a few pullbacks in an unlevered, non-compounding
strategy. It requires some combination of controlled leverage, repeated capital compounding,
short profits during declines, or a much shorter benchmark window.

The system presently combines a full-capital B&H seed with a $2,500 ordinary-entry cap and
multiple incompatible return calculations. Until the equity ledger and capital policy are
fixed, “2×” and “10×” do not describe a reproducible objective.

Use this hierarchy:

1. timing skill: strategy return on the same deployed capital versus same-window B&H;
2. risk efficiency: return/drawdown and return/exposure;
3. sizing benefit: incremental return from the ladder at a declared leverage cap;
4. only then measure xB&H.

## Correct campaign order

1. Quarantine all old cells whose engine/data hashes predate the HTF-event and DST fixes.
2. Add a preflight data contract. VT and HAO currently fail it for the requested HTFs.
3. Rebuild no-look-ahead NPZs from authentic bars and prove prefix invariance.
4. Build one side-isolated, capital-based B&H floor.
5. Implement one discrete ladder trigger and verify requested versus filled multiplier.
6. Hold entries fixed. Test each exit alone, then bounded pairs/triples. Store every result,
   including gray below-B&H cells.
7. Enforce and test mandatory re-entry with a violation counter.
8. Only after exit/re-entry timing works, test WT-DC, Golden Rule and StochRSI-structure entry
   filters and symbol-specific timeframe weights.
9. Fresh Tier-2 replay is mandatory for every vector shortlist.

## Acceptance tests before matrix results become green

- **Prefix invariance:** indicators at time T are byte-identical whether the NPZ ends at T or
  contains another year of future bars.
- **HTF availability:** a completed HTF bar first appears only at/after its close.
- **Event sparsity:** raw and Tier-2 active rates match for cross/divergence flags.
- **Coverage:** every required field has adequate finite/nonconstant coverage for the tested
  window.
- **Side isolation:** zero opposite-side fills.
- **Reachability:** the knob changes a named decision counter and the trade fingerprint.
- **Sizing:** requested multiplier, filled multiplier and clamp reason are recorded.
- **Ledger reconciliation:** event ledger P&L equals engine result to the cent.
- **Re-entry invariant:** zero executable bars above the long re-entry ceiling (below for
  short) while flat, except a recorded hard execution refusal.
- **Benchmark parity:** same start, end, session, side, costs and capital basis.

Only after these checks pass does failure to beat B&H become evidence about the strategy rather
than evidence about the test system.
