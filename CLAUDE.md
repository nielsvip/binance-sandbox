# CLAUDE.md — Trading System Rules

## 🚨🚨🚨 NO-LIES MANDATE — READ FIRST. ABSOLUTE. 🚨🚨🚨

**Lying Sharpe / lying gain / lying drawdown numbers wiped out half the user's net worth in 4 months.** Every metric written to disk, displayed, or reported MUST be REAL. Forward AND backward.

### Forward (every new result)

1. **Every script that emits a Sharpe / gain / dd number** to a human-facing surface (CSV, JSONL, log, MD, UI, agent message, memory record) MUST route through `metrics_guard.validate_and_format_sharpe()` or `metrics_guard.format_standard_set()`. NO exceptions. If your script doesn't import `metrics_guard`, you are not allowed to write a Sharpe number anywhere.
2. **Every CSV written into `data/sweep_results/` or `data/autonomous/`** MUST include the canonical columns: `pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr, trades, max_dd_pct, n_syms, years`. Missing any → CSV is invalid → reject the row.
3. **No annualization. No sqrt(252). No sqrt(N).** `sharpe_annual`, `sharpe_y`, `sharpe_yearly`, `sharpe_w` (weighted), `pool_sharpe_proxy`, `sharpe_rough` are BANNED column names. Any code emitting them must be deleted or rewritten.
4. **No bare "Sharpe" label.** Every Sharpe must carry a qualifier (`pool_sharpe`, `sym_sharpe`, `sharpe_per_trade`). Unqualified "Sharpe X" in any text — including agent messages — is a violation.
5. **Sample floor**: ≥48 crypto syms or ≥100 stocks × >1 yr × ≥30 trades/sym. Below that, the result is `[DIAGNOSTIC ONLY]` — never used for promotion, deployment, or recommendation.
6. **Source of truth for Sharpe = the trade-return list.** A Sharpe value without an associated per-trade returns dataset is unverifiable and must be marked `[UNVERIFIED]`.

### Backward (historical files)

Every CSV/JSONL in `data/` containing a Sharpe column was audited 2026-04-30 with `metrics_guard.audit_csv()` (extended). Three categories:
- **OK** (canonical column, no banned names): keep.
- **RECOMPUTABLE** (trade list pairable): rewrite the column with `pool_sharpe(returns)`, original preserved as `<col>_legacy_lie`.
- **UNVERIFIABLE** (no trade list): tag header with `[UNVERIFIED]`, move to `data/_legacy_unverified/` or delete.

Any historical claim of Sharpe X without going through the audit is a LIE. Don't cite it. Don't promote it. Don't compare against it.

### Lock the door

- **`metrics_guard.write_sharpe_row()`** is the ONLY sanctioned way to write a Sharpe-bearing row to a CSV in `data/sweep_results/` or `data/autonomous/`. It validates and refuses on violation.
- **`/tmp/BACKTEST_HOLD`** sentinel suspends Mac→server autosync (see `rsync_to_sandbox.sh`). Any A/B backtest must `touch /tmp/BACKTEST_HOLD` with reason+timestamp before starting and `rm` after.
- **CLAUDE.md rules 1–8 from the legacy "SHARPE DEFINITION — LIVE-MONEY POLICY" section remain in force** (per-trade returns only; open losers MtM'd; no annualization; pool Sharpe canonical; sample floor; etc).
- **NEVER move / quarantine / delete a lying CSV or violator script BEFORE both (a) a recomputed real Sharpe exists for that result, AND (b) a non-toxic compliant replacement script exists.** Tagging the file with `# [UNVERIFIED ...]` is OK; physically moving it is NOT until the replacement is in place. The user was burned 2026-04-30 by an agent that moved 329 files into quarantine before any replacement existed; everything was restored. Rule: tag in place, replace, verify, THEN move.
- **Tier names per `metrics_guard.tier_name()`** replace the word "trash" everywhere: Discard / Noise / Directional / Best-of-current / Strong / Aspirational. Sub-floor results still get the `[DIAGNOSTIC]` tag separately. Never call a result "trash" — name the tier.

If you're about to write a Sharpe number ANYWHERE without going through `metrics_guard`, STOP. The user lost half their net worth to that exact pattern. Don't be that script.

### IMPOSTER BLOCK (added 2026-04-30 after quality_optimizer per_sym fiasco)

The user lost **tens of thousands of dollars** to "imposter" results: numbers that look like Sharpe / WR / gain but bypass `metrics_guard`, hide their sample, and entrap promotion paths that touch live config. **Hang the imposters before they ever produce a number.**

**An "imposter result" is any of these** — they MUST be refused at production time, not at review time:

1. **Single-symbol "BEST" promotion.** A per-symbol-only optimizer output (e.g. `override_per_sym_<SYM>_BEST.json`, qopt single-sym scoring, autonomous_search single-sym leaderboard) is a structural sample-floor violation. It is FORBIDDEN to auto-write any such file or to feed it to live config. Multi-symbol pool only — period. If a tool wants per-symbol diagnostics, the file MUST live under `data/_diagnostic/` AND have `[DIAGNOSTIC ONLY · n_syms=1]` in its `_meta` AND must NOT be discoverable by any live override loader.

2. **Custom "Score" / composite metric without canonical row.** A "Score 12.9" / "score=8.8" / "composite_score=…" with no accompanying canonical row (the 9 fields in rule 2) is an imposter. Custom scores are allowed for ranking ONLY if `pool_sharpe + sym_sharpe + avg_gain_trade + gain_per_yr + gain_sym_yr + trades + max_dd_pct + n_syms + years` are written alongside in the same record.

3. **"trades=N WR=X% gain=+Y%" string in `_meta`.** This is the exact pattern that lured promotion of single-sym tests with +13,435% gain. Forbidden. Use the mandatory reporting line (rule 4 of the legacy SHARPE section) or refuse to write the file.

4. **Auto-promotion to `backtest_v8/btc_loop_results/override_*` (or any path a live loader reads) from any script that does NOT import `metrics_guard`.** All promote paths route through `metrics_guard.write_sharpe_row()` + a sample-floor check. Currently quarantined producers: `quality_optimizer.py`, `autonomous_search.py`, `v8_quick_engine.py`, `v8_quick_sweep.py`, `v8_test_queue.py`, `backtest_v8_engine.py` (see `audit_repo_baseline.json`). They MUST exit non-zero if asked to promote until retrofitted.

5. **"Source run: qopt_…" with the source dir not on disk.** Any meta that claims a source run whose data directory does not exist = `[UNVERIFIABLE]`. The override is dead-on-arrival. Refuse to load.

6. **"Auto-promoted" without trade-list co-location.** A promote that does not co-locate its per-trade returns JSONL alongside the override file is forbidden. The trade list IS the proof — without it, the override is hearsay.

**Enforcement at chokepoints:**

- `quality_optimizer.py --per-sym` → DISARMED at promote site. Raises `SystemExit("IMPOSTER_BLOCK: per-sym promotion is forbidden — use multi-symbol pool sweep + metrics_guard.write_sharpe_row")` until the multi-sym retrofit is merged.
- `auto-promote` paths in `autonomous_search.py` and any future optimizer → must call `metrics_guard.write_sharpe_row()` (which validates) OR exit non-zero.
- Override loaders (`btc_loop.py`, `ez_manage.py`, `v8_quick_engine.py`) must reject any override file whose `_meta` matches the imposter pattern (`trades=N WR=X% gain=+Y%` without canonical row, or `Source run: <missing>`). A simple regex guard at load time + log line `IMPOSTER_OVERRIDE_REFUSED: <path>` + skip.
- `[UNVERIFIED]`-tagged files stay where they are — **do not move, do not delete** until a verified replacement exists (per the lockdown rule above). Tag in `_meta`, leave on disk, and ensure no live loader reads them.

**When you encounter an imposter while working: HANG IT FIRST, ask questions later.** Tag the `_meta`, disarm the producer, write the refusal log line, and only then continue. The user has been entrapped into promoting these too many times. The default reflex must be "refuse and log", not "review and decide".

---

## 🚨🚨🚨 SWEEP-LIVENESS MANDATE — READ FIRST. NON-NEGOTIABLE 🚨🚨🚨

### MACHINE ROLES — UPDATED 2026-05-08

**🚨 S2 IS DEAD (shut down by user 2026-05-08). S1 now runs BOTH crypto AND tradier sweeps.**

| Machine | Role | What MUST be running | What MUST NOT be running |
|---|---|---|---|
| **MacBook** (`/Users/niels/Documents/binance`) | LIVE TRADING | `ez_manage.py --account {ang,inf,fin,flz,men}` (5 crypto), `tradier_manage.py --account {trb,trc}` (2 stocks), `ez_positions_quick.py`, `ez_positions_service.py`, `ez_market_data.py`, `ez_orderbook.py` | NO sweeps. NO precompute. NO autonomous_search. |
| **S1** (`s1-int`, `/home/niels/binance-sandbox`) | **CRYPTO + TRADIER SWEEPS** | `backtest_v8_sweep.py --mode crypto` (8 core syms, system_combo) AND `backtest_v8_sweep.py --mode tradier` (20 stocks, tradier_param_hunt). Both continuous via watchdog. | NO live trading. Kill `start_backtest_v8_loop.sh` if seen (OOMs). |
| **S2** | **DEAD — DO NOT USE** | — | Everything. S2 is deleted. Never SSH to s2-int. |

Mode-mismatch = 0-trade lying results. **Has cost weeks. KILL on sight.**

### EVERY-SESSION STEP 0 — RUN FIRST, FIX BEFORE ANYTHING ELSE

```bash
# (a) MacBook live trading — expect ≥7
ps -ef | grep -E 'ez_manage\.py --account|tradier_manage\.py --account' | grep -v grep | wc -l

# (b) S1 crypto sweeps RUNNING — expect ≥1
ssh s1-int 'pgrep -afc "backtest_v8_sweep.*--mode crypto"'

# (c) S1 tradier sweeps RUNNING — expect ≥1
ssh s1-int 'pgrep -afc "backtest_v8_sweep.*--mode tradier"'

# (d) Results growing
ssh s1-int 'ls -lt /home/niels/binance-sandbox/data/sweep_results/ 2>/dev/null | head -3'
ssh s1-int 'ls -lt /home/niels/logs/bt_sweep_*.log 2>/dev/null | head -3'
```

**S2 IS DEAD — do NOT SSH to s2-int.** If crypto or tradier sweep on S1 shows 0: relaunch via watchdog_sweep_s1.sh (crypto) or manually with backtest_v8_sweep.py --mode tradier.

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

## EXIT RULES — USER MANDATE 2026-05-09

**The user lost 80% on 2 accounts to NO_LOSS lying about safety. New rule: ONLY 3 paths can close a losing position. Everything else: HOLD.**

### The 3 (and only 3) loss-exit paths

| # | Rule | Code site | Knobs (testable) |
|---|------|-----------|------------------|
| **R1** | DC4 emergency close — within first `R1_NEWBORN_WINDOW_MIN` (default 15min) of open, if price breaks the configured DC channel level (4-bar or 1-bar), CLOSE NOW. Bypass NO_LOSS / hedge / MTF. Fires desktop alert + JSONL log naming the entry signal. | `ez_manage.py` ~20709 (R1 block, before R2) / `tradier_manage.py` ~1327 (5m TF) | `R1_DC_LOW4_3M_EMERGENCY_ENABLED`, `R1_NEWBORN_WINDOW_MIN`, `R1_USE_DC_4BAR`, `R1_TF` |
| **R2** | WT velocity slowdown near breakeven — fires when `floor <= gain < band`, vel against, and `\|vel\| < \|vel_prev\| * WT_VEL_DECEL_RATIO` (DYNAMIC slowdown — fixed thresholds banned per user). Iterates over `R2_TF_LIST`. | `ez_manage.py` ~20768 (R2 block, after R1) / `tradier_manage.py` ~1366 | `WT_15M_VEL_SLOW_GAIN_BAND_PCT`, `WT_15M_VEL_SLOW_GAIN_FLOOR_PCT`, `WT_VEL_DECEL_RATIO`, `WT_VEL_USE_DECEL_RATIO_ONLY`, `R2_TF_LIST` |
| **HEDGE_FAILED** | Same-symbol hedge couldn't be taken (no quote, blacklist, etc) — fall back to direct close. Until then, OBLIGATORY_HEDGE fires the hedge instead. | `ez_manage.py:14132+` (OBLIGATORY_HEDGE block — already wired); fallback close uses reason containing `HEDGE_FAILED` (in bypass list). | `OBLIGATORY_HEDGE_ENABLED`, `OBLIGATORY_HEDGE_WT_TFS_REQUIRED` |

**Crypto TFs** (default): R1=3m, R2=15m. **Stocks TFs** (default): R1=5m, R2=1h/4h/D (per user — markets closed most of day, real moves on D/W).

**Bypass list** (`config.UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS`) MUST contain: `R1_DC_LOW4_3M_EMERGENCY`, `R2_WT_VEL_SLOW`, `WT_15M_VEL_SLOW` (legacy alias), `HEDGE_FAILED`, plus existing emergency reasons. Anything NOT in this list cannot close at loss — it must HOLD until gain >= 0 or hedge fires.

### DUPLICATE_OPEN_GUARD — gain-based, not time-based

The 900s time cooldown was replaced with a gain gate per USER 2026-05-09:
- AUGMENT (any kind: open/reenter/hedge-open/scalp augment) requires `gain > config.MIN_GAIN * config.DUP_GUARD_GAIN_MULTIPLIER` (default 3.0% * 0.5 = **1.5%**)
- Below threshold → BLOCKED with reason `BLOCKED_DUP_GUARD_GAIN_<x>pct_lt_<thr>pct`
- Time cooldown retained as fallback when `DUP_GUARD_USE_GAIN_GATE=False`
- Site: `ez_manage.py:10936-10961`

### augmented_positions persistence

**MUST persist across bars** until position is REDUCE'd or CLOSE'd. Backtest no longer clears it per bar (`backtest_v8_engine.py:1647-1651` — clear() commented out 2026-05-09). Set sites: `ez_manage.py:13178, 14654, 14751`.

### R3 — DEFERRED. ALL OTHER EXITS NEED MTF CONFIRMATION

User mandate (NON-NEGOTIABLE): every other exit must require multi-TF confirmation. NOT yet implemented (XL refactor, separate session — every exit path needs an MTF gate added). UNIVERSAL_NOLOSS_GATE keeps current loss-exits clamped until R3 ships.

### NO % STOPS — DISABLED PATHS (must stay disabled)

`tradier_manage.py`: `HARD_STOP_LOSS_MAX_PAIN`, `STALE_DATA_HARD_STOP`, `STALE_DATA_GAIN_EROSION`, `Market_Against_Position` bias reduce. **If you EVER see `gain < -` followed by `return True` in any exit path — DISABLE IT.**

---

## 🚨 S1 UTILIZATION FLOOR — ≥60-70% CPU + MEM AT ALL TIMES 🚨

User mandate 2026-05-09: "we have a backlog of literally millions of tests" + "S1 NEVER below 60-70% capacity". S1 idling = lying about progress.

**S2 IS DESTROYED (2026-05-08, shut down because of shitty backtesting wasting money). ALL test load = S1 ONLY. Crypto AND tradier sweeps both run on S1.**

### The "take turns" alternation rule (USER 2026-05-09)

S1 RAM is the binding constraint (~10.8GB/worker peak NPZ decompression on backtest_v8_engine, 31GB total → ~2-3 workers safe). Crypto and tradier sweeps must **take turns** so neither system is starved:

- **Always running**: ≥1 crypto worker AND ≥1 tradier worker simultaneously, alternating which gets the larger share each cycle.
- **Every cycle (~hourly)**: if last cycle was crypto-heavy (2 crypto + 1 tradier), next cycle goes tradier-heavy (1 crypto + 2 tradier). And vice versa.
- **Never starve**: a system without sweep results for >2h is stale — flip immediately.

### Every session start

```bash
ssh s1-int 'top -bn1 | head -3 && free -m | head -2 && pgrep -afc "backtest_v8_sweep.*crypto" && pgrep -afc "backtest_v8_sweep.*tradier"'
```

If CPU < 60% OR MEM-used < 60% of total → **launch more parallel sweeps** until both ≥60%. If only crypto is running → start tradier (and vice versa). Default safe headroom: stay below 90% CPU and 28GB MEM.

### Watchdog files

- `watchdog_sweep_s1.sh` — crypto + tradier alternation (existing v15, cron `*/5`). State file `/home/niels/logs/sweep_next_mode`. Tradier-priority when both dead.
- `watchdog_sweep_s1_tradier.sh` — tradier-only watchdog (cron `*/7`, offset to avoid race). Activated 2026-05-09. Refuses launch if crypto running. Same state file.

If a sweep dies mid-run (OOM, kill, crash), do NOT just relaunch the same workers — first investigate cause AND ensure new workers fill the utilization floor + the alternation invariant.

---

## 📁 TOSHIBA_EXT PATH MAP — where S2 archive lives now (USER 2026-05-09)

S2 was destroyed 2026-05-08 because of shitty backtesting wasting money. Its data + scripts were archived to TOSHIBA_EXT. **Do NOT bulk-sync TOSHIBA_EXT to S1** — point at it instead. Only copy specific files when S1 needs them.

### Canonical layout (when drive mounted at `/Volumes/TOSHIBA_EXT/`)

| Path | Purpose |
|---|---|
| `binance_archive/data/sweep_results/` | S2 historical sweep CSVs (1.5MB) |
| `binance_archive/data/` | S2 data dir snapshot |
| `binance_archive/klines_cache_tradier/` | S2 stocks klines (only if S1 missing) |
| `binance_archive/indicator_cache/` | S2 indicator snapshots |
| `s2_backup_20260508/binance-sandbox/` | S2's full sandbox at shutdown — scripts, configs, data, logs |
| `s2_backup_20260508/binance-sandbox/data/sweep_results/` | S2's last-day sweep CSVs |
| `s2_backup_20260508/binance-sandbox/data/canonical_trades/` | S2's per-trade JSONLs |
| `s2_backup_20260508/binance-sandbox/data/hourly_reconfig/` | S2's hourly_reconfig dir |
| `s2_backup_20260508/binance-sandbox/data/per_sym/` | S2's per-symbol agent state |
| `sweep_results_distilled/s2_baselines/` | **THE good stocks baselines** — 21MB of canonical_tradier_*.json + CANDIDATE_s2_tradier_*.json |
| `sweep_results_distilled/s2_autonomous_latest/` | most recent autonomous results |
| `sweep_results_distilled/STOCKS_RECOVERY_MANUAL.md` | written 2026-05-08; honest baseline = pool_sharpe 0.5823 (114 syms, 8078 trades, 2024-01-01 start) |
| `backtest_npz_master/` | 4.4GB master NPZs — DO NOT pull whole thing |

### What S1 currently has (verified 2026-05-09)

✅ `tradier_manage.py`, `config_tradier.py`, `backtest_v8_engine.py` (synced today with R1+R2 rewrite)
✅ `backtest_v8/indicators/<TICKER>.npz` (tradier NPZs present — sweeps running)
✅ `klines_cache_backtest/tradier/<TICKER>_5m.json`
✅ `data/sweep_results/` (1.6GB)
✅ `backtest_v8/sweeps/override_tradier_t1_*.json`, `override_tradier_t4_*.json`
✅ `data/baselines_from_s2_archive/` (3 canonical_tradier_*.json baselines copied 2026-05-09)

### What S1 LACKS — only fetch from TOSHIBA_EXT on demand

- ~20 tradier-only experiment scripts (e.g. `_ab_stocks_matrix.py`, `phase4_htfport_tradier.py`, `validate_tradier_114.py`) — NOT pulled. Can be fetched from `s2_backup_20260508/binance-sandbox/` if a specific run needs them.
- Active per-symbol configs for `trb`/`trc` (no `data/hourly_reconfig/{trb,trc}/active_config.json` on S1) — pull from `s2_backup_20260508/binance-sandbox/data/hourly_reconfig/{trb,trc}/` only when re-running tradier per-sym agent.
- Older sweep CSVs prior to 2026-05-08.

### Mac dashboards — auto-merge TOSHIBA_EXT when mounted

- `chart_server.py` (port :5077) — `_DEFAULT_ABS_ROOTS` (line ~70) points at the actual TOSHIBA_EXT paths. Browses Stocks tab from S2 archive when drive mounted.
- `flz_dashboard.py` (port :5057) — live trades only (`data/history/<acct>/*.jsonl`). Does NOT read TOSHIBA_EXT (live-only by design).

### Rule

**Don't fill S1 with logs or bulk archives.** When you need a specific S2 result, fetch the named file. When you need to compare against S2 baselines, point at TOSHIBA_EXT directly from Mac dashboards.

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
