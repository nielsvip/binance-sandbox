# cut#6 wire-push patch manifest — 2026-10-05 ~02:15 UTC (Mac tree)

Lane: wire-push (batches 1+2, wave-1/2, P0). Coordinator owns cut mechanics;
this file locates every hunk (fleet co-edits share these files — grep markers).

## Proposed split
- cut#5 candidate (v12+config+templates, red's train): v12_quick_engine.py
  wave-1 vec-side hooks (grep `twin_exits_dead\|twin_entry_ports\|TWIN_EXITS_DEAD\|2026-10-04` in v12; NO twin_p0 in v12 — P0 was live-side only).
- cut#6 (managers + wire-push config): everything below.

## cut#6 files (Mac paths)
- ez_manage.py — batch twins (NOLOSS/BB/TECHNICAL-DC), wave-1/2 hooks, P0-A/B
  (11+10 hooks incl 2 OPEN elifs + 4 baked WTDC vetoes). Markers: `twin_p0`,
  `twin_p0_crypto_b`, `2026-10-04`, `TWIN_EXITS_DEAD`, `GATES-A`, `ENTRY_PORTS`.
- tradier_manage.py — P0-stocks 12 hooks (incl 4 stoch threshold flips),
  wave hooks. Markers: `twin_p0`, `2026-10-04`.
- ez_positions_quick.py — P0-A augment/exit hooks, batch exit hooks.
  Markers: `twin_p0`, `2026-10-04`.
- config.py — 5 P0 fields ONLY (lines ~59-80): WT_DC_ENABLED=True,
  WT_DC_DC_POS_MIN=0.20, WT_DC_FINAL_SCORE_MAX=0.40,
  WT_DC_K5M_HARD_ENABLED=False, WT_DC_K5M_MIN_SHORT_HARD=20.0.
  Marker: `2026-10-05 P0`.
- config_tradier.py — NOT touched by this lane (15 dated markers are fleet lanes').
- vec_decisions/twin_*.py — 15 twin modules (new this push) + shared twins
  dc_channel_exits.py, noloss_hold.py, bb_stoch_exits.py; twin_vec_special.py
  +delta_pyramid_adds_allowed. NOTE vec_decisions/ is gitignored — `git add -f`.
- tests — 19 twin files (507 green) incl test_wire_push_contract_20261005.py.
- hook_spec_*.json — 15 files (merge evidence + contract-test input).
- tools/wire_status_paint.py, tools/twin_watch.sh; data/SWITCH_BIBLE.json;
  SPREADSHEETS/TEMPLATE_*.xlsx (font-color status only, verified zero
  value/dimension change); data/reports/cut4_*manifest/decisions/log.

## Proofs (all Mac paths)
- 507/507 twin suite green (19 files); watch log data/reports/twin_watch_20261005.log.
- Bible: every merged switch verified WIRED_BOTH_UNPROVEN post-merge (per-batch
  flip checks in session log); no demotions from this lane.
- Behavior manifest: data/reports/cut4_behavior_manifest_20261004.md
  (inert vs behavior-changing per item, incl 2 OPEN paths + stoch flips).
- Operator decisions: data/reports/cut4_operator_decisions_20261004.md
  (needs-decision switches NOT wired — nothing invented).
- Backups: backups/before_p0_* + before_wire_paint_* (bisection points).

## Needs from coordinator
- ~~cat_side sync for the 5 P0 keys~~ RESOLVED 02:25 UTC: my MISS was a
  structural misread (queried top-level keys; file is cat_side-keyed). All 5
  verified present in all 4 cat_sides with correct values. No action needed.
- v12 hunk placement: RESOLVED 02:30 UTC — coordinator diffed all 8 regions
  vs 92feb8f3, ALL present identically. Wave-1 vec side ships WITH cut#5.
  Cut#6 = managers + twins + tests + specs/tools/templates (this manifest).
