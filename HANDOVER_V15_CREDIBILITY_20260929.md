# HANDOVER — V15 Sheet-Credibility Overhaul (2026-09-28 → 09-29)

Session goal (USER): sheets fill fast, complete, and credible — every cell traces to a real
function, augments/reduces visible on charts, no fake/echo/stale numbers, <1h per sheet,
LONG+SHORT pairs per server, four impeccable sheets before adding servers.

## WHAT WAS BROKEN (all proven, all fixed or contained)

**Four silent process assassins** (why "days of compute, zero sheets"):
1. Mac launchd `com.binance.monitor-crypto` — kill -9s any s1 process matching `1000BONK.*30d`,
   750×/day, also kills legit pilots. MITIGATED (env-var launches hide names). **STILL LOADED —
   user must run:** `launchctl bootout gui/501/com.binance.monitor-crypto && mv
   ~/Library/LaunchAgents/com.binance.monitor-crypto.plist{,.disabled} && pkill -f monitor_crypto_pipeline`
2. s1 tmp-sweeper deleting atomic-save tmp files mid-write → hardened (dot-tmp + retries, v15_assure).
3. Mac launchd `binancemirror` → `tools/sync_history_to_ext.sh` pushed the ENGINE ALONE to s1
   every 15 min (mid-edit snapshots crashed 14+4+ pilots on ModuleNotFoundError). **Push block
   DELETED** (USER "get rid of it"); ext-drive backups intact.
4. Herd "orphan reaper" kill-9'd every own pilot (herd-launched pilots are ppid==1 by
   construction) → reaps only >30min-old + >15min-idle now (tools/v15_local_herd.py).

**Two throughput killers:** `xls_prev` used read-only `ws.cell()` random access = re-parse from
start per access = HOURS (now streaming iter_rows = 1.0s); 12 pilots × 16 workers = 192 threads
on 16 cores = 0.3-1.2 rows/min (now twin-pair: 2 pilots × 8 workers per box).

**Credibility rot:**
- Fake-audit stub farms in BOTH codebases (engine line comments admitted "audit counts getattr
  regardless of use"); 52 FILTER_TF names were scaffold both sides → farms DELETED (bit-identical
  proof), census of all 117 filters: 54 wired-real / 46 dead-with-reason / 9 live-diff-proposed /
  8 vec-twin-pending (`data/reports/census_scoreboard_20260928.json`, FILTER_WIRING_CENSUS_20260928.md).
- Augment/reduce had NO real vector path → live-parity gain-ladder + PPL + WT_D_BOUNCE + NOLOSS
  bypass + MI voter + OI_CONFIRM + HTF direction gate wired; OPEN/AUGMENT/REDUCE ledger events;
  ~12 silent QuickConfig-vs-live default divergences aligned. gain_pct definition unchanged.
- Vigilance DC4: USER 4th mandate — now a SWITCH (VIGILANCE_GUARD_ENABLED default False both
  sides) + VIGILANCE_DC4_BREACH_TOLERANCE_PCT 0.25 on the channel level. Proven: ALL prior
  vigilance closes were sub-tolerance touches.
- **Resumed-chain contamination** (SCCO "+11.29" title vs −42% chart): chains resumed across
  engine cuts carry mixed-engine arithmetic. FIXED: 875 pre-cut chains archived
  (`~/v15_herd_precut_archive/bd804da7/` both boxes), 5 contaminated "finished" sheets retracted
  to `backups/contaminated_finished_20260929/` (Mac), finisher now RE-ANCHORS (fresh engine eval
  of final overrides before naming; >0.5 mismatch = hold as CONTAMINATED, never publish).
- **BadZip once-and-for-all**: 10 in-place `wb.save()` sites in v15_pilot (incl. a red-fixer
  path installing known-invalid zips) + 6 tool writers → all through zip-validated atomic saves
  (`tools/xlsx_atomic.py`); transfer scripts quarantine invalid arrivals; 31 corpses purged
  across 3 hosts. Invariant: no code path can produce or propagate an invalid xlsx silently.
- Yellow-compute waste: pilots evaluate ONLY `data/wired_filters.json` allowlist (79 names);
  rest grey PENDING_WIRING (fail-open if file missing). Empty-candidate template rows (B=None)
  skip honestly as NO_CANDIDATE (they blocked sheets at "8 rows pending" forever).

## RUNNING NOW (per box, all setsid daemons)
- herd `tools/v15_local_herd.py` (twin pairs 2×8, log /tmp/v15_herd_local.log)
- sentinel `tools/v15_cell_sentinel.py` (kills+relaunches pilots idle >12min, /tmp/v15_sentinel.log)
- finisher `tools/v15_finisher.py --watch` (re-anchor → bh/gain name → chart → state; /tmp/v15_finisher.log)
- assure watch `tools/v15_assure.py watch` (refills JSON-ahead sheets, /tmp/v15_assure_watch*.log)
- redflag sweeps `tools/v15_redflag.py` (echo/zero/stall/coverage detectors; run per monitor cycle)
- Mega sweep PAUSED by USER order (`~/.v15_mega_pause_*`); herds only.

## VERSIONS (md5, identical Mac/s1/s2 unless noted)
engine `bd804da7` (+ NOT-YET-DEPLOYED Mac-only pilot round? none — pilot `c08c7dda`),
evaluate_v12 `455516b3`, wired_filters `9514a02d`+FLOKI-era appends, xlsx_atomic `720b68f4`,
finisher (re-anchor build, deployed 00:0x), templates 5b4b7614/d613b29e/dcbff15d/7bdf62e1.
LIVE files (Mac only, managers NOT restarted — USER controls): ez_manage/tradier_manage/config/
config_tradier carry: crash-loop fix (indicators_cache property collision, was 167 crashes),
exit-gate fix (exits no longer blocked by is_symbol_tradeable — AGI/AU/CVX/LSCC were stranded),
vigilance switch+tolerance, FILTER_TF entry gates (7 names, both venues, default OFF),
FAST_RISER TF-select + fixed double-scaled log. FAST_RISER unit RESOLVED: percent (0.8 = 0.8%),
vec and live agree, no change was needed.

## OPEN ITEMS (ordered)
1. USER: run the monitor-crypto bootout (3 commands above) + compile-check was done for live files.
2. USER: restart live managers when ready (fixes+parity gates activate then); trc broker
   ORDER_API_ERROR root-cause report delivered separately (universe overlay re-gating closes).
3. Live twins NOT yet applied (permission-blocked/deferred): DC_BREACH_REDUCE + BREAKEVEN_GAIN_EROSION
   reduce/erosion confirms; wave-4 reads (WT_DC thresholds, EMA_9_21_MIN_TFS, LH_HL, BB exit TFs)
   — exact diffs in PROPOSAL_FILTER_WAVE1_20260928.md + PROPOSAL_HIDDEN_REDUCE_SWITCHES_20260928.md
   (also INTRADAY_RATIO loop-blindness fix, Market_Against/Overbought TP de-hardcoding).
4. 8 vec-twin-pending names (census wave-5, live line cites in scoreboard).
5. Template rows with empty B cells need real candidates (or deletion) — currently honest-skipped.
6. OPEN markers on charts: agent deploying (reduces looked like they preceded buys — rendering gap).
7. First four impeccable sheets: candidates arriving from CLEAN chains only (FLOKI_SHORT +0.42,
   AAPL_SHORT −3.52 landed; verify each via finisher re-anchor log line + fill audit before showing).
8. s2 carries ~18-19G non-pilot resident RAM (limits stock throughput) — worth its own hunt.

## LANDMINES
- NEVER resume a chain across an engine cut — archive + clean restart (this was the fake-numbers factory).
- A nonzero delta is credible ONLY with a changed trade ledger (identical yellows across a row = filter no-op).
- Headline gains come ONLY from the finisher's re-anchored fresh eval.
- The permission classifier blocks: launchd unloads, some live-file edit routes, engine rsync from
  agents — deploys go through USER `!` commands or binance-28's session (their own user order).
- Peer sessions binance-28/99/d0 coordinate cuts; announce engine batches (whole vec_decisions dir,
  tf_secs gate 3/4, import test both boxes) before any push.
