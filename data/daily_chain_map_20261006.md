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

## S1 daily chain installed (2026-10-06 ~17:45Z, director design A-F)

Steps 1+2 of §68.3 now run on **S1** (the Mac sleeps; S1 does not):

    S1:  0 13 * * * cd /home/niels/binance-sandbox && flock -n /tmp/v15_daily_chain.lock bash tools/v15_daily_chain_s1.sh >> /home/niels/logs/v15_daily_chain.log 2>&1 # V15_DAILY_CHAIN_S1
    Mac: 20 13 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin /bin/bash /Users/niels/Documents/binance/tools/v15_daily_chain_mac_apply.sh >> /tmp/v15_daily_chain_mac_apply.log 2>&1 # V15_DAILY_CHAIN_MAC_APPLY

- `tools/v15_daily_chain_s1.sh`: rebuild (`V15_FLEET_HOSTS=tools/fleet_hosts_final.json`, 127.0.0.1/10.0.0.4/10.0.0.5) → template writer on
  SPREADSHEETS then TEMPLATE_FINAL_NORM with `V15_TEMPLATE_SYNC_SURFACES=0` (S1 never edits config.py / config_tradier.py / v12_quick_engine.py;
  the promoted keys land in the report as `promoted_keys`) → build_cat_side_defaults_4 + sweep copy → `v15_possym_json_from_agg.py` →
  zero-delta watchdog (non-fatal) → push to s2/s5 + md5 verify + kv sync → `data/daily_chain/<date>.json` (FAILED stamp `<date>.FAILED.json`).
  `DRYRUN=1` = rebuild into /tmp + writer report-only.
- `tools/v15_daily_chain_mac_apply.sh`: requires S1's DONE stamp, pulls the chain state md5-verified against it, kv-syncs, runs
  `switch_parity.py sync-defaults --keys <promoted_keys> --apply --confirm-unlocked` on the Mac (backup + compile + import), pushes changed code
  files to S1/s2/s5 (sandbox + ~/binance) md5-verified, logs `switch_parity.py gate` for the 4 cat_sides on Mac/S1/s2/s5. Restarts nothing.
- `tools/v15_state_kv_sync.py`: the template writer and `cat_side_defaults._load` read SQLite `kv_json` FIRST and the JSON only as fallback,
  so a copied JSON alone changes nothing while a stale kv row exists. Found today: S1's kv rows for `cat_side_defaults_4` and
  `cat_side_promotions` were from 16:25Z (older than the 17:32Z JSON push) → S1 pilots were reading the pre-chain cat_side defaults. Fixed
  in the state move; every chain push/pull now runs the kv sync on the receiving host.
- Disabled on the Mac (tag `#REPLACED_BY_S1_CHAIN_20261006#`): the 05:00Z `v15_daily_selfimprove.sh` line and the 03:00Z
  `verify_template_defaults.py` line (forbidden second template writer, §68.2 #4).

## Still missing from the chain (NOT implemented — open items)

1. **Step 3, per-sym settings go-live: built, DRY-RUN only (added 2026-10-06 ~18:10Z, coordinator scope addition).**
   `tools/v15_persym_golive.py` runs as Mac apply step 5b. It reads the rebuild's one-latest-file-per-sym_side selection, freshly evaluates
   each `cumulative_overrides` on the server that holds the NPZ (30D, `prepare_batch` + `evaluate_prepared_sanitized`), maps the renamed
   `*_VEC_ONLY_*` keys (CRYPTO_VEC_ONLY_REENTRY to CRYPTO_REENTRY_PATHWAYS, HAIKU_WINNER_VEC_ONLY to HAIKU_WINNER_AUGMENT,
   KEY_LEVEL_CRASH_VEC_ONLY to KEY_LEVEL_CRASH_EXIT, KG_STOCKS_LIVE_GATE_VEC_ONLY to KG_STOCKS_HARD_VETO; EMA50 VEC_ONLY dropped), and gates
   with `switch_parity.register_workbook_result(dry_run=True)` + the 365D verdict + no SIMPLE_PRICE_GT0. The report is
   `data/daily_chain/persym_golive_<date>.json`. `--apply` (the registrar's real writer) is NOT wired into cron and waits for director/user
   confirmation. First report (20261006, director's selection): 543 selected, 457 evaluated, **18 PASS**, 394 not qualified on the fresh
   eval, 19 SIMPLE_PRICE_GT0, 32 type-gate (RECENT_REDUCTION_GUARD_ENABLED, AUGMENT_MIN_GAIN_PCT, ...), 4 failing a 365D verdict.
   **Revised ~18:50Z (director decision):** registration is now `diff_only` (new `register_workbook_result(..., diff_only=True)`): only keys
   that differ from the effective default (cat_side_defaults_4 > venue global) are pinned per-sym, so later daily promotions still reach them.
   The 32 type refusals are fixed: keys missing from cat_side_defaults_4 are now typed against the venue global (RECENT_REDUCTION_GUARD_ENABLED,
   AUGMENT_MIN_GAIN_PCT, stocks REENTRY2_DC_BREAK_FILTER_TF); non-live keys (WT_DC_DETAILED_TF, dict-typed EZ_MANAGE_THROTTLER_RATE) are dropped
   when they sit at the QuickConfig default; a float for a live-int field is kept as float when QuickConfig declares it float. Re-run: 31 PASS,
   0 type refusals, 401 not qualified on the fresh eval. The fresh 30D numbers moved between the two runs ~40 min apart (e.g. AAVEUSDC_LONG
   +25.98%/TIM 24 to +3.38%/TIM 11.6 for the same set), so the fleet NPZ/engine changed in between. Registering replaces the whole book entry,
   so today's `clean_ONLY_CROSSES_v33` keys that are not in the diff-set fall back to cat_side defaults.
2. **Step 3b, inf universe** (`tools/v15_daily_inf_universe.py`, Mac 13:05Z, dry-run only). Blocked for `--apply`: the locked live
   `ez_rankings.py` rewrites `symbols_inf_long/short.json` every ~2.5 min (`[INF_BEST_SAVE]`), so a daily write would be overwritten within
   minutes. Needs a user unlock of ez_rankings.py to retire that writer (+ restart), then flip the cron to `--apply`.

## Tradeable-only universe + open positions + tradeable check (2026-10-06 ~19:30Z, USER ruling)

- `tools/v15_universe.py` `tradeable_sym_sides()` now ADDS every sym_side with an open position (`data/open_position_sym_sides.json`), after the
  `data/universe_exclude.json` subtraction (an excluded symbol such as GOOGLUSDT comes back only while it has an open position). Today it adds
  AVGO_SHORT, RRC_SHORT, USAR_SHORT, giving 263 sym_sides. Today's universe was rebuilt and pushed.
- `tools/v15_open_positions_push.py` (Mac cron `*/5 # V15_OPEN_POSITIONS_PUSH`): {flz,men,ang,inf,fin,tra,trb,trc}/{long,short}_positions.json,
  |positionAmt|>0, atomic write + rsync to S1/s2/s5 with md5 verify. An unreadable positions file aborts the run and keeps the last good file.
- `tools/v15_tradeable_check.py` = S1 chain step 6b (non-fatal; included in the stamp): every tradeable sym_side's latest sheet is re-evaluated fresh at 30D;
  SYSTEM_ERROR when there is no sheet, the eval fails, trades < 10 or TIM < 20%. First run (dry, today's selection): 263 tradeable, 40 OK, 223 SYSTEM_ERROR
  (no sheet 53, zero trades 89, TIM<20 44, set incompatible with engine 15, trades<10 13, both 9); 26 of the 36 open positions are SYSTEM_ERROR.
  Report: data/daily_chain/20261006_tradeable_check_DRYRUN.json.
- Timing gap: the Mac `v15_universe.py --push` cron is still 13:45Z (after the open and after the 12:15Z chain), so the chain reads the previous day's
  universe plus the fresh 5-min open-position file.
