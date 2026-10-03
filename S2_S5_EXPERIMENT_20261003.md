# S2/S5 SPLIT EXPERIMENT — set up 2026-10-03 ~01:30Z (USER order)

Question: is the bad output (a) already present at Claude's handover, or
(b) introduced after — and does obligate-calc change it?

## Who runs what (~/binance-sandbox, md5-verified)

- s1 (10.0.0.3): UNTOUCHED, current code. pilot `3e99b403`, engine `0b43055f`,
  SHORT template `1cce893a`. Reference "known crap". Autopilot + scheduler run
  here as before (round run25, sweep phase at setup).
- s2 (10.0.0.4): HANDOVER set (commit 0ea8cb03d ≈ 2026-10-02 02:25Z + closest
  backups). pilot `fc0d75eb`, engine `ecacb3be`, SHORT template `6a97538a`
  (throttler rows at 35/36, as at handover). 18 files reverted, all md5-match
  staging. Import-tested.
- s5 (10.0.0.5): CURRENT + obligate-calc guard. pilot variant `76e84f14`
  (base Mac 3e99b403 + 77-line OBLIGATE diff) + new `tools/v15_obligate.py`
  (`fa003cd3`). Engine/templates stock current. Import-tested, banner fires.

Mac: only NEW files added (tools/v15_obligate.py, tests/test_v15_obligate.py,
s5_only/, this doc + inventory/restore docs). NO locked file touched; a peer
session is actively editing Mac v15_pilot.py — left alone.

## The obligate guard (answers "calculate every row, no false 0")

Built from a full audit of v15_pilot write paths (all G/yellow writes are
honest at write time — None/RED, never bare 0). Real false-0 vectors found:

1. Resume across code versions (NO stamp existed) — stale 0s kept silently
   across engine cuts. FIXED: code-stamp resume gate recalcs the sheet.
2. Silently-ignored overrides -> fake exact 0.0 (the code's own comment at
   ex-line 188). Defense was the zero-audit UNWIRED list, stale across engine
   cuts. FIXED (s5): audit not trusted, every row calculated + far-value
   sample probes annotate binding context.
3. possym sampling skips (ON since run21). FIXED (s5): forced OFF.
4. Eval-layer gain-0.0-on-error (tools/opt/v12_pilot.py:88/89/109/143/163).
   FIXED (s5): error reasons refused, never numeric.

Tests: tests/test_v15_obligate.py, 12 passed. Details: s5_only/README.md.

## Cutover (nothing to launch — scheduler does it)

s2/s5 slots were 6/6 full at setup; in-flight pilots finish on old code (it is
already imported), new scheduler launches take the new code automatically.
s2 held: AVAXUSDC EGLDUSDT LLY MCD NKE SNXUSDT. s5 held: AAVEUSDC COTIUSDT
LINKUSDC MASKUSDT RLCUSDT XLMUSDT.

## How to observe

- s5 variant live? `ssh s5 "md5sum ~/binance-sandbox/v15_pilot.py"` must stay
  `76e84f14...`. Next s5 launches log `[OBLIGATE-CALC]`, `[OBLIGATE-RECALC]`,
  `[OBLIGATE-PROBE]`, `[OBLIGATE-SUMMARY]` in /tmp/sweep_*_30D.log.
- s2 handover sheets: progress `~/v15_run25_*/progress/*_v14_progress.json` on
  s2 with pilot fc0d75eb (check `head` of the sheet's sweep log for launch
  time > 01:28Z); delta logs `v15_delta_log/` per sym_side.
- Compare: s1 sheet (current) vs s2 sheet (handover) vs s5 sheet (obligate)
  for the SAME sym_side + window. E3/baseline + G-distribution + zero-rate.

## Caveats (read before concluding)

1. s1's round-end SYNC pushes s1 templates + promotions + sweep-defaults to
   s2/s5 (never .py). s2's handover TEMPLATES survive only until the next
   successful round-end (run25 crypto cov 28-42% at setup → hours away; and
   collect has been FAILING, which delays sync). After sync, s2 = handover
   CODE + current templates (still a clean code A/B).
2. s2's data/sweep_defaults/cat_side_defaults_4.json = CURRENT (handover-era
   copy unrecoverable: untracked, no round backups — round-ends fail before
   the defaults stage). Templates carry bolds explicitly, so this is a
   second-order mix, but it is a mix. Noted, not hidden.
3. s2's tools/build_cat_side_defaults_4.py = CURRENT (unrecoverable, no
   backup). Pilot uses it inside try/except (fail-open: all bolds trusted).
   Low risk.
4. s2's tools/v15_gain_pusher.py, v15_daily_template_update.py and other
   non-pilot-imported tools = CURRENT (handover pilot doesn't import them;
   verified by grep). vec_decisions/bottom_top_signals.py left in place:
   INERT under handover engine (no module auto-discovery, verified by grep).
5. PEER TRAFFIC: a peer session is editing + deploying Mac v15_pilot.py (s5
   had 3e99b403 pre-deploy = peer's 01:17 build). If the peer deploys stock
   pilot to s5, the variant dies silently — re-check md5 before reading s5
   results. s2 faces the same risk for its handover set.
6. s2 ~/binance/ (stale mirror, pilot b80f8f5c) deliberately untouched;
   scheduler root is the sandbox.

## Restore

- s5 -> stock: `~/v15_pilot_pre_obligate_20261003012613.py` (3e99b403) back to
  `~/binance-sandbox/v15_pilot.py`; delete `tools/v15_obligate.py`. Or rsync
  current Mac pilot.
- s2 -> current: `~/s2_pre_handover_revert_20261003012828.tar.gz`
  (8a3244c3, 18 files) extracts into `~/binance-sandbox/`.
- Full current-state restore (any host): RESTORE_TO_CURRENT_STATE_20261003.md.
- Reapply inventory (all post-handover changes):
  CHANGES_SINCE_HANDOVER_20261003.md.
