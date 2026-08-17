# Tradier R1 churn incident — 2026-08-03

## Verified submitted fills

Only `[TRADE] ... Status: SUBMITTED` records in
`/Users/niels/logs/tradier_manage_trb.log` are counted. Queue dedupes,
`GFV_BLOCKED`, `REDUCTION_COOLDOWN`, and `VALIDATION_FAILED:NO_POS_HELD` attempts
are excluded.

| Symbol/side | Submitted R1 loss fills | Loss | Submitted R1 gains | Gain | Net |
|---|---:|---:|---:|---:|---:|
| SNDK_LONG | 4 | -$114.30 | 1 | +$25.20 | -$89.10 |
| VLO_LONG | 1 | -$29.80 | 1 | +$1.60 | -$28.20 |
| CLX_SHORT | 0 | $0.00 | 1 | +$5.70 | +$5.70 |
| AGCO_SHORT | 1 | -$62.70 | 0 | $0.00 | -$62.70 |
| RBLX_SHORT | 1 | -$88.00 | 1 | $0.00 | -$88.00 |
| TSLA_SHORT | 1 | -$35.60 | 0 | $0.00 | -$35.60 |
| **Total** | **8** | **-$330.40** | **3** | **+$32.50** | **-$297.90** |

The approximately $417 claim is not reproducible from submitted fills in this
log. It can be inflated by counting blocked/failed attempts or can reflect
broker-side fills/costs absent from this file; it must be reconciled against the
broker execution ledger before being called realized P&L.

## Cause

- R1 was still globally enabled; AGCO_SHORT also had an explicit stale True
  override. No R5 vector finding had been promoted (`state_writes=false`).
- Persisted `opened_at` strings were not parsed when `entry_time` was blank.
- The R1 guard initialized unknown age to `0.0m`, so old/reused position objects
  repeatedly qualified as newborn.
- The tight four-bar 5m Donchian stop then combined with automatic reentry to
  reproduce the already documented churn loop.

## Containment and permanent repair

- R1 is false in every current TRB/TRC hourly entry and false in the global
  Tradier master; hourly or per-symbol overlays cannot re-enable it.
- `opened_at` is parsed from its persisted field with `entry_time` as fallback.
- Unknown age fails closed and logs `R1_SKIPPED_OPEN_TIME_UNAVAILABLE`.
- Last R1 trigger before containment: `2026-08-03T14:57:32Z`; safety overlay
  write: `2026-08-03T14:57:50Z`. A later `GFV_BLOCKED` line at 15:00:34Z was a
  queued attempt originating from the earlier trigger, not a new trigger or fill.
- A later source-integrity check at 15:43Z caught the checked-in global flag
  reverted to `True`; it was restored to `False` and the regression test now
  asserts the disabled value. All 201 TRB and 170 TRC runtime overlay entries
  were independently audited as explicit `False`. Through 15:50Z the live TRB
  log contains zero R1 evaluation/hold/trigger lines after the 14:57:50Z overlay,
  confirming the already-running manager is taking the runtime kill switch.

## 16:55 recurrence and final containment

The 14:57 overlay containment was incomplete.  At 16:55 the still-running TRB
process evaluated R1 again for `RBLX_SHORT`; its attempted close was
`GFV_BLOCKED`, so it was not another submitted fill.  The exact precedence bug
was an early return in `_cfg`: when the anti-churn flag was enabled, it returned
the process's imported global R1 value before reading the live-reloaded overlay.
That process had imported an older `True`, so the documented false overlays were
present on disk but powerless.  This is why an apparently repaired source tree
and a live R1 attempt coexisted.

Final containment at `2026-08-03T17:24:13Z` writes both
`R1_DC_LOW4_3M_EMERGENCY_ENABLED=false` and
`R1_NEWBORN_WINDOW_MIN=-1` into every current hourly entry: 201/201 TRB and
170/170 TRC.  The negative window is a hot fail-closed gate that the stale
process already reloads.  Source repair removes the force-True early return and
prohibits both hourly R1 candidate clusters from enabling R1.  The regression
test asserts these invariants.  Fresh TRB scans through 17:26:02Z contain zero
R1 lines after the final overlay mtime.
