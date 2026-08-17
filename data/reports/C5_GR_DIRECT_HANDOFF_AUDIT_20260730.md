# GR_HTF direct-exit handoff audit

Status: c4 result is not accepted as a domain plateau. No active engine source
was changed.

## Evidence

- Current c4 exact rows for MU_LONG, NVDA_LONG, and VT_LONG have an explicit
  `GR_HTF_DIRECT_EXIT_ENABLED=true` receipt but are bit-identical to the
  ladder floor with zero real closes.
- The same frozen raw NPZs were scored with the actual
  `golden_rule_htf.score_entry_htf()` reader, opposite-side semantics,
  `GOLDEN_RULE_MIN_IND=5`, activation enabled, and direct-exit threshold 12.
- A 1-in-10 RTH sample produced 1,098 MU, 1,065 NVDA, and 642 VT qualifying
  observations. MU's last observed event on 2026-07-21 had all three entry
  timeframes qualified (`1h:6/5`, `15m:7/5`, `5m:7/5`).
- The MU c4 exact audit recorded 45,917 ordinary `evaluate_stop` calls while
  the position remained open for 99.97% of the window. Thus absence of an
  open position or absence of source events cannot explain the zero result.
- A narrow replay spanning the final MU events retained one open seed and
  called the ordinary stop evaluator, yet produced zero GR closes.

## Handoff conclusion

The frozen data reaches the exact engine and the GR scorer can classify it,
but no causal GR close reaches the ordinary exit result. The fault boundary is
between the ordinary stop-evaluator invocation and the GR direct return, not
the parameter domain. c4 must keep these cells red; assigning a unique number
would be fabricated evidence.

## C5 diagnostic/fix requirement

The c5 cutover should add path-scoped telemetry around the live-owned GR block:

1. `gr_direct_reader_enabled`
2. `gr_direct_score_calls`
3. `gr_direct_threshold_passes`
4. `gr_direct_returns`
5. wrapper-accepted GR closes

The trace must record resolved config owner/value, option classification,
opposite-side score, activation detail, and any earlier-return reason. One
short frozen replay should first prove a threshold pass becomes a
`GR_HTF_DIRECT_EXIT_*` return. Only then should the full pilot cells be
rerun under a new c5 fingerprint. Rollback removes the telemetry/shared helper
and restores c4 source; c5 receipts remain historical and must never be
relabeled.
