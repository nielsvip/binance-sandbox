# v15 Orange-Row Audit (per-tab) — 20260930

_Generated 2026-09-30T21:15Z. GATE for retiring `EMA_BLANKET_FILTER` (USER 2026-09-30): destroy it only once every OTHER orange-row filter is connected (vec-moves) AND tested **on every tab it lives on** this run. Coverage is per (filter, tab) — a filter tested on EXIT does NOT count as tested on ENTRY. Source: templates + per-tab `v15_avg_delta_latest.xlsx`. CONNECTED = vec moves gain ≥0.01%; crypto/stocks LIVE wiring still needs a per-filter 4-surface trace to count as fully connected._

## Coverage by cat_side (per filter×tab cell)

| cat_side | filter×tab cells | untested | ENTRY-tab untested | disconnected (NOOP on ALL tabs) | placement-NOOP (some tabs) |
|---|---|---|---|---|---|
| CRYPTO_LONG | 678 | 0 | 0 | 0 | 116 |
| CRYPTO_SHORT | 595 | 0 | 0 | 0 | 0 |
| STOCKS_LONG | 511 | 0 | 0 | 4 | 43 |
| STOCKS_SHORT | 453 | 0 | 0 | 3 | 61 |

_'disconnected' = a filter that moves nothing on ANY of its tabs (real wiring defect). 'placement-NOOP' = does nothing on some tab but works on another (informational, not a gate fail)._

## KINDERGARTEN + EMA family — ENTRY-tab coverage (top priority)

| base | set | entry tabs tested (cat_side:tabs) | entry tabs connected |
|---|---|---|---|
| `EMA_9_21_FILTER_ENABLED` | KG | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE |
| `EMA_9_21_FILTER_FILTER_TF` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `EMA_9_21_FILTER_MIN_TFS` | KG | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, C_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, C_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE |
| `EMA_9_21_FILTER_TFS` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `EMA_9_21_SCORE_BONUS` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `EMA_9_21_TIMEFRAME` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `EMA_BLANKET_FILTER_ENABLED` | RETIRING | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, C_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, C_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE |
| `EMA_BLANKET_FILTER_FILTER_TF` | RETIRING | — | — |
| `EMA_BLANKET_FILTER_MIN_TFS` | RETIRING | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, C_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, C_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE, S_SHORT:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE |
| `KINDERGARTEN_ALWAYS_TEST` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_CROSS_TYPE` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_CUMULATIVE_MIN_TFS` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_CUMULATIVE_MODE` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_EMA_GATE_ENABLED` | KG | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE | C_LONG:BREAKOUT_CHANNEL/CONFIRMATION_GATES/REVERSAL_BOUNCE |
| `KINDERGARTEN_EMA_PERIOD` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_FILTER_TF` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_SMA_PERIOD` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_STRICT_TFS` | KG | — _(not an orange row in any template — ADD it)_ | — |
| `KINDERGARTEN_TF` | KG | — _(not an orange row in any template — ADD it)_ | — |

## KINDERGARTEN entry-tab gaps (must be 0): **174**

- **ABSENT (KG base not an orange row on that entry tab — ADD it): 174** cells across 15 bases × cat_sides × entry tabs.
- **present but NOT vec-connected: 0** — wire/verify.
- KG bases with gaps: `EMA_9_21_FILTER_ENABLED`, `EMA_9_21_FILTER_FILTER_TF`, `EMA_9_21_FILTER_TFS`, `EMA_9_21_SCORE_BONUS`, `EMA_9_21_TIMEFRAME`, `KINDERGARTEN_ALWAYS_TEST`, `KINDERGARTEN_CROSS_TYPE`, `KINDERGARTEN_CUMULATIVE_MIN_TFS`, `KINDERGARTEN_CUMULATIVE_MODE`, `KINDERGARTEN_EMA_GATE_ENABLED`, `KINDERGARTEN_EMA_PERIOD`, `KINDERGARTEN_FILTER_TF`, `KINDERGARTEN_SMA_PERIOD`, `KINDERGARTEN_STRICT_TFS`, `KINDERGARTEN_TF`

## Fix worklist (must clear before EMA_BLANKET can be destroyed)

### CRYPTO_LONG — ✅ every filter×tab cell tested & vec-connected

### CRYPTO_SHORT — ✅ every filter×tab cell tested & vec-connected

### STOCKS_LONG — disconnected bases (NOOP on ALL tabs): `DC_MOMENTUM_BOTA_SCORER_FILTER_TF`, `FH_MOMENTUM_FILTER_TF`, `FIRST_OPEN_THROTTLE_FILTER_TF`, `HAIKU_WINNER_FILTER_TF`
### STOCKS_LONG
- placement-NOOP cells (filter does nothing on that specific tab but works on another): 43 — informational; a later pass can prune these rows from tabs where they are inert.

### STOCKS_SHORT — disconnected bases (NOOP on ALL tabs): `GOLDEN_RULE_BASE_USD`, `MTF_EXIT_USE_COMPOUND`, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`
### STOCKS_SHORT
- placement-NOOP cells (filter does nothing on that specific tab but works on another): 61 — informational; a later pass can prune these rows from tabs where they are inert.

## GATE verdict

🔴 **RED — do NOT destroy `EMA_BLANKET_FILTER` yet.** See worklist; re-run until GREEN.

