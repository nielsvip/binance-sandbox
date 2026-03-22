# LOCKED FILES — DO NOT TOUCH WITHOUT EXPLICIT PERMISSION

## How This Works

- If a file is listed here as **LOCKED**, Claude MUST NOT edit it unless the user says "unlock X" or explicitly permits the edit in that message.
- Even then, confirm before touching.
- To lock a file: add a row below. To unlock: user says "unlock <file>" and row gets removed.
- Locks apply to BOTH local and server copies.

---

## Currently Locked Files

| File | Locked Since | Reason / What Is Working | Who Locked |
|------|-------------|--------------------------|-----------|
| `ez_prices.py` | 2026-03-13 | WebSocket price feeds stable, no issues reported | user |
| ~~`ez_klines.py`~~ | UNLOCKED 2026-03-16 | Unlocked to fix klines deletion destroying historical data | user |
| ~~`ez_indicators.py`~~ | UNLOCKED 2026-03-17 | Unlocked to fix dc_position ghost field → 0dc_moment always zero | user |
| `ez_indicators_merger.py` | 2026-03-14 | Indicator merging pipeline — infrastructure | user |
| `ez_market_data.py` | 2026-03-14 | Market-wide data aggregation — infrastructure | user |
| `ez_positions.py` | 2026-03-13 | Core position data structures — fundamental, only touch if data model changes | user |
| ~~`ez_positions_service.py`~~ | UNLOCKED 2026-03-16 | Unlocked to fix position corruption backdoors | user |
| `ez_positions_realtime.py` | 2026-03-13 | Real-time monitor stable (all account variants) | user |
| `ez_positions_watchdog.py` | 2026-03-14 | Process watchdog — infrastructure | user |
| ~~`ez_rankings.py`~~ | UNLOCKED 2026-03-18 | Unlocked to apply backtest ranking changes (BACKTEST_CHANGE_44, _49) | user |
| `ez_mark_prices.py` | 2026-03-13 | Mark price tracking stable | user |
| `ez_share_ind.py` | 2026-03-13 | Shared indicator server stable, heartbeat working | user |
| `ez_gain_protector.py` | 2026-03-13 | Trailing stop logic stable | user |
| `ez_gap_filler.py` | 2026-03-13 | Gap-fill entry logic stable | user |
| `ez_crosses.py` | 2026-03-13 | Cross-detection stable | user |
| `ez_double.py` | 2026-03-13 | Double-down logic stable | user |
| `ez_prices.py` | 2026-03-13 | WebSocket price feeds stable | user |
| `ez_prices_ws.py` | 2026-03-14 | WebSocket price feed (ws variant) — infrastructure | user |
| ~~`ez_klines.py`~~ | UNLOCKED 2026-03-16 | (duplicate entry) Unlocked to fix klines deletion | user |
| `tradier_api.py` | 2026-03-13 | Tradier API client stable — any breakage kills all stock trading | user |
| `tradier_prices.py` | 2026-03-13 | Stock price feeds stable | user |
| `tradier_positions.py` | 2026-03-13 | Stock position management stable | user |
| ~~`tradier_indicators.py`~~ | UNLOCKED 2026-03-16 | Unlocked to fix klines retention | user |
| ~~`tradier_rankings.py`~~ | UNLOCKED 2026-03-18 | Unlocked to apply backtest ranking changes (BACKTEST_CHANGE_T39-T42) | user |
| `tradier_webhook_bridge.py` | 2026-03-13 | Webhook integration stable | user |
| `utils.py` | 2026-03-13 | Helpers (pk_is_long, pk_is_short, parse_position_key) stable — touching breaks everything | user |

---

## Actively Editable (Hot Zone — Requires Care)

These files are currently being worked on. They are NOT locked but treat every edit as high risk.

| File | Why It's Active | Last Known Issue |
|------|----------------|-----------------|
| `ez_manage.py` | Main orchestrator — trading decisions, constant tuning | Hedge execution, reduce fill verification |
| `ez_positions_quick.py` | Scalp/hedge execution — constant tuning | Ratio recovery, staleness gate |
| `tradier_manage.py` | Stock orchestrator — tuning | pk.endswith fix |
| `config.py` | Config flags — intentional changes | N/A |
| `ez_news_scanner.py` | News scanner — active dev | Free sources v4 |

---

## Rules For Claude When This File Exists

1. **Before editing ANY file** — check this list.
2. If the file is in the **Locked** table → STOP. Tell the user the file is locked and ask for explicit unlock.
3. If a fix to a Hot Zone file would require touching a Locked file → raise it explicitly, get permission first.
4. When a Hot Zone file is confirmed stable and the user says "lock it" → add it to the Locked table immediately.
5. Never silently edit a locked file "just this one import" or "just one line" — the lock is absolute.
