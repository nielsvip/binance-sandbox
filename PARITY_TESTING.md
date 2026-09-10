# PARITY_TESTING.md — Live ↔ Backtest Parity Policy

> Created 2026-09-07 to resolve dangling refs CONFIG_CONVENTIONS.md:101 / CLAUDE.md:176. S1 liveness + alt-share tier gap investigated same session.

## 1. Master Switch: PARITY_COMPARISON_MODE

`config.py` + `config_tradier.py`, default `False`.

- `False` (normal): all gain-augmenting strategies active — Rotation / RSI2 / GapFill / ORB / EP / Clenow / SMFI / Minervini / Connors / FH_Momentum run live.
- `True` (parity A/B only): suppresses ALL gain-augmenting strategies so live == backtest apples-to-apples. Crypto path `tradier_manage._rotation_rsi2_loop ~15360` skips when enabled. **Set back to `False` immediately after confirming parity.**
- Never leave `True` in production — it is a measurement mode, not a trading mode.

Ref: [`CONFIG_CONVENTIONS.md`](CONFIG_CONVENTIONS.md) § PARITY_COMPARISON_MODE.

## 2. What Parity Means

| Domain | Live Source | Vector / Backtest Source | Parity Contract |
|--------|-------------|--------------------------|-----------------|
| Crypto entry/exit | `ez_manage.py` + `config.py` | `v12_quick_engine.py` / `backtest_v8_engine.py` + `SweepConfig` | 351 TEMPLATE.xlsx keys must exist in both configs and QuickConfig; FILTER_TF gates via `vec_decisions/filter_tf_gate.py` |
| Stocks entry/exit | `tradier_manage.py` / `tradier_matrix_gates.py` + `config_tradier.py` | `v12_quick_engine.py` (stocks mode) | Same — `_ALL_FILTER_TF` must be present in live variant |
| Per-sym overrides | `data/per_sym/*.json` (live recipes) | `tools/opt/lifecycle_pilot.py` / `per_sym_parity_contract.py` | Every non-orchestration override must be a causal Quick read AND a V12/live decision read — quick-only knobs are not sweep-eligible |

Cross-config bug class (LOCKED_FILES 2026-07-20): `run_simulation_tradier` must read `getattr(tm_mod.config, …)` not crypto `config` — otherwise crypto `BB_FROZEN_STOP_ENABLED` etc poisons stocks baselines.

## 3. Tools (Mac LIVE is source of truth — S2 dead, S1 = backtest only)

| Tool | Purpose | Pass Criteria |
|------|---------|---------------|
| `tools/verify_switch_parity.py` | Guard: 351-switch full parity never lost | Exit 0, prints `PARITY GUARD OK: N TEMPLATE keys …` |
| `tools/audit_v12_live_vector_parity.py` | Inventory: Quick ↔ V12 causal overlap | Reports `hooked_both / quick_not_causal / v12_not_hooked / not_declared` |
| `tools/opt/per_sym_parity_contract.py` | Fail-closed per_sym → Quick → V12 contract | JSON at `data/reports/lifecycle_pilot/per_sym_parity_contract.json`, zero `v12_not_hooked` for tradable knobs |
| `tools/opt/baseline_backtest.py` | Tier smoke: `--baseline baselines/baseline_crypto_{long,short}.json --window-days 30` | Workers finish, CSV rows > header, no Traceback |

**Current Mac status 2026-09-07 05:07 UTC:**

- `verify_switch_parity.py` → `PARITY GUARD FAILED: Missing from configs: ['WT_ACCEL_EXIT_ENABLED', 'WT_DIV_EXIT_ENABLED'] (2 total)` — open gap, not blocking sweep but must be added to `config.py`/`config_tradier.py` or removed from TEMPLATE.xlsx.
- `audit_v12_live_vector_parity.py` → `total 3368, hooked_both 615, quick_not_causal 285, v12_not_hooked 574, not_declared 1894` — expected; only `hooked_both` are sweep-eligible.
- MacBook LIVE: `5/5 ez_manage --account {ang,inf,fin,flz,men}` alive (10 procs incl watchers). Tradier live procs not running on Mac this session (weekend / market closed) — consistent with `INFRASTRUCTURE.md` (Mac LIVE, S1 backtest-only).

## 4. Parity Test Procedure (STEP-0)

```bash
# Mac live
ps -ef | grep -E 'ez_manage\.py --account|tradier_manage\.py --account' | grep -v grep | wc -l  # expect ≥5 crypto (tradier 0 on weekend)

# S1 liveness
ssh s1-int 'pgrep -afc "backtest_v8_sweep.*--mode crypto"; pgrep -afc "backtest_v8_sweep.*--mode tradier"'
ssh s1-int 'top -bn1 | head -3 && free -m | head -2'

# Parity guards (Mac)
python3 tools/verify_switch_parity.py
python3 tools/audit_v12_live_vector_parity.py
python3 tools/opt/per_sym_parity_contract.py && cat data/reports/lifecycle_pilot/per_sym_parity_contract.json | head -20

# Alt-share / tier smoke (Mac LIVE only — S2 dead, S1 has no alt-share tier)
python3 tools/opt/baseline_backtest.py --baseline baselines/baseline_crypto_long.json --window-days 7 --workers 4 --out /tmp/parity_smoke_long 2>&1 | tail -20
python3 tools/opt/baseline_backtest.py --baseline baselines/baseline_crypto_short.json --window-days 7 --workers 4 --out /tmp/parity_smoke_short 2>&1 | tail -20
```

Alt-share tier note: no `alt_share` / `alt-share` / `ALTSHARE` tier exists in repo (`grep -r alt.share` 0 hits). Historically tiers are `wt_dc_full`, `mega_crypto_v8_a1234`, `stocks_repaired_…`, `baseline_crypto_{long,short}`. Smoke therefore exercises `baseline_crypto_{long,short}` as the canonical alt-tier representative on Mac.

## 5. S1 Liveness Check — 2026-09-07 05:05–05:07 UTC (repo evidence)

- Host `157.180.125.52` (`s1-int` via `gateway-internal 157.90.168.35` → `127.0.0.1:2201`) is **UP**: `up 16:00, load 94–147, 595 tasks, 161 running`.
- `ControlMaster` sockets `cm-gw` + `cm-s1-int` alive (`ssh -O check` = `Master running`).
- **CPU/MEM floor ≥60-70% is MET but via undocumented workload:** `top` shows `%Cpu 95.8 us`, `Mem 27Gi/30Gi used (90%)`, `3.0Gi avail`. `load 90–147` — S1 is saturated.
- **Documented sweeps NOT running:** `pgrep -afc "backtest_v8_sweep.*--mode crypto"` → `1` (the pgrep itself), `…--mode tradier` → `1` — i.e. **0 real `backtest_v8_sweep` workers**. No `~/logs/bt_sweep*.log`, no `~/binance-sandbox/data/sweep_results/` rows (empty).
- **Actual workload:** `backtest_v8_precompute.py --all --mode crypto --workers 3` (x3, 99.6% CPU), `mega_sweep.py` (x5), `v15_real_filler.py --sym-side MSTR_LONG`, `aapl_1yr_filter_redesign.py`, `baseline_backtest.py --baseline baseline_crypto_{long,short}` (x4). All legitimate backtest work but not the `SWEEP_OPERATIONS.md` launcher path.
- **Watchdog / launcher gap:** `SWEEP_OPERATIONS.md` mandates `watchdog_sweep_s1.sh` (cron `*/5`) + `watchdog_sweep_s1_tradier.sh` (`*/7`) and `start_crypto_sweeps.sh wt_dc_full`. `crontab -l` on S1 shows **neither** — only `oom_guard.sh */2`, `build_master_switch_sheet */30`, `refresh_indicators_daily`, `chart_server`, `s1_disk_space_watchdog`. `ls /home/niels/binance-sandbox/watchdog* /home/niels/binance-sandbox/start_*` confirms no such scripts at those paths (S1 check 05:07 UTC). Result: alternation invariant (crypto/tradier take-turns, never starve >2h) is **not enforced by cron** — utilization floor is met by accident, not by watchdog.
- **Remediation:** Do not `killall python3` — many workers are productive. To restore documented ops: `rsync` current `watchdog_sweep_s1*.sh` + `start_crypto_sweeps.sh` to S1, `crontab -e` to restore `*/5`/`*/7` entries, then `pgrep` + `take-turns` check hourly. Until then, S1 is LIVE and utilized but **not under SWEEP_OPERATIONS watchdog control** — note this in sweep handoffs.
- **S2:** Dead per `INFRASTRUCTURE.md` / `SWEEP_OPERATIONS.md` 2026-05-08 — `s2-int` not probed (expected DOWN). All sweep work on S1.

## 6. Alt-Share Tier Smoke Test — Result / Block

- **Requested:** `alt-share tier smoke test` on S1 live run.
- **Repo search:** `grep -r "alt.share|alt_share|ALTSHARE|alt-share"` across `*.md, *.py, *.sh` → **0 hits**. No tier definition, no launcher, no baseline for `alt-share`.
- **S1 smoke BLOCKED by policy + workload:** S1 is backtest-only per `INFRASTRUCTURE.md` — do not run ad-hoc `baseline_backtest.py` there while precompute/mega_sweep saturate RAM (27Gi/30Gi, swap 0). Launching another tier would OOM. `SWEEP_OPERATIONS.md` explicitly warns `start_backtest_v8_loop.sh` OOMs and mandates launcher discipline.
- **Mac LIVE smoke substitute executed:** `tools/opt/baseline_backtest.py` is S1/Mac-portable. Running on Mac with `--window-days 7 --workers 4 --out /tmp/parity_smoke_{long,short}` proves tier plumbing without disturbing S1. S1 alternative is to wait for `backtest_v8_precompute` to finish (load 147 → idle) then run `ssh s1-int 'bash /home/niels/binance-sandbox/start_crypto_sweeps.sh status'` + `pgrep` verification — document T+5s/T+30s/T+5min checks per `SWEEP_OPERATIONS.md` § POST-LAUNCH VERIFICATION.
- **Block documented, not hidden:** No `alt-share` tier exists to smoke — use `baseline_crypto_long/short` or `wt_dc_full` as the canonical tier smoke representatives.

## 7. Dangling-Ref Fix

- This file satisfies `CONFIG_CONVENTIONS.md:101` and `CLAUDE.md:176` links to `PARITY_TESTING.md`.
- For sweep handoffs, cite this file §5 for S1 liveness proof and §6 for alt-share tier disposition — do not claim S1 `backtest_v8_sweep` is running until `pgrep -afc …--mode crypto/tradier ≥1` and watchdogs are restored.

## 8. Open Actions (owner)

- [ ] Add `WT_ACCEL_EXIT_ENABLED` / `WT_DIV_EXIT_ENABLED` to `config.py` + `config_tradier.py` or drop from TEMPLATE.xlsx so `verify_switch_parity.py` passes.
- [ ] Restore `watchdog_sweep_s1*.sh` + `start_crypto_sweeps.sh` to S1 and cron (`*/5` crypto, `*/7` tradier offset) — then re-run §4 STEP-0.
- [ ] If an `alt-share` tier is intended, define it (baseline JSON + launcher tier name) — until then §6 smoke via `baseline_crypto_*` is the tier smoke.
