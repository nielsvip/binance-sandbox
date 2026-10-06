# Daily pre-market chain: what actually runs (map as of 2026-10-06 ~17:10Z)

Spec: BACKTEST_BIBLE §68.3. Steps: 1 deltas, 2 template defaults + surface sync, 3 per-sym to live, 3b inf universe,
4 live trades, 5 next sweep. Market open is 13:30Z. All clocks are UTC (the Mac runs in UTC).
Evidence comes from `crontab -l` on Mac/S1/s2/s5, the scripts, and their logs. Where something was not checked, the
text says so.

## Summary: no daily chain runs end to end today

| §68.3 step | Intended owner | Schedule | State today |
|---|---|---|---|
| 1 deltas (`v15_avg_delta_rebuild` / `v15_vector_delta_rebuild`) | Mac `tools/v15_daily_selfimprove.sh`; S1 `tools/v15_autopilot.py` stage `collect` | Mac 05:00Z daily; S1 autopilot every 5 min, round-based | **BROKEN on the Mac**: fails on every run because `timeout: command not found`, since cron PATH has no /opt/homebrew/bin (log `/tmp/v15_selfimprove_cron.log`: 10-04, 10-05 and 10-06 all died at line 11; it has never finished). **HELD on S1**: autopilot cron is commented `#BUILDER_HOLD_20261006#`. Last aggregate on the Mac is `SPREADSHEETS/v15_avg_delta/v15_avg_delta_20261004.xlsx`; `*_latest.xlsx` is from 10-03 |
| 2 template defaults (`v15_daily_template_update.py --apply`) + `sync_default_surfaces` + cat_side_defaults_4 | same two scripts | same | Mac: never reached (step 1 dies first). S1 autopilot: held; even when it runs it writes **S1's** templates and only the sweep-only `data/sweep_defaults/cat_side_defaults_4.json` ("NO --sync-defaults", by design), and it is round-based (last round-end 2026-10-02 21:50Z; round run25 has been open for about 4 days). That makes two template writers on two hosts, which conflicts with "templates go Mac→servers" (§68.2.5) |
| 3 per-sym to live (`switch_parity.register_workbook_result`) | `v15_pilot._parity_register_at_done` at each workbook DONE, **on the server running the pilot** | continuous, not daily | **0 registrations fleet-wide.** No `data/parity_promotions.jsonl` exists on S1, s2, s5 or the Mac. The only two attempts (s2 NEM_LONG, RRC_LONG) were refused: `type-gate refused ALL ... *_VEC_ONLY_ENABLED: not a cat_side/config key`. Every fresh crypto final set also carries the 4 `*_VEC_ONLY_*` keys (all False), so the registrar refuses them too. Even a successful registration writes only the server's own `data/hourly_reconfig/per_sym_store.db` + JSON book (root = the folder holding switch_parity.py). **No job pushes per-sym settings from the servers to the live Mac books.** The only Mac-side applier was `tools/golive_final.py`, a one-shot crontab entry dated 10-05. It aborted ("no qualifiers file"), and its `--apply` lines are commented `#UNFREEZE_USER_20261004#`. `backfill_per_sym_store.py` also ran once (10-05 12:52/13:12) |
| 3b inf universe | `tools/v15_daily_inf_universe.py` (new) | Mac 13:05Z, DRY-RUN | Installed (see below). **Blocked for --apply by a competing writer**: running `ez_rankings.py` rewrites `symbols_inf_long/short.json` every ~2.5 min (`[INF_BEST_SAVE]`, ez_rankings.py:5632-5691) from the hardcoded `INF_CRYPTO_*_BEST_15` + `SPREADSHEETS/BEST` filename gains. It pads shorts with non-positive gains and currently lists GOOGLUSDT, which is excluded. A daily write would be overwritten within minutes |
| 4 live trades | ez_manage/tradier_manage | live | not part of this map |
| 5 next round | S1 `v15_fleet_scheduler` + autopilot `restart` | */2 + */5 | held (`#BUILDER_HOLD_20261006#`), on purpose for the NPZ rebuild. Herds still run from @reboot / herd_watchdog |

## Other findings that matter for the chain

1. **Every host's progress dir is `~/binance-sandbox/data/reports/lifecycle_pilot`** (S1, s2, s5 `~/v15_current_progress_dir.txt`).
   Autopilot `stage_restart` would normally point it at `~/v15_runNN_DATE/progress`, so someone reset it by hand.
   `~/v15_defaults_round.txt` says `run27_complete_20261003` on all hosts, while autopilot state says `run25` and the files carry
   `run25-jsonea38a414-…`. Round identity is not consistent.
2. **Pilots refused to run on s5** (`/tmp/sweep_*_30D.log`, 10-06 ~05:42Z): `[PARITY-GATE] hard default disparity —
   RECENT_REDUCTION_GUARD_ENABLED: bold=True global=True quick=False` (XMR/CHR/ATOM/ALGO/XTZ/COTI/DASH). Not re-checked after that time.
3. **switch_parity.py differs between Mac and fleet**: Mac md5 287d8d2a (08:37Z, adds verify-live-switches) vs S1/s2/s5 62bb7872 (04:29Z).
   The registrar code path looks unchanged, but the fleet was not redeployed. v15_pilot, v15_autopilot, v15_final_orch,
   v15_daily_template_update and v15_universe match on Mac and S1.
4. **A second template writer is still installed**: Mac `0 3 * * * tools/verify_template_defaults.py` rewrites template bold from config,
   which §68.2.4 forbids. Right now it crashes (`/usr/bin/python3` has no openpyxl), so it does nothing. If someone "fixes" its python, it becomes a live violation. Remove it.
5. **Dead Mac crons**: `*/10 /tmp/run_organize_best_v2.sh` and `*/30 /tmp/run_promote_incremental.sh` point at files that no longer exist.
6. **Timing vs open**: Mac `v15_universe.py --push` runs at 13:45Z and `v15_morning_report.py` at 14:15Z, both **after** the 13:30Z open.
   The weekly "final phase" (autopilot + `v15_final_orch`, Mon 06:00→13:30Z → `qualifiers.json` → `golive_final.py`) is disabled
   (`AUTOPILOT_FINAL_T0=2099-…` in the held cron line).
7. **Fresh full-set metrics** (gain/trades/TIM/DD of the final set) exist in progress JSONs only as `diagnose_repair.after`.
   Today 6 of 128 crypto sym_sides have them. Every other record has only `final_gain_fresh_vec` (gain only), so step 3/3b gates
   (TIM/DD) cannot be checked from server results for most sym_sides. `final_gain` can come from an older kept xlsx (e.g. ALGOUSDT_LONG
   30.93 from a 09-25 file vs a fresh 20.10), so it must not be used for ranking.
8. **ez_positions_service interaction (inf)**: `cleanup_positions` (every 180 s) builds `inf:` keys from the books, keeps removed keys for
   `PERSIST_INF`=4 h, keeps any key with an open position (amt≠0) and any inf active hedge, and **drops BOTH keys of a symbol listed on
   both inf sides with no open position unless the symbol is in winners/losers** (HEDGE PAIR CLEANUP). So new inf symbols become
   tradeable keys within about 3 min, and removed symbols without a position drop out after about 4 h.

## What a working daily chain needs (order, all before 13:30Z)

1. 05:00Z Mac selfimprove (steps 1+2): fix PATH (`PATH=/opt/homebrew/bin:$PATH` in the script or the cron line), add s5 to its push loop, and decide
   whether the Mac or S1 autopilot is the ONE template writer (today both are wired).
2. Step 3: a Mac-side daily per-sym applier that pulls the qualified final sets from the servers and runs `switch_parity.register_workbook_result`
   **on the Mac** (the live surfaces are there). Prerequisite: strip `*_VEC_ONLY_*` keys from templates/sets (§68.2.1), or the registrar refuses every set.
3. 13:05Z step 3b `tools/v15_daily_inf_universe.py --apply`. Prerequisite: ez_rankings.py stops writing `symbols_inf_*.json` (locked live file:
   needs a user unlock + restart).
4. Move `v15_universe.py --push` before the open.

## Step 3b wiring (installed 2026-10-06, dry-run)

Mac crontab (backup `backups/before_inf_universe_cron_mac_crontab_202610061707.txt`):

    5 13 * * * cd /Users/niels/Documents/binance && /Users/niels/Documents/binance/.venv/bin/python tools/v15_daily_inf_universe.py >> /tmp/v15_daily_inf_universe.log 2>&1 # V15_INF_UNIVERSE_DRYRUN

Tested under a cron-like env (`env -i`, PATH=/usr/bin:/bin): ssh to s1-pub/s2/s5 works. The proposed --apply form, once the director confirms
and the ez_rankings writer is retired:

    5 13 * * * cd /Users/niels/Documents/binance && /Users/niels/Documents/binance/.venv/bin/python tools/v15_daily_inf_universe.py --apply >> /tmp/v15_daily_inf_universe.log 2>&1 # V15_INF_UNIVERSE
