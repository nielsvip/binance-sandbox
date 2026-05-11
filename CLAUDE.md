# CLAUDE.md — Trading System Rules

## 🚨🚨🚨 NO-LIES MANDATE — READ FIRST. ABSOLUTE. 🚨🚨🚨

**Lying Sharpe/gain/dd numbers wiped out half the user's net worth in 4 months.** Every metric written/displayed/reported MUST be REAL. Forward AND backward.

### Forward (every new result)

1. **Every script emitting Sharpe/gain/dd** to a human-facing surface (CSV, JSONL, log, MD, UI, agent message, memory) MUST route through `metrics_guard.validate_and_format_sharpe()` or `format_standard_set()`. No `metrics_guard` import → not allowed to write a Sharpe number anywhere.
2. **Every CSV in `data/sweep_results/` or `data/autonomous/`** MUST include canonical columns: `pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr, trades, max_dd_pct, n_syms, years`. Missing any → reject row.
3. **No annualization. No sqrt(252). No sqrt(N).** BANNED column names: `sharpe_annual, sharpe_y, sharpe_yearly, sharpe_w, pool_sharpe_proxy, sharpe_rough`. Delete/rewrite emitters.
4. **No bare "Sharpe" label.** Always qualified: `pool_sharpe`, `sym_sharpe`, or `sharpe_per_trade`. Unqualified "Sharpe X" = violation.
5. **Sample floor**: ≥48 crypto syms OR ≥100 stocks × >1yr × ≥30 trades/sym. Below → `[DIAGNOSTIC ONLY]` — never for promotion/deployment/recommendation.
6. **Source of truth = trade-return list.** Sharpe without per-trade returns dataset = `[UNVERIFIED]`.

### Backward (historical files)

Every CSV/JSONL in `data/` with Sharpe column audited 2026-04-30 via `metrics_guard.audit_csv()`. **OK** (canonical, no banned names) → keep. **RECOMPUTABLE** (trade list pairable) → rewrite column with `pool_sharpe(returns)`, original preserved as `<col>_legacy_lie`. **UNVERIFIABLE** (no trade list) → tag header `[UNVERIFIED]`, move to `data/_legacy_unverified/` or delete. Any historical Sharpe not audited = LIE. Don't cite/promote/compare.

### Lock the door

- **`metrics_guard.write_sharpe_row()`** = ONLY sanctioned way to write Sharpe row to `data/sweep_results/` or `data/autonomous/`. Validates + refuses violations.
- **`/tmp/BACKTEST_HOLD`** suspends Mac→server autosync (`rsync_to_sandbox.sh`). Any A/B: `touch` with reason+ts before, `rm` after.
- Legacy SHARPE rules 1–8 remain in force (per-trade returns; open losers MtM'd; no annualization; pool Sharpe canonical; sample floor; etc).
- **NEVER move/quarantine/delete a lying CSV or violator BEFORE** (a) recomputed real Sharpe exists AND (b) compliant replacement exists. Tag `# [UNVERIFIED ...]` in place; do NOT move. 2026-04-30: agent moved 329 files to quarantine before replacements existed; all restored. Rule: tag → replace → verify → THEN move.
- **Tier names per `metrics_guard.tier_name()`** replace "trash": Discard / Noise / Directional / Best-of-current / Strong / Aspirational. Sub-floor still gets `[DIAGNOSTIC]` tag. Never say "trash" — name the tier.

### IMPOSTER BLOCK (2026-04-30, post quality_optimizer per_sym fiasco)

Imposter results bypass `metrics_guard`, hide sample, entrap live config. **Refuse at production time:**

1. **Single-symbol "BEST" promotion** (e.g. `override_per_sym_<SYM>_BEST.json`, qopt single-sym, autonomous_search single-sym leaderboard) = sample-floor violation. FORBIDDEN to auto-write or feed live. Multi-sym pool only. Per-sym diagnostics MUST be in `data/_diagnostic/`, have `[DIAGNOSTIC ONLY · n_syms=1]` in `_meta`, NOT discoverable by live override loader.
2. **Custom "Score"/composite without canonical row** = imposter. Custom scores OK for ranking ONLY if all 9 canonical fields written alongside.
3. **"trades=N WR=X% gain=+Y%" in `_meta`** — exact pattern that lured +13,435% single-sym promotion. Forbidden. Use mandatory reporting line or refuse to write.
4. **Auto-promotion to `backtest_v8/btc_loop_results/override_*`** (or any live-loader path) from any script not importing `metrics_guard`. Must route through `metrics_guard.write_sharpe_row()` + sample-floor check. Quarantined producers (`audit_repo_baseline.json`): `quality_optimizer.py, autonomous_search.py, v8_quick_engine.py, v8_quick_sweep.py, v8_test_queue.py, backtest_v8_engine.py`. MUST exit non-zero on promote until retrofitted.
5. **"Source run: qopt_…" with source dir missing** = `[UNVERIFIABLE]`. Override DOA. Refuse load.
6. **"Auto-promoted" without trade-list co-location** = forbidden. Trade list IS the proof.

**Chokepoints:**
- `quality_optimizer.py --per-sym` → DISARMED. Raises `SystemExit("IMPOSTER_BLOCK: per-sym promotion forbidden — use multi-symbol pool sweep + metrics_guard.write_sharpe_row")`.
- Auto-promote in `autonomous_search.py` + future optimizers → must call `metrics_guard.write_sharpe_row()` OR exit non-zero.
- Override loaders (`btc_loop.py, ez_manage.py, v8_quick_engine.py`) reject any `_meta` matching imposter pattern. Regex guard at load + log `IMPOSTER_OVERRIDE_REFUSED: <path>` + skip.
- `[UNVERIFIED]`-tagged files stay in place — do not move/delete until verified replacement exists.

**Encountering an imposter: HANG IT FIRST.** Tag `_meta`, disarm producer, write refusal log, THEN continue. Default = "refuse and log".

---

## 🚨🚨🚨 SWEEP-LIVENESS MANDATE — NON-NEGOTIABLE 🚨🚨🚨

### MACHINE ROLES (2026-05-08)

**S2 DEAD (user shut down). S1 runs BOTH crypto AND tradier sweeps.**

| Machine | Role | MUST run | MUST NOT run |
|---|---|---|---|
| **MacBook** (`/Users/niels/Documents/binance`) | LIVE TRADING | `ez_manage.py --account {ang,inf,fin,flz,men}`, `tradier_manage.py --account {trb,trc}`, `ez_positions_quick.py`, `ez_positions_service.py`, `ez_market_data.py`, `ez_orderbook.py` | NO sweeps. NO precompute. NO autonomous_search. |
| **S1** (`s1-int`, `/home/niels/binance-sandbox`) | **CRYPTO + TRADIER SWEEPS** | `backtest_v8_sweep.py --mode crypto` (8 syms, system_combo) AND `--mode tradier` (20 stocks, tradier_param_hunt). Continuous via watchdog. | NO live. Kill `start_backtest_v8_loop.sh` (OOMs). |
| **S2** | **DEAD — DO NOT USE** | — | Everything. Never SSH s2-int. |

Mode-mismatch = 0-trade lying results. **Has cost weeks. KILL on sight.**

### EVERY-SESSION STEP 0 — RUN FIRST

```bash
# (a) MacBook live trading — expect ≥7
ps -ef | grep -E 'ez_manage\.py --account|tradier_manage\.py --account' | grep -v grep | wc -l
# (b) S1 crypto + (c) tradier sweeps — expect ≥1 each
ssh s1-int 'pgrep -afc "backtest_v8_sweep.*--mode crypto"; pgrep -afc "backtest_v8_sweep.*--mode tradier"'
# (d) Results growing
ssh s1-int 'ls -lt /home/niels/binance-sandbox/data/sweep_results/ | head -3; ls -lt /home/niels/logs/bt_sweep_*.log | head -3'
```
Sweep shows 0 → relaunch via `watchdog_sweep_s1.sh` (crypto) or manually `backtest_v8_sweep.py --mode tradier`.

### LAUNCHERS — USE THESE, NEVER AD-HOC NOHUP

GUI: **http://localhost:5051/sweeps** (crypto→S1, tradier→S2, refuses mismatch).
```bash
ssh s1-int 'bash /home/niels/binance-sandbox/start_crypto_sweeps.sh wt_dc_full'
ssh s1-int 'bash /home/niels/binance-sandbox/start_crypto_sweeps.sh status'
```
Launchers refuse wrong-host, use `nohup ... > ~/logs/sweep_<tier>_<ts>.log 2>&1 < /dev/null & disown`, verify workers after 30s. **Ad-hoc nohup dies on ssh disconnect — lost months of compute.**

### POST-LAUNCH VERIFICATION (every sweep)
1. T+5s: `pgrep -af <tier>` ≥3 procs.
2. T+30s: log shows banner — no `Traceback`/`ERROR`/`MODE_CONFIG_MISMATCH_SKIP`.
3. T+5min: CSV rows > header. 0 → stuck, investigate.
4. Only then: "S1 wt_dc_full: N workers, log advancing, CSV at R rows."

Log path: `~/logs/<name>.log` NOT `/tmp/` (cleared on reboot). Always `< /dev/null & disown`.

### END-OF-SESSION CHECK
Repeat (a)–(d). "MacBook 7 procs. S1: N workers, CSV R rows, growing." NEVER "Launched, exiting." — sweep dying after you leave = compute wasted.

---

## 🚨 USDC-OVER-USDT — HARD POLICY 🚨

**If a sym has USDC perp on Binance Futures, EVERYTHING refers to it as USDC. NOTHING uses USDT for those syms.** Applies to mark_price, klines, funding, OI, NPZ, indicators, sweep symbol-list, live order routing — ALL.

10 USDC-perp syms (2026-04-29): **ETH, BTC, SOL, ADA, BNB, AVAX, XRP, LINK, LTC, UNI**. Re-check `/fapi/v1/exchangeInfo` if uncertain.

50 legacy USDT syms: 1INCH, ALGO, ANKR, ATOM, AXS, BAND, BAT, BEL, BTCDOM, C98, CELR, CHR, COMP, COTI, DASH, DOT, EGLD, ENJ, ETC, GRT, GTC, HOT, IOST, IOTA, IOTX, KAVA, KNC, KSM, LRC, MANA, MTL, NKN, QTUM, RLC, RSR, RVN, SAND, SKL, SNX, STORJ, SUSHI, SXP, THETA, TRX, VET, XLM, XMR, XTZ, YFI, ZEN.

**Backtest convention**: USDT historical data labeled USDC for 10 majors (USDC perps launched 2024). Label=USDC; source rows=USDT. Live queries actual USDC bid/ask before ordering.

**MUST NOT EXIST** for 10 majors: `klines_cache{,_backtest,_gateway}/{sym}USDT_*.json`, `backtest_v8/indicators/{sym}USDT.npz`, `data/{funding,oi}_cache/{sym}USDT.json`. Found → DELETE. Accidental USDT fetch for USDC sym → rename to USDC immediately. Never leave both.

---

## 🚨 NPZ REGEN — STOP DOING THIS WRONG (12+ TIMES) 🚨

1. **NPZ source = `klines_cache_backtest/` ONLY.** NOT `klines_cache/` (live), NOT `klines_cache_gateway/`. Holds 5+ yrs 15m klines for 50 USDT + 10 USDC majors. **15m is BASIS — all TFs (1h, 4h, D, W, M, 3m, 5m) derived from 15m by precompute.**
2. **Mac uses `klines_cache/`** (live + V3 forward-test, ~1200 bars). No multi-year sweeps on Mac.
3. **Servers backtest from `klines_cache_backtest/`.**
4. **Tail freshness**: pre-regen, append latest klines from `klines_cache/` into `klines_cache_backtest/` so 15m base ≤1hr old.
5. **NPZ scope**: 48+ crypto × 4yr + 128+ stocks × 4yr. Narrower = noise.
6. **NPZ MUST include EVERY param v8 sweep asks for** — all TFs (adx_15m/4h/D/5m, macd_crossover_*/crossunder_* all TFs, wt*_W/wt*_M etc.). Zero-filled missing = lying results.
7. **Audit pre-sweep**: `python /tmp/audit_npz_fields.py v8_quick_engine.py backtest_v8/indicators MODE` — must report `ALL FIELDS PRESENT ✓`. Fail = REJECT.

---

## ☠️ DEATH PENALTY — NEVER REVERT LIVE CODE ☠️

**REVERTING live scripts is PROHIBITED. NO EXCEPTIONS.** A "revert" = copying older backup over newer; removing strategy wiring (First-Hour Momentum, Momentum Interception, DC Daytrade, K-Zone, RSI2, Stoch Entry, WT Composite Scoring, SATOSHIT, DELTA_ENGINE, etc.); deleting config switches; replacing long file with shorter; auto-accepting merge that drops strategies.

**If reverting seems necessary — STOP. Ask user.** Dead switch → ONLY action is **IMPLEMENT IT** — never skip/disable/remove/rollback.

**Verify at session start**: grep each of 33 canonical switches (`data/sweep_alerts/canonical_switches.json`) in `tradier_manage.py, ez_manage.py, config.py, config_tradier.py, backtest_v8_engine.py`. ANY missing → red alert, stop all work.

---

## 🚨 MANDATORY BACKUP BEFORE EVERY EDIT

```bash
cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py
```
Autosave: `autosave_15min.py` → `backups/autosave/<timestamp>/`, auto-commits git every 15min via launchd (`~/Library/LaunchAgents/com.niels.autosave-15min.plist`). `/backups/` = ONLY reliable history.

---

## 🔴 SANDBOX PARITY — S1 MUST ALWAYS MATCH MACBOOK

**MacBook is SOLE SOURCE OF TRUTH.** Sandbox drift → sweep results LIE.

### 6 files bit-identical on MacBook + S1 at ALL times
`ez_manage.py, ez_positions_quick.py, ez_positions_service.py, tradier_manage.py, config.py, config_tradier.py`
Plus backtest infra: `v8_quick_engine.py, v8_quick_sweep.py, backtest_v8_*.py, breakout_multi_lung.py`, all `ez_*.py/tradier_*.py/wt_*.py/utils.py/symbols.json`.

### Rules
1. **Session start**: `python3 check_sandbox_parity.py` — DRIFT → STOP until fixed.
2. **Before server sweep**: confirm 24 core files bit-match MacBook.
3. **After editing critical file**: immediately rsync to S1, verify md5.
4. **Automated edits count**: bots silently editing `v8_quick_engine.py` cause drift. Resync after any mtime change.
5. **Never edit scripts on servers** — edit on MacBook, rsync.
6. **Never use `push.py`** — use `rsync --existing --update` (preserves server-local files).

Past disasters: 2026-04-14 sandbox had older version → 33 canonical switches wiped on copy-back. 2026-04-16 S2 had pre-D4 stub → garbage results.

### Sync command
```bash
rsync -az --existing --update \
  ez_*.py tradier_*.py wt_*.py utils.py config.py config_tradier.py symbols.json \
  v8_*.py backtest_v8_*.py breakout_multi_lung.py \
  s1-int:/home/niels/binance-sandbox/
```

### Files that LEGITIMATELY differ
`/old/inventory_*/`, `data/sweep_results/*.csv`, `data/decisions/` JSONL, `SWEEP_RUNNING` lock, `backups/autosave/`, `klines_cache*/`. Everything else must bit-match.

---

## 🚫 CRYPTO vs TRADIER CONFIGS — NEVER CONFUSE MODES

| Config | Machine | Mode | Python |
|---|---|---|---|
| `config.py` | **S1** | `crypto` | `/home/niels/.conda/envs/binance_env/bin/python` |
| `config_tradier.py` | **S1** | `tradier` | `/home/niels/.conda/envs/binance_env/bin/python` |

Guards: `v8_test_queue.py` returns `status="mode_skip"` on mismatch (not silent 0-trade). `sweep_cockpit.py` hardcodes correct config per mode. `V8_PYTHON` env var overrides Python path.

Monitoring: `WINNER=<X> Δ=0.000` with both arms `trades=0` = mode mismatch or weak baseline. `v8_test_queue.py` logs `MODE_CONFIG_MISMATCH_SKIP` — do not ignore. Baseline Sharpe <2 = trash.

---

## ⚠️ NO LYING / NO GUESSING — REAL MONEY

- **NEVER** claim something works without log/exchange proof.
- **NEVER** guess root causes — trace the actual code path.
- **NEVER** present backtest from reimplemented logic. Only `process_position()`, `check_entry_candidates()`, `check_exit_candidates()` produce valid numbers.
- **NEVER** inflate/extrapolate/cherry-pick. Report exactly what code produced.
- **NEVER** cover up errors. Say "I made an error in X" immediately.
- **NEVER** blame external systems (Redis, API) before exhausting code-level causes.
- When in doubt: "I don't know yet" or "Can you clarify?"

---

## 📊 BACKTEST REPORTING RULES

### 🩸 SHARPE DEFINITION — LIVE-MONEY POLICY (2026-04-29)

**Hardened after 30%-net-worth-in-a-week loss from inflated Sharpe reaching live. Every violation = hard error.**

Any "Sharpe" in UI/CSV/log/message/memo/plan MUST satisfy ALL:

1. **Per-trade returns only.** `sharpe = mean(trade_returns)/std(trade_returns)`. NEVER sum/accumulate. NEVER divide by anything other than std of same distribution. Dimensionless — depends on bar-count/days/sqrt → NOT Sharpe.
2. **Open losers MUST be MtM'd** at final bar and appended BEFORE Sharpe. Skipping = fraudulent, proximate cause of live blow-up.
3. **`sharpe_annual = sharpe_per_trade * sqrt(trades/yr)` BANNED.** Same for `sharpe * sqrt(252)/sqrt(N)`. Frequency-gaming. MUST NOT appear anywhere — including diagnostics. Find → delete.
4. **Pool Sharpe IS THE Sharpe**: `mean(all_trade_returns)/std(all_trade_returns)` pooled across all trades of all syms. Per-symbol-avg = diagnostic ONLY, label `sym_sharpe`.
5. **MIN SAMPLE for publishable**: ≥48 crypto OR ≥100 stock syms, >1yr trades each, pool-averaged. Below floor → debug only with `[DIAGNOSTIC ONLY · n_syms=X · years=Y]` tag. Sub-floor CANNOT decide/promote/recommend.
6. **Cap per-sym Sharpe at ±5.0** before averaging. Exclude sym with <30 trades from `sym_sharpe`. >5 = 3-trade luck.
7. **Pool Sharpe < 1.0 = trash.** Not a promotion candidate. Stop ranking; redesign.
8. **NEVER bare "Sharpe X.X" in user-facing text.** Always qualified: `pool_sharpe`, `sym_sharpe`, `sharpe_per_trade`.

**Mandatory "Best" reporting line** (sweep summary, leaderboard, status, memory):
```
pool_sharpe=X.XXXX | sym_sharpe=X.XXXX | avg_gain_trade=X.XX%/trade | gain_per_yr=XX.X%/yr | gain_sym_yr=X.XXXX%/sym/yr | trades=N | dd=X.X% | n_syms=N | years=Y.Y
```
All 9 fields. Drop any → invalid.

**Enforcement**: every Sharpe-displaying script imports `metrics_guard.py` + routes through `validate_and_format_sharpe()`. REFUSES rules 1/3/4/8 (raises). DOWNGRADES to `[DIAGNOSTIC]` for rule 5. Direct `f"{sharpe:.2f}"` user-facing = bug.

### Legacy compatibility
Historical CSVs/JSONLs may have `sharpe_annual` columns. INVALID until converted: read trade-list, recompute pool_sharpe, overwrite. NEVER read stored "Sharpe X.XX" at face value. `metrics_guard.audit_csv()` handles this.

### Required columns in every sweep CSV / report

1. Sharpe = AVERAGE across all symbols (never single best). Report range: min, p25, median, p75, max.
2. `max_dd_pct` (worst-single-sym DD) + `avg_dd_pct` (mean) — MANDATORY.
2b. `accumulated_gain_pct` — MANDATORY. Sharpe alone insufficient.
2c. **Engine-tier architecture**:
   - **Tier 1 — `v8_quick_engine.py`** (vectorized): shortlist only. "Just a feel" — NEVER report Tier 1 as "this config better". ~85% live parity; divergences: DELTA_ENTRY velocity proxy, DC_DAYTRADE/FAST_RISER_REDUCE not wired, L/S ratio not vectorized.
   - **Tier 2 — `backtest_v8_engine.py`** (real-code replica): THE REAL TEST. Calls actual `check_entry_candidates_for_account()`, `check_exit_candidates_for_account()`, `hedge_engine`, `MultiAccountTradeManager`. Run top Tier-1 candidates on 12→48 syms.
   - **Tier 3 — Full production** (48+ crypto / 128+ stocks × 4yr): only Tier-2 winners with per-trade per-sym-avg Sharpe >1.
2d. **Ratio NOT in NPZ**: `market_sentiment_score` precomputed; L/S ratio runtime-only (`ez_positions_quick.py:1170`). Prior agents claiming ratio fields in NPZ were wrong.
2e. **PARTIAL_PROFIT_LOCK v2**: 3-step TP. Step 1: close 50% @ +0.5% via `place_maker_order` → `send_webhook(url_variant="2")`; set `stop_level = entry × (1 ± BE_BUFFER_PCT/100)`. Step 2: @ +0.75% upgrade stop to `first_exit_price`. Step 3: stop hit → close remainder via webhook_url (100%). Config: `PARTIAL_PROFIT_LOCK_ENABLED/GAIN_PCT=0.5/ARM_GAIN_PCT=0.75/BE_BUFFER_PCT=0.02/FRAC=0.5`. State: `trade_manager.partial_profit_lock_state[pk] = {fired, first_exit_price, stop_level, stop_upgraded}`. Wired: ez_manage, tradier_manage (pre UNIVERSAL_NOLOSS_GATE), backtest_v8_engine, v8_quick_engine.
2f. **NOLOSS_BYPASS_WT_5OF5** (default OFF): loss exit when ALL 5 WT TFs (3m/15m/1h/4h/D) flip against. Config: `NOLOSS_BYPASS_WT_5OF5_ENABLED=False`, `MIN_TFS=5`. Wired tradier_manage + v8_quick_engine; NOT ez_manage (per-path edit needed).
3. **Labels**: crypto base TF = **3m**, stocks = **5m**. Always state `N syms × N bars × N years × base-TF`.
4. Sharpe on <30 trades/sym = noise. Prefer ≥200 trades/sym.

### STANDARD METRIC SET — ALL FIVE REQUIRED (2026-04-25)

| Metric | Formula | Purpose |
|---|---|---|
| `pool_sharpe` | mean(trade_returns)/std(trade_returns) | Risk-adj per-trade quality |
| `sym_sharpe` | mean(per-symbol Sharpes) | Diagnostic; consistency |
| `avg_gain_trade` | acc_gain_pct / trades | Per-trade return |
| `gain_per_yr` | acc_gain_pct / n_years | Annual return |
| `gain_sym_yr` | acc_gain_pct / n_syms / n_years | Cross-machine comparable |

Reporting "Best": `pool_sharpe=X | avg_gain_trade=X%/trade | gain_per_yr=X%/yr | gain_sym_yr=X%/sym/yr | trades=N | dd=X%`

CSV columns: `iter, pool_sharpe, sym_sharpe, acc_gain_pct, gain_sym_yr, avg_gain_trade, gain_per_yr, max_dd_pct, trades, gain_vs_bh, elapsed_s, overrides_count, reliable, useless, overrides_json`

Translating old: tradier n_syms=114, crypto n_syms=50, n_years=(current − 2022-01-01)/365.25.

---

## ⚠️ ALL CLOCKS = UTC. MARKETS = ET (UTC−4 now)

| Event | ET | UTC |
|-------|-----|-----|
| Open | 9:30 AM | **13:30** |
| Close | 4:00 PM | **20:00** |
| Pre-market prep | 8:00 AM | **12:00** |

**NEVER write cron in ET. NEVER assume `date` is ET.**

---

## STEP 0 — Every Conversation Start
1. Read STATE OF AFFAIRS at bottom.
2. Read `100.md` — master audit (Parts 1–15). Skim headers, read relevant.
3. Refresh KB: `cd /Users/niels/Documents/binance && python3 export_conversations.py`
4. Parity: `python3 check_sandbox_parity.py` — DRIFT → STOP.
5. Memory: script → `SCRIPT_STATE.md`; topic → `TOPIC_STATE.md`; "continue from last time" → `INDEX.md`.

## STEP 0b — Before ANY File Edit
1. Read `LOCKED_FILES.md` — locked? → **STOP**.
2. Continue only if user says **"unlock <file>"** same message.

## STEP 1 — Every File Edit Workflow
```
1. Read LOCKED_FILES.md — locked? STOP.
2. Backup: cp <file> backups/before_<description>_<YYYYMMDDHHMM>.py
3. Edit locally (/Users/niels/Documents/binance/<file>)
4. Compile: python -c "import py_compile; py_compile.compile('<file>', doraise=True)"
5. Verify: grep/read changed lines.
6. SANDBOX SYNC — rsync to S1 IMMEDIATELY.
7. Verify md5 parity MacBook+S1 before "done".
```
**Edit without rsync = sandbox tests OLD code, every sweep LIES.**

```bash
rsync -az --existing --update <edited_files> niels@157.180.125.52:/home/niels/binance-sandbox/
```

Rsync needed: 6-critical + `v8_quick_engine.py, v8_quick_sweep.py, backtest_v8_*.py, breakout_multi_lung.py`, any `ez_*.py/tradier_*.py/wt_*.py/utils.py/symbols.json`. Sweep running on sync: running workers hold OLD code; only next-spawned workers pick up change.

---

## Absolute Prohibitions

| NEVER | Detail |
|-------|--------|
| git reset / restore / checkout | Only move forward. |
| Revert to backup/older version | Ask user. |
| Access `.history/` | Unless explicitly permitted. |
| delete/pop/clear position dict entries | No `.pop()`, `del`, `.clear()`. `symbols.json` count must match positions exactly. |
| Overwrite file with older version | Without explicit permission. |
| Create position from zero | Only `add_new_symbols.py` creates positions. |
| Zero position from API absence | Absence ≠ closed. Only WS `positionAmt=0` (threshold 5+) confirms closure. |
| Zero entry_price, max_gain, opened_at | SACRED. Only `positionAmt` may be zeroed on confirmed close. |
| Overwrite Redis without validation | Redis may be poisoned/stale. |
| Use `.clear()` on positions dicts | Empty source = source failed, NOT positions gone. |
| Make account-specific scripts | Account logic as conditions inside shared scripts. |
| Call `ez_backup.py` | Live system script, not Claude utility. |
| Close positions at a loss on live | STRICT_NO_LOSS active. |
| Add stop-loss code to tradier_manage.py | `HARD_STOP_LOSS_MAX_PAIN` caused $500+ losses 2026-03-24. ALL paths DISABLED. |
| Present backtest from reimplemented logic | Only real `process_position()` / `check_entry/exit_candidates()`. |
| Inflate trade counts or PnL | Report raw. 120 trades = 120. |
| Call backtest "working" until matches live logs | Compare vs `data/decisions/` JSONL. |
| Blame "strict gates" for low trade counts | Missing trades = missing mocks or broken patches. |
| Place orders outside `execute_now()` | THE ONLY gate for ALL Binance orders. |
| Add `not is_hedge` bypasses to execute_now guards | Guards apply to ALL callers. |
| Let MOMENTUM_RIDER bypass execute_now | PERMANENTLY DISABLED. |
| Add new entry strategies to live code | See NEW STRATEGY PROHIBITION. |
| Add autonomous position-opening loops | No `asyncio.create_task(scan_and_open_*)`. |
| Add percentage-based exit triggers | No `if gain < -X%: exit`. Technicals ONLY. |
| Hardcode config values in backtest engines | Backtests MUST read from config.py. |
| Auto-expand tradeable_keys | Hand-picked per account. No `.add()` from scanners. |
| Add hedge loops independent of HEDGE_MODE | All hedges via ez_positions_quick only. |
| Implement YouTube/web strategies directly | Research → sweep → paper → user approval → live. |
| Use HANDS_FREE to add new strategies | HANDS_FREE = bug fixes + proven changes only. |

---

## NEW STRATEGY PROHIBITION (2026-03-27: 8 strategies → 0 trades + 15,378 rogue opens)

1. NEVER add strategy without explicit user approval outside HANDS_FREE.
2. NEVER enable on real money without full sweep proof (48 crypto 4yr + 128 stocks 2yr) + paper trading days.
3. NEVER "backtest" with reimplemented logic.
4. NEVER add >1 new strategy per conversation.
5. NEVER wire strategy without kill switch defaulting to OFF.
6. Before ANY new strategy: present entry logic, exit logic, data pipeline, trade freq, risk, interaction with existing.
7. After implementation: verify fires in 24h paper with trades in decision JSONL.

**Banned without V8 proof**: ORB, Clenow, SMFI, Minervini, Connors RSI, DC Daytrade, Episodic Pivot, Squeeze, Outlier Scalper, Outlier Hunter, Mover Detection, momentum fade/rider, autonomous scanners.

---

## EXECUTE_NOW IS THE ONLY GATE

1. ALL orders (open/augment/reduce/close/hedge) go through `execute_now()` in ez_manage.py.
2. NO `not is_hedge` bypasses — guards apply to ALL callers.
3. NO reason-string bypasses — reason is for logging only.
4. OPEN on empty = allowed. AUGMENT on existing = gain check applies to ALL.
5. No `futures_create_order` or REST `/fapi/v1/order` calls outside execute_now.
6. **CONSULT TRACKER BEFORE HEDGING** — check `active_hedges` + `exit_candidates` in tracker.json. (2026-03-29: 24,833 rogue orders from skipping this.)
7. `persist_hedge_record` MUST be called after any execute_now for a hedge.

---

## EXIT RULES — USER MANDATE 2026-05-09

**User lost 80% on 2 accounts to NO_LOSS lying about safety. ONLY 3 paths can close a losing position. Everything else: HOLD.**

### The 3 loss-exit paths

| # | Rule | Code site | Knobs |
|---|------|-----------|-------|
| **R1** | DC4 emergency close — within first `R1_NEWBORN_WINDOW_MIN` (default 15min) of open, price breaks configured DC channel (4-bar or 1-bar) → CLOSE NOW. Bypass NO_LOSS/hedge/MTF. Fires desktop alert + JSONL log naming entry signal. | `ez_manage.py` ~20709 (before R2) / `tradier_manage.py` ~1327 (5m TF) | `R1_DC_LOW4_3M_EMERGENCY_ENABLED`, `R1_NEWBORN_WINDOW_MIN`, `R1_USE_DC_4BAR`, `R1_TF` |
| **R2** | WT velocity slowdown near breakeven — fires when `floor <= gain < band`, vel against, AND `\|vel\| < \|vel_prev\| * WT_VEL_DECEL_RATIO` (DYNAMIC slowdown — fixed thresholds banned). Iterates `R2_TF_LIST`. | `ez_manage.py` ~20768 (after R1) / `tradier_manage.py` ~1366 | `WT_15M_VEL_SLOW_GAIN_BAND_PCT`, `WT_15M_VEL_SLOW_GAIN_FLOOR_PCT`, `WT_VEL_DECEL_RATIO`, `WT_VEL_USE_DECEL_RATIO_ONLY`, `R2_TF_LIST` |
| **HEDGE_FAILED** | Same-symbol hedge couldn't be taken (no quote/blacklist) → fall back to direct close. Else OBLIGATORY_HEDGE fires hedge. | `ez_manage.py:14132+` (OBLIGATORY_HEDGE wired); fallback close reason contains `HEDGE_FAILED` (in bypass list). | `OBLIGATORY_HEDGE_ENABLED`, `OBLIGATORY_HEDGE_WT_TFS_REQUIRED` |

**Crypto TFs** (default): R1=3m, R2=15m. **Stocks TFs** (default): R1=5m, R2=1h/4h/D (markets closed most of day, real moves on D/W).

**Bypass list** (`config.UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS`) MUST contain: `R1_DC_LOW4_3M_EMERGENCY`, `R2_WT_VEL_SLOW`, `WT_15M_VEL_SLOW` (legacy), `HEDGE_FAILED`, plus existing emergency reasons. NOT in list → cannot close at loss; HOLD until gain >= 0 or hedge fires.

### DUPLICATE_OPEN_GUARD — gain-based, not time-based
900s time cooldown replaced with gain gate (USER 2026-05-09):
- AUGMENT (open/reenter/hedge-open/scalp augment) requires `gain > config.MIN_GAIN * config.DUP_GUARD_GAIN_MULTIPLIER` (default 3.0% * 0.5 = **1.5%**)
- Below → BLOCKED, reason `BLOCKED_DUP_GUARD_GAIN_<x>pct_lt_<thr>pct`
- Time cooldown = fallback when `DUP_GUARD_USE_GAIN_GATE=False`
- Site: `ez_manage.py:10936-10961`

### augmented_positions persistence
**MUST persist across bars** until REDUCE'd or CLOSE'd. Backtest no longer clears per bar (`backtest_v8_engine.py:1647-1651` — clear() commented out 2026-05-09). Set sites: `ez_manage.py:13178, 14654, 14751`.

### R3 — DEFERRED. ALL OTHER EXITS NEED MTF CONFIRMATION
User mandate (NON-NEGOTIABLE): every other exit must require multi-TF confirmation. NOT yet implemented (XL refactor, separate session). UNIVERSAL_NOLOSS_GATE keeps current loss-exits clamped until R3 ships.

### NO % STOPS — DISABLED PATHS (must stay disabled)
`tradier_manage.py`: `HARD_STOP_LOSS_MAX_PAIN`, `STALE_DATA_HARD_STOP`, `STALE_DATA_GAIN_EROSION`, `Market_Against_Position` bias reduce. **If you EVER see `gain < -` followed by `return True` in any exit path — DISABLE IT.**

---

## 🚨 S1 UTILIZATION FLOOR — ≥60-70% CPU + MEM AT ALL TIMES 🚨

USER 2026-05-09: "backlog of literally millions of tests" + "S1 NEVER below 60-70% capacity". S1 idle = lying about progress.

**S2 DESTROYED 2026-05-08. ALL test load = S1 ONLY. Crypto AND tradier both on S1.**

### "Take turns" alternation (USER 2026-05-09)
S1 RAM = binding constraint (~10.8GB/worker peak NPZ decompression on backtest_v8_engine, 31GB total → ~2-3 workers safe). Crypto/tradier **take turns**:
- **Always running**: ≥1 crypto AND ≥1 tradier simultaneously, alternating which gets larger share each cycle.
- **Every cycle (~hourly)**: last crypto-heavy (2+1) → next tradier-heavy (1+2). Vice versa.
- **Never starve**: system without sweep results >2h = stale → flip immediately.

### Every session start
```bash
ssh s1-int 'top -bn1 | head -3 && free -m | head -2 && pgrep -afc "backtest_v8_sweep.*crypto" && pgrep -afc "backtest_v8_sweep.*tradier"'
```
CPU <60% OR MEM-used <60% → **launch more parallel sweeps** until both ≥60%. Only crypto running → start tradier (vice versa). Safe headroom: <90% CPU, <28GB MEM.

### Watchdog files
- `watchdog_sweep_s1.sh` — crypto + tradier alternation (v15, cron `*/5`). State `/home/niels/logs/sweep_next_mode`. Tradier-priority when both dead.
- `watchdog_sweep_s1_tradier.sh` — tradier-only (cron `*/7` offset). Activated 2026-05-09. Refuses launch if crypto running. Same state file.

Sweep dies mid-run (OOM/kill/crash) → don't just relaunch — investigate cause AND ensure new workers fill utilization floor + alternation invariant.

---

## 📁 TOSHIBA_EXT PATH MAP — S2 archive location (USER 2026-05-09)

S2 destroyed 2026-05-08. Data + scripts archived to TOSHIBA_EXT. **Do NOT bulk-sync to S1** — point at it. Copy specific files only on demand.

### Canonical layout (mounted at `/Volumes/TOSHIBA_EXT/`)
- `binance_archive/data/sweep_results/` — S2 historical sweep CSVs (1.5MB)
- `binance_archive/data/` — S2 data dir snapshot
- `binance_archive/klines_cache_tradier/` — S2 stocks klines (only if S1 missing)
- `binance_archive/indicator_cache/` — S2 indicator snapshots
- `s2_backup_20260508/binance-sandbox/` — S2 full sandbox at shutdown
- `s2_backup_20260508/binance-sandbox/data/{sweep_results,canonical_trades,hourly_reconfig,per_sym}/` — S2 last-day CSVs / per-trade JSONLs / hourly_reconfig / per-sym agent state
- `sweep_results_distilled/s2_baselines/` — **THE good stocks baselines** (21MB canonical_tradier_*.json + CANDIDATE_s2_tradier_*.json)
- `sweep_results_distilled/s2_autonomous_latest/` — recent autonomous results
- `sweep_results_distilled/STOCKS_RECOVERY_MANUAL.md` — 2026-05-08; honest baseline pool_sharpe 0.5823 (114 syms, 8078 trades, 2024-01-01 start)
- `backtest_npz_master/` — 4.4GB master NPZs, DO NOT pull whole thing

### S1 currently has (verified 2026-05-09)
✅ `tradier_manage.py, config_tradier.py, backtest_v8_engine.py` (synced with R1+R2 rewrite); `backtest_v8/indicators/<TICKER>.npz`; `klines_cache_backtest/tradier/<TICKER>_5m.json`; `data/sweep_results/` (1.6GB); `backtest_v8/sweeps/override_tradier_t1_*.json, t4_*.json`; `data/baselines_from_s2_archive/` (3 canonical_tradier_*.json copied 2026-05-09).

### S1 LACKS — fetch from TOSHIBA_EXT on demand only
- ~20 tradier-only experiment scripts (e.g. `_ab_stocks_matrix.py, phase4_htfport_tradier.py, validate_tradier_114.py`) — fetch from `s2_backup_20260508/binance-sandbox/`.
- Active per-symbol configs for `trb`/`trc` (no `data/hourly_reconfig/{trb,trc}/active_config.json` on S1) — pull from `s2_backup_20260508/binance-sandbox/data/hourly_reconfig/{trb,trc}/` only when re-running tradier per-sym agent.
- Older sweep CSVs pre 2026-05-08.

### Mac dashboards auto-merge TOSHIBA_EXT when mounted
- `chart_server.py` (`:5077`) — `_DEFAULT_ABS_ROOTS` (~line 70) points at TOSHIBA_EXT. Browses Stocks tab from S2 archive when mounted.
- `flz_dashboard.py` (`:5057`) — live trades only (`data/history/<acct>/*.jsonl`). Does NOT read TOSHIBA_EXT.

**Rule**: Don't fill S1 with logs/bulk archives. Need specific S2 result → fetch named file. Comparing baselines → point Mac dashboards at TOSHIBA_EXT.

---

## Current Operating Mode

- MacBook: LIVE TRADING (source of truth). S1: backtesting only. S2: dead.
- DO NOT start/restart trading services on servers. DO NOT edit scripts on servers.
- Server Redis tunnel DISABLED (dummy 6381). Local Redis: 6379. Gateway: 6380.
- **TRADIER IS PRIORITY** — $70k stocks vs $1k crypto.
- **SERVER LOCKS**: Read `SERVER_LOCKS.md` AND check `/home/niels/SWEEP_RUNNING` before ANY server action. NEVER `killall python3` without checking. Screen sessions `sweep48h` PROTECTED.

---

## Code Style

**Formatter**: `black` | **Linter**: `pylint`
- `logger.*` and function calls always one line — never broken.
- One blank line between functions. **Zero blank lines inside bodies.**
- Use `config.BASE_PATH` — never hardcoded paths.
- No docstrings/comments/type annotations on code you didn't change.
- Naming: `symbol` not `sym`/`s`/`sb`.

---

## Environments & Servers

| Location | Python | Path |
|----------|--------|------|
| Local | `/opt/anaconda3/envs/binance_env/bin/python` | `/Users/niels/Documents/binance` |
| Server 1 | `/home/niels/.conda/envs/binance_env/bin/python` | `/home/niels/binance` |
| Server 2 | DEAD (2026-05-08) | — |
| Sandbox | same as Server 1 | `/home/niels/binance-sandbox` |
| Klines box | — | `157.90.168.35` — klines ONLY |

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

**Accounts**: Crypto: `ang, inf, flz, men, fin` | Stocks: `trb, trc`

**Config**: `config.py` (crypto), `config_tradier.py` (stocks), `symbols.json` (350+ pairs, count must match positions exactly), `.env.gpg` (API keys — do not touch)

**Trade data — /history/ IS THE TRADE LEDGER. /decisions/ IS NOT.** (clarified 2026-05-09)
- `data/history/<acct>/<SYMBOL>_<SIDE>.jsonl` — **LIVE TRADES**: one event per fill (OPEN/AUGMENT/REDUCE/CLOSE/HEDGE_*). Source-of-truth. Auditing trades, live-vs-backtest, counting volume → **read /history/.**
- `data/decisions/decisions_<acct>_<YYYYMMDD>.jsonl` — **decision event log** per account/day: every entry/exit evaluation (fires or not), price ticks, signal scores, blocks, gate decisions. Most lines NOT trades. **Never treat /decisions/ count as trade count.**
- `V8_RESULT_LIVE: ...` engine log = backtest IN-FLIGHT heartbeat (`closes=N`). "LIVE" = "live during simulation," NOT "live trading." Don't quote as live numbers.

---

## Timeframe Derivation — NEVER claim missing data

**15m klines = ALL timeframes since 2020.**

| 15m → Derived | Method |
|--------------|--------|
| 1h | `resample('1h').agg(open=first, high=max, low=min, close=last, volume=sum)` |
| 4h | `resample('4h')` same |
| D | `resample('1D')` same |
| 3m/5m | each 15m bar × 5/3 (interpolated) |

Map HTF arrays back to base TF via `np.searchsorted`.

---

## Backtest System (V8 — only valid)

`backtest_v8_precompute.py` → NPZ. `v8_quick_engine.py` (Tier 1 vectorized). `backtest_v8_engine.py` (Tier 2 real-code). `v8_quick_sweep.py`/`autonomous_search.py` (sweep runners). V3/V4/V5/old = RETIRED in `old/`.

**NPZ paths** (S1 sandbox): Crypto + Tradier indicators in `backtest_v8/indicators/`. Klines: `klines_cache_backtest/` (crypto) | `klines_cache_backtest/tradier/` (stocks).

**Rules**: NEVER write new backtest reimplementing logic. NEVER trust `old/`. Validate with `backtest_evaluate_functions*.py` before deploying. NEVER compare results across precomputed versions.

---

## Crypto vs Stock Parameters — OPPOSITE — NEVER copy between

| Parameter | Crypto | Stock |
|-----------|------------|------------|
| Entry score | 18 | **24** |
| Reentry stoch gate | K<50 | K<**80** |
| HTF alignment | ≥1 | ≥**2** |
| ADX in sizing | Disable | **Keep** |
| Sizing indicator | RSI ok | **MFI only** |
| WT cross alignment | ≥2 | ≥**3** |
| Combined stoch gate | 50 | **60** |

**MANDATORY**: Before any tradier_manage.py/config_tradier.py change, run `backtest_evaluate_functions_tradier.py`. NEVER assume crypto finding transfers to stocks.

---

## position_key Conventions

- Always: `key.endswith("_LONG")`/`key.endswith("_SHORT")` — NEVER `"LONG" in key`
- Helpers: `pk_is_long()`, `pk_is_short()`, `pk_symbol()` in `utils.py`
- `parse_position_key` uses `split("_", 1)` — do not change to rsplit
- LONG = profits price UP (open=BUY, close=SELL); SHORT = profits price DOWN (open=SELL, close=BUY)
- `is_reduce = (SELL+LONG) or (BUY+SHORT)`

---

## HANDS_FREE / HANDS_OFF Modes
- **`HANDS_FREE`** in message: no confirmations, auto-approve edits, chain steps, handle errors silently, full report at end.
- **`HANDS_OFF`** in message: no questions, no confirmation prompts, full autonomy until final result.
Both: do NOT override LOCKED_FILES.md, Absolute Prohibitions, STRICT_NO_LOSS.

---

## Misc Rules
- **Strategy development**: Phase 1 = cross-symbol rules. Phase 2 = per-symbol. DO NOT skip to Phase 2.
- **Context compaction**: Before compacting, save full conversation to disk + note path.
- **100.md condense**: Keep last 7 days perf rows. Merge "Applied Today" after 3 days. Move raw test data to CSV. Never delete Parts 1–5, active BC entries, "Not Yet Applied" priorities.
- **Auto-Confirm**: Proceed without asking for "Command contains empty quotes before dash" or "Command contains `$()` substitution".

---

## STATE OF AFFAIRS — Config Values That Must Not Change

| Config | Value | Why |
|--------|-------|-----|
| `MIN_GAIN_TO_BUY_AGGRESSIVELY` | 3.0% | NEVER below 2.5% |
| `STRICT_NO_LOSS` | ELIMINATED (STRICT_NO_LOSS_ACCOUNTS=[]) | Replaced by technical exits (WT/DC). Hedge + ratio IS the protection. |
| `RATIO_MULTIPLIER` | 3.0 | Ratio-only Sharpe 357 vs closing-losers 19 |
| `HARD_STOP_LOSS_MAX_PAIN` | DISABLED | $500+ losses 2026-03-24 |
| `STALE_DATA_PROFIT_SHIELD` | DISABLED | Was killing USO position |
| `HEDGE_MODE` | True (inf/fin/men) | Re-enabled 2026-03-13 |
| `ATR_TRAIL_ENABLED` (tradier) | False | #1 stock PnL destroyer (-2557%) |
