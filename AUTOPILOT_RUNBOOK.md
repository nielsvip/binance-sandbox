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
| s1 disk > 90 % | logs / backups / old progress dirs | delete only: `/tmp/sweep_*_365D.log`, `~/v15_run*/progress` dirs of rounds older than the previous one, `~/stkt_npz_backup_*` older than 7 days, `~/npz_backup_*` older than 14 days. Never delete NPZs, templates, `backups/autopilot_*` of the last 2 rounds |
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
