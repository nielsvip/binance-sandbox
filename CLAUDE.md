# CLAUDE.md — Trading System Rules

## ☠️ DEATH PENALTY — NEVER REVERT LIVE CODE ☠️

**REVERTING live scripts (tradier_manage.py, ez_manage.py, config*.py, backtest_v8_engine.py, ez_positions_quick.py, or any other core file) to an older version is PROHIBITED. NO EXCEPTIONS.**

A "revert" includes:
- Copying an older backup OVER a newer file
- Removing strategy wiring that was previously implemented (First-Hour Momentum, Momentum Interception, DC Daytrade, K-Zone, RSI2, Stoch Entry filters, WT Composite Scoring, SATOSHIT, DELTA_ENGINE, etc.)
- Deleting config switches that sweep files test for
- Replacing a long file with a shorter one without tracing EVERY removed line
- Auto-accepting a merge/resolution that drops implemented strategies

**If you believe reverting is necessary — STOP. Ask the user first. Never auto-revert.**

If a switch is dead (flipping produces identical backtest results), the only acceptable action is **IMPLEMENT IT**. Never "skip", "disable", "remove from sweep", or "rollback" — those are reverts and are FORBIDDEN.

Historical damage from reverts includes the 2026-04-14 wipeout of 33 strategy switches documented in `data/sweep_alerts/FIX_REQUIRED_*.json`. This must NEVER happen again.

**Verification at every session start:** grep each of the 33 canonical switches (see `data/sweep_alerts/canonical_switches.json`) in tradier_manage.py AND ez_manage.py AND config.py AND config_tradier.py AND backtest_v8_engine.py. If ANY are missing → immediate red alert, no other work until restored.

---

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

## 🔴 SANDBOX PARITY — S1 & S2 MUST ALWAYS MATCH MACBOOK

**Live MacBook is the SOLE SOURCE OF TRUTH. Sandboxes (S1 `/home/niels/binance-sandbox/` and S2 `/home/niels/binance-sandbox/`) backtest against these files. If sandboxes drift, sweep results LIE — we've been here before, it cost real money.**

### The 6 files that MUST be bit-identical on MacBook + S1 + S2 at ALL times

1. `ez_manage.py`  · 2. `ez_positions_quick.py`  · 3. `ez_positions_service.py`
4. `tradier_manage.py`  · 5. `config.py`  · 6. `config_tradier.py`

Plus the backtest infrastructure: `v8_quick_engine.py`, `v8_quick_sweep.py`, `backtest_v8_*.py`, `breakout_multi_lung.py`, and all `ez_*.py` / `tradier_*.py` / `wt_*.py` / `utils.py` / `symbols.json` that existed on MacBook root.

### Rules

1. **Session start**: run `python3 check_sandbox_parity.py` (tool section below). If any DRIFT, STOP all work until resolved.
2. **Before launching any sweep on a server**: confirm all 24 core files bit-match MacBook via size+md5. A stale sandbox engine running a sweep produces GARBAGE results that corrupt decisions.
3. **After editing ANY of the 6 critical files**: immediately `rsync --existing --update MacBook → S1` AND `→ S2`. Verify with `check_sandbox_parity.py`.
4. **Automated edits count**: bots like `build_switch_registry.py` or `config_usage_audit.py` that silently edit `v8_quick_engine.py` can cause drift. After any MacBook file mtime change, resync the sandboxes.
5. **Never edit scripts on servers** (per "Current Operating Mode"). If an edit on a server is required, STOP and do it on MacBook first, then rsync.
6. **Never use `push.py`**. Use `rsync --existing --update` — this preserves server-local files that never existed on MacBook and does not re-add files MacBook has archived to /old.

### Past disasters caused by sandbox drift (to prevent, not to relive)

- 2026-04-14: 33 canonical switches wiped by a revert — sandbox had older version that got copied back. See `data/sweep_alerts/FIX_REQUIRED_*.json`.
- 2026-04-16 early: D4 breakout multi-lung sweep on S2 ran 5 configs with wrong engine (MacBook had newer `v8_quick_engine.py` with D4 logic, S2 had pre-D4 stub). Results were garbage. Caught before poisoning decision DB — only by explicit parity check.

### The sync command (run anytime)

```bash
# From MacBook — keeps server-local files intact, only updates if MacBook is newer.
rsync -az --existing --update \
  ez_*.py tradier_*.py wt_*.py utils.py config.py config_tradier.py symbols.json \
  v8_*.py backtest_v8_*.py breakout_multi_lung.py \
  s1-int:/home/niels/binance-sandbox/
# Then repeat with s2-int.
```

### Files that LEGITIMATELY differ between machines

- `/old/inventory_*/` archives — each machine has its own history
- `data/sweep_results/*.csv` — servers write, MacBook reads
- `data/decisions/` JSONL — live-only on MacBook
- `SWEEP_RUNNING` lock — server-specific
- `backups/autosave/` — MacBook-only (15min launchd)
- `klines_cache*/` — bidirectional rsync handled separately

Anything NOT in that legitimate-differ list must bit-match. If you find a drift, fix it IMMEDIATELY — don't queue it.

---

## 🚫 CRYPTO vs TRADIER CONFIGS — NEVER CONFUSE MODES

**Historical cost:** weeks of backtest time wasted because `v8_test_queue.py --mode crypto` silently ran tradier-config tests producing 0-trade "results" that looked green. This is now prohibited at the runner.

### Routing rules (hard-enforced)

| Config file | Machine | Mode | Python |
|---|---|---|---|
| `config.py` | **S1** | `crypto` | `/home/niels/.conda/envs/binance_env/bin/python` |
| `config_tradier.py` | **S2** | `tradier` | `/home/niels/miniconda3/envs/binance_env/bin/python` |
| `wt_dc_delta.py:DEFAULT_CFG` | S1 (crypto) | `crypto` | — |

### Guards in place

1. **`v8_test_queue.py run_item()`** — if `--mode X` but the entry's `config` file infers a different mode, it returns `status="mode_skip"` (not "done", not silent 0-trade). The item stays in the queue, gets picked up on the correct machine.
2. **`sweep_cockpit.py`** — two separate Flask routes (`/param/<name>/queue_test` hardcodes `config_tradier.py`; `/param/crypto/<name>/queue_test` hardcodes `config.py`). The UI cannot produce a wrong-config entry.
3. **Env var `V8_PYTHON`** overrides the hardcoded MacBook Python path. Set it on servers when launching the runner. Already handled by launcher scripts.

### When launching a runner

```bash
# On S1 (crypto)
export V8_PYTHON=/home/niels/.conda/envs/binance_env/bin/python
cd /home/niels/binance-sandbox
nohup "$V8_PYTHON" -u v8_test_queue.py --mode crypto > ~/logs/v8_test_queue_crypto_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# On S2 (tradier)
export V8_PYTHON=/home/niels/miniconda3/envs/binance_env/bin/python
cd /home/niels/binance-sandbox
nohup "$V8_PYTHON" -u v8_test_queue.py --mode tradier > ~/logs/v8_test_queue_tradier_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

### Monitoring

- Result files land in `data/test_queue_results/abtest_<param>_<ts>.json` on the runner's machine.
- If you see `WINNER=<X>  Δ=0.000` with both arms at `sharpe=0.000 trades=0`, that's still a sign of either mode mismatch OR baseline being too weak to generate trades — investigate, don't trust the winner field.
- Per `feedback_sharpe_2_baseline.md`: a result with baseline Sharpe < 2 is trash. Re-run on stronger baseline.

**If this rule gets violated again:** the guard in `v8_test_queue.py` will log `MODE_CONFIG_MISMATCH_SKIP` loudly. That's the signal — do not ignore it.

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

## 📊 BACKTEST REPORTING RULES — AVERAGES, NOT OUTLIERS

### ⚠️ SHARPE DEFINITION IS NON-NEGOTIABLE (repeated 13+ times; keeps getting violated)

**Sharpe for any symbol over any time window MUST be computed as follows. NO EXCEPTIONS:**

1. **Per-trade returns only.** Sharpe = `mean(trade_returns) / std(trade_returns)`. `mean()` divides by N. NEVER sum. NEVER accumulate. NEVER skip the division.
2. **Open losing positions at end-of-test MUST be subtracted.** Any position still open at the final bar must be marked-to-market at the closing price and its (negative) unrealized P&L appended to the trade-return distribution BEFORE computing mean/std. Holding losers forever without counting their paper loss = fraudulent Sharpe. `v8_quick_engine.py:2105-2115` does this correctly — any other engine MUST do the same. `NOLOSS_ENABLED` blocks premature exits during simulation; it does NOT and MUST NOT filter negatives out of the Sharpe denominator.
3. **NEVER annualize Sharpe by sqrt(trades_per_year) or sqrt(N).** `backtest_v8_engine.py:732` emits `sharpe_annual = sharpe_per_trade * sqrt(trades_per_year)` — that column is BANNED in any user-facing report, baseline claim, or sweep ranking. It is mathematical frequency-gaming. The "frozen 2.52 baseline" was `sharpe_annual` (0.14 per-trade × √324) and is therefore invalid. `sharpe_per_trade` is the only Sharpe that may be reported as "Sharpe".
4. **Per-symbol average, NOT pool.** Compute Sharpe once per symbol with rules 1-3, then average across all symbols in the test. Pool Sharpe (`mean(all_trades) / std(all_trades)` across concatenated symbols) may be a diagnostic column but MUST NOT be the ranking metric. Sweep ranking must switch to per-symbol-avg Sharpe.
5. **Cap the per-symbol Sharpe low enough to prevent single-symbol domination.** Current cap = ±20.0 in `v8_quick_engine.py:2141`; a lucky symbol with tight-std few-trade run hits 20.0 and poisons the mean. Lower to ±5.0 and exclude symbols with <30 trades from the per-symbol average (they're the ones that cap).
6. **Per-symbol-avg Sharpe < 1.0 = trash.** Risk-to-reward is unacceptable. Before ranking variants or proposing a live change, the baseline must clear 1.0 per-trade per-symbol-avg on ≥48 crypto / ≥100 stock symbols with ≥30 trades each. If it doesn't, stop ranking and redesign.
7. **Separately track `total_gain_pct` and `avg_gain_per_trade_pct`.** These are useful complementary metrics — not substitutes for Sharpe. Must be labeled distinctly, never called "Sharpe".

Violating any of these = the number is a LIE and the decision it supports is invalid.

### Required columns in every sweep CSV / report

1. **Sharpe and mean-gain = AVERAGE across all symbols in the test**, never the single best outlier.
   - A "Sharpe 5.2" number because one symbol had a lucky run is useless. The number that matters is the per-symbol mean.
   - Also report the range (min, p25, median, p75, max) so spread is visible.
   - If a pool aggregate is shown instead of per-symbol avg, label it clearly as `pool` not `avg`.

2. **Max drawdown % over the full 4-year sequence is MANDATORY** in every summary.
   - Max peak-to-trough equity drawdown as % of starting capital across the ENTIRE tested window.
   - Not per-trade, not per-year — the worst the account ever looked from its high-water mark.
   - CSV column name: `max_dd_pct` (worst-single-symbol DD) + `avg_dd_pct` (mean across syms). Both required.

2b. **Accumulated gain % is MANDATORY** in every summary. CSV column: `accumulated_gain_pct` = sum of all per-trade %-returns across all symbols in the test. Rationale: a 1.2M-trade system at per-trade Sharpe 0.6 can outperform a 10-trade system at per-trade Sharpe 2 by raw return. Sharpe alone is insufficient — must be paired with accumulated gain AND drawdown.

2c. **Engine-tier architecture** (2026-04-21 user directive — VECTORIZED TESTING ONLY AND ALWAYS):
   - **Tier 1 — `v8_quick_engine.py` (vectorized, 24/7 on 3 machines)**: feeds the priority matrix. Tests billions of combos across 12 symbols (different sectors). NO SAVED DECISIONS — output is "just a feel" to shortlist configs for real tests. Parity with live ~85% on shared gates (most thresholds read from config.py); divergences: full DELTA_ENTRY z-score tracking uses velocity proxy only; DC_DAYTRADE/FAST_RISER_REDUCE not wired; portfolio-level L/S ratio cannot be vectorized (symbol-parallel by design).
   - **Tier 2 — `backtest_v8_engine.py` (real-code replica)**: THE REAL TEST. Calls `check_entry_candidates_for_account()` at line 1427, `check_exit_candidates_for_account()` at line 1346, `hedge_engine.monitor_and_manage_hedges()` at line 1311, `hedge_engine.scan_and_hedge_losers()` at line 1316, `execute_trade_action()` at 1409 (on SRS fires), real `MultiAccountTradeManager` at line 765. LS_RATIO enforced via execute_trade_action gates (lines 1968-1986/2112-2130). `V8_SKIP_PROCESS_POSITION=1` env flag stubs `process_position` — user confirmed this does not measurably affect Sharpe/WR since exits flow through check_exit_candidates. Run top candidates from Tier 1 through this on 12 syms, then 48 syms, then full 4yr all syms — winners only.
   - **Tier 3 — Full production sweep (48+ crypto / 128+ stocks × 4yr)**: only for candidates that cleared Tier 2 with per-trade per-symbol-avg Sharpe > 1, accumulated_gain_pct and max_dd_pct validated.
   - **NEVER** treat Tier 1 output as decision material. **NEVER** report Tier 1 results to the user as "this config is better".

2d. **Ratio in NPZ — it is NOT there**: prior agents claimed NPZ files contain ratio rebalance fields since 2022. FALSE. Only `0market_sentiment_score` (global exchange-wide L/S breadth) is precomputed. Portfolio-level L/S ratio (account balance of longs vs shorts) is computed at runtime in `ez_positions_quick.py:1170` and enforced via LS_RATIO gates in execute_trade_action during backtest_v8_engine runs. v8_quick_engine cannot access this without breaking vectorization.

2e. **PARTIAL_PROFIT_LOCK (2026-04-21) — replaces SATOSHIT_PARTIAL_EXIT**: At +0.5% gain, closes 50% via `place_maker_order` (maker fees); falls back to `send_webhook(url_variant="2")` → webhook_url_2 Finandy endpoint. At +0.7% gain, arms trailing stop at first-exit price. When price returns to first-exit price, closes remainder (maker → webhook_url_2 fallback). Config: `PARTIAL_PROFIT_LOCK_ENABLED/ACCOUNTS/GAIN_PCT/ARM_GAIN_PCT/FRAC/USE_MAKER`. State tracked per position_key on `trade_manager.partial_profit_lock_state`. Wired in `ez_manage.process_position` only (not yet in tradier_manage or backtest engines). `send_webhook` gained `url_variant=""` parameter — set to "2" or "3" to route to webhook_url_2/3.
   - If drawdown isn't computed, the summary is INCOMPLETE — flag it.

3. **Labels must specify base timeframe and sample scope**:
   - Crypto base TF = **3m** (so "delay=1 bar" = 3 min).
   - Stocks base TF = **5m** (so "delay=1 bar" = 5 min).
   - Always state: `N symbols × N bars × N years × base-TF`.
   - Never collapse a 15-symbol test into the same row as a 109-symbol test without labeling.

4. **Number of trades required**: Sharpe on <30 trades per symbol is noise. Prefer samples with ≥200 trades/symbol over pool Sharpe on rare combos.

Violations = the 15-sym-40-Sharpe lie that collapsed to 0.35 on full data. This rule prevents reliving that.

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
4. **Sandbox parity check**: `python3 check_sandbox_parity.py` — if any of the 24 core files DRIFT vs S1 or S2, STOP until fixed (see SANDBOX PARITY section above).
5. **Search based on request**:
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
6. ⚠️ SANDBOX SYNC — rsync the edited file(s) to S1 AND S2 IMMEDIATELY (see rule below).
7. Verify md5 parity across MacBook + S1 + S2 before declaring "done".
```

### 🔴 RULE: ANY LIVE EDIT → SANDBOX SYNC IS NOT OPTIONAL

**If you edit a live file on MacBook and do NOT sync it to S1 + S2 in the same turn, the sandboxes are now testing OLD code and every backtest/sweep that runs on them is LYING.** This has cost real money before (2026-04-14 canonical-switch wipeout, 2026-04-16 D4 stub mismatch). It will keep costing money until the reflex is automatic.

**The reflex** — after EVERY edit to any live file in the 6-critical list (`ez_manage.py`, `ez_positions_quick.py`, `ez_positions_service.py`, `tradier_manage.py`, `config.py`, `config_tradier.py`) OR the backtest infra (`v8_quick_engine.py`, `v8_quick_sweep.py`, `backtest_v8_*.py`, `breakout_multi_lung.py`, any other `ez_*.py`/`tradier_*.py`/`wt_*.py`/`utils.py`/`symbols.json`):

```bash
# Run these in the same turn as the edit, no exceptions:
rsync -az --existing --update <edited_files> niels@157.180.125.52:/home/niels/binance-sandbox/
rsync -az --existing --update <edited_files> niels@204.168.181.211:/home/niels/binance-sandbox/
# Verify md5 match across MacBook + S1 + S2.
```

**If a sweep is currently running on S1 or S2** and you just synced new code, flag it: the running worker has the OLD code loaded in memory — only NEXT-spawned workers pick up the change. Either wait for the sweep to finish a batch naturally, or explicitly restart it with user approval.

**"I forgot to rsync"** is not an acceptable failure mode. Apply every live-file edit as a two-step atomic: edit + rsync-to-sandboxes. Missing the rsync = the edit didn't happen, because sweeps now test a ghost version of the file.

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
