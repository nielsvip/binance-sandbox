# HANDOVER — impeccable-sheet pipeline PROVEN; adaptive sample-floor + server scale-out next
**Written 2026-09-28 ~23:50 UTC by binance-28 (successor to HANDOVER_MEGA_SWEEP_20260928.md).
Read this file, then memory `sweep_repair_2026_09_28_pm.md`, `v15_column_semantics_2026_09_28.md`,
`engine_push_protocol_2026_09_28.md`, `switch_auto_filler_registry.md`. Everything verified
first-hand this session unless marked.**

## NEW USER MANDATE (verbatim intent, 23:48Z) — FIRST PRIORITY
Sample-floor violations are NOT dead ends. When a sheet fails the trade floor, the pilot must ADAPT:
- **too few trades → add/enable entries** (toggle entry-family switches ON in an adaptive pass)
- **too many trades / low win rate → add filters**
- **TIM too high → add exits**
"Not rocket science." NOT IMPLEMENTED YET. Where it goes: v15_pilot's `[SAMPLE-FLOOR-VIOLATION]`
path (grep it) currently writes baseline XLS and skips the sweep (TSLA_LONG hit this: baseline
3.895% but <10 trades under the final engine). Replace the skip with an adaptive retry: sweep the
ENTRY_* tabs first with the floor as the objective (maximize trades to ≥ floor with gain ≥ 0), then
run the normal greedy pass. binance-99 renamed 50 stock sheets `{SS}_DIAGNOSTIC_{n}trades_*` for the
same reason — those all become candidates once this lands.

## WHAT WAS PROVEN TONIGHT (the user's acceptance test)
From-scratch, fully-honest sheet fill in **~4 minutes** (target was 1h). Three published and
audited impeccable; OXY_LONG in flight at write time (mid AUGMENT_RISK_SIZING; TSLA was swapped out
after honestly failing the sample floor):
| sheet | gain | bh | trades | audit |
|---|---|---|---|---|
| ADAUSDC_LONG_bh9p75_gain21p86 | +21.86% | +9.75 | 24 | ALL PASS, reproduce exact |
| SCCO_SHORT_bhm1p03_gain11p29 | +11.29% | −1.03 | 171 | ALL PASS, reproduce exact |
| UNIUSDC_LONG_bh111p80_gain74p04 | +74.04% | +111.80 | 106 | ALL PASS, reproduce exact (note: trails its monster B&H) |
Audit = every row, E2 header intact, F/G/K numeric per USER column semantics, C seeded+promotions,
0 error cells, no constant-delta echo, `final_gain == final_gain_fresh_vec ==` independent
`evaluate_sanitized` re-run to the last decimal. Auditor: `/tmp/impeccable_check.py` on s1+s2
(source in the session scratchpad; rewrite from this description if lost — 40 lines).
Old state archived per sheet in `~/binance-sandbox/from_scratch_archive_202609282340/` (both boxes)
+ `{SS}_best.json` seeds in V15_V16_CELL_BY_CELL.

## THE PIPELINE (what makes it work now — do not regress)
1. **Publish happens BEFORE live-verify** inside `_spec_fill_workbook`'s DONE stage
   (`[PUBLISH]` log line). The old publish block in main() (~line 5720) is UNREACHABLE dead code
   (spec-fill returns early). DONE-stage tails died silently inside live-verify all day — with
   publish-first it no longer matters. `[DONE-STEP]` prints instrument the stage.
2. **Fresh-final NO-LIES guard**: final_gain is set to the fresh full-set eval under the pilot's
   own engine; `engine_mixed_chain` stamped on divergence. Bh/gain filename carries only
   reproducible numbers.
3. **Resume gates** (v15_pilot): pending-template-rows resume (post-merge sheets), publish-only
   pass (complete + no bh file on disk), both replacing the done<2800 legacy gate.
   Board `is_complete` = complete AND published (drives herd requeue of unpublished completes).
4. **Fork safety**: pool pre-forks at creation; red-fixer thread disabled; pool released + eval
   cache dropped before DONE (OOM fix); herd reaps orphaned pilots (ppid==1) every poll.
5. **Column semantics (USER)**: E2=header, E3=baseline, E greedy blank-unless-pos, F=delta vs
   all-settings-so-far, G=delta vs INITIAL baseline, K=numeric sum of pos yellow deltas, C=
   switch=value + pos filters name=value. Board matches rows by (sheet, switch=cand) identity —
   template edits only ever add calculations for new rows.
6. **Speed**: dead-switch purge (census: 117/117 names accounted, 54 wired-real) + wired-filter
   allowlist + DEAD_VEC/LIVE_ONLY skip lists in the pilot = 4-minute honest full fills.

## CURRENT DEPLOY STATE (identical Mac==s1==s2 unless noted, ~23:45Z)
engine `bd804da7` (census cut; 99's mega cut lines 22:43Z+), evaluate_v12 `455516b3`,
wired_filters `9514a02d` (79 names), pilot `88d411a2`, herd `7d2c4f8e` (+publish-pass patch),
board `da0c849c`+identity+publish patches, managers ez `dfa63a31`/tradier `c573b58e` (LIVE Mac
procs restarted by user ~22:41Z on reconciled masters). Live vigilance = 4th mandate: a SWITCH
(default OFF) + 0.25% breach tolerance. `V15_SKIP_LIVE_AT_DONE=1` was used for the showcase
pilots — live H/I verify deferred (see open items).

## HERDS ARE STOPPED (deliberately, for the showcase). To resume the full 354 sweep:
```
ssh s1-int 'rm -f /tmp/v15_local_herd.lock; cd ~/binance-sandbox && (setsid nohup .venv/bin/python -u tools/v15_local_herd.py >> /tmp/v15_local_herd.log 2>&1 < /dev/null &)'
ssh s2     '...same...'
```
BEFORE resuming: do the **clean requeue** — archive pre-cut progress JSONs (protocol = 99's:
`~/v15_mega_precut_archive`, never delete) so chains re-baseline on bd804da7; resumed pre-cut
chains produce degenerate constant-delta rows (HTF_DIRECTION_GATE default flip dominates them —
ADAUSDC proof: recheck promoted HTF off at +4.20). Never-started sheets first, archived restarts last.

## PRIORITY QUEUE
1. Finish OXY_LONG (or accept 3 + pick another), charts for the four, Mac copies (chart tool:
   `tools/generate_zoomable_charts_mac.py`, runs on Mac over V15_V16_CELL_BY_CELL(_FINAL), NO auto
   trigger — run it manually; output SPREADSHEETS/charts/{stem}_zoom.html, bh/gain in name).
   ACCEPT: 4 xlsx + 4 charts on the Mac, filenames carry bh+gain.
2. Adaptive sample-floor (the new mandate above).
3. Clean requeue + herd resume (above), then the full 354 on the final engine.
4. **Server scale-out** (user: "we can finally start adding servers"): clone s1's image per
   INFRASTRUCTURE.md (rsync s1 → 10.0.0.x, never provision fresh), extend
   V15_SERVER_QUEUE_S*.txt venue split, sync_indicators.sh targets, herd hostname/max_parallel
   table (tools/v15_local_herd.py ~line 740), and the meet-in-the-middle protocol with binance-99.
5. Wave-5 vec twins (8 pending, scoreboard `data/reports/census_scoreboard_20260928.json`) +
   9 live-diff proposals (binance-5d's PROPOSAL_FILTER_WAVE1_20260928.md; FAST_RISER gain-gate
   unit ambiguity needs the user).
6. Live-verify hardening: the DONE-stage `live_evaluate` (backtest_v12_engine in-process) killed
   parents silently ~14min in (no OOM, no segfault, no traceback — unexplained; publish-first made
   it non-blocking). Re-enable per-sheet live verify (drop V15_SKIP_LIVE_AT_DONE) once diagnosed;
   d9's 6accd92e fixed SHORT-side pollution (V8_LADDER_ONLY_SIDE) so H/I for shorts are only
   trustworthy after that build.

## FLEET PROTOCOL (all sessions signed on)
- Engine pushes: announce to binance-99 (uds:/tmp/cc-socks/94468.sock) + binance-28 BEFORE; ONE
  atomic batch; **modules/config/templates FIRST, engine LAST**; md5 + `import v12_quick_engine`
  on both boxes before pilot launches. rsync -a preserves source mtimes — never trust file times
  for push forensics; 99 stamps ENGINE_AT_LAUNCH per pilot.
- NO killing in-flight pilots at cuts (they finish on their launch engine; new launches take the
  cut). Requeue pre-cut sheets instead.
- Peers: binance-99 mega (s1+s2, live, POST_CUTLINE_DONE.txt importable), binance-5d engine
  parity waves, binance-d9 WT_DC/live wiring, binance-d0 MTF exits. Mac master is edited
  concurrently — re-md5 at rsync time, expect intermediates, never revert.

## LANDMINES (tonight's)
- zsh `====`/`=x` args self-destruct; pgrep -f patterns match your own ssh wrapper (split the
  pattern: `PAT="tools/v15_local_"; pgrep -f "${PAT}herd"`); kills inside one ssh call can drop
  the connection mid-script — split kill and relaunch.
- rsync `--relative` with repo-relative paths nests dirs under the remote home; push data files
  plainly.
- SPREADSHEETS/TEMPLATE.xlsx is legacy-purged everywhere. verify_template_defaults checks the 4
  cat_side templates; pilot startup wraps it in an 8s timeout (skips on servers — Mac 03:00 cron
  is the authoritative run).
- ~180 orphaned pilot procs accumulated from OOM-killed parents before the reaper; if boxes feel
  mysteriously full, `ps -eo pid,ppid,args | awk '$2==1' | grep v15_pilot`.
- publish_final_winners cron feeds V15_V16_CELL_BY_CELL_FINAL → Mac pull (60s, --delete). Mac-side
  files not in _FINAL get deleted by that pull — winners must satisfy its criteria to persist.
