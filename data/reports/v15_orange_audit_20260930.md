# v15 Orange-Row Audit — 20260930

_Generated 2026-09-30T20:24Z. GATE for retiring `EMA_BLANKET_FILTER` (USER 2026-09-30): destroy it only once every OTHER orange-row filter is connected (vec-moves) AND tested this run. Source: templates + `v15_avg_delta_latest.xlsx`. CONNECTED here = vec moves gain ≥0.01%; crypto/stocks LIVE wiring still needs a per-filter 4-surface trace before it counts as fully connected._

## Coverage by cat_side

| cat_side | orange bases | tested | untested | tested-but-NOOP (not connected) | entry-tab untested |
|---|---|---|---|---|---|
| CRYPTO_LONG | 72 | 72 | 0 | 4 | 0 |
| CRYPTO_SHORT | 67 | 67 | 0 | 0 | 0 |
| STOCKS_LONG | 60 | 60 | 0 | 0 | 0 |
| STOCKS_SHORT | 40 | 40 | 0 | 3 | 0 |

## KINDERGARTEN + EMA family (entry-tab priority)

| base | set | cat_sides tested | cat_sides connected | on entry tabs |
|---|---|---|---|---|
| `EMA_9_21_FILTER_ENABLED` | KG | C_LONG | C_LONG | C_LONG |
| `EMA_9_21_FILTER_FILTER_TF` | KG | — | — | — |
| `EMA_9_21_FILTER_MIN_TFS` | KG | C_LONG,C_SHORT,S_LONG,S_SHORT | C_LONG,C_SHORT,S_LONG,S_SHORT | C_LONG,C_SHORT,S_LONG,S_SHORT |
| `EMA_9_21_FILTER_TFS` | KG | — | — | — |
| `EMA_9_21_SCORE_BONUS` | KG | — | — | — |
| `EMA_9_21_TIMEFRAME` | KG | — | — | — |
| `EMA_BLANKET_FILTER_ENABLED` | RETIRING | C_LONG,C_SHORT,S_LONG,S_SHORT | C_LONG,C_SHORT,S_LONG,S_SHORT | C_LONG,C_SHORT,S_LONG,S_SHORT |
| `EMA_BLANKET_FILTER_FILTER_TF` | RETIRING | C_LONG | C_LONG | — |
| `EMA_BLANKET_FILTER_MIN_TFS` | RETIRING | C_LONG,C_SHORT,S_LONG,S_SHORT | C_LONG,C_SHORT,S_LONG,S_SHORT | C_LONG,C_SHORT,S_LONG,S_SHORT |
| `KINDERGARTEN_ALWAYS_TEST` | KG | — | — | — |
| `KINDERGARTEN_CROSS_TYPE` | KG | — | — | — |
| `KINDERGARTEN_CUMULATIVE_MIN_TFS` | KG | — | — | — |
| `KINDERGARTEN_CUMULATIVE_MODE` | KG | — | — | — |
| `KINDERGARTEN_EMA_GATE_ENABLED` | KG | C_LONG | C_LONG | C_LONG |
| `KINDERGARTEN_EMA_PERIOD` | KG | — | — | — |
| `KINDERGARTEN_FILTER_TF` | KG | — | — | — |
| `KINDERGARTEN_SMA_PERIOD` | KG | — | — | — |
| `KINDERGARTEN_STRICT_TFS` | KG | — | — | — |
| `KINDERGARTEN_TF` | KG | — | — | — |

## Fix worklist (must clear before EMA_BLANKET can be destroyed)

### CRYPTO_LONG
- **TESTED but NOOP / not connected in vec (4)** — wire on all surfaces (vec + ez_manage[crypto] + tradier_manage[stocks] + config), never a proxy (memory switch_wiring_pattern_20260930); a connected filter MUST move gain:
    - `EZ_MANAGE_THROTTLER_RATE`, `GOLDEN_RULE_BASE_USD`, `HTF_GATE_BYPASS_RZ`, `LH_HL_FILTER_REQUIRE_BOTH`

### CRYPTO_SHORT — ✅ all orange bases tested & vec-connected

### STOCKS_LONG — ✅ all orange bases tested & vec-connected

### STOCKS_SHORT
- **TESTED but NOOP / not connected in vec (3)** — wire on all surfaces (vec + ez_manage[crypto] + tradier_manage[stocks] + config), never a proxy (memory switch_wiring_pattern_20260930); a connected filter MUST move gain:
    - `GOLDEN_RULE_BASE_USD`, `MTF_EXIT_USE_COMPOUND`, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`

## GATE verdict

🔴 **RED — do NOT destroy `EMA_BLANKET_FILTER` yet.** Orange rows remain untested or vec-disconnected (see worklist). Fix those first; re-run this audit until GREEN.

