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
- [x] B1 Back up 4 templates to backups/ (before_avgdelta_fix_<ts>_*).
- [x] B2 New single writer `tools/v15_daily_template_update.py` (replaces both apply scripts; old ones become refusers):
      aggregate per (tab,kind,name) -> write **AVG_DELTA / POS_SYM** (by header) -> promote ONE bold default per
      group/filter (highest positive avg, breadth gate pos_sym>=2) -> sort worst_first (whole rows, white above orange)
      -> **clear VECTOR_DELTA/HUSTLE/E.. template garbage** -> verify (one default per switch & filter, row multiset
      unchanged) -> atomic save -> ledger `data/cat_side_promotions.json` + `build_cat_side_defaults_4.py`.
- [x] B3 (idempotent: 2nd/3rd run promoted 0, cleared {}) Idempotent rounds: POS_SYM = base + this round, AVG_DELTA replaced; a re-run of the same round_id never double adds.
- [x] B4 (audit 0 violations x4; md5 equal on s1/s2/s5, sandbox+~/binance) Dry-run on copies, inspect, then apply to SPREADSHEETS/TEMPLATE_*.xlsx; md5-sync to s1/s2/s5 (+ ~/binance).
- [ ] B5 Grey census + un-grey rows that are calculable (pilot computes them; only NOT_IN_CONFIG stays grey).

## C. Data collection (all servers -> one aggregate)
- [ ] C1 Per host: pick newest `*_v14_progress.json` per sym_side from the current round's progress dirs
      (`~/v15_run*/progress`, `data/reports/lifecycle_pilot`) -> `--emit-partial` -> Mac merge.
- [ ] C2 Round completeness gate: all sym_sides of the universe finished (or deadline hit -> partial apply, flagged).
- [ ] C3 30D and 365D both considered: promotion uses 30D avg; per_sym go-live needs pos 30D AND pos 365D (confirmed_365d.json).

## D. Pipeline + scheduling (agent "orchestrator")
- [x] D1 (written by fork; dry-run only) `tools/v15_daily_pipeline.py`: stages  finish-check -> collect -> template update -> sync -> restart sweeps with new
      templates (fresh progress dir) -> 365D verify -> per_sym apply; resumable, state in `data/daily_pipeline_state.json`.
- [x] D2 (written; simulated only) Scheduler `tools/v15_fleet_scheduler.py`: N-server aware (discover via hosts file), one shared work queue
      (sym_side / window jobs), per-host admission by CPU (<90% -> add a job) and RAM (MemAvailable guard -> OOM-safe),
      work-stealing so every box stays >90% CPU; works with 1 server.
- [x] D3 (simulated) Market-clock priority: crypto always eligible; while US market open -> crypto only; market closed ->
      stocks take precedence until open; stocks done -> crypto resumes. Stock results must be applied before 13:30 UTC
      (09:30 ET, DST-aware via zoneinfo America/New_York).
- [ ] D4 (script tools/v15_fleet_cron.sh written, NOT installed) Cron (per host + Mac) survives reboot, idempotent, flock singleton.
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

## Status log
- 21:30Z templates rewritten by tools/v15_daily_template_update.py (AVG_DELTA/POS_SYM, VECTOR_DELTA blanked, promotions, worst_first).
  Live defaults NOT synced (--sync-defaults is opt-in; cat_side_defaults_4.json unchanged). Old apply scripts now refuse.
- User 21:40Z: v15_avg_delta.xlsx must count each sym_side ONCE (latest result) + ever-nonzero filter cells yellow -> fork ae814737 working.
- Held until that fork finishes: pipeline stage 5 (restart on new progress dir) and scheduler go-live, so sweeps start on the final templates.
- Known gaps: s1-int tunnel down (use s1-pub); promote_365cycle_winners reads a stale 09-29 evidence file; market holidays not modelled.
- 22:57Z monitor: run17 progress s1=9 s2=9 s5=4 files, 0 final. s1 was over-launched (7 jobs, avail 3.6GB) -> killed MOVEUSDT_LONG + ZENUSDT_SHORT (newest, ~8 min old), avail now 9.5GB. Fleet cron intentionally OFF while orchestrator agent reworks scheduler (pairs, caps, universe).
- 23:15Z monitor: pair scheduler live on s1 cron (slots s1 3/3 s2 4/3 s5 2/2; cpu 124/58/92%; mem avail 4.8/19/20GB), no OOM, disk 66/33/38%. run17 finals: s1=1 s2=4 (BMNR/CRM/PBF/UEC _SHORT, ~2847 rows, ~30min each, honest finals) s5=2. 365D chain started (UEC S365D). Stock NPZ refresh fork running. Crypto pairs are the slow ones (~8.4GB/pair).
- 23:36Z monitor: cpu s1 73/s2 79/s5 57% (slots full/chains); raised max_pairs s1 4, s2 4, s5 3 (mem avail 15/19/19GB, mem guard stays). finals s1=7 s2=6 s5=7 (incl 365D chain running, v365 ok 3 bad 5 -> repair starting).
- 00:0xZ: Agent A found run17 sheets finished before pilot 47794652 carry fake zeros: moved 10 finished legacy sym_sides to ~/v15_run17_20260930/legacy_old_code (scheduler re-queues); 9 more skipped (365D chain running) -> redo next monitor pass (ss without 'naked_binding' in progress json). Staged templates ready (SPREADSHEETS/TEMPLATE_STAGED/202609302320) awaiting user approval. Stock NPZs refreshed (99/100).
- 23:57Z monitor: cpu s1 69 s2 86 s5 24% (s5 launches 1 pair/tick; its stock NPZs stale -> crypto only, s2 can't ssh s5, not syncing). finals s1=8 s2=6 s5=6 (+10 legacy moved). 365D/REPAIR chains running (SREPAIR#a2 ABT). No OOM/Tracebacks except old logs. Agents: A audit, B templates v3, C wiring running.
- 00:10Z: found clean_timestamped_interim (*/5 cron, all hosts+Mac) deleted active pilot atomic-save *.tmp files -> pilot clone_template/atomic-save crashes; patched (skip tmp/bak <30min old), deployed s1/s2/s5. Templates restructure 202609302320 APPLIED (user approved). Agents: A(yellow rekey+audit) B(v3 options/types) C(wiring+ALL_TF rename both variants) D(live parity staged; needs user 'unlock' for live files).
- 00:20Z: scheduler: chain launches (365D/REPAIR) consumed the 1-new-pair/tick budget -> s2 49% cpu with free slots; budget now counts only NEW pairs, max-launch default 2; deployed s1. Status: finals s1=9 s2=11 s5=6; cpu 142/49/60%; no OOM.
- 01:00Z PAUSED scheduler cron (V15_FLEET_SCHED commented) until Agent B stages v3a (orange filter rows restored in all tabs, lost new switches restored, every tab starts with default row, vec-function coverage, yellow painted); running pilots continue. RESUME: uncomment the 2 V15_FLEET_SCHED lines on s1 crontab after apply+sync.
- 01:25Z RESUMED scheduler (cron on). Live templates now = post-restructure (NO orange rows, tabs not starting with default: known defects, Agent B fixing in v3a) + bright/light yellow painted (v15_yellow_from_deltas --reuse --apply) + pilot bc5b4b6f evaluates bright+light yellow; synced s1/s2/s5 md5 OK. User: resume with whatever we have, accumulate data for tomorrow's avg deltas. Agent E building SWITCH_BIBLE.
- 00:41Z monitor (real clock): cpu s1 162 s2 114 s5 64%, finals s1=9 s2=19 s5=11, no OOM/Tracebacks, disk 67/34/39. Template v3a (orange rows restored, default-first rows, WT_DIV_EXIT row) APPLIED+synced+yellow repainted; v3b (orange calc proof, coverage) staged by Agent B next. Scheduler cron ON.
- 01:05Z LIVE FIX (user 'unlock and fix'): ez_manage.py _batch1_template_live_gate disabled (return True) — synthetic entry gate created 2026-09-07/10 blocked 100% ENTRY_VET entries inf/ang/fin/men since 09-28; ez_indicators.py L2582 BASE_PATH->self.base_path (crash loop since 09-24). Backups backups/before_batch1_gate_disable_*, before_lg04_basepath_*. LOCKED_FILES.md is immutable (could not log there). Restarted indicators + ez_manage accounts (watchdog relaunch). Synced to s1/s2/s5.
- 01:01Z monitor: cpu s1 158 s2 126 s5 27% (chains), finals s1=12 s2=29 s5=14 (+21 legacy aside), 365D crypto L 3ok/4bad S 3/1, stocks L 8/6 S 6/8, repairs ok 11; no OOM/Tracebacks; disk 68/35/39. ez_indicators relaunched after fix but data/latest_market_data.json still Sep-24 (first cycle slow, shared-mem publish TimeoutErrors) — recheck next pass.
- 01:15Z monitor: cpu s1 154 s2 88 s5 37%, finals s1=12 s2=35 s5=16, 365D L-crypto 3ok/5bad S 4/2 stocks L 9/6 S 6/8, no OOM/Tracebacks, disk 68/35/39. LIVE: data/latest_market_data.json refreshed 01:14 (ez_indicators fix works), all 5 ez_manage accounts up. 3m/1m NPZ staging (Agent H) running on s1.
- 01:55Z run18 (restored templates md5 2d9f76fc on all): stocks finals s1=11 s2=15 s5=12 in ~10min (≈52% of rows NOT_WIRED_VEC => fast); stocks ETA ≈ 03:30Z incl chains — ahead of 13:15 deadline; crypto waits for Agent J NPZ rebuild. No OOM/Tracebacks, disk 69/36/39.
- 02:01Z Agent J: crypto NPZ rebuild DONE (64 installed on s1/s2/s5 md5-verified: 61 rebuilt + 3 new relaxed GOOGLUSDT/MRVLUSDT/QNTUSDT, close mismatch vs klines 13117/36777 -> 0/41434). NOT rebuilt (precompute guard, long installed history, ~3.6% recent bars off): FILUSDC KASUSDT MASKUSDT NOTUSDT TRBUSDT TRUMPUSDC. Hosts file flipped: s1 [crypto,stocks], s5 [crypto,stocks], s2 [stocks]. Backups ~/npz_backup_crypto_20261001 on s1/s2/s5.
- 02:09Z run18: stocks 26/100 symbols done30 (26 L + 26 S) in 25 min, finals s1=12 s2=20 s5=20; 365D L 3ok/12bad S 8/7, repairs running; ETA stocks ≈ 04:00-04:30Z (deadline 13:15Z OK); crypto starts after stocks launched; templates md5 2d9f76fc unchanged; no OOM/Tracebacks; cpu 61/75/33 (chain-heavy).
- 02:25Z ETA risk: done30 only 26->29 in 15min (slots held by REPAIR chains a3..): max_pairs s1 6 s2 6 s5 5 (cpu 55-70%, mem ok) + cron --max-attempts 2 (was 4).
- 02:45Z yellow discovery launched (Agent Y): 365D audit s2=stocks s5=crypto pass1, 30D evidence (new-code only) collected, merge tool + supervisors installed; 365D/REPAIR chains now nice 10. See data/yellow_discovery/LOG.md
- 02:39Z run18: stocks done30 39/100 syms (was 29 at 02:24) ≈10 syms/14min after max_pairs 6/6/5 + attempts 2 → stock 30D done ≈04:10Z; finals s1=21 s2=35 s5=28; failing after 2 repair attempts: 11 sides; cpu 66/156/92; no OOM/Tracebacks; templates md5 2d9f76fc unchanged. Agent Y (yellow discovery) launching.
- 02:55Z USER: filters still not calculated (old engine unwired). Decision: Agent M merges+deploys ALL staged wiring (b1,b2c2,b3,b5b,b6..), filter census, then restart round (run19). Scheduler cron PAUSED (running run18 pilots finish; no new launches). Y (yellow discovery) on hold until new engine. Resume = uncomment V15_FLEET_SCHED on s1 + new progress dir.
- 02:55Z monitor: scheduler paused by design; run18 pilots drained (s1=1 s2=2 s5=0), finals 32/44/36; CPUs idle until Agent M engine v1 deploy then Agent Y discovery (>90% cpu). Mac v12_quick_engine.py md5 now 2eb422fd (was 8fbc7ffb) — Agent M staging/merging, hosts not yet updated (verify at deploy).
- 02:56Z ENGINE v1 DEPLOYED (Agent M): v12_quick_engine.py md5 2eb422fd + backtest_v12_engine.py 8949bc1a + vec_decisions (10 modules) + vec_unwired.json on Mac/s1/s2/s5 (~/binance-sandbox + ~/binance), import OK; manifest data/engine_deploy/202610010253.json; backups before_engine_m_202610010253. Pilots started AFTER 02:56Z use the new engine; running ones keep the old (restart round = run19). v12_quick_engine.py is LOCKED_FILES-listed: deployed on user order.
- 03:01Z ENGINE v2a DEPLOYED (Agent M): tools/opt/evaluate_v12.py (+ data/switch_dependencies.json): sub-knob master forced ON + OR-peer OFF together (EMA_9_21_FILTER_ENABLED<->KINDERGARTEN_EMA_GATE_ENABLED); result carries dep_forced. Proof: UEC_SHORT EMA filter off 68->84 trades. Pilot should add dep_forced masters to the override string (todo).
- 03:07Z v2b: data/switch_dependencies.json (73 sub-knob→master links incl. 39 derived/manual + EMA peers) deployed s1/s2/s5; changed-key list data/filter_census/m1/v2_changed_keys.json (Agent Y re-runs only these). Census: data/filter_census/m1/census.csv + report.
- 03:11Z: parent launched 365D yellow audit (existing tool, new engine 2eb422fd) on s1 CRYPTO_LONG / s2 STOCKS_L+S / s5 CRYPTO_SHORT, 14 workers each, CPU 95-100%; dir data/yellow_discovery/20261001/audit365_new. Agent Y builds the every-filter-every-switch-row version. Scheduler paused; sweep run19 not started.
- 03:19Z ENGINE c2-b7 DEPLOYED (Agent C2) Mac/s1/s2/s5 sandbox+~/binance: v12_quick_engine.py b3dbcac3 (base 2eb422fd) + vec_decisions/overtrade_guard.py e9dc60e4. Stocks queue_trade_action COUNTER_TREND_ADD_BLOCK now gates ALL opens (fresh+reentry) + augments (was only inside the permissive OR); defaults unchanged. Manifest data/engine_deploy/c2b7_*.json
- 03:26Z monitor: audits: s1 CRYPTO_LONG + s2 STOCKS running 97%/89% cpu; s5 CRYPTO_SHORT pass1 done (17242 cells, 344 pos_sym>0) -> launched pass2 + token tier on s5; engine now b3dbcac3 (C/C2 v2 deploy), pilot 7da80377, templates 2d9f76fc unchanged; scheduler paused (waiting user templates).
- 03:30Z yellow FILTER discovery running on s1/s2/s5 (Stage A/B, engine b3dbcac354eb); see data/yellow_discovery/LOG.md
- 03:39Z monitor: discovery (Agent Y v15_filter_discovery) running on all 3 (cpu 98.5/100/100%), engine b3dbcac3 + templates 2d9f76fc unchanged on all hosts, disk 70/36/40; s2/s5 load ~31 (oversubscribed 2x, acceptable); sweep scheduler paused until user templates land.
- 03:50Z ENGINE c2-b7c DEPLOYED (Agent C2) Mac/s1/s2/s5 both dirs: v12_quick_engine.py 5d7dbab8 (base b3dbcac3). Stocks EXIT_BLOCKER_REQUIRE_LH_LL now enforced at every close site (default OFF), WT_DC_LIVE_GATES_ENABLED added (default OFF). Defaults unchanged. Census: data/wiring/stocks_census_c2.{md,csv}. Manifest data/engine_deploy/c2b7c_*.json
- 03:55Z monitor: discovery at 100% cpu all hosts (load ~31), engine 5d7dbab8 (C2 b7c) + templates 2d9f76fc unchanged (user's real templates not yet delivered), disk 70/36/40; stock round (run19) not started — needs ~2h after templates land, deadline 13:15Z.
