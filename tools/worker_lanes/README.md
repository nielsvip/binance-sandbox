# worker_lanes — persistent home of the per-worker backtest/NPZ/STATE lane scripts

On 2026-10-25 the s2/s5/s6 workers ran their whole autonomous loop (quarter-hour
klines→NPZ→STATE lane, 2-min self-dispatch of slow 30D boards, NPZ inbox install)
from scripts that lived ONLY in `/tmp` (`self_dispatch.sh`, `overload_run.sh`,
`npz_quarter.sh`, `npz_shard_wrap.sh`, `qstate_all.sh`, `install_npz.sh`,
`s6_univ*.sh`). A reboot wipes `/tmp`, which silently kills every lane on that
worker until a human notices — exactly the "server idle while billed" failure.

This directory is the persistent source of truth. Rule:

1. Edit here on the Mac, never in a worker's `/tmp`.
2. Deploy with `tools/fleet_code_sync.sh`-style rsync to
   `~/binance-sandbox/tools/worker_lanes/<host>/` on the worker, md5-verify.
3. Worker crontabs reference the persistent path, never `/tmp/*.sh`.

Layout: one subdir per worker (`s2` = CRYPTO_LONG, `s5` = STOCKS_LONG,
`s6` = STOCKS_SHORT). Files are VERBATIM copies of what each worker ran on
2026-10-25 ~04:00Z (md5s match the live files). Per-host tunings are DELIBERATE
and must not be unified without measuring first:

- `overload_run.sh`: s5/s6 carry `FORCE_DC_RERUN=1`, s2 does not.
- `npz_quarter.sh`: s2 pushes refreshed NPZs to the s6 inbox as well
  (cross-feed); s6 honours `NPZ_QUARTER_NO_REFRESH=1` (its `NPZ_SHARD_REFRESH`
  cron owns the refresh, the quarter lane only pulls klines + recomputes STATE
  + pushes). s2 calls `npz_bar_refresh.py` directly; s5/s6 go through
  `npz_shard_wrap.sh` (lane shard + dispatch-queue syms).
- `s6_univ*.sh` (s6 only): rebuild the dispatch window from local progress
  files. Append-only in effect — never drop a side that has no progress file
  yet, or newly seeded symbols starve.

Not persisted here on purpose: one-shot kill/relaunch/debug scripts
(`s6_kill.sh`, `s6_relaunch.sh`, `s6_fix2.sh`, `s6_trig.sh`, `wp1_*.sh`, ...).
They stay out of cron and out of this directory.

2026-10-25 SLOW-RUN (weekend): on s5/s6 the `NPZ_QUARTER`,
`NPZ_SHARD_REFRESH` and `NPZ_INBOX_INSTALL` cron lines are commented with
`#SLOWRUN_20261025#` — stocks print no new bars on weekends, so the refresh
lanes only stole CPU from the boards. `SELF_DISPATCH`, the vec registry sync
and the `VEC_LIVE_EXECUTOR` shard stay on. (The Monday re-arm line from this
experiment was removed when the lanes were restored — see CORRECTION below.)
Pre-suspend crons: `backups/cron_20261025_s5_slowrun.bak`,
`backups/cron_20261025_s6_slowrun.bak`.

2026-10-25 ~04:55Z CORRECTION: the lanes are BACK ON everywhere — suspending
anything on billed boxes was wrong. NPZ calc stays on (gap-healing has real
resolution value for unstarted sides); throughput comes from the boards, and
the measured pace at full saturation is ~12 finishes/hr. Self caps raised for
the push: s5 `8 10 -> 8 12`, s6 `28 30 -> 28 36` (direct cron args, no script
change). The Monday 12:00Z re-arm line was removed again with the re-enable
nothing left suspended, nothing to re-arm.

2026-10-25 22:50Z current dispatch contract:

- `fleet_hosts_final.json` assigns s1 crypto SHORT, s2 crypto LONG, s5 stock
  LONG, and s6 stock SHORT. Both the central scheduler and these local launchers
  set `V15_PAIR_MODE=0`; a pilot must not calculate the other host's side.
- Central and local launches share `/tmp/v15_fleet_launch_v2.lock`. The former
  forks the pilot and closes both launch lock descriptors; every worker
  `overload_run.sh` closes inherited descriptors before `nohup`. Locks cover
  counting and spawning only. v2 retires a legacy lock held by pilots started
  before the fix. Do not mix v1 and v2 launchers. The central scheduler counts
  every observed pilot, including locally dispatched symbols outside its
  tradeable universe, against the four symbol cap and in its slot snapshot.
- Local self-dispatch filters to its configured side. s2/s5/s6 read progress
  once per file change, cache `final_gain` and `npz_id.ts_last`, and use the
  existing 48-bar cooldown to avoid recomputing a current completed board.
  The local cache is disposable; progress JSON remains the source of truth.
- The central scheduler still enforces memory, swap and disk admission. At
  22:48Z s1 had 6.3 GB available with a 6 GB reserve and a 6 GB estimated
  board; s2 had 4.0 GB available with a 4 GB reserve and the same board
  estimate. Four boards per host cannot be guaranteed on those 32 GB boxes
  while their live data services occupy memory. Do not force slots past the
  memory gate to satisfy a CPU percentage.
