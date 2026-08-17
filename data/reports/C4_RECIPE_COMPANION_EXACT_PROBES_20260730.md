# C4 recipe-companion exact probes — 2026-07-30

Scope: read-only validation of the newly repaired `DELTA_EXIT_ENABLED` and
`MTF_EXIT_USE_COMPOUND` isolated matrix recipes. No source, manifest, live
configuration, or promotion state was changed.

## Contract and comparison

- Campaign: `stocks_repaired_20260725_c2`
- Key: `TTD_SHORT`
- Contract: `tradier-matrix-exec-c4-20260729:eae9ee5cfc2b2cde46433c98cb54334bc826f899a23148611f9e2403a6ae77de`
- Code stamp: `backtest_v8_engine:f02957c2|tradier_manage:94325d27|wt_dc_delta:7b0f4a7f|config_tradier:181b70eb`
- The false/off member of each pair is the accepted exact ladder-floor
  baseline. It is not rerun as a parameter cell because the complete false
  recipe is a deliberate baseline alias.
- Baseline fingerprint: `1:1e6e216da713e9c4`
- Baseline result: 0 real closes, one final MTM record, 99.9722% short TIM,
  0.5768 gain/month.

## `DELTA_EXIT_ENABLED`

Verified effective recipe:

- `DELTA_EXIT_ENABLED=true`
- `DELTA_ENGINE_ENABLED=true`
- `DELTA_EXIT_TF=15m`
- `DELTA_EXIT_TYPE=speed_decay`
- `NOLOSS_MIN_PROFIT_PCT_TRADIER=0.01`

Exact result:

- Status: `PASS_WITH_CAPACITY_CLAMPS`
- Fingerprint: `98:4ebb9461c54bfa98` (different from the false baseline)
- 98 real SHORT closes, 0 LONG opens, 77.9148% short TIM
- 3.0192 gain/month, +2.4424 gain/month versus the ladder floor
- 0 re-entry overshoot violations
- 33 close reasons begin with `DELTA_EXIT_`
- The remaining 65 closes use ordinary stop reasons reached while the Delta
  engine is enabled: 17 `STRUCT_HL5M_EXIT`, 13 `IBS_15m_EXHAUSTION`, 12
  `Structure_Break_1H_Basis`, 7 `Extreme_Oversold_TP`, 13 other IBS-family,
  and 3 other reasons.

Verdict: **wired and behavior-changing, but not family-pure**. This is not a
no-event plateau. The true recipe causes direct Delta closes, but enabling the
Delta engine also makes ordinary close paths reachable. Do not interpret the
whole 98-close result as pure Delta attribution.

## `MTF_EXIT_USE_COMPOUND`

Verified effective recipe:

- `MTF_EXIT_USE_COMPOUND=true`
- exactly one enabled MTF child: `MTF_DC_REJECT_EXIT_ENABLED=true`
- `MTF_DC_REJECT_EXIT_TF=1h`
- `MTF_DC_REJECT_EXIT_LOOKBACK=5`
- `MTF_BB_REJECT_EXIT_ENABLED=false`
- `MTF_WT_CROSS_EXIT_ENABLED=false`

Exact result:

- Status: `PASS_WITH_CAPACITY_CLAMPS`
- Fingerprint: `237:e9717b7a9e8aa5bd` (different from the false baseline)
- 237 real SHORT closes, 0 LONG opens, 73.5987% short TIM
- 4.1939 gain/month, +3.6171 gain/month versus the ladder floor
- 0 re-entry overshoot violations
- All 237 close reasons begin with `MTF_DC_REJECT_1h_`

Verdict: **wired, causal, and reason-pure for the representative DC child**.
It is not a no-event plateau.

The result and fingerprint are intentionally identical to
`MTF_DC_REJECT_EXIT_ENABLED=true`, because the umbrella's executable true
probe is defined as that exact representative child recipe. Therefore these
two rows are a documented recipe alias and must not be advertised as unique
data.

## Cross-key corroboration

Current-contract exact true receipts on `ACN_SHORT` and `LAC_SHORT` also
change both switches away from their respective ladder-floor fingerprints:

| key | recipe | real closes | TIM | gain/mo | fingerprint |
|---|---|---:|---:|---:|---|
| ACN_SHORT | Delta | 67 | 91.2329% | 1.7006 | `68:8904a27a6a72875a` |
| LAC_SHORT | Delta | 56 | 95.2015% | 2.0275 | `57:a1eee1e404ef71f2` |
| ACN_SHORT | MTF compound/DC | 227 | 78.4869% | -0.5687 | `228:c3c170cd2edb8e94` |
| LAC_SHORT | MTF compound/DC | 284 | 79.8146% | 2.9669 | `284:76623b02ecc5946e` |

These rows are wiring evidence only. They are not live-promotion decisions,
and several are outside the ranked key TIM band or below the performance bar.

