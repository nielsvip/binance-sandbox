# HANDOFF — v15 fleet, 2026-09-29 PM (binance-59)

Single operator now (all other agents exited). Everything below runs **independently on cron**, survives
reboots, and resumes where it left off. Servers must never drop <85% CPU.

## 1. WHAT IS RUNNING (cron-driven, reboot-proof)

**Main 30D full-universe sweep** — `tools/v15_sweep_cron.sh` (cron `*/5` + `@reboot`) launches
`tools/v15_full_sweep_driver.py` per host and holds the herd singleton lock (`flock_hold_herd` on
`/tmp/v15_local_herd.lock`) so the OLD self-resurrecting rig (herd/iso_dispatch/mega) can never come back.

| host | venue | shard | max_parallel(syms) | workers | order file |
|------|-------|-------|--------------------|---------|-----------|
| s1 (`niels`, `s1-int`) | crypto | 0/2 | 3 | 3 | `run_order_crypto.txt` |
| s5 (`s5`)              | crypto | 1/2 | 3 | 3 | `run_order_crypto.txt` |
| s2 (`s2`, `s2-fresh-int`, 10.0.0.4) | stocks | 0/1 | 6 | 3 | `run_order_stocks.txt` |

- Order = **worst-first BY SYMBOL, disabled symbols first** (`data/reports/per_sym_recheck_20260929/run_order_{crypto,stocks}.txt`).
- Each symbol runs **LONG+SHORT together** (one NPZ into OS page cache, not re-read for the 2nd side).
- Per-side base: a now-disabled (neg/zero) side gets `V15_TEMPLATE_DEFAULTS=1` (reset toward cat_side
  template defaults; the FRESH `[ADAPT-BASE]` stage still picks the best credible of {defaults, recipe,
  prev_best} — for most neg sides defaults win). Positive sides = FRESH best-base.
- FRESH runs use an **isolated progress dir** `~/v15_run1_20260929/progress` — this is what lets every
  side re-run: the pilot's `ALREADY FINISHED (early) — MUST NOT RETOUCH` guard only fires when it finds a
  prior progress JSON in `V15_PROGRESS_DIR`; an empty iso dir ⇒ guard silent ⇒ fresh run. **Sheets still
  land in `SPREADSHEETS/V15_V16_CELL_BY_CELL/` (harvest + avg_delta path).**
- Live parity at DONE is skipped on servers (`V15_SKIP_LIVE_AT_DONE=1`) — parity runs on the Mac.

**Stock 365D kline fetch** — `download_stock_klines_15m.py --from-date 2024-01-01` on s2. LEFT RUNNING
(Massive.com 15m→365D). Never kill it. Crypto klines are complete (Binance) unless a symbol is brand new.

**Mac harvest loop** — `tools/v15_mac_harvest_loop.sh` (pid was 10110) pulls finished
`{SYM}_{SIDE}_bh…gain…_30d_matrix.xlsx` + charts into `SPREADSHEETS/V15_MAC_DONE/`.

**Counting drivers correctly:** use `ps -eo args | grep -c "[v]15_full_sweep_driver.py --order"` (exactly
1 per host). Do NOT use `pgrep -fc v15_full_sweep_driver` — it also matches the wrapper's `setsid nohup …`
launch line and reports phantom 2s. The driver holds a singleton flock (`/tmp/v15_full_sweep_driver.lock`)
and the wrapper is flock-guarded (`/tmp/v15_sweep_cron.wrapper.lock`); duplicates only ever appeared from
manually launching concurrently with the `*/5` cron — let the cron manage it, don't hand-launch.

## 2. THE PIPELINE (order is law)

1. **30D run #1, ALL sym_sides** (running now; disabled first). One pass = driver prints `PASS COMPLETE`.
2. **Recalc `v15_avg_delta`** — `tools/v15_avg_delta_rebuild.py` → `SPREADSHEETS/v15_avg_delta/v15_avg_delta_{YYYYMMDD}.xlsx` (+ `_latest`).
3. **Rebuild templates** — `tools/v15_template_restructure.py --cat-side ALL` → preview → verify yellow
   multiset preserved (streaming `iter_rows`, NOT `ws.cell()` in read_only — that is O(n²) and hangs) →
   backup + swap live + rsync s1/s2/s5 + md5 verify (see `scratchpad/swap_templates.sh` pattern).
4. **30D run #2, ALL sym_sides** — the advance is: when all 3 shard markers `~/v15_run1_20260929/PASS_COMPLETE_shard{i}of{n}` exist → recalc avg_delta → rebuild+swap+rsync templates → on each server `echo ~/v15_run2_20260929/progress > ~/v15_current_progress_dir.txt && mkdir -p ~/v15_run2_20260929/progress && rm ~/v15_run1_20260929/PASS_COMPLETE_shard*`; the wrapper then launches run #2 with the fresh dir (guard silent ⇒ every side reruns). **This advance is NOT yet auto-wired to a controller — it is triggered by the operator/monitoring session when run #1 completes** (run #1 takes hours; markers are the signal).
5. **365D verification + adjustments** — ONLY after run #2. Not before.

## 3. MONITORING RUNBOOK (what to watch every cell fill; fix 0/repeated delta immediately)

Progress lives in the iso dir on each server (`~/v15_run1_20260929/progress/{SYM}_{SIDE}_v14_progress.json`)
and the sheets in `SPREADSHEETS/V15_V16_CELL_BY_CELL/`. A healthy row has: `E` numeric (≠BASELINE), `F/G`
float (≠None), `L:BI` yellow floats, `C` override string when delta>0, `H` present.

**Red flags to fix immediately (NO-LIES / BIBLE §18–21):**
- **0 delta everywhere for a side** → its NPZ is missing/gapped (DATA_ERROR) OR the switch is a no-op stub.
  Check `/tmp/sweep_{SS}.log` for `DATA_ERROR` / `0 trades`. Fix the NPZ (precompute) or mark stub.
- **Repeated identical delta across different filters/switches** → the fabricated-distinctness trap
  (`v12_quick_engine` arange/hash flips — see memory `v12_quick_engine_synthetic_distinctness`). A real
  sweep has varied deltas. Identical non-zero deltas on unrelated filters = synthetic → do NOT promote;
  re-run that side and trace the engine path.
- **Baseline sign flip** → trust `initial_baseline_gain`; engine sign is authoritative (memory
  `v15_baseline_sign_corruption`).

Monitor: `tools/v15_delta_health_monitor.py` (Mac cron `*/10`) scans the newest sheets and logs
0/repeated-delta offenders to `data/reports/delta_health_{date}.md`.

## 4. YELLOW AGENT — MUST KEEP RUNNING (days), resumable

The yellow/orange full matrix (**every switch × every filter × 5 TF × 4 cat_side**) runs on an OLD
template and, when complete, its yellow/orange map is applied to the LATEST template. It was a spawned
fork that did **not** survive this session's compaction/reset — it is **NOT running** and must be brought
back as a **cron job** (forks don't survive reboots). Candidate tool: `tools/all_filter_on_all_row.py`
("929 switches × 440 filters per switch") / `tools/simple_switch_filter_full.py`. It writes CSV/DB and
must resume from its own store. **ACTION PENDING: confirm exact tool + state store, wrap in a `*/10`
cron guard (launch if absent), on a box with spare cycles or interleaved — it must not starve the 30D
sweep.** See §6.

## 5. PARITY FORWARD-TESTS → MAC ONLY

Never on servers (they stay 100% on 30D). `tools/v15_parity_saturator.py` runs on the Mac via cron. Server
parity crons (`parity_saturator_watchdog`) are DISABLED in each server crontab (commented, reversible).

## 6. KEY FILES / COMMANDS / LANDMINES

- Reset the fleet again if the old rig returns: `tools/reset_fleet_20260929.sh` (kills spawners+orchestrators+pilots, keeps kline fetch; crontab backed up to `~/crontab_backup_reset_*.txt`).
- Relaunch/repair main sweep: `bash ~/binance-sandbox/tools/v15_sweep_cron.sh` (idempotent).
- Disabled-set + order: `data/reports/per_sym_recheck_20260929/{priority_negzero,run_order}_{crypto,stocks}.txt`.
- Disabled counts: stocks 90 (87 neg + 3 zero), crypto 129 (124 neg + 5 zero). Unevaluable (NPZ): 24 stock, 80 crypto — will DATA_ERROR until their NPZ is built.
- **Host alias trap:** `s2-int` (127.0.0.1:2202) still points at **s1**; the REAL s2 is `s2-fresh-int` (10.0.0.4). s1 = `s1-int`.
- **s2 herd landmine:** never launch the herd with `V15_PILOT_PY=/usr/bin/python3.12` (system python, no deps → every pilot crashes). Use `.venv/bin/python`.
- **Guard bypass:** every fresh pass needs a NEW empty `--progress-dir`, else `ALREADY FINISHED` skips.
- Backups of the 4 live templates before the reorder swap: `backups/before_reorder_swap_*_202609292109.xlsx`.
