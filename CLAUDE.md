# CLAUDE.md — Trading System Rules

## 🚨 MANDATORY BACKUP BEFORE EVERY EDIT — NO EXCEPTIONS

**Every edit MUST follow this exact sequence:**

```bash
# 1. Backup to /backups/ with timestamp BEFORE editing
cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py

# 2. Then edit
```

**Additionally — autosave runs every 15 minutes via launchd:**
- `autosave_15min.py` copies all critical files to `backups/autosave/<timestamp>/`
- Auto-commits to git every 15 min as safety net
- LaunchAgent: `~/Library/LaunchAgents/com.niels.autosave-15min.plist`
- If autosave process dies, launchd restarts it. If missing, reload it.

**If you find yourself editing without a fresh backup STOP — create the backup first.** Losing an hour of work because rogue agent reverted something = unacceptable. The /backups/ folder is the ONLY reliable history — `.history/` is stale, git has 1 ancient commit.

---

## ⚠️ NO LYING / NO GUESSING — REAL MONEY

- **NEVER** claim something works without log/exchange proof.
- **NEVER** guess root causes — trace the actual code path.
- **NEVER** present backtest numbers from reimplemented logic. Only `process_position()`, `check_entry_candidates()`, `check_exit_candidates()` produce valid numbers.
- **NEVER** inflate, extrapolate, or cherry-pick numbers. Report exactly what the code produced.
- **NEVER** cover up errors. Say "I made an error in X" immediately.
- **NEVER** blame external systems (Redis, API) before exhausting code-level causes.
- When in doubt: say "I don't know yet" or "Can you clarify?" — not a confident wrong answer.

---

## ⚠️ ALL CLOCKS = UTC. MARKETS = ET (UTC−4 now)

| Event | ET | UTC |
|-------|-----|-----|
| Market open | 9:30 AM | **13:30** |
| Market close | 4:00 PM | **20:00** |
| Pre-market prep | 8:00 AM | **12:00** |

**NEVER write cron times in ET. NEVER assume `date` is ET. NEVER say "market closed" without checking UTC.**

---

## STEP 0 — Every Conversation Start

1. **Read STATE OF AFFAIRS** at bottom of this file first.
2. **Read `100.md`** — master audit doc (Parts 1–15). Skim headers, read relevant sections.
3. **Refresh knowledge base**: `cd /Users/niels/Documents/binance && python3 export_conversations.py`
4. **Search based on request**:
   - Script mentioned → `memory/conversations/SCRIPT_STATE.md`
   - Topic (hedge, ratio, positions, klines, redis, scalp) → `memory/conversations/TOPIC_STATE.md`
   - "Continue from last time" → `memory/conversations/INDEX.md` → relevant `session_*.md`
   - No match → skip (don't read speculatively)

---

## STEP 0b — Before ANY File Edit

1. Read `LOCKED_FILES.md` — is it locked? → **STOP** if yes.
2. Only continue if user says **"unlock \<file\>"** in same message.

---

## STEP 1 — Every File Edit Workflow

```
1. Read LOCKED_FILES.md — locked? STOP.
2. Backup: cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py
3. Edit locally (/Users/niels/Documents/binance/<file>)
4. Compile: python -c "import py_compile; py_compile.compile('<file>', doraise=True)"
5. Verify: grep/read changed lines to confirm.
```

---

## Absolute Prohibitions

| NEVER | Detail |
|-------|--------|
| git reset / restore / checkout | Only move forward. |
| Revert to backup/older version | Ask user instead. |
| Access `.history/` | Unless explicitly permitted. |
| delete/pop/clear position dict entries | `symbols.json` count must match position count exactly. No `.pop()`, `del`, `.clear()`. |
| Overwrite file with older version | Without explicit permission. |
| Create position from zero | Only `add_new_symbols.py` creates positions. |
| Zero position from API absence | Absence ≠ closed. Only explicit WS `positionAmt=0` (threshold 5+) confirms closure. |
| Zero entry_price, max_gain, opened_at | SACRED. Only `positionAmt` may be zeroed on confirmed close. |
| Overwrite Redis memory without validation | Redis may be poisoned/stale. |
| Use `.clear()` on positions dicts | Empty source = source failed, NOT positions gone. |
| Make account-specific scripts | Put account logic as conditions inside shared scripts. |
| Call `ez_backup.py` | Live system script, not a Claude utility. |
| Close positions at a loss on live | STRICT_NO_LOSS active. Under review via Part 15. |
| Add stop-loss code to tradier_manage.py | `HARD_STOP_LOSS_MAX_PAIN` caused $500+ losses 2026-03-24. ALL stop loss paths DISABLED. |
| Present backtest results from reimplemented logic | Only real `process_position()` / `check_entry/exit_candidates()` results count. |
| Inflate trade counts or PnL | Report raw numbers. 120 trades = 120. Not "~150". |
| Call backtest "working" until it matches live logs | Compare vs `data/decisions/` JSONL trade-by-trade. |
| Blame "strict gates" for low trade counts | Missing trades = missing mock attributes or broken patches. |
| Place orders outside `execute_now()` | THE ONLY gate for ALL Binance orders. No exceptions. |
| Add `not is_hedge` bypasses to execute_now guards | Guards apply to ALL callers. Hedge-specific logic goes INSIDE execute_now. |
| Let MOMENTUM_RIDER bypass execute_now | PERMANENTLY DISABLED. |
| Add new entry strategies to live code | See NEW STRATEGY PROHIBITION. |
| Add autonomous position-opening loops | No `asyncio.create_task(scan_and_open_*)`. |
| Add percentage-based exit triggers | No `if gain < -X%: exit`. Exits on technicals ONLY. |
| Hardcode config values in backtest engines | Backtests MUST read from config.py. |
| Auto-expand tradeable_keys | Hand-picked per account. No `.add()` from scanners. |
| Add hedge loops independent of HEDGE_MODE | All hedges via ez_positions_quick only, HEDGE_MODE=True. |
| Implement YouTube/web strategies directly into live | Research → full sweep backtest (48 crypto 4yr S1 + 128 stocks 2yr S2) → paper trade days → user approval → live. |
| Use HANDS_FREE to add new strategies | HANDS_FREE = bug fixes + proven changes only. |

---

## NEW STRATEGY PROHIBITION (2026-03-27 disaster: 8 strategies → 0 trades + 15,378 rogue opens)

1. NEVER add strategy to ez_manage/tradier_manage/ez_positions_quick without explicit user approval outside HANDS_FREE.
2. NEVER enable on real money without full sweep backtest proof (48 crypto 4yr on S1 + 128 stocks 2yr on S2) + paper trading days.
3. NEVER "backtest" with reimplemented logic.
4. NEVER add >1 new strategy per conversation.
5. NEVER wire strategy into main loop without a kill switch defaulting to OFF.
6. Before ANY new strategy: present entry logic, exit logic, data pipeline, trade freq, risk, interaction with existing strategies.
7. After implementation: verify it fires in 24h paper with trades in decision JSONL.

**Banned without V5 backtest proof**: ORB, Clenow, SMFI, Minervini, Connors RSI, DC Daytrade, Episodic Pivot, Squeeze, Outlier Scalper, Outlier Hunter, Mover Detection, momentum fade/rider, autonomous scanners.

---

## EXECUTE_NOW IS THE ONLY GATE

1. ALL orders (open, augment, reduce, close, hedge) go through `execute_now()` in ez_manage.py.
2. NO `not is_hedge` bypasses — guards apply to ALL callers.
3. NO reason-string bypasses (MOMENTUM_RIDER etc.) — reason is for logging only.
4. OPEN on empty position = allowed. AUGMENT on existing = gain check applies to ALL.
5. No `futures_create_order` or REST `/fapi/v1/order` calls outside execute_now.
6. **CONSULT TRACKER BEFORE HEDGING** — check `active_hedges` + `exit_candidates` in tracker.json. Don't hedge-the-hedge. Don't double-hedge. (2026-03-29 death spiral: 24,833 rogue orders from not consulting tracker.)
7. `persist_hedge_record` MUST be called after any execute_now for a hedge.

---

## STRICT_NO_LOSS — UNDER REVIEW (Part 15, deadline 2026-04-03)

**LIVE: STRICT_NO_LOSS = active. BACKTEST (P15 configs): disabled for testing.**

Disabled % stop paths in tradier_manage.py (MUST STAY DISABLED):
- `HARD_STOP_LOSS_MAX_PAIN` (gain < -1.5%)
- `STALE_DATA_HARD_STOP` (gain < -1.5% on stale data)
- `STALE_DATA_GAIN_EROSION` (trailing on stale)
- `Market_Against_Position` bias reduce (gain < -0.5%)

**If you EVER see `gain < -` followed by `return True` in any exit path — DISABLE IT.**

Part 15 tests: % stops ALWAYS bad. Technical exits (WT cross, structure break) at a loss MAY be ok — that's what the sweep tests.

---

## Current Operating Mode (2026-03-31)

| Location | Role | Status |
|----------|------|--------|
| **Local MacBook** | SOURCE OF TRUTH — all trading runs here | ACTIVE |
| **Server 1 (157.180.125.52)** | Tera-sweep backtesting | ACTIVE |
| **Server 2 (204.168.181.211)** | Tera-sweep backtesting | ACTIVE |

- DO NOT start/restart trading services on servers. DO NOT edit scripts on servers (local only).
- Server Redis tunnel DISABLED — dummy on 6381. DO NOT re-enable.
- Local Redis: 6379. Gateway Redis: 6380.
- To restore server trading: run `push.py` from local.
- **TRADIER IS PRIORITY** — $70k stocks vs $1k crypto.
- **⚠️ SERVER LOCKS**: Before ANY server action, read `SERVER_LOCKS.md` AND check `/home/niels/SWEEP_RUNNING` on the target server. If a lock exists, DO NOT kill processes or start new scripts. NEVER run `killall python3` without checking locks. Screen sessions named `sweep48h` are PROTECTED.

---

## Code Style

**Formatter**: `black` | **Linter**: `pylint`

- `logger.*` and function calls are **always one line** — never broken across lines.
- One blank line between functions. **Zero blank lines inside function bodies.**
- Use `config.BASE_PATH` — never hardcoded paths.
- No docstrings/comments/type annotations on code you didn't change.
- Follow existing naming: `symbol` = `symbol`, not `sym`/`s`/`sb`.

---

## Environments & Servers

| Location | Python | Path |
|----------|--------|------|
| Local | `/opt/anaconda3/envs/binance_env/bin/python` | `/Users/niels/Documents/binance` |
| Server 1 | `/home/niels/.conda/envs/binance_env/bin/python` | `/home/niels/binance` |
| Server 2 | `/home/niels/miniconda3/envs/binance_env/bin/python` | `/home/niels/binance` |
| Sandbox | same as Server 1 | `/home/niels/binance-sandbox` (klines + backtest data) |
| Klines box | — | `157.90.168.35` — klines ONLY, no scripts |

---

## Architecture

**WaveTrend (WT)** = primary signal. 26 fields/TF/symbol. See `wt_composite.py` + `ez_indicators.py`.

| TF Level | Timeframes | Role |
|----------|-----------|------|
| LTF (Triggers) | M, W, D, 4h, 1h | Setup detection |
| Entry Confirmation | 15m, 3m | Structure break + momentum |
| Entry Execution | 3m | 3m break + 15m confirm (PF 1.55, Sharpe 3.21) |
| Exit | 3m | Structure break |

**Crypto core**: ez_manage.py, ez_positions_service.py, ez_positions_quick.py, ez_prices.py, ez_rankings.py, ez_market_data.py, ez_indicators.py, ez_klines.py, wt_composite.py

**Stock core**: tradier_manage.py (STOP LOSSES DISABLED), tradier_api.py, tradier_positions.py, tradier_prices.py, tradier_rankings.py, tradier_indicators.py

**Accounts**: Crypto: `ang`, `inf`, `flz`, `men`, `fin` | Stocks: `trb`, `trc`

**Config**: `config.py` (crypto), `config_tradier.py` (stocks), `symbols.json` (350+ pairs, count must match positions exactly), `.env.gpg` (API keys — do not touch)

**Trade data**: `data/decisions/` JSONL per account per day — best source for trade info.

---

## Timeframe Derivation — NEVER claim missing data

**15m klines = ALL timeframes since 2020.**

| 15m → Derived | Method |
|--------------|--------|
| 1h | resample('1h').agg(open=first, high=max, low=min, close=last, volume=sum) |
| 4h | resample('4h') same |
| D | resample('1D') same |
| 1m | each 15m bar × 15 with interpolated OHLCV |
| 3m/5m | each 15m bar × 5/3 |

Map HTF arrays back to 15m via `np.searchsorted`.

---

## Backtest System (V5 — only valid system)

V5 calls ACTUAL live functions. V3/V4/old scripts = RETIRED in `old/` — do NOT use.

**Phase 0 (prep, run once or after indicator changes)**:
- `backtest_v4_precompute.py` — crypto indicators → NPZ
- `backtest_v4_precompute_tradier.py` — stock indicators → NPZ
- `backtest_v5_interpolate_3m.py` / `_5m.py` — higher-res NPZ

**Phase 1 (run backtest)**:
- `backtest_v5_engine.py` — ONLY valid crypto backtest (all evaluate functions)
- `backtest_v5_full_tradier.py` — ONLY valid stock backtest (all evaluate functions)
- ⚠️ NEVER use `backtest_v5_run.py` / `_run_tradier.py` — stripped, wrong results

**Phase 2 (sweep)**: `backtest_v5_sweep.py`, `backtest_v5_hedge_sweep.py`, `backtest_v5_master.py`

**Phase 3 (analyze/validate)**:
- `backtest_v5_analyze.py` — Sharpe, WR, drawdown breakdown
- `backtest_evaluate_functions.py` — validate vs live `data/decisions/` JSONL
- `backtest_evaluate_functions_tradier.py` — **MANDATORY before any tradier_manage.py change**

**Data paths** (on Server 1):
- Crypto NPZ: `/home/niels/binance-sandbox/backtest_v4/indicators/`
- Stock NPZ: `/home/niels/binance-sandbox/backtest_v4_tradier/indicators/`
- 3m NPZ: `/home/niels/binance-sandbox/backtest_v5/indicators_3m/`
- 5m NPZ: `/home/niels/binance-sandbox/backtest_v5/indicators_5m_tradier/`
- Logs: `/home/niels/binance-sandbox/backtest_v5/logs/`
- Klines: `/home/niels/binance-sandbox/klines_cache/` (67 symbols, 4–6yr, 15m)
- Stock klines: `/home/niels/binance-sandbox/klines_cache_backtest/tradier/` (128 symbols, 25mo)
- Standard test: 48 symbols in `/home/niels/binance-sandbox/backtest_48_symbols.json`

**Rules**: NEVER write new backtest that reimplements logic. NEVER trust `old/`. ALWAYS validate with `backtest_evaluate_functions*.py` before deploying. NEVER compare results across different precomputed versions.

---

## Crypto vs Stock Parameters — OPPOSITE — NEVER copy between them

| Parameter | Crypto Best | Stock Best |
|-----------|------------|------------|
| Entry score | 18 | **24** |
| Reentry stoch gate | K<50 | K<**80** |
| HTF alignment | ≥1 | ≥**2** |
| ADX in sizing | Disable | **Keep** |
| Sizing indicator | RSI ok | **MFI only** |
| WT cross alignment | ≥2 | ≥**3** |
| Combined stoch gate | 50 | **60** |

**MANDATORY**: Before any tradier_manage.py / config_tradier.py change, run `backtest_evaluate_functions_tradier.py`. NEVER assume crypto finding transfers to stocks.

---

## position_key Conventions

- Always: `key.endswith("_LONG")` / `key.endswith("_SHORT")` — NEVER `"LONG" in key`
- Helpers: `pk_is_long()`, `pk_is_short()`, `pk_symbol()` in `utils.py`
- `parse_position_key` uses `split("_", 1)` — do not change to rsplit
- LONG = profits price UP (open=BUY, close=SELL) | SHORT = profits price DOWN (open=SELL, close=BUY)
- `is_reduce = (SELL+LONG) or (BUY+SHORT)`

---

## HANDS_FREE Mode

Activate with **`HANDS_FREE`** in your message. When active: no confirmations, no check-ins, auto-approve edits, chain steps, handle errors silently, full report at end.

Does NOT override: LOCKED_FILES.md, Absolute Prohibitions, STRICT_NO_LOSS.

---

## HANDS_OFF Mode

Activate with **`HANDS_OFF`** in your message. When active: no questions, no confirmation prompts, green light for any changes — keep executing until the final result is delivered. Full report at end.

Does NOT override: LOCKED_FILES.md, Absolute Prohibitions, STRICT_NO_LOSS.

---

## Misc Rules

- **Strategy development**: Phase 1 = general cross-symbol rules. Phase 2 = per-symbol. DO NOT skip to Phase 2.
- **Context compaction**: Before compacting, save full conversation to disk + note path.
- **100.md condense**: Keep only last 7 days of performance rows. Merge "Applied Today" after 3 days. Move raw test data to CSV. Never delete Parts 1–5, active BC entries, or "Not Yet Applied" priorities.
- **Auto-Confirm**: Proceed without asking for: "Command contains empty quotes before dash" or "Command contains `$()` command substitution".

---

## STATE OF AFFAIRS (2026-03-31)

### What Is Working (DO NOT BREAK)

| System | Status |
|--------|--------|
| Crypto trading (local) | ACTIVE — ez_manage + ez_positions_quick + services on MacBook |
| Stock trading (local) | ACTIVE — tradier_manage + tradier_indicators + tradier_rankings |
| WaveTrend pipeline | ACTIVE — 28 WT fields/TF, Redis via ez_market_data |
| Signal accuracy tracker | ACTIVE — 61% accuracy at 15m–1h |
| Trade analytics | ACTIVE — Flask :5050, /feed per-account |
| BTC ticker | ACTIVE — localhost:8777 |
| Backtest V4 sweep | Server running — DO NOT interfere |

### What Was Recently Fixed (DO NOT REVERT)

| Fix | Date |
|-----|------|
| execute_dual_hedge re-enabled (inline trigger only, no loops) | 2026-04-01 |
| Triple SIREN order bypass | 2026-03-25 |
| 14 entry pipeline root causes | 2026-03-25 |
| Server Redis tunnel DISABLED | 2026-03-25 |
| Augment gate 5.0→3.0% | 2026-03-26 |
| BC_152 direction-favorable reentry | 2026-03-26 |
| BC_153 ratio dead zone 2pp/5pp | 2026-03-26 |
| BC_150 compression breakout (ATR) | 2026-03-26 |
| Metrics key aliases stoch_k/d_3m | 2026-03-26 |
| Logging fix ez_market_data.py | 2026-03-26 |
| Watchdog timeout 180s→300s | 2026-03-26 |
| NOT_TRADEABLE logging | 2026-03-26 |
| Manipulation flag sizing ×0.3 cap $10 | 2026-03-25 |
| evaluate_technical_indicator_signals fixed | 2026-03-25 |

### In Progress (DO NOT DUPLICATE)

| Project | Status |
|---------|--------|
| Backtest V4 sweep | Running: 24 configs × 48 symbols |
| Master revalidation | 15/143 (10.5%) |
| SBA v2 rerun | Paused at 10/239 symbols |
| WT 15m deep sweep | 32/160 combos done |
| Tradier recovery agent | Recovering ASTS, SNDK, STZ, FIVN |

### Config Values That Must Not Change

| Config | Value | Why |
|--------|-------|-----|
| `MIN_GAIN_TO_BUY_AGGRESSIVELY` | 3.0% | NEVER below 2.5% |
| `STRICT_NO_LOSS` | ELIMINATED 2026-04-01 (STRICT_NO_LOSS_ACCOUNTS=[]) | Replaced by technical exits (WT/DC). Hedge + ratio IS the protection. |
| `RATIO_MULTIPLIER` | 3.0 | Ratio-only Sharpe 357 vs closing-losers 19 |
| `HARD_STOP_LOSS_MAX_PAIN` | DISABLED | $500+ losses 2026-03-24 |
| `STALE_DATA_PROFIT_SHIELD` | DISABLED | Was killing USO position |
| `HEDGE_MODE` | True (inf/fin/men) | Re-enabled 2026-03-13 |
| `ATR_TRAIL_ENABLED` (tradier) | False | #1 stock PnL destroyer (-2557%) |

### Active BACKTEST_CHANGEs

- **BC_150**: COMPRESSION_BREAKOUT ATR sizing boost (ACTIVE)
- **BC_151**: MIN_GAIN_TO_BUY_AGGRESSIVELY 5.0→3.0% (ACTIVE)
- **BC_152**: Direction-favorable reentry within 120min (ACTIVE)
- **BC_153**: Dynamic ratio dead zone 2pp/5pp (ACTIVE)
- See `100.md` for full history (BC_1 through BC_160)

### ACTIVE: Part 15 — NOLOSS Dogma Sweep (deadline 2026-04-03)

Testing 10 configs (P15_BASELINE through P15_HYBRID) to validate removing STRICT_NO_LOSS. Configs in `backtest_v5_sweep.py`. Results in `data/sweep_results/` + `100.md Part 15`. **NO applying winners to live until ALL 10 complete.**

### Centralization Rules

1. `100.md` = single source of truth for backtest results
2. `data/sweep_results/` = all CSV sweep output
3. `backtest_v5_sweep.py` = only way to run sweep configs
4. `push.py` syncs `100.md` + `config.py` + sweep configs to server
5. `CLAUDE.md` syncs to server via `push.py`
