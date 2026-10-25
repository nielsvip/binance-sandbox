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
and the `VEC_LIVE_EXECUTOR` shard stay on. A `NPZ_REARM_MON` cron line on each
box uncomments the lanes Monday 12:00Z (08:00 ET premarket) before RTH.
Pre-suspend crons: `backups/cron_20261025_s5_slowrun.bak`,
`backups/cron_20261025_s6_slowrun.bak`.
