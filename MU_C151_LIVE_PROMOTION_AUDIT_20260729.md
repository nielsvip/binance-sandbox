# MU_LONG C151 prospective 65–80 live-promotion audit — 2026-07-29

## Verdict

**BLOCKED — no live mutation, no canary, incumbent MU_LONG preserved.**

The new prospective contract is
`MU_C151_LIVE_PROMOTION_V1_TIM65_80`, canonical SHA-256
`eae5304dad6bfdf927de651befcafbc650c0c686176e7a2a0fedf59156ee6757`.
It is a deployment gate applied to an already-sealed candidate, not a rewritten
holdout rule. The original 70–80 receipt remains byte-identical at SHA-256
`1e92251380b5ceb04a96243ba8a02cb514689ab43319eccf6a632b4181ab4c15`.

The 65% lower exposure floor does what the user intended: C151 passes the
exposure gate on all three folds (77.3903%, 77.4247%, 68.6153%). It also passes
the evidence gates for side isolation, positive side-aware B&H, at least 2× B&H
on every fold, the $2,000 benchmark/$16,000 hard-cap contract, solvency, fill
ratio, capacity, completed-parent causality, mandatory reclaim, and exact-v3
schedule/accounting/TIM parity.

That is not yet evidence that the same algorithm can run live. Three independent
deployment gates fail:

1. **Ordinary-live semantic parity.** C151 enters on each newly completed
   D/4h/1h bullish WT-cross, selects the strongest absolute target rung, exits on
   a completed 4h Donchian N=30 breakdown, fills at the next strictly later
   availability, and reclaims `max(exit_fill, prior_4h_high)`. The ordinary
   Tradier path instead uses a 5m rebound from a running low plus
   `mtf_arrow_score`, sizes one configured `LR_BAND_ENTRY_TF`, and then passes
   through the independent live exit/reentry cascade. Those are different
   entry, sizing, exit, and state machines.
2. **Current-engine exact validation.** The accepted exact-v3 receipt is bound to
   engine SHA `00ea247b…`; the current engine is `f2933ba5…`. More importantly,
   the replay injects a frozen schedule through the engine-private
   `--research-ladder-spec` route. It proves execution/accounting of that
   schedule, not that the ordinary live decision path produces it.
3. **Freshness.** The sealed data ends before 2026-07-25. At the audit time
   (2026-07-29 15:35 UTC) it was 111.58 hours old and omitted multiple current
   sessions, beyond the prospective 36-hour deployment limit.

The requested 13:30 UTC market-open deadline had already passed before this
audit began, so no deadline claim is made.

## Gate record

| gate | result | evidence |
|---|---|---|
| candidate/hash chain | PASS | C151 label and all three immutable input hashes agree |
| MU LONG side isolation | PASS | stability receipt is `MU` / `LONG` |
| TIM 65–80 each fold | PASS | 77.3903 / 77.4247 / 68.6153 |
| positive B&H and ≥2× each fold | PASS | 12.8697× / 5.3197× / 5.7037× |
| $2k B&H / $16k capacity | PASS | source constants and receipt agree; peak post-fill $16,000 |
| solvency/fill/capacity | PASS | min equity $8,136 / $8,614 / $7,912; zero clamps; fill 1.0 |
| completed-parent causality | PASS | zero future HTF in discovery, final, and exact-v3 |
| reclaim invariant | PASS | zero flat-beyond-reclaim and zero unfilled obligation |
| exact-v3 schedule/accounting/TIM | PASS | 33/33 actions, zero refusals, zero TIM delta |
| ordinary-live semantic parity | **FAIL** | research schedule and live state machine differ |
| current-engine revalidation | **FAIL** | exact engine hash differs; no current ordinary-path replay |
| fresh deployment data | **FAIL** | data age 111.58h > 36h |

## Safe state and next proof

No file in either live overlay was edited. The audit captured the incumbent TRB
and global per-symbol configuration hashes and explicitly records
`activation_performed=false`, `canary.started=false`, and that no rollback is
needed because there was no mutation.

C151 may be reconsidered only after:

1. implementing one shared pure C151 decision/state machine used by research,
   `backtest_v8_engine.py`, and ordinary Tradier live code;
2. refreshing MU into a new versioned NPZ without overwriting sealed evidence;
3. rerunning the unchanged 65–80 contract, ordinary-path signal diff, exact-v3,
   side/capacity/solvency/reclaim checks on the current engine; and
4. taking an atomic rollback snapshot before a shadow-only canary. The canary
   must emit decisions but place no orders until its completed-parent events,
   target quantities, E02 exits, and reclaim obligations match the exact receipt.

Machine-readable evidence:

- `data/reports/vec_research/MU_C151_LIVE_PROMOTION_CONTRACT_20260729_TIM65_80.json`
- `data/reports/vec_research/MU_C151_LIVE_PROMOTION_AUDIT_20260729.json`
- `tools/audit_mu_c151_live_promotion.py`

