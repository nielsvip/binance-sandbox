# EXIT_ALGO_EXIT_ENABLED job66 — wiring audit

Classification: **DISCONNECTED_DISABLED_INERT_REGISTRY_ROW**.

## Active code proof

- Stale inventory key `ALGO_EXIT_ENABLED` declared/read: False/False.
- Actual config key `EXIT_ALGO_SCORE_ENABLED` default: `False`.
- Its only active read lines: `[528]`; conditional/router reads: `[]`.
- Active `calculate_signal_score(..., is_exit=True)` calls: `[]`.
- Active ALGO exit reason constants: `[]`.

The declared key is present only in the Group-C discoverability tuple. The old call and both ALGO return branches are comments, so toggling either name cannot emit an exit.

## Historical compound pieces — inventory only

| event type | hard-coded semantics |
|---|---|
| `STRUCTURE_1H_DC` | `{"score_delta": -15, "timeframe": "1h"}` |
| `STRUCTURE_15M_DC` | `{"score_delta": -10, "timeframe": "15m"}` |
| `STOCH_4H_ROLL` | `{"long_k_min": 60, "score_delta": -5, "short_k_max": 20, "timeframe": "4h"}` |
| `PROFIT_TAKE_15M` | `{"gain_pct_strictly_above": 5.0, "score_delta": -5, "timeframe": "15m"}` |
| `BEAR_MODE_BIAS` | `{"long_score_delta": -20, "short_score_delta": 15}` |

These components are not one authorized research arm. They need separate path IDs, completed-bar definitions, and ranges before any future screen. The registry description's RSI/MFI/staleness claim does not match the historical exit-specific code.

## Campaign disposition

No top/bottom cohort vector screen was run. With no connected event path, such a screen would manufacture a strategy and then mislabel it as live parity. Job66 is retained red/disconnected; there are no exact-replay or matrix candidates.
