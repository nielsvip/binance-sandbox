# ALL DISASTERS 2026-10-19 — live inventory (user-ordered, nothing dropped)

Legend: ✅ fixed+deployed · 🔄 fix shipped, rollout pending · 🆕 open · ⏳ blocked on time/lease.
S1 live = `~/binance` on niels. Mac = `/Users/niels/Documents/binance`. Times UTC.

## P0 — venue / trading now
1. 🆕 **MAC FENCED, S1 HOLDS LEASE — user: Mac ALWAYS first.** Mac wrappers log `[FENCED] S1 holds the lease` every 15s. Failback to Mac pending Mac-health proof (broker 200 OK 20:05, data file fresh 20:05, but klines breaker was holding till ~19:44 and indicators re-warming). Next: prove Mac data fresh → quiesce S1 engines → flip lease → Mac spawns → verify Mac SUCCESS.
2. ⏳ **S1 .52 BANNED till ~01:23 UTC Oct 20 (-1003, escalating all day).** Rotation to .216/.233 with REAL durations deployed (was: every ban marked 900s, re-banned in 15 min). Trading on S1 possible via clean IPs once writers/engines fully bounced.
3. 🆕 **Mac IP banned ~19:26 (~18 min) by klines startup burst** (`fetch_all_max_historical` re-downloaded years for 197 syms × 5 TFs on every restart). Lifts ~19:44. Same burst would re-ban on every klines restart → gated (see 7).
4. 🔄 **ENTRY TIMEOUT STORM.** 34.5k/26.9k/19.2k `BLOCKED_DISPATCH_TIMEOUT` (Mac), 58/58 (S1), zero successes. Causes: 120s budget vs 44.7s serialized twin evals; owned-poisoning (`owned_drop` zero callers); held keys burning serial budget ahead of flat keys. Fixed: `take_plus` (1 acquisition), `owned_fresh`, ENTRY flat-first, **timeout_s=None (entries NEVER time out)**. Deployed S1, fleet bounced (fin landing).
5. 🆕 **ORCHESTRATOR shared-memory connect failure** (reported 20:06, investigating next).

## P1 — data pipeline (wrong data = wrong trades)
- ✅ **15GB sync skip REMOVED** (`scripts/rsync_helpers.sh`: remote skip → always-sync warn-only; local size-cleaner that DELETED oldest-first → warn-only. Backup `backups/before_always_sync_202610192025_rsync_helpers.sh`). The cleaner was a prime delete-loop suspect.
6. ✅ **S1 live missing `published_selection.json`** → all S1 entries refused fail-closed. Backfilled sandbox→live md5-OK.
7. ✅ **`fetch_klines_api`: TOP-UP ONLY** (`startTime`+small limit; full 1500 cold-start only). Was: 1500 bars for a 1-bar gap.
8. ✅ **Startup max-history gated** to below-MIN-depth files + breaker-aware (was: unconditional multi-year redownload every restart).
9. ✅ **"Marked maxed (1 bars), won't retry 168h".** Root: top-up fetches return 1-3 bars → old condition maxed EVERY fresh file. Now: maxed only when file complete (≥threshold) with nothing newer. (168h gates dead code — `is_symbol_maxed` has no callers.)
10. ✅ **ez_prices CLIP loop.** Real destroyer = the MERGE (concat+coerce+dedup dropped 1000+ valid rows, BSV 4h 1766→540) → shrink → refetch → bans. Merge now APPEND-ONLY (existing rows untouched, shrink = hard block, floor = min(1200, existing) so young listings pass). Cleanup: S1 NEVER clips; Mac 4h/D floors raised to (1200,1800). 535 thin Mac files pulled from S1 (535→526; rest are young listings complete since listing — correct as-is).
11. 🆕 **15GB sync skip** (`Remote ... exceeds 15GB, skipping sync`). User: ALWAYS SYNC. Find guard, remove skip (keep warning).
12. 🆕 **Stale indicators blocking entries** (`STALE_INDICATORS 91m`). Feeds re-bounced; watch warmup.

## P2 — supervision / infra
- 🆕 **S1 OOM: 30/30G used, 0 free.** Top: flz 3.9G, men 3.7G, inf 2.9G, fin 2.4G, ang 1.8G + rankings. OOM-killer prime suspect for indicators crash-burst (20:52-55) and flz/fin deaths. Triage: killed `tradier_rankings.py` (2.5G, stocks closed; RESTART pre-open Tue: `setsid nohup /home/niels/.conda/envs/binance_env_mac/bin/python /home/niels/binance/tradier_rankings.py >> ~/logs/tradier_rankings.log 2>&1 </dev/null &`). Engines' RSS climbs for hours — leak watch open.
- 🆕 **Orchestrator shm (20:06): NOT a fault.** Server (ez_share_ind.py:97452) healthy, heartbeat unbroken; symptom was a 53s planned-restart outage, self-recovered. No change.
13. 🆕 **Engines dying on their own** (flz+fin died ~18:12 uncommanded; stocks trb/trc crash-loop every ~10 min). OOM? ban-crash? NOT investigated yet. My own bounces are logged separately — do not confuse.
14. 🆕 **Supervisor loop starves crypto**: synchronous `s1_failover_start.sh` (up to 240s gate) blocks all later accounts; stocks crash-loop makes it chronic. Single-flight added (active next supervisor start — never signal it: TERM trap kills all traders; HUP restarts unit via cgroup kill).
15. 🆕 **S1 lease file 3.5h stale** (14:39) + Mac `lease.json` empty; tunnel/SSH flapping (banner timeouts on all new handshakes — use `cm-pulse-s1` master, split pgrep patterns or supervisor sees phantoms).
16. ✅ **`klines_append --all` UNPAUSED 2026-10-20 01:26Z** (crontab backup `~/binance-sandbox/backups/crontab_s1_before_unpause_klines_append_202610200126.txt`). The pause froze `klines_cache_backtest` (= crypto `CRYPTO_KLINES`) at 10-19 17:45Z → every crypto NPZ fed only by the lagging canonical push → 75/75 crypto sym_sides `tail-stale`, CRYPTO done30=0, S1 0/6 slots. `klines_append.py` makes NO REST calls (merges `klines_cache/gateway` + macbook + canonical), so it never needed a ban pause. Also: `tools/npz_bar_refresh.py` crypto finality now reads `klines_cache_gateway` + `klines_cache_macbook` too (`_crypto_final_dirs`, md5 f389974b, S1+S2).
17. ⏳ **Post-lift verification**: `OPEN -> SUCCESS`, zero `BLOCKED_DISPATCH_TIMEOUT`, `fixed_stop_audit.py` 0 findings (now: 5/5 BROKER_UNREACHABLE = unverifiable, not missing-stop).

## Shipped this session (deployed S1, tests green)
- `take_plus`, `owned_fresh`, ENTRY flat-first + no-timeout, board backfill, holders live-mirror, ban real durations, klines top-up + history gate. Tests: +4 owned, +2 take_plus, +3 dispatch (incl. no-timeout), +4 ban-cooldown = 19 green; parity file has only its 2 pre-existing `ez_manage`-text failures (file untouched).
- **SELF-INFLICTED**: `kill -HUP` on supervisor restarted the unit → cgroup stop killed 5/5 engines ~4 min (during ban; stops persisted; rebuilt 5/5).
- Backups: `backups/before_*` + `backups/LOCKED_FILES_row_20261019_entry_dispatch_rescue.md` (LOCKED_FILES.md itself FS-locked, row staged).
- Reverted: socket-bind pin (fought the sanctioned 3-IP rotation; rotation + real durations is the fix).

## Round 2 — 22:3x–23:0xZ (Claude, under USER "audit its changes unlock where necessary if there are any pitfalls")

18. ✅ **ROOT CAUSE of #1 (Mac fenced, S1 holding the lease) — the freshness probe measured an HOURLY BACKUP.**
    `live_failover_common.newest_own_snapshot_age` scanned only `data/market_data_<ts>.json`, which `ez_indicators`
    writes once an hour as `backup_file` (and never at all when `WORKER_TOTAL_INSTANCES > 1`). Against
    `SNAPSHOT_STALE_S=90` that made the Mac read **stale for ~98% of every hour** with klines 0 s behind and zero
    ban lines — measured 22:27Z `snapshot_age=102s` as the SOLE reason, while `latest_market_data.json` (the file
    every engine actually reads) was 79 s old. `healthy` in `trading_failover` was therefore unreachable and the
    Mac could never get its own lease back. Fix: the probe now counts `latest_market_data.json` too and the
    FRESHEST wins (a genuinely stalled producer still ages both, so nothing is hidden). **Mac re-took the lease at
    22:37:55Z, gen 60, `trading_procs` 0 → 17.**
19. ✅ **The eager 90 s producer bar no longer holds the TRADING lease hostage.** New `TRADING_SNAPSHOT_STALE_S=420`
    + `trading_stale`/`trading_reasons` in both `measure_crypto`/`measure_stocks`; `venue_reading` passes it through
    (absent key → falls back to `stale`, fail-closed) and `healthy` uses it. 90 s stays the DATA bar, because
    starting an S1 producer is cheap and reversible. Measured normal cadence of the live snapshot: 60–140 s.
20. ✅ **`failback.blocked_by` told the operator nothing.** It recorded only `nt_left`, so a lease blocked purely by
    a data reading or an absent heartbeat reported `blocked_by: []` with `healthy_since: null` (exactly the 22:2xZ
    state). It now names every blocker: heartbeat age, per-venue data reasons, per-venue not-trading kinds.
21. 🔴 **`LF_FAILBACK_S=60` MUST NOT be set — already tried and measured as the flap.** Source default aligned
    600 → 300 to match S1's pinned unit value instead. Per `takeover-timings.conf` (2026-10-17): at 60 s the lease
    was yanked before either host finished starting a venue (`s1_trading_supervisor.sh` starts one account per 20 s),
    gen 23 → 39 in ~100 min and **twice BOTH hosts sat at 0 engines**. The hand-back cannot be faster than venue
    startup; making startup faster is the only real lever. `FAILBACK_NT_S` split out at the same 300 s so the
    whole-host arm can be shortened later without touching the nt arm (whose evidence is hidden behind the fence —
    a fenced trader places no orders, so its kinds stop firing and the venue reads "healthy" merely by being idle).
22. ✅ **LIGHT/NORMAL/EXTREME auto-selection was DEAD since the 2026-10-18 02:35 reboot (~44 h).**
    `data/market_mode.json` was a symlink into `/tmp/binance-monitoring/` (made 2026-09-30 00:07); macOS wipes `/tmp`
    on reboot. `ez_rankings.trigger_market_mode` wrote the file BEFORE `Config.set_market_mode`, so the resulting
    `[Errno 2]` ALSO killed the in-process application — logged once a minute through 20:05Z. `ez_manage`'s monitor
    found no file and kept `config.py`'s pinned `LIGHT_MODE` forever. Fixes: durable `data/` path in `config.py` +
    `ez_news_scanner.py`, apply-then-publish ordering, atomic write that unlinks a dangling symlink,
    `ez_rankings.py` added to `crypto_watchdog_cron.sh` (its ONLY lifeline was an iTerm tab whose watchdog shut down
    20:05:23 with nothing to resurrect it), and `run_with_watchdog.sh` `ez_rankings` no-output 300 → 900 (it was
    SIGKILLed at 305 s mid-init at 22:57:05Z having never reached the monitor loop). **This gates live size**: with
    `CRYPTO_MINIMUM_TRADING_MODE=False` the per-sym sizing ceiling IS the active mode table, so 329 crypto sym_sides
    ran at LIGHT (START 25 / MAX_POSITION 140 / MAX_ORDER 120) instead of a computed mode for 44 h.
23. ✅ **In-code warm-up heartbeat landed in `ez_indicators.py`** — the permanent fix
    `tools/ez_indicators_warmup_guard.py` documented as owed but could not apply (locked). `[WARMUP] <tf> n/N`
    every 25 symbols inside `_process_timeframe`'s gather. Removes the 120 s bogus-kill trap (34 of 38 kills in six
    hours were "No output for 12Xs" against a healthy process, each restart discarding the warm-up).
24. 🆕 **STILL OPEN — the warm-up BLACKOUT itself.** `_save_loop` is created only AFTER all five timeframes finish
    (`ez_indicators.py` `run()`), so the snapshot does not advance for the whole 25–47 min warm-up; measured again
    at 22:54Z (`tfs_since_restart=[]` 13 min in, snapshot 864 s stale). Making it advance during warm-up means
    publishing PARTIAL indicator sets to live engines — not a call to make blind on real money. Needs a decision.
25. 🆕 **THE MAC IS OVERLOADED, and that is why it keeps losing its own venue.** Measured 23:0xZ: load average
    **82.75 / 76.43 / 77.24**, swap **5.49 GB of 7.17 GB used**, ~700 MB free of 36 GB, 63 project python processes,
    14 interactive sessions. Top non-trading consumers: `UVFSXPCService` 97%, browsers (Opera + Edge + Chrome +
    WebKit) ~90% combined and ~6% RAM, `SkyLight` 49%, `Finder` 36%. No herd/pilot runs here (checked). Every
    producer stall and every S1 takeover today sits downstream of this.
26. 🆕 **LATENT double-manage on stocks.** `tradier_manage.py` with no `--accounts` defaults to `["trb","trc"]`
    (`tradier_manage.py:60703`), and the crontab's `1 13 * * 1-5 run_with_watchdog.sh tradier_manage.py`
    (`# V15_TRADIER_OPEN`) passes none, while `crypto_watchdog_cron.sh` separately keeps `--accounts trb` and
    `--accounts trc` alive. The singleton guard deliberately does NOT match across those arg patterns
    (`run_with_watchdog.sh:122-128`) and the watchdog singleton lock is keyed per-account, so neither stops it.
    It has not fired — only because 13:01Z is before `TRADIER_MARKET_GATE`'s 13:30Z open, so the wrapper exits 0.
    Verified today: every start carries an explicit `--accounts`. The line is dead weight; if it ever runs later it
    is two managers on trb and trc — the documented FIXED_STOP-war / double-close shape. Recommend deleting it
    (not done: operator's crontab, and this repo has wiped a crontab by editing it remotely before).
27. 🆕 **Crosses feed dead since 2026-09-30 16:15.** `config.CROSSES_FILE`/`LAST_EVENTS_FILE` still point at
    `/tmp/binance-monitoring/last_events.json` (wiped); `ez_rankings` logs `Crosses file does not exist` every
    start. Deliberately NOT repointed at `data/last_events.json`: that copy is 19 days old and no Python writer of
    it exists, so repointing would feed stale cross events into rankings instead of erroring honestly.
28. ✅ **`tools/per_sym_live_parity_audit.py` was under-reporting the live sizing ceiling** — it printed the flat
    `CRYPTO_MIN_SIZE_CAPS` literals ("→ 6.0", "→ 12.0") although `per_sym_live_set._cap_table()` became mode-aware
    on 2026-10-17. It now prints the EFFECTIVE ceiling and names its source. Nothing live changed; the report did.
29. ✅ **per_sym → live verified clean.** `tools/per_sym_live_parity_audit.py`: **0 of 753** live rows differ from
    their newest published board, **0 of 329** crypto rows resolve off their board, reader resolves all of them.
    Only breaches are 6 sym_sides traded in 7 d with NO board at all (`WUSDT_LONG` on ang/inf/men, `LRCXUSDT_LONG`,
    `VUSDT_LONG`, `trb/LEXX_SHORT`) — those run on cat_side/global defaults.
30. ⚠️ **Two `muse` sessions were editing the same files during this work** (pids 88756, 25795; `_mac_fence_acked`
    landed in `tools/live_failover_s1.py` mid-edit). All edits here were surgical string replacements, never
    whole-file writes, and Mac↔S1 md5 parity was verified afterwards on all six touched shared files.

## Round 3 — 23:2x–23:4xZ (USER: indicators→S1, where the disk went, best-data-trades, overlap)

31. ✅ **THE DISK: 58.5 GB reclaimed, 30 GB → 89 GB free (94% → 80%).** `backups/` held **68 copies of ONE 1.2 GB
    SQLite store** (`before_h2h_{crypto,stocks}_<stamp>.db`) = **79 GB**, written hourly by the `:25`/`:55` h2h
    cycles (`tools/v15_h2h_cycle.sh:94`) with no retention at all. `tools/backup_retention_prune.py` had already
    diagnosed this on 2026-10-18 ("96.3 GB was 79 hourly copies of one 1.2 GB SQLite store") and **was never
    scheduled** — the tool existed, the cron line did not. Ran `--apply` (keeps newest 3 per group + anything
    <6 h + the last snapshot of every day, never a sole copy; audit JSON in `data/reports/backup_retention/`) and
    installed `# BACKUP_RETENTION_PRUNE` hourly at `:45`, between the two h2h cycles. This is the missing ~65 GB.
32. ✅ **Mac → S1 live indicator snapshots, continuously** (USER "ez/tradier_indicators rsync to s1 so when it has
    to take over it has all data (incl positions)"). `tools/mac_market_data_push_s1.sh` existed since 2026-10-15
    for this request but pushes only the TIMESTAMPED snapshots — which are ez_indicators' HOURLY backups — so S1's
    seed was up to an hour stale by construction. New `Agent.push_snapshots` (`LF_SNAP_PUSH_S=120`, `-z`) stages
    `data/latest_market_data.json` + `data/tradier/tradier_indicators_latest.json` into
    `mac_state/root/data/` every tick while the Mac holds the lease, so `merge_mac_state`'s EXISTING
    newer-mtime-wins rule hands S1 the Mac's snapshot at takeover only while the Mac's is genuinely fresher — which
    is "the system with the best data should trade", expressed as data. **Verified: 8.4 MB staged on S1, mtime
    preserved so the comparison is honest.** Positions were already correct and are untouched: `merge_mac_state`
    never copies `*_positions.json` (broker is the only source, USER law) and writes identity seeds so held
    `opened_at` carries.
    Measured need, with S1's crypto producers ACTIVE: S1's own `latest_market_data.json` was **2838 s** old and its
    `tradier_indicators_latest.json` **7780 s** old, because S1 runs the same `ez_indicators` and was mid-warm-up.
33. ✅ **30-minute data overlap on S1** (USER "maybe we should have a 30min overlap if you think that is what it
    takes?" — yes, and the number is S1's own warm-up). `LF_RECOVER_S` 180 → **1800** in
    `takeover-timings.conf`, daemon-reloaded, watchdog restarted, verified in the effective environment. At 180 s
    S1 stood its producers down 3 min after the Mac looked fresh; if the Mac then relapsed — and today it relapsed
    every few minutes — S1 had to cold-start a 25–47 min warm-up with NO data. Detection stays at 90 s, so this
    widens only the stand-DOWN, never the reaction.
34. ✅ **`ez_indicators` kill-loop is BROKEN, proven live.** The `[WARMUP] <tf> n/N` heartbeat emits every 1–2 min
    (`15m 75/199 … 199/199`, 23:20:55→23:28:56Z), and the last bogus kill was **23:06:39Z — before the fix
    landed**. The process has now run unkilled since 23:06:48 and is completing a full warm-up for the first time
    today. NOTE: the `SCRIPT RESTART` banner in the application log is import-time noise, not a restart — judge
    restarts only from `ez_indicators_watchdog.log`.
35. 🆕 **SHARED MEMORY: 294–328 ESTABLISHED connections on 127.0.0.1:50005, 150 threads in the server.** The
    warning fired **3785 times today** with no cause attached because the real exception was captured into a local
    and discarded. Now surfaced (`shared_mem_safe.last_error()`), and the diagnosis is measured: TCP accepts in
    **0.00 s** and a FRESH client completes `connect()` + `get_store()` + `health_check()` fine, so nothing is
    dead — the handshake stalls on a saturated server. Cause is architectural: a `BaseProxy` opens ONE CONNECTION
    PER THREAD, and the proxy is used from short-lived asyncio worker threads, so each long-lived client
    accumulates connections (`ez_manage --account fin`: 26 connections in 29 min of life). **Deliberately NOT
    "fixed" by restarting the server or rewriting multiprocessing internals on a live trading box** — the hot path
    has documented fallbacks (Redis / market_data files) and the server answers fresh clients. The real fix is
    thread-local proxy reuse or a server-side connection cap; it needs its own scoped change.
36. ✅ **`# V15_TRADIER_OPEN` cron line deleted** (USER "delete if necessary"), commented out in place with the
    full reason. Crontab backed up to `backups/crontab_before_prune_and_tradier_open_202610192326.txt` (139 lines,
    non-empty-verified before install per the 2026-10-13 crontab-wipe lesson); new crontab 147 lines, all key
    lines spot-checked present.
37. ℹ️ **On Mac load: the USER's call is that the Mac has ample RAM to run both ez_ and tradier_, and it does
    (36 GB).** Item 25 stands as measurement, not as an argument to move trading: the actionable part of it was
    the DISK (item 31), now fixed. While crypto indicators are stale the engines refuse ENTRIES
    (`STALE_INDICATORS`) rather than trade on bad data — exits are never gated — so a warm-up blackout costs
    opportunity, not correctness.

## Post-lift checklist (~01:23 UTC)
- [ ] S1 .52 unbanned (no fresh `banned until` lines) · [ ] unpause klines_append · [ ] audit 0 findings · [ ] SUCCESS lines flowing · [ ] Mac failback if healthy · [ ] close 9/10/11/13/14/15.
