# CLAUDE.md — Trading System Rules

## 🚨🚨🚨 SWEEP-LIVENESS MANDATE — READ FIRST. NON-NEGOTIABLE 🚨🚨🚨

### MACHINE ROLES — IMMUTABLE

| Machine | Role | What MUST be running | What MUST NOT be running |
|---|---|---|---|
| **MacBook** (`/Users/niels/Documents/binance`) | LIVE TRADING | `ez_manage.py --account {ang,inf,fin,flz,men}` (5 crypto), `tradier_manage.py --account {trb,trc}` (2 stocks), `ez_positions_quick.py`, `ez_positions_service.py`, `ez_market_data.py`, `ez_orderbook.py` | NO sweeps. NO precompute. NO autonomous_search. |
| **S1** (`s1-int`, `/home/niels/binance-sandbox`) | **CRYPTO SWEEPS ONLY** | `v8_quick_sweep.py --mode crypto` and/or `autonomous_search.py --mode crypto` and/or `v8_test_queue.py --mode crypto`. Continuous. | NO `--mode tradier`. NO live trading. |
| **S2** (`s2-int`, `/home/niels/binance-sandbox`) | **TRADIER SWEEPS ONLY** | `v8_quick_sweep.py --mode tradier` and/or `autonomous_search.py --mode tradier` and/or `v8_test_queue.py --mode tradier`. Continuous. | NO `--mode crypto`. NO live trading. |

Mode-mismatch = 0-trade lying results. **Has cost weeks. KILL on sight.**

### EVERY-SESSION STEP 0 — RUN FIRST, FIX BEFORE ANYTHING ELSE

```bash
# (a) MacBook live trading — expect ≥7
ps -ef | grep -E 'ez_manage\.py --account|tradier_manage\.py --account' | grep -v grep | wc -l

# (b) S1 crypto sweeps RUNNING — expect ≥3
ssh s1-int 'pgrep -afc "v8_quick_sweep.*--mode crypto|autonomous_search.*--mode crypto|v8_test_queue.*--mode crypto"'

# (c) S1 has NO tradier sweeps — expect empty
ssh s1-int 'pgrep -af "v8_quick_sweep.*--mode tradier|autonomous_search.*--mode tradier|v8_test_queue.*--mode tradier"'

# (d) S2 tradier sweeps RUNNING — expect ≥3
ssh s2-int 'pgrep -afc "v8_quick_sweep.*--mode tradier|autonomous_search.*--mode tradier|v8_test_queue.*--mode tradier"'

# (e) S2 has NO crypto sweeps — expect empty
ssh s2-int 'pgrep -af "v8_quick_sweep.*--mode crypto|autonomous_search.*--mode crypto|v8_test_queue.*--mode crypto"'

# (f) CSVs are GROWING
ssh s1-int 'ls -lt /home/niels/binance-sandbox/data/sweep_results/v8_quick_crypto_*.csv 2>/dev/null | head -1'
ssh s2-int 'ls -lt /home/niels/binance-sandbox/data/sweep_results/v8_quick_tradier_*.csv 2>/dev/null | head -1'
```

If (b)/(d) shows 0: find newest CSV in `sweep_results/`, relaunch via launcher script, tell user "Found dead sweep on {S1|S2}, last alive {mtime}, relaunched {tier}."

### LAUNCHER SCRIPTS — USE THESE, NEVER AD-HOC NOHUP

GUI: **http://localhost:5051/sweeps** (crypto → S1, tradier → S2, refuses mode-mismatch).

```bash
# S1 crypto:
ssh s1-int 'bash /home/niels/binance-sandbox/start_crypto_sweeps.sh wt_dc_full'
ssh s1-int 'bash /home/niels/binance-sandbox/start_crypto_sweeps.sh status'

# S2 tradier:
ssh s2-int 'bash /home/niels/binance-sandbox/start_tradier_sweeps.sh wt_dc_full'
ssh s2-int 'bash /home/niels/binance-sandbox/start_tradier_sweeps.sh status'
```

Launcher scripts refuse wrong-host, use `nohup ... > ~/logs/sweep_<tier>_<ts>.log 2>&1 < /dev/null & disown`, verify workers after 30s. **Ad-hoc nohup chains die on ssh disconnect — we lost months of compute to this.**

### POST-LAUNCH VERIFICATION (required for every sweep you start)

1. **T+5s**: `pgrep -af <tier>` returns ≥3 processes.
2. **T+30s**: log shows startup banner — no `Traceback`, no `ERROR`, no `MODE_CONFIG_MISMATCH_SKIP`.
3. **T+5min**: CSV has rows > header. If 0 rows → stuck or dying, investigate.
4. Only then state: "S1 wt_dc_full: N workers, log advancing, CSV at R rows."

Log path: `~/logs/<name>.log` NOT `/tmp/` (cleared on reboot). Always `< /dev/null & disown`.

### END-OF-SESSION CHECK

Repeat (a)–(f). State: "MacBook 7 procs. S1: N workers, CSV R rows, growing. S2: N workers, CSV R rows, growing." NEVER just "Launched, exiting." — if sweep dies after you leave, it's compute wasted.

---

## 🚨 USDC-OVER-USDT — HARD POLICY 🚨

**If a sym has a USDC perp on Binance Futures, EVERYTHING refers to it as USDC. NOTHING uses USDT for those syms.**

This applies to: mark_price, klines, funding rates, OI, NPZ, indicators, sweep symbol-list, live trading order routing — ALL of it.

The 10 syms with USDC perps (as of 2026-04-29): **ETH, BTC, SOL, ADA, BNB, AVAX, XRP, LINK, LTC, UNI**. Re-check `/fapi/v1/exchangeInfo` if uncertain — never default to USDT for these.

The 50 legacy syms (1INCH, ALGO, ANKR, ATOM, AXS, BAND, BAT, BEL, BTCDOM, C98, CELR, CHR, COMP, COTI, DASH, DOT, EGLD, ENJ, ETC, GRT, GTC, HOT, IOST, IOTA, IOTX, KAVA, KNC, KSM, LRC, MANA, MTL, NKN, QTUM, RLC, RSR, RVN, SAND, SKL, SNX, STORJ, SUSHI, SXP, THETA, TRX, VET, XLM, XMR, XTZ, YFI, ZEN) stay as `USDT`.

**Backtest convention**: USDT historical data labeled as USDC for the 10 majors (USDC perps launched 2024, insufficient history). Label = USDC; source rows = USDT. Live trading queries actual USDC bid/ask before ordering.

**Files that MUST NOT exist** for the 10 majors: `klines_cache/{sym}USDT_*.json`, `klines_cache_backtest/{sym}USDT_*.json`, `klines_cache_gateway/{sym}USDT_*.json`, `backtest_v8/indicators/{sym}USDT.npz`, `data/funding_cache/{sym}USDT.json`, `data/oi_cache/{sym}USDT.json`. If found — DELETE. If you accidentally fetch USDT data for a USDC sym, rename to USDC immediately — never leave both files coexisting.

---

## 🚨 NPZ REGEN — STOP DOING THIS WRONG (12+ TIMES NOW) 🚨

1. **NPZ source = `klines_cache_backtest/` ONLY.** NOT `klines_cache/` (live), NOT `klines_cache_gateway/`. The `_backtest` dir holds 5+ years of 15m klines for 50 legacy USDT + 10 USDC majors. **15m is the BASIS — all TFs (1h, 4h, D, W, M, 3m, 5m) are derived from 15m by the precompute.**
2. **Mac uses `klines_cache/`** (live + V3 forward-test, ~1200 bars). Mac does NOT run multi-year sweeps.
3. **Servers backtest from `klines_cache_backtest/`.**
4. **Tail freshness**: before each regen, append latest klines from `klines_cache/` into `klines_cache_backtest/` so 15m base goes up to within 1 hour of NOW.
5. **NPZ scope**: 48+ crypto × 4yr + 128+ stocks × 4yr. Narrower = noise.
6. **NPZ MUST include EVERY param the v8 sweep ever asks for** — all TFs (adx_15m/4h/D/5m, macd_crossover_*/crossunder_* all TFs, wt*_W/wt*_M etc.). Zero-filled missing fields = lying results.
7. **Mandatory audit before any sweep**: `python /tmp/audit_npz_fields.py v8_quick_engine.py backtest_v8/indicators MODE` — must report `ALL FIELDS PRESENT ✓`. Failures = REJECT.

---

## ☠️ DEATH PENALTY — NEVER REVERT LIVE CODE ☠️

**REVERTING live scripts is PROHIBITED. NO EXCEPTIONS.** A "revert" includes: copying older backup over newer file, removing strategy wiring (First-Hour Momentum, Momentum Interception, DC Daytrade, K-Zone, RSI2, Stoch Entry, WT Composite Scoring, SATOSHIT, DELTA_ENGINE, etc.), deleting config switches, replacing long file with shorter one, auto-accepting merge that drops strategies.

**If you believe reverting is necessary — STOP. Ask the user first.**

If a switch is dead, the only acceptable action is **IMPLEMENT IT** — never skip/disable/remove/rollback.

**Verification at every session start:** grep each of the 33 canonical switches (see `data/sweep_alerts/canonical_switches.json`) in tradier_manage.py, ez_manage.py, config.py, config_tradier.py, backtest_v8_engine.py. ANY missing → red alert, stop all other work.

---

## 🚨 MANDATORY BACKUP BEFORE EVERY EDIT

```bash
cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py
# Then edit.
```

Autosave: `autosave_15min.py` → `backups/autosave/<timestamp>/`, auto-commits to git every 15min via launchd (`~/Library/LaunchAgents/com.niels.autosave-15min.plist`). `/backups/` is the ONLY reliable history.

---

## 🔴 SANDBOX PARITY — S1 & S2 MUST ALWAYS MATCH MACBOOK

**MacBook is the SOLE SOURCE OF TRUTH.** If sandboxes drift, sweep results LIE.

### The 6 files bit-identical on MacBook + S1 + S2 at ALL times

`ez_manage.py`, `ez_positions_quick.py`, `ez_positions_service.py`, `tradier_manage.py`, `config.py`, `config_tradier.py`

Plus backtest infra: `v8_quick_engine.py`, `v8_quick_sweep.py`, `backtest_v8_*.py`, `breakout_multi_lung.py`, all `ez_*.py`/`tradier_*.py`/`wt_*.py`/`utils.py`/`symbols.json`.

### Rules

1. **Session start**: `python3 check_sandbox_parity.py` — any DRIFT → STOP until fixed.
2. **Before any server sweep**: confirm 24 core files bit-match MacBook.
3. **After editing ANY critical file**: immediately rsync to S1 AND S2. Verify md5.
4. **Automated edits count**: bots that silently edit `v8_quick_engine.py` cause drift. Resync after any mtime change.
5. **Never edit scripts on servers** — edit on MacBook, then rsync.
6. **Never use `push.py`** — use `rsync --existing --update` (preserves server-local files).

Past disasters: 2026-04-14 sandbox had older version → 33 canonical switches wiped on copy-back. 2026-04-16 S2 had pre-D4 stub → garbage results.

### Sync command

```bash
rsync -az --existing --update \
  ez_*.py tradier_*.py wt_*.py utils.py config.py config_tradier.py symbols.json \
  v8_*.py backtest_v8_*.py breakout_multi_lung.py \
  s1-int:/home/niels/binance-sandbox/
# Repeat with s2-int.
```

### Files that LEGITIMATELY differ

`/old/inventory_*/`, `data/sweep_results/*.csv`, `data/decisions/` JSONL, `SWEEP_RUNNING` lock, `backups/autosave/`, `klines_cache*/`. Everything else must bit-match.

---

## 🚫 CRYPTO vs TRADIER CONFIGS — NEVER CONFUSE MODES

| Config file | Machine | Mode | Python |
|---|---|---|---|
| `config.py` | **S1** | `crypto` | `/home/niels/.conda/envs/binance_env/bin/python` |
| `config_tradier.py` | **S2** | `tradier` | `/home/niels/miniconda3/envs/binance_env/bin/python` |

Guards: `v8_test_queue.py` returns `status="mode_skip"` on mismatch (not silent 0-trade). `sweep_cockpit.py` routes hardcode correct config per mode. `V8_PYTHON` env var overrides Python path on servers.

Monitoring: `WINNER=<X> Δ=0.000` with both arms `trades=0` = mode mismatch or weak baseline. `v8_test_queue.py` logs `MODE_CONFIG_MISMATCH_SKIP` — do not ignore. Baseline Sharpe < 2 = trash.

---

## ⚠️ NO LYING / NO GUESSING — REAL MONEY

- **NEVER** claim something works without log/exchange proof.
- **NEVER** guess root causes — trace the actual code path.
- **NEVER** present backtest numbers from reimplemented logic. Only `process_position()`, `check_entry_candidates()`, `check_exit_candidates()` produce valid numbers.
- **NEVER** inflate, extrapolate, or cherry-pick numbers. Report exactly what the code produced.
- **NEVER** cover up errors. Say "I made an error in X" immediately.
- **NEVER** blame external systems (Redis, API) before exhausting code-level causes.
- When in doubt: say "I don't know yet" or "Can you clarify?"

---

## 📊 BACKTEST REPORTING RULES

### 🩸 SHARPE DEFINITION — LIVE-MONEY POLICY (2026-04-29)

**This rule was hardened after a 30%-net-worth-in-a-week loss caused by inflated Sharpe numbers reaching live deployment. EVERY violation in this section is now a hard error, not a warning.**

Any number labeled "Sharpe" in any UI, CSV, log, message, memo, or planning doc MUST satisfy ALL of:

1. **Per-trade returns only.** `sharpe = mean(trade_returns) / std(trade_returns)`. NEVER sum. NEVER accumulate. NEVER divide by anything other than std of the same return distribution. Sharpe IS dimensionless — if your number depends on bar-count, days, or sqrt of anything, it's NOT Sharpe and must not be called Sharpe.
2. **Open losing positions MUST be marked-to-market** at the final bar and appended to the return distribution BEFORE computing Sharpe. Skipping this is fraudulent — it hides losses behind held positions and was the proximate cause of the live blow-up.
3. **`sharpe_annual = sharpe_per_trade * sqrt(trades_per_year)` is BANNED.** Same for `sharpe * sqrt(252)`, `sharpe * sqrt(N)`, or any multiplier whose only purpose is making the number bigger. These are frequency-gaming, not Sharpe. They MUST NOT appear in any column, label, log, or report — including diagnostics. If you find one, delete it.
4. **Pool Sharpe IS THE Sharpe**: `mean(all_trade_returns) / std(all_trade_returns)` pooled across ALL trades of ALL symbols. Per-symbol-avg Sharpe is diagnostic ONLY and MUST be labeled `sym_sharpe` — never bare "sharpe".
5. **MINIMUM SAMPLE for any *publishable* Sharpe**: ≥48 crypto symbols OR ≥100 stock symbols, >1 year of trades each, pool-averaged. Below this floor a Sharpe is "internal debug" — it MAY be displayed only with an attached `[DIAGNOSTIC ONLY · n_syms=X · years=Y]` tag. Decisions, promotions to live, and recommendations to user CANNOT use sub-floor Sharpe — period.
6. **Cap per-symbol Sharpe at ±5.0** before averaging. Exclude any symbol with <30 trades from `sym_sharpe`. Single-symbol Sharpes >5 are noise — a 3-trade symbol with luck hits 20.
7. **Pool Sharpe < 1.0 = trash.** A config below 1.0 on the canonical metric is NOT a candidate for promotion. Stop ranking variants; redesign the strategy.
8. **NEVER bare "Sharpe X.X" in user-facing text.** Always use the qualifier name: `pool_sharpe`, `sym_sharpe`, or `sharpe_per_trade`. Bare "Sharpe" is the format that historically misled into live deployment and is forbidden.

**Mandatory reporting line for "Best" anywhere** (sweep summary, leaderboard, status update, memory entry):
```
pool_sharpe=X.XXXX | sym_sharpe=X.XXXX | avg_gain_trade=X.XX%/trade | gain_per_yr=XX.X%/yr | gain_sym_yr=X.XXXX%/sym/yr | trades=N | dd=X.X% | n_syms=N | years=Y.Y
```
ALL nine fields. Drop any one and the report is invalid.

**Enforcement**: every Sharpe-displaying script imports `metrics_guard.py` and routes through `validate_and_format_sharpe()`. The function REFUSES to format a Sharpe number that violates rules 1, 3, 4, or 8 (raises). It DOWNGRADES to "[DIAGNOSTIC]" tag for violations of rule 5. Direct `f"{sharpe:.2f}"` in user-facing code = bug to fix.

### Legacy compatibility for stored numbers
Historical CSVs/JSONLs may contain `sharpe_annual` columns from before the rule. Treat these as INVALID until converted: read trade-list, recompute pool_sharpe from per-trade returns, overwrite the column. NEVER read a stored "Sharpe X.XX" column at face value — verify the formula by which it was produced. The `metrics_guard.audit_csv()` helper does this.

### Required columns in every sweep CSV / report

1. Sharpe = AVERAGE across all symbols (never single best). Report range: min, p25, median, p75, max.
2. `max_dd_pct` (worst-single-symbol DD) + `avg_dd_pct` (mean across syms) — MANDATORY in every summary.
2b. `accumulated_gain_pct` — MANDATORY. Sharpe alone is insufficient.
2c. **Engine-tier architecture**:
   - **Tier 1 — `v8_quick_engine.py`** (vectorized): shortlist only. Output is "just a feel" — NEVER report Tier 1 to user as "this config is better". ~85% parity with live; divergences: DELTA_ENTRY uses velocity proxy, DC_DAYTRADE/FAST_RISER_REDUCE not wired, portfolio L/S ratio not vectorized.
   - **Tier 2 — `backtest_v8_engine.py`** (real-code replica): THE REAL TEST. Calls actual `check_entry_candidates_for_account()`, `check_exit_candidates_for_account()`, `hedge_engine`, `MultiAccountTradeManager`. Run top Tier-1 candidates here on 12→48 syms.
   - **Tier 3 — Full production sweep** (48+ crypto / 128+ stocks × 4yr): only for Tier-2 winners with per-trade per-sym-avg Sharpe > 1.
2d. **Ratio NOT in NPZ**: `market_sentiment_score` is precomputed; portfolio L/S ratio is runtime-only (`ez_positions_quick.py:1170`). Prior agents claiming ratio fields in NPZ were wrong.
2e. **PARTIAL_PROFIT_LOCK v2**: 3-step TP. Step 1: close 50% at +0.5% via `place_maker_order` → `send_webhook(url_variant="2")`; set `stop_level = entry × (1 ± BE_BUFFER_PCT/100)`. Step 2: at +0.75% upgrade stop to `first_exit_price`. Step 3: stop hit → close remainder via webhook_url (100%). Config: `PARTIAL_PROFIT_LOCK_ENABLED/GAIN_PCT=0.5/ARM_GAIN_PCT=0.75/BE_BUFFER_PCT=0.02/FRAC=0.5`. State: `trade_manager.partial_profit_lock_state[pk] = {fired, first_exit_price, stop_level, stop_upgraded}`. Wired in ez_manage, tradier_manage (before UNIVERSAL_NOLOSS_GATE), backtest_v8_engine, v8_quick_engine.
2f. **NOLOSS_BYPASS_WT_5OF5** (default OFF): allows loss exit when ALL 5 WT TFs (3m/15m/1h/4h/D) flip against position. Config: `NOLOSS_BYPASS_WT_5OF5_ENABLED=False`, `MIN_TFS=5`. Wired in tradier_manage + v8_quick_engine; NOT in ez_manage (requires per-path edit).
3. **Labels**: crypto base TF = **3m**, stocks = **5m**. Always state `N symbols × N bars × N years × base-TF`.
4. Sharpe on <30 trades/symbol = noise. Prefer ≥200 trades/symbol.

### STANDARD METRIC SET — ALL FIVE REQUIRED (2026-04-25)

| Metric | Formula | Purpose |
|---|---|---|
| `pool_sharpe` | mean(trade_returns)/std(trade_returns) | Risk-adjusted per-trade quality |
| `sym_sharpe` | mean(per-symbol Sharpes) | Diagnostic; consistency |
| `avg_gain_trade` | acc_gain_pct / trades | Per-trade return |
| `gain_per_yr` | acc_gain_pct / n_years | Annual return |
| `gain_sym_yr` | acc_gain_pct / n_syms / n_years | Cross-machine comparable |

When reporting "Best": `pool_sharpe=X | avg_gain_trade=X%/trade | gain_per_yr=X%/yr | gain_sym_yr=X%/sym/yr | trades=N | dd=X%`

CSV columns (canonical): `iter, pool_sharpe, sym_sharpe, acc_gain_pct, gain_sym_yr, avg_gain_trade, gain_per_yr, max_dd_pct, trades, gain_vs_bh, elapsed_s, overrides_count, reliable, useless, overrides_json`

Translating old results: tradier n_syms=114, crypto n_syms=50, n_years=(current − 2022-01-01)/365.25.

---

## ⚠️ ALL CLOCKS = UTC. MARKETS = ET (UTC−4 now)

| Event | ET | UTC |
|-------|-----|-----|
| Market open | 9:30 AM | **13:30** |
| Market close | 4:00 PM | **20:00** |
| Pre-market prep | 8:00 AM | **12:00** |

**NEVER write cron times in ET. NEVER assume `date` is ET.**

---

## STEP 0 — Every Conversation Start

1. **Read STATE OF AFFAIRS** at bottom of this file.
2. **Read `100.md`** — master audit doc (Parts 1–15). Skim headers, read relevant sections.
3. **Refresh knowledge base**: `cd /Users/niels/Documents/binance && python3 export_conversations.py`
4. **Sandbox parity check**: `python3 check_sandbox_parity.py` — DRIFT → STOP until fixed.
5. **Search memory**: script mentioned → `SCRIPT_STATE.md`; topic → `TOPIC_STATE.md`; "continue from last time" → `INDEX.md`.

---

## STEP 0b — Before ANY File Edit

1. Read `LOCKED_FILES.md` — locked? → **STOP**.
2. Continue only if user says **"unlock \<file\>"** in same message.

---

## STEP 1 — Every File Edit Workflow

```
1. Read LOCKED_FILES.md — locked? STOP.
2. Backup: cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py
3. Edit locally (/Users/niels/Documents/binance/<file>)
4. Compile: python -c "import py_compile; py_compile.compile('<file>', doraise=True)"
5. Verify: grep/read changed lines to confirm.
6. SANDBOX SYNC — rsync to S1 AND S2 IMMEDIATELY.
7. Verify md5 parity across MacBook + S1 + S2 before declaring "done".
```

**Edit without rsync = sandboxes test OLD code, every sweep LIES.**

```bash
rsync -az --existing --update <edited_files> niels@157.180.125.52:/home/niels/binance-sandbox/
rsync -az --existing --update <edited_files> niels@204.168.181.211:/home/niels/binance-sandbox/
```

Files requiring rsync: 6-critical list + `v8_quick_engine.py`, `v8_quick_sweep.py`, `backtest_v8_*.py`, `breakout_multi_lung.py`, any `ez_*.py`/`tradier_*.py`/`wt_*.py`/`utils.py`/`symbols.json`.

If a sweep is running when you sync: running workers have OLD code in memory — only next-spawned workers pick up the change.

---

## Absolute Prohibitions

| NEVER | Detail |
|-------|--------|
| git reset / restore / checkout | Only move forward. |
| Revert to backup/older version | Ask user instead. |
| Access `.history/` | Unless explicitly permitted. |
| delete/pop/clear position dict entries | No `.pop()`, `del`, `.clear()`. `symbols.json` count must match positions exactly. |
| Overwrite file with older version | Without explicit permission. |
| Create position from zero | Only `add_new_symbols.py` creates positions. |
| Zero position from API absence | Absence ≠ closed. Only WS `positionAmt=0` (threshold 5+) confirms closure. |
| Zero entry_price, max_gain, opened_at | SACRED. Only `positionAmt` may be zeroed on confirmed close. |
| Overwrite Redis without validation | Redis may be poisoned/stale. |
| Use `.clear()` on positions dicts | Empty source = source failed, NOT positions gone. |
| Make account-specific scripts | Account logic as conditions inside shared scripts. |
| Call `ez_backup.py` | Live system script, not a Claude utility. |
| Close positions at a loss on live | STRICT_NO_LOSS active. |
| Add stop-loss code to tradier_manage.py | `HARD_STOP_LOSS_MAX_PAIN` caused $500+ losses 2026-03-24. ALL stop loss paths DISABLED. |
| Present backtest results from reimplemented logic | Only real `process_position()` / `check_entry/exit_candidates()` results. |
| Inflate trade counts or PnL | Report raw numbers. 120 trades = 120. |
| Call backtest "working" until it matches live logs | Compare vs `data/decisions/` JSONL. |
| Blame "strict gates" for low trade counts | Missing trades = missing mock attributes or broken patches. |
| Place orders outside `execute_now()` | THE ONLY gate for ALL Binance orders. |
| Add `not is_hedge` bypasses to execute_now guards | Guards apply to ALL callers. |
| Let MOMENTUM_RIDER bypass execute_now | PERMANENTLY DISABLED. |
| Add new entry strategies to live code | See NEW STRATEGY PROHIBITION. |
| Add autonomous position-opening loops | No `asyncio.create_task(scan_and_open_*)`. |
| Add percentage-based exit triggers | No `if gain < -X%: exit`. Exits on technicals ONLY. |
| Hardcode config values in backtest engines | Backtests MUST read from config.py. |
| Auto-expand tradeable_keys | Hand-picked per account. No `.add()` from scanners. |
| Add hedge loops independent of HEDGE_MODE | All hedges via ez_positions_quick only. |
| Implement YouTube/web strategies directly | Research → sweep backtest → paper trade → user approval → live. |
| Use HANDS_FREE to add new strategies | HANDS_FREE = bug fixes + proven changes only. |

---

## NEW STRATEGY PROHIBITION (2026-03-27: 8 strategies → 0 trades + 15,378 rogue opens)

1. NEVER add strategy without explicit user approval outside HANDS_FREE.
2. NEVER enable on real money without full sweep proof (48 crypto 4yr S1 + 128 stocks 2yr S2) + paper trading days.
3. NEVER "backtest" with reimplemented logic.
4. NEVER add >1 new strategy per conversation.
5. NEVER wire strategy without a kill switch defaulting to OFF.
6. Before ANY new strategy: present entry logic, exit logic, data pipeline, trade freq, risk, interaction with existing.
7. After implementation: verify fires in 24h paper with trades in decision JSONL.

**Banned without V8 backtest proof**: ORB, Clenow, SMFI, Minervini, Connors RSI, DC Daytrade, Episodic Pivot, Squeeze, Outlier Scalper, Outlier Hunter, Mover Detection, momentum fade/rider, autonomous scanners.

---

## EXECUTE_NOW IS THE ONLY GATE

1. ALL orders (open, augment, reduce, close, hedge) go through `execute_now()` in ez_manage.py.
2. NO `not is_hedge` bypasses — guards apply to ALL callers.
3. NO reason-string bypasses — reason is for logging only.
4. OPEN on empty position = allowed. AUGMENT on existing = gain check applies to ALL.
5. No `futures_create_order` or REST `/fapi/v1/order` calls outside execute_now.
6. **CONSULT TRACKER BEFORE HEDGING** — check `active_hedges` + `exit_candidates` in tracker.json. (2026-03-29: 24,833 rogue orders from not doing this.)
7. `persist_hedge_record` MUST be called after any execute_now for a hedge.

---

## STRICT_NO_LOSS

**LIVE: active. BACKTEST: disabled for testing.**

Disabled % stop paths in tradier_manage.py (MUST STAY DISABLED): `HARD_STOP_LOSS_MAX_PAIN` (gain < -1.5%), `STALE_DATA_HARD_STOP` (gain < -1.5% stale), `STALE_DATA_GAIN_EROSION`, `Market_Against_Position` bias reduce (gain < -0.5%).

**If you EVER see `gain < -` followed by `return True` in any exit path — DISABLE IT.**

---

## Current Operating Mode

- MacBook: LIVE TRADING (source of truth). S1/S2: backtesting only.
- DO NOT start/restart trading services on servers. DO NOT edit scripts on servers.
- Server Redis tunnel DISABLED (dummy on 6381). Local Redis: 6379. Gateway: 6380.
- **TRADIER IS PRIORITY** — $70k stocks vs $1k crypto.
- **SERVER LOCKS**: Read `SERVER_LOCKS.md` AND check `/home/niels/SWEEP_RUNNING` before ANY server action. NEVER `killall python3` without checking. Screen sessions `sweep48h` are PROTECTED.

---

## Code Style

**Formatter**: `black` | **Linter**: `pylint`

- `logger.*` and function calls are **always one line** — never broken across lines.
- One blank line between functions. **Zero blank lines inside function bodies.**
- Use `config.BASE_PATH` — never hardcoded paths.
- No docstrings/comments/type annotations on code you didn't change.
- Follow existing naming: `symbol` not `sym`/`s`/`sb`.

---

## Environments & Servers

| Location | Python | Path |
|----------|--------|------|
| Local | `/opt/anaconda3/envs/binance_env/bin/python` | `/Users/niels/Documents/binance` |
| Server 1 | `/home/niels/.conda/envs/binance_env/bin/python` | `/home/niels/binance` |
| Server 2 | `/home/niels/miniconda3/envs/binance_env/bin/python` | `/home/niels/binance` |
| Sandbox | same as Server 1 | `/home/niels/binance-sandbox` |
| Klines box | — | `157.90.168.35` — klines ONLY, no scripts |

---

## Architecture

**WaveTrend (WT)** = primary signal. 26 fields/TF/symbol. See `wt_composite.py` + `ez_indicators.py`.

| TF Level | Timeframes | Role |
|----------|-----------|------|
| LTF (Triggers) | M, W, D, 4h, 1h | Setup detection |
| Entry Confirmation | 15m, 3m | Structure break + momentum |
| Entry Execution | 3m | 3m break + 15m confirm |
| Exit | 3m | Structure break |

**Crypto core**: ez_manage.py, ez_positions_service.py, ez_positions_quick.py, ez_prices.py, ez_rankings.py, ez_market_data.py, ez_indicators.py, ez_klines.py, wt_composite.py

**Stock core**: tradier_manage.py (STOP LOSSES DISABLED), tradier_api.py, tradier_positions.py, tradier_prices.py, tradier_rankings.py, tradier_indicators.py

**Accounts**: Crypto: `ang`, `inf`, `flz`, `men`, `fin` | Stocks: `trb`, `trc`

**Config**: `config.py` (crypto), `config_tradier.py` (stocks), `symbols.json` (350+ pairs, count must match positions exactly), `.env.gpg` (API keys — do not touch)

**Trade data**: `data/decisions/` JSONL per account per day.

---

## Timeframe Derivation — NEVER claim missing data

**15m klines = ALL timeframes since 2020.**

| 15m → Derived | Method |
|--------------|--------|
| 1h | resample('1h').agg(open=first, high=max, low=min, close=last, volume=sum) |
| 4h | resample('4h') same |
| D | resample('1D') same |
| 3m/5m | each 15m bar × 5/3 (interpolated) |

Map HTF arrays back to base TF via `np.searchsorted`.

---

## Backtest System (V8 — only valid system)

`backtest_v8_precompute.py` → NPZ. `v8_quick_engine.py` (Tier 1 vectorized). `backtest_v8_engine.py` (Tier 2 real-code). `v8_quick_sweep.py` / `autonomous_search.py` (sweep runners). V3/V4/V5/old scripts = RETIRED in `old/` — do NOT use.

**NPZ paths** (S1/S2 sandbox):
- Crypto indicators: `backtest_v8/indicators/`
- Tradier indicators: `backtest_v8/indicators/` (tradier mode)
- Klines: `klines_cache_backtest/` (crypto) | `klines_cache_backtest/tradier/` (stocks)

**Rules**: NEVER write new backtest that reimplements logic. NEVER trust `old/`. Validate with `backtest_evaluate_functions*.py` before deploying. NEVER compare results across different precomputed versions.

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

Activate with **`HANDS_FREE`** in your message: no confirmations, auto-approve edits, chain steps, handle errors silently, full report at end.

Does NOT override: LOCKED_FILES.md, Absolute Prohibitions, STRICT_NO_LOSS.

---

## HANDS_OFF Mode

Activate with **`HANDS_OFF`** in your message: no questions, no confirmation prompts, full autonomy until final result.

Does NOT override: LOCKED_FILES.md, Absolute Prohibitions, STRICT_NO_LOSS.

---

## Misc Rules

- **Strategy development**: Phase 1 = cross-symbol rules. Phase 2 = per-symbol. DO NOT skip to Phase 2.
- **Context compaction**: Before compacting, save full conversation to disk + note path.
- **100.md condense**: Keep last 7 days of perf rows. Merge "Applied Today" after 3 days. Move raw test data to CSV. Never delete Parts 1–5, active BC entries, "Not Yet Applied" priorities.
- **Auto-Confirm**: Proceed without asking for: "Command contains empty quotes before dash" or "Command contains `$()` command substitution".

---

## STATE OF AFFAIRS

### Config Values That Must Not Change

| Config | Value | Why |
|--------|-------|-----|
| `MIN_GAIN_TO_BUY_AGGRESSIVELY` | 3.0% | NEVER below 2.5% |
| `STRICT_NO_LOSS` | ELIMINATED (STRICT_NO_LOSS_ACCOUNTS=[]) | Replaced by technical exits (WT/DC). Hedge + ratio IS the protection. |
| `RATIO_MULTIPLIER` | 3.0 | Ratio-only Sharpe 357 vs closing-losers 19 |
| `HARD_STOP_LOSS_MAX_PAIN` | DISABLED | $500+ losses 2026-03-24 |
| `STALE_DATA_PROFIT_SHIELD` | DISABLED | Was killing USO position |
| `HEDGE_MODE` | True (inf/fin/men) | Re-enabled 2026-03-13 |
| `ATR_TRAIL_ENABLED` (tradier) | False | #1 stock PnL destroyer (-2557%) |
