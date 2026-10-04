# AUTOPILOT RUNBOOK — for a "dumb" agent (or a tired human). Written 2026-10-02.

The user is away until **Monday 2026-10-05 21:00 local**. Everything below runs WITHOUT any Claude session. If you are an agent reading this: **do less, not more.** The system is built to fix itself. Your job is to LOOK first, and only act on the table in section 4.

## 0. Hard rules (break one and real money / days of compute are lost)
1. **NOTHING GOES LIVE.** Never write `data/cat_side_defaults_4.json` (live hot-reloads it), `config.py`, `config_tradier.py`, `ez_manage.py`, `tradier_manage.py`, `execute_now`. The autopilot only writes `data/sweep_defaults/cat_side_defaults_4.json`.
2. **Never revert / restore / overwrite a newer file with an older one.** Backup first (`cp f backups/before_<what>_<YYYYMMDDHHMM>.ext`), then edit forward.
3. **Never kill processes by pattern** (`pkill -f row365`, `pkill python` …). Kill by exact PID or exact job-file path, and only when section 4 says so.
4. **Never run a second scheduler / herd / pilot by hand.** Old launchers (`v15_local_herd`, `fleet_monitor.sh`, `monitor_mega_sweep`) are retired and MUST stay off (they were disabled in the Mac crontab with the marker `#AUTOPILOT_OFF#`).
5. **Never write `SPREADSHEETS/TEMPLATE_*.xlsx` yourself.** Only `tools/v15_autopilot.py` (stage `template`) writes them, on **s1**. s1 is the template source of truth while the user is away. Do NOT re-enable Mac→server template pushes (`template_push.sh`, `v15_cleaned_norm_sync.sh`, `sync_mac_to_s1_30min.sh`): they would overwrite s1's newer templates.
6. **No fabricated numbers.** Every Sharpe/gain figure comes from a real trade list (see CLAUDE.md NO-LIES). `0` only for an exact 0.00000; never for None.
7. Do not ask the user questions; they are unreachable. Write what you did in `data/autopilot/LOG.md` and `DAILY_AVG_DELTA_CHECKLIST.md`.

## 1. What runs, and where (all coordinated by s1)
| Piece | Where | How often | What it does |
|---|---|---|---|
| `tools/v15_fleet_scheduler.py` | s1 cron | every 2 min | launches 30D sym_side pilots (long+short pair per symbol) on s1/s2/s5 under memory + CPU admission; writes `data/autopilot/sched_snapshot.json`; log `/tmp/v15_fleet_sched.log` |
| `tools/v15_autopilot.py` | s1 cron | every 5 min | round machine: sweep → collect → template → normalise → defaults → sync → restart; heartbeat `data/autopilot/STATUS.json`; log `data/autopilot/LOG.md`, `/tmp/v15_autopilot.log` |
| `tools/v15_npz_keeper.py` | s1 cron | every 10 min | refreshes the NPZ of the next 10 pending STOCK symbols (Tradier tail rebuild on s1), pushes to s2/s5 with md5 |
| `~/npzb/npzb_loop.sh` | s1 cron | every 10 min | slow crypto NPZ rebuild (the ONLY crypto NPZ refresh) |
| `tools/v15_cpu_guard.py`, `v15_stall_watch.py`, `v15_progress_board.py` | s1 cron | 1–10 min | CPU/stall watchdogs (pre-existing) |

Servers: **s1** `127.0.0.1:2201` tunnel or `ssh s1-pub` (public); **s2** = `10.0.0.4`; **s5** = `10.0.0.5` (reach via `ssh s2 'ssh 10.0.0.3 …'` if the Mac tunnel to s1 is dead). Sandbox dir on each: `~/binance-sandbox`. s1 reboots daily 12:55Z: crons come back by themselves (`@reboot` entries re-run the scheduler tick).

## 2. How to check health in 60 seconds (on s1)
```
cd ~/binance-sandbox
.venv/bin/python tools/v15_autopilot.py --status            # round, phase, why, alert, coverage per cat_side
tail -5 data/autopilot/LOG.md                               # human log of every stage
tail -c 1500 /tmp/v15_fleet_sched.log                       # last scheduler tick: hosts cpu/mem/slots, launched, skipped_unready
cat ~/v15_defaults_round.txt ~/v15_current_progress_dir.txt # current defaults round id + progress dir
crontab -l | grep -E "V15_AUTOPILOT|V15_NPZ_KEEPER|v15_fleet_scheduler"   # all three must be present, none commented
```
Healthy = `STATUS.json.tick_at` < 10 min old, `alert: null`, scheduler snapshot < 10 min old, every host CPU ≥ 85 % (non-niced) most of the time, no `Traceback` in recent `/tmp/sweep_*_30D.log`.

## 3. The round, in plain words
1. **SWEEP**: each stock/crypto symbol gets a 30D pilot pair (LONG+SHORT; `_SHORT` skipped for `NON_SHORTABLE` stocks). Results go into the round's progress dir (`~/v15_runN_<date>/progress` on each host).
2. The sweep is "done" when every expected sym_side is done, or the fleet has been quiet for 15 min with coverage ≥ 85 % (≥ 60 % after 48 h). If quiet with low coverage the autopilot resets the scheduler's attempt counters (max 4×) so unready symbols get another chance.
3. **Round-end** (state is saved after every stage; a crash resumes at the failed stage): `collect` (per-host partials merged into `SPREADSHEETS/v15_vector_delta_latest.xlsx`) → `template` (AVG_DELTA/POS_SYM written, best value per switch/filter promoted as the single bold default when `pos_sym ≥ 3`, rows re-ordered worst-first) → `normalise` (`TEMPLATE_FINAL_NORM`) → `defaults` (`data/sweep_defaults/cat_side_defaults_4.json`) → `sync` (s2/s5, md5 verified) → `restart` (new empty progress dir, new defaults round id, attempt counters reset).
4. Next round starts immediately on the new defaults. **Never average across defaults rounds.**
5. Template stage is SLOW (10–40 min: four huge xlsx). That is normal. Do not kill it.

## 4. What can go wrong → what to do
| Symptom | Likely cause | Action |
|---|---|---|
| `STATUS.json` older than 15 min | autopilot cron missing, or lock held by a hung stage | `crontab -l | grep V15_AUTOPILOT`; `ps -eo pid,etime,args | grep v15_autopilot`; if a stage has run > 2 h kill THAT PID only (state resumes) |
| `alert: STALLED …` | quiet, coverage low, resets exhausted | `tail /tmp/v15_fleet_sched.log`: read `skipped_unready` reasons. `history < 32d` / `stale` = data problem (below). If most are unready, lower nothing — wait for the keeper/NPZB; the round will complete at 48 h with ≥ 60 % coverage |
| `alert: round-end stage X failed 6x` | template/defaults/sync problem | read `data/autopilot/template_update_<run>.log` / `defaults_build_<run>.log`. The autopilot ALREADY moved on to the next round on the previous defaults, so servers are busy. Fix only if obvious (disk full, missing file); otherwise leave it for the user |
| A server CPU < 70 % for > 30 min | all slots held, or `max_pairs` too low, or host unreachable | `tail -c 1500 /tmp/v15_fleet_sched.log` → `hosts` block. If `slots` full and CPU low raise that host's `max_pairs` by 1 in `tools/fleet_hosts_final.json` (backup first; hard ceiling 6; keep ≥ 3 GB `mem_avail`). If `UNREACHABLE`, check ssh; scheduler continues with the others |
| Memory < 3 GB available on a host, OOM kills in `dmesg` | too many pairs | lower that host's `max_pairs` by 1 (min 2); do not kill pilots (scheduler requeues) |
| `skipped_unready: … stale: last bar Nh old` (stocks) | NPZ not refreshed | the keeper does it within 10–20 min; check `/tmp/v15_npz_keeper.log` and `data/autopilot/npz_fail.json` (3 fails/day → skipped until next UTC day) |
| keeper: `no Tradier ext bars` / `overlap mismatch` | Tradier API/quota, or bad cache | leave; it retries next day. Never copy an older NPZ over a newer one |
| `skipped_unready: … stale` (crypto) | crypto gate is 336 h; NPZB loop stalled | check `~/npzb/run_loop.log`, `ls -t ~/npzb/logs | head`. NPZB defers symbols that have a running pilot; it resumes by itself |
| `skipped_unready: history 28.0d < 32d` | short-history stock | ignore: it is excluded from the round until its NPZ has ≥ 32 days (STKH rebuild list in `~/stkh/installed.csv` on s5) |
| `BadZip` / `zipfile` error on a template | truncated xlsx (OOM during save) | restore from `backups/autopilot_<run>_*/` (the backup taken just before the failed save) — this is a restore of the JUST-WRITTEN-BEFORE copy, allowed; then let the autopilot retry |
| `DEFAULTS-GATE` refusal in a sweep log | pilot's defaults round id mismatch | `cat ~/v15_defaults_round.txt` must exist on every host and equal across hosts; re-run `python tools/v15_autopilot.py --once --force-stage restart` ONLY if the file is missing |
| s1 disk > 90 % | logs / backups fill | delete only: `/tmp/sweep_*_365D.log`, `backups/autopilot_*` older than the last 12, `~/stkt_npz_backup_*` older than 7 days, `~/npz_backup_*` older than 14 days. NEVER delete `~/v15_run18+` (Monday go-live evidence — the disk guard protects them since 2026-10-04) or NPZs/templates. If still > 90 %: offload a big cold dir to s2+s5 with a checksum reverify first (precedent 2026-10-04: 12G SPREADSHEETS/archive_20250928_0730 → s2+s5, then removed on s1 only) |
| Scheduler dead (snapshot > 10 min) | cron removed / lock stuck | the autopilot runs a tick itself; also check `ls -l /tmp/v15_fleet_tick.lock` and that no stray `timeout 110 python3 tools/v15_fleet_scheduler.py` is hung (> 5 min) |
| Mac asleep | normal | nothing depends on the Mac. Mac crons that push templates are intentionally OFF |
| Tunnel to s1 dead | normal flakiness | use `ssh s1-pub` or `ssh s2 'ssh 10.0.0.3 …'` |

## 5. Data facts every agent must know
* **Stocks**: Tradier (5m + D) is truth since July; Massive only fills deep history. Stock NPZs ≈ 730 days. The keeper rebuilds only the TAIL (rolling cutoff 8 days back) with the unchanged builder (md5 `3e5c1477`) and `npz_guard` (never shortens).
* **Crypto**: a crypto NPZ cannot be rebuilt from `klines_cache` (only ~17 days of 15m). Crypto NPZ freshness depends on the NPZB loop. This is a KNOWN LIMITATION: crypto 30D windows end at the NPZ's last bar.
* **Look-ahead**: leaky HTF arrays are shifted at load (`vec_decisions/htf_causal_align.py`); never set `V12_HTF_LEAK_LEGACY=1`.
* **Engine** md5 must be equal on s1/s2/s5 (`data/engine_deploy/CURRENT.json`). Do not deploy engine changes unattended.
* A white row = switch that ADDS trades; an orange/yellow cell = filter that REDUCES trades. A switch/filter that never produced a value in 365D row evidence is DEAD → sampled 1-in-20.
* Sample floor: ≥ 48 crypto or ≥ 100 stock sym_sides, 30D, ≥ 30 trades/sym; below that = `[DIAGNOSTIC ONLY]`.

## 6. What is intentionally NOT automated (waits for the user)
* Promoting sweep defaults to LIVE (`data/cat_side_defaults_4.json`, config files). The new defaults are in `data/sweep_defaults/` and the templates; `tools/v15_defaults_diff.py` shows the diff against live.
* Per-sym 365D certification (`tools/confirm_365d.py`, `tools/promote_365cycle_winners_20260929.py`), live restarts, ghost-row retirement, `_FILTER_TF` family decisions, rotating the Massive API key (it was printed once in an agent transcript; key now in `~/.config/massive_api_key`, mode 0600).
* Crypto NPZ tail refresh tool (needs a deep-history source; see 5).

## 7. Emergency stop / restart
* Stop everything new: comment the `V15_AUTOPILOT` and scheduler lines in s1's crontab (`crontab -e`). Running pilots finish by themselves.
* Resume after any outage: nothing to do — crons restart, `state.json` resumes, the scheduler requeues unfinished sym_sides (attempt counters reset per round).
* Force a stage: `python tools/v15_autopilot.py --once --force-stage collect|template|normalise|defaults|sync|restart` (this marks earlier stages done and runs from that stage).
* Rollback of a bad template round: backups are in `backups/autopilot_<run>_<timestamp>/` (4 xlsx) and `data/sweep_defaults/cat_side_defaults_4.before_<run>.json`. Copy them back, then `--force-stage normalise`.

## 8. MONDAY 2026-10-05 — FINAL PHASE → GO-LIVE (USER 2026-10-02, start moved to 06:00 UTC; fully automatic)
**Rule:** best settings run 365D; if 365D is negative the 30D sheet is repaired (BIBLE §58 loop) until both are positive; ONLY positive sym_sides trade live; negative / unverified sym_sides do not trade AT ALL (not on old settings either); the live-faithful `backtest_v12_engine` parity test runs the 30D settings; everything that qualifies is live before the 13:30 UTC open.

| UTC | Who | What |
|---|---|---|
| Mon 06:00 | `v15_autopilot` (s1) → `tools/v15_final_orch.py` | stops normal rounds; picks per sym_side the NEWEST round's finished 30D sheet (`~/v15_run<N>_*/progress`, N ≥ 18), copies all of them into `~/v15_final_20261005/progress` on s1/s2/s5 (balanced; LONG+SHORT of a symbol together), points `~/v15_current_progress_dir.txt` there, kills the old-round pilots by exact PID, creates `data/autopilot/chain_mode.flag` |
| 06:00–12:30 | scheduler (cron) | with the flag present the scheduler runs the chain: `tools/v15_365_cycle.py` 365D verify → REPAIR (≤3 rounds, 2 attempts) into `~/v15_final_20261005/chain/` |
| 06:00–12:30 | autopilot every 5 min | for each sym_side whose chain verdict is `final_both_ok`: launches the parity run (`tools/v15_parity_check.py --progress <final set>`, 30D, `backtest_v12_engine` scalar vs vector) and, for crypto, the 365D certification (`confirm_365d.confirm_symside`, ONLY that recipe, staged in `~/v15_final_20261005/confirm/`); exports `data/autopilot/final/qualifiers.json` |
| 12:30 | autopilot | FINALIZE: freezes `qualifiers.json` (`finalized: true`) |
| 12:35 / 12:50 / 13:10 | **Mac cron** `tools/golive_final.py` | `--pull` (dry-run report) / `--pull --apply` / retry. Writes the live books (see below). Log `/tmp/golive_final.log`, report `data/autopilot_final/golive_report_<ts>.json` |
| 13:30 | autopilot | leaves the final phase, removes the chain flag, restores every host's progress-dir pointer to the PRE-FINAL round dir (unfinished sym_sides of that round keep sweeping = the cycle continues where it left off), then runs the round-end: collect → template (row order + AVG_DELTA/POS_SYM) → normalise → defaults → sync → restart into the next round on the new defaults |

**Qualified** = chain `both_ok` (30D AND 365D valid, gain > 0, TIM ≤ 80, DD ≤ 30, trades ≥ 10 / ≥ 80, 365D span ≥ 330 d) + (crypto) certification record + NOT (live-faithful parity run valid AND negative). **Parity** is run and recorded for every candidate; `UNAVAILABLE` (scalar harness exits without a result – the known gap, ~60 % of runs) is NOT a block; a valid negative scalar result IS a block.
**Not qualified** (NEGATIVE / UNVERIFIED) → the book entry gets `_NEG_BLOCK` in `winning_tag` (`ez_negbook_is_blocked` / `tradier_manage.negbook_is_blocked`: no opens; an open position exits at the next WT turn against it — existing 2026-09-28 mandate) and the crypto 365D certification is removed (fail-closed). Blocks are written only if the chain really ran (qualified+negative ≥ 40 % of expected); never-evaluated sym_sides are untouched.
**Files written by `golive_final.py` (all hot-reloaded, no restart, backups in `backups/before_golive_final_<ts>_*`):** `data/hourly_reconfig/per_sym_active_config.json` (crypto), `per_sym_active_config_stocks.json`, `data/hourly_reconfig/trb/active_config.json` (stocks overlay read first by `tradier_manage._cfg`), `data/confirmed_365d.json`, `data/full_recipe_live_config.json` (exact recipes of qualified sym_sides removed). Stocks live starts from the Mac cron at 13:30 UTC; crypto live (`ez_manage --account …`) runs on the Mac and must be AWAKE.

### What to check Monday morning (agent or human)
```
ssh s1-pub 'cd ~/binance-sandbox; .venv/bin/python tools/v15_autopilot.py --status; cat data/autopilot/final/progress_report.json; ls ~/v15_final_20261005/chain/v365 | wc -l'
tail -20 /tmp/golive_final.log            # on the Mac, after 12:35
ls /tmp/golive_rehearsal 2>/dev/null       # rehearsal sandbox (ignore)
```
### Failure table (Monday)
| Symptom | Action |
|---|---|
| `qualifiers.json` missing / not finalized at 12:50 | autopilot dead or final phase never started: on s1 run `AUTOPILOT_FINAL_T0=2026-10-05T00:00:00+00:00 .venv/bin/python tools/v15_autopilot.py --once` once (it assembles immediately), wait for the chain, then on the Mac `python tools/golive_final.py --pull` (dry) and `--apply` inside the window |
| `golive_final` prints `ABORT … engine md5 mismatch` | `data/engine_deploy/CURRENT.json` on s1 and the Mac differ: do NOT bypass; sync CURRENT.json from the Mac (the deploy source of truth) and rerun |
| `ABORT … outside the go-live window` | the apply window is 11:30–13:29 UTC on 2026-10-05; use `--force-time` only if you are certain the data is final |
| `ABORT … 0 qualified` | the chain produced nothing positive (or never ran). Nothing was written: live is unchanged. Do NOT promote anything by hand |
| few qualified (expected: roughly a third of the sym_sides, from the run20 rehearsal) | normal. The rest is blocked by design |
| Mac asleep at 12:50 | wake it, run `python tools/golive_final.py --pull --apply` before 13:29 UTC |
| Wrong promotion discovered | restore the 5 files from `backups/before_golive_final_<ts>_*` (a rollback of YOUR OWN just-applied write; ask the user before anything else) |
