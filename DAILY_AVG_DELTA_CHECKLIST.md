# DAILY AVG_DELTA + SCHEDULER — WORK CHECKLIST (2026-09-30)

Owner request (niels): fix the avg-delta calculation/apply loop, make it run automatically right after the
30D/365D backtests, feed results into per_sym settings before market open (stocks first), then restart the
tests on the new templates; orchestrate all servers (1..N) dynamically at >90% CPU without OOM.
Specs: BACKTEST_BIBLE §12, §56.0, §57-59, DAILY_OPTIMIZATION_PLAN.md. Mark items `[x]` as they are PROVEN (not written).

## A. Diagnose (findings)
- [x] Two competing pipelines exist: `v15_avg_delta_{rebuild,apply}.py` (writes AVG_DELTA, promotes, sorts groups)
      and `v15_vector_delta_{rebuild,apply}.py` (per-tab, best-per-sym_side aggregate, but WRITES INTO `VECTOR_DELTA`).
- [x] Templates are contaminated: VECTOR_DELTA populated on ~99% of rows (3646/3660 CRYPTO_LONG) with the cross-sym
      aggregate; AVG_DELTA only 2-570 rows; `data/cat_side_promotions.json` missing (promotion never ledgered).
      VECTOR_DELTA is the pilot's per-sym per-row output column -> must be BLANK in a template.
- [x] Old avg rebuild pooled the same switch across ALL tabs (key dropped the tab) -> wrong averages. Vector rebuild
      keys per (tab, kind, name) and takes best-per-sym_side (n == #sym_sides) -> keep this aggregation.
- [ ] Grey (col A FFBFBFBF) rows: census by reason; only NOT_IN_CONFIG / no-option rows may stay grey.

## B. Template fix (me, FIRST — unblocks everything)
- [ ] B1 Back up 4 templates to backups/ (before_avgdelta_fix_<ts>_*).
- [ ] B2 New single writer `tools/v15_daily_template_update.py` (replaces both apply scripts; old ones become refusers):
      aggregate per (tab,kind,name) -> write **AVG_DELTA / POS_SYM** (by header) -> promote ONE bold default per
      group/filter (highest positive avg, breadth gate pos_sym>=2) -> sort worst_first (whole rows, white above orange)
      -> **clear VECTOR_DELTA/HUSTLE/E.. template garbage** -> verify (one default per switch & filter, row multiset
      unchanged) -> atomic save -> ledger `data/cat_side_promotions.json` + `build_cat_side_defaults_4.py`.
- [ ] B3 Idempotent rounds: POS_SYM = base + this round, AVG_DELTA replaced; a re-run of the same round_id never double adds.
- [ ] B4 Dry-run on copies, inspect, then apply to SPREADSHEETS/TEMPLATE_*.xlsx; md5-sync to s1/s2/s5 (+ ~/binance).
- [ ] B5 Grey census + un-grey rows that are calculable (pilot computes them; only NOT_IN_CONFIG stays grey).

## C. Data collection (all servers -> one aggregate)
- [ ] C1 Per host: pick newest `*_v14_progress.json` per sym_side from the current round's progress dirs
      (`~/v15_run*/progress`, `data/reports/lifecycle_pilot`) -> `--emit-partial` -> Mac merge.
- [ ] C2 Round completeness gate: all sym_sides of the universe finished (or deadline hit -> partial apply, flagged).
- [ ] C3 30D and 365D both considered: promotion uses 30D avg; per_sym go-live needs pos 30D AND pos 365D (confirmed_365d.json).

## D. Pipeline + scheduling (agent "orchestrator")
- [ ] D1 `tools/v15_daily_pipeline.py`: stages  finish-check -> collect -> template update -> sync -> restart sweeps with new
      templates (fresh progress dir) -> 365D verify -> per_sym apply; resumable, state in `data/daily_pipeline_state.json`.
- [ ] D2 Scheduler `tools/v15_fleet_scheduler.py`: N-server aware (discover via hosts file), one shared work queue
      (sym_side / window jobs), per-host admission by CPU (<90% -> add a job) and RAM (MemAvailable guard -> OOM-safe),
      work-stealing so every box stays >90% CPU; works with 1 server.
- [ ] D3 Market-clock priority: crypto always eligible; while US market open -> crypto only; market closed ->
      stocks take precedence until open; stocks done -> crypto resumes. Stock results must be applied before 13:30 UTC
      (09:30 ET, DST-aware via zoneinfo America/New_York).
- [ ] D4 Cron (per host + Mac) survives reboot, idempotent, flock singleton.
- [ ] D5 per_sym apply before open: stocks first (`data/hourly_reconfig/{trb,inf}/active_config.json` via the sanctioned path).

## E. Verification
- [ ] E1 Template audit: 4 templates, every group exactly one YES == bold, every filter one bold header, VECTOR_DELTA empty,
      white rows above orange, md5 equal on all servers.
- [ ] E2 Dry-run the whole pipeline on a copy dir; one real pass on one sym_side proves the pilot reads the new template.
- [ ] E3 Scheduler simulation: 1 host vs 3 hosts, market open/closed, OOM guard.
- [ ] E4 Update BACKTEST_BIBLE §12/§56.0/§57 + memory; record what is NOT done.

## Rules honoured
Backup before edit; never revert; no row added/removed; NO-LIES (only real recorded deltas); live configs only
through the sanctioned per_sym path; Mac edit -> rsync -> md5 verify.
