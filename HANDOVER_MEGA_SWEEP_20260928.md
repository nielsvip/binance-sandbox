# HANDOVER — get the 354 sym_side 30d sweep FINISHED and the gap/ratio rules LIVE
**Written 2026-09-28 ~16:00 UTC by the outgoing session. Successor: read this whole file, then
`~/.claude/.../memory/vigilance_guard_2026_09_28.md`, then CLAUDE.md. Everything here is verified
first-hand this session unless marked ASSUMED.**

## WHY THIS HANDOVER EXISTS (failure honesty)
The main task — all 354 sym_sides swept to 30d completion with `{SYM}_{SIDE}_bh{..}_gain{..}_30d_matrix.xlsx`
+ `*_30D_REAL_ZOOMABLE.html` published — is **0/354 complete** (283 partial = 54.5% of rows, 71 empty;
newest BEST files date Sep 18–19). Two failures let compute sit idle:
1. **s1's `v15_local_herd` has been effectively dead for ~12h+**: 26 sym_sides in its **in-memory**
   `stalled` set (from pre-fix pilot bugs) + `todo 13 launched+0` forever. Its own log says the remedy:
   "fix pilot then restart herd". The pilot IS fixed now; the herd was never restarted.
2. The outgoing session's permission mode **refused process kills** ("Interfere With Workloads") and it
   surfaced the restart commands too late and too quietly instead of escalating them as THE blocker.
Do not repeat: identify the single blocking resource first, escalate loudly, verify throughput every hour.

## CURRENT STATE (verified 2026-09-28 15:30–16:00 UTC)
- **Universe**: `SPREADSHEETS/V15_FULL_354.txt` (354 sym_sides). Completion via
  `tools/v15_progress_board.py: symside_status()` — DONE = all tabs full in progress JSON.
- **s1** (`s1-int`, 16 cores, crypto NPZ source): herd pid alive but STALLED (see above), cpu was ~20%.
  Running pilots: UUUU_LONG (herd) + USAR_LONG, MSFT_LONG, XLMUSDT_LONG (launched directly by me,
  logs `/tmp/v15_<SYM_SIDE>.log`). **ps shows each pilot's fork-pool workers with the parent's cmdline —
  15 identical `--sym-side X` rows = ONE pilot, not a stampede.**
- **s2**: saturated (load ~40/16, RAM ~2G free) by the OTHER session's mega sweep:
  `v15_mega_pilot.py` + `tools/dc_simple_8_sweep.py`, supervisor `tools/v15_mega_supervisor.sh`
  (cron-kept), progress env-marked `V15_PROGRESS_DIR=~/v15_mega_progress`. **DO NOT add pilots to s2.
  DO NOT pkill by broad `v15_pilot` patterns — you will kill their env-marked pilots.**
- **Peer coordination**: another Claude session **binance-99** owns the mega sweep
  (`SendMessage` to `uds:/tmp/cc-socks/94468.sock`). AGREED PROTOCOL: message binance-99 BEFORE any push
  of v12_quick_engine.py / config*.py / v15_pilot.py / tradier_manage.py / ez_manage.py / TEMPLATE_*.xlsx
  to s1/s2 (they checkpoint; mid-run engine changes flipped 84 crypto sheet baselines once already).
  Final requeue cut line agreed: the ~11:50Z 2026-09-28 push (DC4 build). A status/ETA request was sent
  ~15:45Z; answer may be pending their user's approval — re-ask if silent.
- **Live Mac**: 5× `ez_manage.py --account` (ang/fin/men/flz/inf) under `run_with_watchdog.sh` — still
  running PRE-vigilance code (never restarted). 2× `tradier_manage.py --accounts` — restarted 13:30Z,
  runs DC4 vigilance (17 closes at open ≈ −$642 realized, 21 blocks, recovery-unblocks confirmed firing)
  but NOT the `indicators_cache` fix (pushed to disk ~15:45Z, needs restart).

## THE PRIORITY QUEUE (in order; each has an acceptance test)
1. **Restart s1 herd** (user must run or approve: `ssh s1-int 'pkill -f v15_local_herd'` — a relauncher
   brings it back; singleton lock `/tmp/v15_local_herd.lock`). ACCEPT: `/tmp/v15_local_herd.log` shows
   `launched+N` (N>0) within 15 min and distinct new `--sym-side` pilots appear.
2. **Restart live tradier on Mac** (`pkill -f "tradier_manage.py --accounts"`, watchdog relaunches) to load
   the `indicators_cache` property fix (tradier_manage.py ~line 22810). ACCEPT next RTH morning 09:36 ET:
   `data/gap_inventory_tradier_per_symbol.json` mtime updates and `[GAP_MOC] morning rebuy` lines replace
   the "no indicators yet" loop. NOTE: until then, EOD MOC uses Sep-23-frozen per-symbol gap stats
   (`data/gap_close_inventory_tradier_per_symbol.json` is 2 bytes = empty — the close-gap (shorts) sentinel
   has NO data until first recording).
3. **Restart live crypto ez_manage on Mac** (`pkill -f "ez_manage.py --account"`) to activate crypto
   vigilance + the ULTIMATE_DC NOLOSS-bypass fix. WARNING: on restart the guard evaluates open losers
   (SNX/ADA/XLM-class) against dc_low4_15m and may close+block them, then auto-reenter on recovery — that
   is the user's mandate, tell them, don't ask twice.
4. **Throughput babysit until 354/354**: every ~hour run the progress-board count (see snippet below).
   If s1 herd re-stalls: the stall guard marks a sym_side STALLED only when a relaunch added no rows —
   with the fixed pilot that should no longer happen; if it does, read `/tmp/v15_<SYM_SIDE>.log` for the
   real error, fix the pilot, restart herd. Idle cores + nonempty todo = something is wrong; do not wait.
   ```bash
   ssh s1-int 'cd ~/binance-sandbox && .venv/bin/python - <<EOF
   import sys, pathlib, collections; sys.path.insert(0,"tools")
   from v15_progress_board import symside_status
   order=[l.strip() for l in pathlib.Path("SPREADSHEETS/V15_FULL_354.txt").read_text().splitlines() if l.strip()]
   c=collections.Counter()
   for s in order:
       st=symside_status(s)
       c["complete" if st["complete"] else "zero" if st.get("zero_trades") else "partial" if st["filled"]>0 else "empty"]+=1
   print(dict(c))
   EOF'
   ```
5. **Publish step**: completed sym_sides must become `*_bh{..}_gain{..}_30d_matrix.xlsx` + ZOOMABLE html in
   `SPREADSHEETS/BEST/{STOCKS|CRYPTO}_{LONG|SHORT}/`. Candidate tools (NOT verified this session — read
   before running): `tools/organize_best_spreadsheets.py`, `tools/publish_final_winners.py`,
   `tools/final_mark_and_365.py`. Confirm with binance-99 whose flow triggers publish, or wire a small
   post-completion hook. ACCEPT: fresh (today+) files in BEST dirs with bh/gain in the name.
6. **Friday-class overnight exposure**: intraday ratio loop (`intraday_ratio_rebalance_loop`,
   tradier_manage.py ~23006) runs ONLY while the process runs — Friday's process wasn't executing it
   (zero log lines all day) and the book closed 35k long/15k short. After restart it runs (verified today,
   tgt≈0.34). Consider an EOD assertion: last 30 min, log long_pct vs target; alert if |dev|>thr.

## WHAT IS ALREADY DONE (do NOT redo, do NOT revert — DEATH PENALTY rules apply)
- v12_quick_engine: synthetic-distinctness purge verified dead (AST-checked); vigilance vectorized
  (structural DC4 stop, NO fixed % — user forbade fixed %: `VIGILANCE_DC4_STOP_TF` default 15m,
  OFF/15m/1h/4h sweepable; `VIGILANCE_MAX_LOSS_PCT` REMOVED and verified inert); recovery-reentry with
  fresh-streak window; tradier DC-cooldown after ULTIMATE_DC.
- tradier_manage: PER_SYM flat-open gate (`_opens_exposure`, CHURN_FIX #1), DC hard-stop reopen cooldown
  (CHURN_FIX #2, `data/dc_hardstop_cooldowns_tradier.json`), vigilance guard + recovery
  (`data/vigilance_blocks_tradier.json`), `indicators_cache` property fix.
- ez_manage: crypto vigilance guard + recovery (`data/vigilance_blocks_ez.json`, full-symbol keys,
  NEVER via BLACKLIST_SYMBOLS — that strands positions); `'ULTIMATE_DC'`+`'VIGILANCE_DC4'` added to
  `UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS` (the 2026-09-19 mandate was a dead switch before).
- v15_pilot: `_merge_new_template_rows()` in clone_template's reuse branch — new template switches
  propagate into EXISTING sheets (idempotent, verified "+41 rows" on MSFT_LONG).
- Templates (all four): vigilance rows incl. `VIGILANCE_DC4_STOP_TF` OFF/15m/1h/4h; MAX_LOSS rows removed.
- All of the above compiled, md5-verified on s1-int AND s2 `~/binance-sandbox/` as of ~15:45Z.
- Backups: full snapshot `backups/system_snapshot_pre_overhaul_202609281000/` + per-edit
  `backups/before_vigilance*_202609281*`, `backups/before_dc4_rows_*`, `backups/before_template_row_merge_*`.

## LANDMINES (each cost this session time or money)
- **zsh**: `echo ===X===` fails (leading `=` triggers =cmd expansion). Use plain labels.
- **ps pilots**: fork-pool children share the parent pilot's cmdline (see above) — the herd's
  `running 15/32` counts them; don't "fix" the dedupe without understanding it caps RAM, not sym_sides.
- **ETHUSDC.npz on Mac is corrupt** (BadZipFile) — re-precompute or ignore; s1 copy may be fine.
- **Sheets store B-column bools as native (crypto) vs strings (stocks)** — match per-sheet style.
- **`data/per_trade_returns/*_20260928.jsonl` contains live fills**; Sep 28 is a MONDAY (market open) —
  don't misread live closes as backtest pollution (this session did, briefly).
- **Permission classifier** (auto mode) refuses: killing remote pilots/herds, killing live traders, heavy
  batch backtests on the Mac. Don't retry variants — hand the user the exact command as a `! ...` line.
- **The 30d window**: pilots run `--window-days 30 --vector-only`; parity verification is
  backtest_v12_engine on the same frozen NPZ (CLAUDE.md rules). Never report sweep numbers as live-verified.
- **Live tradier logs**: `~/logs/tradier_manage_general_{trb,trc,tra}.log`; vigilance state files in
  `data/vigilance_blocks_*.json` (delete a key = manual unblock; recovery auto-unblocks on
  price>exit or wt15m bounce + stoch confirm).

## DEFINITION OF DONE (the user's words)
All 354 sym_sides at full tabs for 30d, settings optimized per sym_side, bh/gain-named xlsx + charts
present and fresh in BEST dirs, live systems running vigilance + gap/ratio rules, and every new switch
(VIGILANCE_*, DC cooldown, flat-open gate) carrying real per-sym_side deltas vs previous settings.
