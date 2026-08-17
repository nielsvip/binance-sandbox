# MU_LONG WT-force × exit fold-3 screen — intentionally partial

Status: **STOPPED FAIL-CLOSED after control + DC**.

Only these two receipts exist and count:

- `exact_mu_wt_exit_interactions_20260730/fold_3/control/receipt.json`
- `exact_mu_wt_exit_interactions_20260730/fold_3/dc_1h_n5/receipt.json`

The `wt_15m_gr3` directory was created when its engine leg started, but the
lane was intentionally stopped before a receipt existed. It is incomplete and
must not be treated as a result. SRS and both combinations were not run. Folds
1 and 2 were not started.

## Exact results

Both rows use exact c4 fingerprint
`tradier-matrix-exec-c4-20260729:a2519f796e73284d35986b7dee6de0221b861c13ee629e9c0f8857d9764cfd08`,
frozen MU NPZ SHA-256
`f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3`,
2025-08-01 through 2026-07-25, `$10k` accounting capital, `$2k` side B&H,
a declared `$16k` strategy capacity, and 0.05% stock round-trip cost.

| recipe | return | side B&H | capture | TIM | real closes | verdict |
|---|---:|---:|---:|---:|---:|---|
| identical-entry HOLD control | +297.2590% | +149.4251% | 1.9894× | 99.9340% | 0 | valid entry floor, not an exit algorithm |
| DC 1h / N5 | +260.4932% | +149.4251% | 1.7433× | 69.0409% | 147 | FAIL |

DC fails every economic objective: it is below 2× B&H, loses 36.7658
percentage points to identical-entry HOLD, and remains above MU_LONG's current
20–60% TIM band. More importantly, its execution contract is structurally
invalid:

- 24 actual `MU_SHORT` P&L rounds appeared in the forced LONG run; 119 were
  LONG. The raw action ledger confirms `position_key=trb:MU_SHORT`,
  `position_side=SHORT`, SELL OPEN and BUY CLOSE.
- maximum single executed open notional was `$35,619`, exceeding the declared
  `$16,000` capacity; `max_requested_mult=40`, fill ratio 0.0760, 493 clamps.
- one mandatory-reentry overshoot violation reached 12.2503%; two reentries
  and one reclaim obligation remained pending.
- 118 intended-side completed rounds closed via `MTF_DC_REJECT`.

No candidate advances and no matrix/live configuration was written.

## Read-only code-path trace

### 1. Forced-side guard is overwritten

`backtest_v8_engine.py` first installs `_always_tradeable`, which honors
`V8_LADDER_ONLY_SIDE`. Later in the same setup it replaces that instance method
with `_v8_exact_tradeable_t`. The later function checks frozen long/short
universe lists but does not carry the forced-side restriction forward. MU is in
both lists, so the ordinary ladder can schedule and execute `_SHORT` orders
inside a nominal LONG-only replay.

The next-availability fill path also invokes `_v8_execute_now` directly. A
fail-closed side assertion must therefore exist both when the order is
scheduled and immediately before the broker-sim fill.

### 2. Absolute ladder target is rescaled after scheduling

The offending SHORT ledger includes an AUGMENT at price 383 with executed
quantity 93 (`$35,619`) even though its entry reason declares an absolute
`usd16000` ladder target and an original quantity near 13.775. Its reason also
contains the same `V8NS_SCALE ... tm=1.50` suffix twice.

The real-ETA wrapper recognizes ordinary ladder reasons as contract entries,
but the later `_v8_execute_now` path applies portfolio sizing scalars without
excluding the ordinary absolute-target route. There is no final broker-sim
clamp against remaining per-position capacity.

There is a second accounting divergence: after real ETA returns, the wrapper
updates simulated `positionAmt` using requested `override_qty or quantity`,
while `_place` records the actual filled quantity. Requested state and executed
ledger can therefore disagree.

The existing `open_sizing_telemetry.max_open_notional` is the maximum *single
open event*, not cumulative open position notional. It happened to catch this
breach because one fill alone exceeded `$16k`; it is not sufficient as a
cumulative-cap audit.

### 3. Reentry postponement is not bounded in c4

The mandatory reentry path records flat bars and overshoot, but c4 allows
multi-timeframe WT/Stoch opposition to postpone indefinitely. It can therefore
finish with an unfilled obligation after price has moved materially beyond the
exit. Next-availability execution introduces a further price gap between
signal and fill.

The c4 result exposes only aggregate reentry counts, not the offending
position-key trace. A c5 receipt must persist per-key exit, signal, fill,
opposition-bar count, maximum overshoot, and final obligation state.

## Required c5 regression gates

Before another exit candidate is tested:

1. Preserve the environment forced side when installing the exact-universe
   tradeability wrapper; assert again at pending-order fill.
2. Reject any opposite-side OPEN/AUGMENT/REENTRY action and any opposite-side
   completed/MTM ledger row.
3. Exempt absolute ladder/reclaim contract entries from all downstream sizing
   scalars, then clamp the final executed order to remaining `$16k` cumulative
   position capacity.
4. Synchronize positions from actual filled quantity, never requested
   quantity; report requested, accepted and executed values separately.
5. Make opposition a temporary postponement with a bounded bar count; persist
   the obligation until filled, and fail on any material overshoot or
   end-of-window pending/reclaim state.
6. Re-run this exact fold with the same override/NPZ. The c5 repair is accepted
   only with zero SHORT ledger rows, cumulative notional at or below `$16k`,
   zero reentry violations/pending obligations, and a new c5 fingerprint.

Rollback is deletion of the isolated report directory. No c4 evidence is
rewritten or reclassified.
