# CLAUDE.md — Trading System Rules for Claude Code

## STEP 0 — On Every Conversation Start (Mandatory)

### 0a — Refresh + Search the Conversation Knowledge Base

**ALWAYS run this first, on every single prompt, no exceptions:**
```bash
cd /Users/niels/Documents/binance && python3 export_conversations.py
```
This regenerates `memory/conversations/` from all past sessions. Run it BEFORE reading any memory files.

Then search the knowledge base based on what the user is asking about:

1. **If a specific script is mentioned** (e.g. `ez_manage.py`, `ez_positions_quick.py`):
   → Read the matching section in `memory/conversations/SCRIPT_STATE.md`

2. **If a topic is mentioned** (hedge, staleness, ratio, positions, klines, orders, pnl/JSONL, redis, news_scanner, scalp):
   → Read the matching section in `memory/conversations/TOPIC_STATE.md`

3. **If context about recent work is needed** (e.g. "continue from last time", "what did we change"):
   → Read `memory/conversations/INDEX.md` to find the relevant session, then open that `session_*.md`

4. **If none of the above applies**: skip — do not read all files speculatively.

---

### 0b — Before ANY File Edit (Mandatory Pre-Check)

1. Open `LOCKED_FILES.md` and scan the **Currently Locked Files** table.
2. If the target file is listed there → **STOP**. Tell the user it is locked. Do NOT proceed.
3. Only continue if the user says **"unlock \<file\>"** in the same message.
4. Lock applies to both local and server copies. No exceptions. Not even imports. Not even one line.

---

## Absolute Prohibitions (Never Do These)

| Rule | Detail |
|------|--------|
| **NEVER** git reset / restore / checkout | Only move forward. No reverting. |
| **NEVER** revert to backup or older version | Ask the user instead. |
| **NEVER** access `.history/` | Unless explicitly permitted. |
| **NEVER** delete/pop/clear position dict entries | Only modify values. Symbol count in `symbols.json` must match position count exactly or the system crashes. |
| **NEVER** overwrite a file with an older version | Without explicit permission. |
| **NEVER** create a position from zero | NO CODE PATH may EVER instantiate Position() or TradierPosition() with zero/default values. Not `ensure_permanent_positions`, not `_create_default_position`, not `ensure_position_present`, not `DYNAMIC_FALLBACK`, not any other function. Only `add_new_symbols.py` creates positions. If a position is missing from memory: (1) search own backups, (2) search own main file, (3) search ALL other accounts' files+backups and copy structure (amt=0 but all other fields preserved). A position ALWAYS exists somewhere. |
| **NEVER** zero a position from API absence | Absence from Binance/Tradier API response does NOT mean closed. The API only returns recently-active symbols. `_handle_missing_positions`, `api_absence` reduction, ghost clearing — ALL DISABLED. Only explicit WS `positionAmt=0` (threshold 5+) can confirm closure. |
| **NEVER** make account-specific scripts | Put account-specific logic as conditions inside the shared scripts. Separate scripts = chaos. |
| **NEVER** call `ez_backup.py` | It is a live system script, not a Claude utility. |

---

## STEP 1 — Required Workflow for Every File Edit

```
1. Read LOCKED_FILES.md — is it locked? → STOP if yes
2. Backup: cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py  (on server)
3. Edit the file on SERVER (/home/niels/binance/<file>)
4. Verify: cat the changed lines to confirm the edit took effect
5. Test run (if applicable)
6. rsync back to local: rsync -av niels@157.180.125.52:/home/niels/binance/<file> /Users/niels/Documents/binance/
7. Run push.py from local to restart services with backup
```

Backup naming: `before_<short description of change>_<YYYYMMDDHHMM>.py`
Example: `before_ratio_recovery_fix_202603141045.py`

---

## Code Style — Non-Negotiable

**Formatter**: `black` | **Linter**: `pylint`

### Long Lines Rule — CRITICAL

Function calls and `logger.*` calls are **always one line**. No exceptions. Even at 2000+ characters.

```python
# WRONG — never break a logger or function call across lines
logger.warning(
    f"Something happened to {symbol} with value {value}"
)
some_func(
    arg1, arg2, arg3
)

# CORRECT — always one line
logger.warning(f"Something happened to {symbol} with value {value}")
some_func(arg1, arg2, arg3)
```

### Other Style Rules

- One blank line between functions. **Zero blank lines inside a function body.**
- Minimize total line count — no code spamming, no redundant comments.
- Follow existing naming conventions exactly. `symbol` is `symbol` — not `sym`, `s`, `sb`, `sm`.
- No docstrings, comments, or type annotations on code you didn't change.

---

## Python Environments

| Location | Python |
|----------|--------|
| Server | `/home/niels/.conda/envs/binance_env/bin/python` |
| Local | `/opt/anaconda3/envs/binance_env/bin/python` |

---

## Servers & Paths

| Location | Address | Path | Role |
|----------|---------|------|------|
| **Server (source of truth)** | `ssh niels@157.180.125.52` | `/home/niels/binance` | All scripts run here — edit here first |
| **Server logs** | same | `/home/niels/logs` | All service logs |
| **Local** | — | `/Users/niels/Documents/binance` | Reference / push target |
| **Klines-only box** | `ssh niels@157.90.168.35` | `/home/niels/binance` | Klines only — NO scripts, NO editing |

---

## Architecture

See `ez_system.md` for full crypto system details and `tradier_system.md` for stock system details.

### Core Services (Crypto — ez_)

| File | Role |
|------|------|
| `ez_manage.py` | Main orchestrator — all trading decisions, entry/exit, risk |
| `ez_positions_service.py` | Position persistence, sync, market snapshots |
| `ez_positions_quick.py` | Scalp/hedge execution, sentiment rebalancing |
| `ez_prices.py` | Binance WebSocket price feeds → Redis |
| `ez_rankings.py` | Symbol scoring, sentiment tracking |
| `ez_market_data.py` | Market-wide RSI/Stochastic/BB aggregation |
| `ez_indicators.py` | Technical indicators, signal generation, shared memory |
| `ez_klines.py` | Kline fetching and caching |
| `ez_positions.py` | Position data structures |
| `ez_gain_protector.py` | Trailing stop logic |
| `ez_gap_filler.py` | Gap-fill entry logic |
| `ez_crosses.py` | MA/indicator cross detection |
| `ez_double.py` | Double-down / averaging logic |
| `ez_positions_realtime.py` | Real-time position monitor (variants: `_ang` `_fin` `_flz` `_inf` `_men`) |
| `ez_mark_prices.py` | Mark price tracking |
| `ez_share_ind.py` | Shared indicator data distribution |
| `ez_news_scanner.py` | News sentiment scanner (CoinGecko + Finnhub + RSS + F&G) |

### Stock Services (Tradier — tradier_)

| File | Role |
|------|------|
| `tradier_manage.py` | Main stock orchestrator |
| `tradier_api.py` | Tradier API client |
| `tradier_positions.py` | Stock position management |
| `tradier_prices.py` | Stock price feeds |
| `tradier_rankings.py` | Stock scoring |
| `tradier_indicators.py` | Stock technical indicators |
| `tradier_webhook_bridge.py` | Webhook alerts |

### Data Flow

```
Binance/Tradier APIs → ez_prices.py / tradier_prices.py (WebSocket)
 → Redis (price_cache, klines_cache)
 → ez_indicators.py / tradier_indicators.py + ez_market_data.py
 → ez_manage.py / tradier_manage.py (decisions)
 → ez_positions_quick.py / tradier_positions.py (execution)
 → Binance/Tradier APIs (orders)
 → Position state files (*/long_positions.json, */short_positions.json)
```

### Accounts

- **Crypto**: `ang`, `inf`, `flz`, `men`, `fin` (scalp: `ang`, `men`, `flz`; strict no-loss: `ang`, `inf`, `men`, `fin`)
- **Stocks**: `trb`, `trc` (Tradier)
- Each account directory: `long_positions.json`, `short_positions.json`, `long_ladder.json`, `short_ladder.json`, `long_reentry.json`, `short_reentry.json`, `long_stop_levels.json`, `short_stop_levels.json`, `tracker.json`

### Config Files

- `config.py` — position sizing, trading mode flags, risk parameters, account classification
- `symbols.json` — 350+ crypto pairs (array); count must match position count exactly
- `symbol_configs.json` — per-symbol config
- `symbols_ang.json`, `symbols_fin.json`, etc. — per-account active lists
- `.env.gpg` — GPG-encrypted API keys — **do not touch**

### Infrastructure

- **Redis**: inter-service message bus
- **Systemd services** (`scripts/`): one per account + price/kline/ranking services
- **Frontend**: React 18 + TypeScript + Vite + Tailwind in `analyzer/` (`cd analyzer && npm run dev`)

### Trade Data Sources

- `data/decisions/` — JSONL per account per day (`decisions_{acct}_{YYYYMMDD}.jsonl`). **Best source for trade info** — full indicators, action, reason, price, stoch/RSI/HA/sentiment.
- `/home/niels/logs/` — Best for investigating **why trades did NOT happen** (stale indicators, cooldowns, gate failures).

### Large Directories (do not bulk-edit)

- `klines_cache/` — 1.5 GB (~1,400 symbol dirs)
- `plots/` — 1.1 GB
- `data/` — 344 MB
- `logs/` — 328 MB

---

## SERVER MODE — BACKTEST ONLY (until Wed 2026-03-18)

**ALL ez_ and tradier_ live trading services on server (157.180.125.52) are DISABLED.**
The server is running backtests on all 16 CPUs at 100% until we have final numbers (deadline: Wed 2026-03-18).

- **DO NOT** start/restart any ez_manage, ez_positions, tradier_manage, or any trading service on the server
- **DO NOT** edit live trading scripts on the server — edits are LOCAL ONLY until backtest window ends
- Infrastructure services (ez_prices, ez_klines, ez_indicators, Redis) may remain running as backtests need price data
- Backtest framework: `/home/niels/binance-sandbox/backtest_framework/`
- Monitor: `ssh niels@157.180.125.52 "htop"` — all 16 cores should be at 100%

---

## position_key Conventions

- Always use `key.endswith("_LONG")` / `key.endswith("_SHORT")` — **never** `"LONG" in key`
- Helpers in `utils.py`: `pk_is_long()`, `pk_is_short()`, `pk_symbol()`
- `parse_position_key` uses `split("_", 1)` — do not change to rsplit
- LONG = profits when price UP (open=BUY, close=SELL)
- SHORT = profits when price DOWN (open=SELL, close=BUY)
- `is_reduce = (SELL+LONG) or (BUY+SHORT)`

---

## L/S Ratio IS the Hedge — Absolute Rule

- **NEVER close losing positions** — the long/short ratio across all positions is the hedge.
- `STRICT_NO_LOSS` is correct and intentional — do NOT circumvent it.
- Any code that closes a loser "to protect gains" violates this principle.
- The hedge engine caused 40%+ loss in one day (2026-03-06) — see `HEDGE_POSTMORTEM.md`.

---

## Strategy Development Philosophy — GENERAL FIRST, PER-SYMBOL LATER

**Phase 1 (CURRENT):** Build and validate **general rules** that work across ALL symbols.
- No per-symbol optimizations, no symbol-specific parameters.
- All backtest results must be evaluated as cross-symbol averages, not cherry-picked top performers.
- A strategy is only valid if it works on the MAJORITY of symbols, not just the best 5.
- Config parameters must be universal: one `NOLOSS_MIN_PROFIT_PCT` for all, not per-symbol.

**Phase 2 (AFTER solid generals):** Day-by-day analysis of best and worst performers.
- Compare daily: which symbols hit TP fastest? Which sit underwater longest?
- Build **short-term per-symbol adjustments** (tighter/wider TP, entry zone shifts, direction bias).
- These adjustments are TEMPORARY and re-evaluated weekly.

**DO NOT skip to Phase 2.** If a general strategy has <90% WR across all symbols, fix the generals first.

---

## HANDS_FREE Mode

### How to start a HANDS_FREE session

**Option A — Current session** (already running): include `HANDS_FREE` anywhere in your message.

**Option B — New overnight session** (zero popups, fully unattended):
```bash
claude --dangerously-skip-permissions
```

**Option C — Permanent** (all sessions, no popups ever): already configured in `~/.claude/settings.json` — all tools auto-approved.

### Rules when HANDS_FREE is active

Activate by including **`HANDS_FREE`** anywhere in your message (e.g. "HANDS_FREE — fix the ratio recovery and deploy").

When active, Claude MUST:

| Rule | Detail |
|------|--------|
| **No confirmation prompts** | Never ask "shall I proceed?", "want me to continue?", "should I also fix X?" — just do it |
| **No progress check-ins** | Do not pause mid-task to report status — finish first, report at end |
| **Auto-approve all edits** | Backup → edit → verify → rsync → restart without asking |
| **Auto-approve restarts** | `push.py` / `systemctl restart` run automatically |
| **Chain all steps** | If fixing A reveals B needs fixing, fix B too without asking |
| **Handle errors silently** | If a step fails, try the next reasonable approach before reporting |
| **End with a full report** | When completely done: list every file changed, every service restarted, every issue found and resolved |

HANDS_FREE does NOT override:
- `LOCKED_FILES.md` — still checked, locked files still blocked
- Absolute Prohibitions table — still enforced
- The L/S Ratio / STRICT_NO_LOSS rule — never violated regardless of mode

---

## Auto-Confirm (No Confirmation Needed)

Proceed without asking when the shell warning is:
- "Command contains empty quotes before dash (potential bypass)"
- "Command contains `$()` command substitution"

---

## Context Compaction

- **Before compacting**: save full conversation to disk, note the path.
- Keep a todo list in case terminal crashes.
