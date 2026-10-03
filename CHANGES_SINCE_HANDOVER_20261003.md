# CHANGES SINCE HANDOVER — inventory 2026-10-03 01:10Z

Every script/template change AFTER Claude's handover (final message
2026-10-02T02:39:00Z). Reference commit: nearest Mac autosave BEFORE handover
`0ea8cb03d` (02:25:37Z). Caveat: Claude's own final 06:00-retime edits
(02:20–02:39Z) landed in autosave `ddb12d44f` (02:47Z), so the first two
post-handover commits may mix Claude's last edits with later agents' work.

Method: `git diff 0ea8cb03d..HEAD` (tracked) + filesystem mtime scan in UTC
(untracked: tools/*, vec_decisions/*, tests/* are gitignored — Bible §24) +
`backups/before_*` for before-images. Per CLAUDE.md, backups beat git on dates.

## A. Tracked scripts (11 files, +2337/−123 vs 0ea8cb03d)

| File | Commits touching it (newest first) | Before-image |
|---|---|---|
| v15_pilot.py (+860) | 424c4be7c 00:53 coherence, c0b1b4d2d 00:04 skipalarm, 017673833 23:43 typegate batch, c465b7c80 22:46 forcebypass/365prorata, d6d325368 22:25 brokersync batch, 0d0a6f617 21:59 topconfirm batch, 79313e966 21:13 qualgate, 0ab87c750 20:04 gainpusher batch, c1f48e130 06:11 quickconfig batch, 4a3759543 03:39 per_sym_sqlite | backups/before_coherence_202610030052.py, before_skipalarm_202610030035_v15_pilot.py, before_typegate_202610022349_v15_pilot.py, before_qual_retry_timeout_20261003.py, before_forcebypass_202610022335.py, before_365prorata_202610022245.py, before_qualgate_202610022139_v15_pilot.py, before_baseline_parallel_wr_dd_20261002.py, before_per_sym_baseline_E3_20261002.py, before_per_sym_sqlite_v15pilot_20261002.py |
| v12_quick_engine.py (+158) | 017673833 23:43 selltop §63, 58eb86ee2 16:46 bottomtop family, 8e0e316e9 05:20 per_sym_quickconfig | backups/before_selltop_engine_202610022247_v12_quick_engine.py, before_bottomtop_land_202610021616_v12_quick_engine.py, before_per_sym_quickconfig_20261002.py |
| ez_manage.py (+192) | c465b7c80 22:46 brokersync_exitfix (MAC ONLY, never deployed), 58eb86ee2 16:46 bottomtop, 8e0e316e9 05:20, 4a3759543 03:39 per_sym_sqlite | backups/before_brokersync_exitfix_202610022227.py, before_bottomtop_land_202610021616_ez_manage.py, before_per_sym_sqlite_ez_20261002.py |
| tradier_manage.py (+86) | 58eb86ee2 16:46 bottomtop, 4a3759543 03:39 per_sym_sqlite | backups/before_bottomtop_land_202610021616_tradier_manage.py, before_per_sym_sqlite_tradier_20261002.py |
| config.py (+49) | 017673833 23:43 selltop knobs, 58eb86ee2 16:46 bottomtop knobs | backups/before_selltop_engine_202610022247_config.py, before_bottomtop_land_202610021616_config.py |
| config_tradier.py (+49) | same 2 as config.py | same pattern, config_tradier variants |
| ez_positions_quick.py (+32) | 017673833 23:43 selltop twins | backups/before_selltop_engine_202610022247_ez_positions_quick.py |
| ez_indicators.py (+75) | ca1efad9e 18:15 btemit rest, 0ba013b7e 17:48 btemit | backups/before_btemit_202610021729_ez_indicators.py |
| tradier_indicators.py (+75) | ca1efad9e 18:15 btemit | backups/before_btemit_202610021729_tradier_indicators.py |
| cat_side_defaults.py (+29) | 0ab87c750 20:04 | no before_* backup found (reapply via `git show 0ea8cb03d:cat_side_defaults.py` vs HEAD) |
| per_sym_store.py (+855) | 0ab87c750 20:04, 4a3759543 03:39 | no before_* backup found (reapply via git show) |

Reapply tracked: `git diff 0ea8cb03d..HEAD -- <file>` shows the exact patch;
`git show 0ea8cb03d:<file>` is the handover version. Autosaves commit every
~25 min, so per-commit slices above localize each change.

## B. Untracked scripts (gitignored — backups/mtime only, 16 files)

| File | mtime UTC | md5 | Before-image / note |
|---|---|---|---|
| tools/backfill_per_sym_store.py | 10-02 03:26 | ef83b85a1cd58ed3d877fbe2321f74c7 | NEW after handover (no backup); reapply = copy current file |
| tools/mac_pull_sheets_charts.sh | 10-02 03:36 | 57d3759b609b48087c6cee1fea6b1a8b | backups/before_mac_pull_html_fix_20261002.sh |
| tools/v15_template_universe.py | 10-02 05:48 | f548e08f263bfb27d4907e74f3dfdb62 | no backup found |
| vec_decisions/ported_entry.py | 10-02 16:00 | b45f93c82efabdbfc97f35c6e90f5358 | backups/before_divgates_entry_202610021556.py (header-verified) |
| vec_decisions/ported_exit.py | 10-02 16:22 | 69eb9f312f008cab18aeb0f1fbda73f1 | backups/before_divgates_exit_202610021556.py |
| tools/v15_npz_augment.py | 10-02 17:54 | c49d36b243df052a2ec28f3071a22383 | no backup found |
| vec_decisions/bottom_top_signals.py | 10-02 18:37 | abb55283c5c319a9f74b43602b3982c1 | NEW (bottomtop family); no backup |
| tools/v15_gain_pusher.py | 10-02 20:02 | 0bce3bdf76e00bb87675e500651c9278 | backups/before_p1musttest_202610021634_v15_gain_pusher.py + before_gainpusher_v2/v2b/v2c_*.py chain |
| tools/v15_daily_template_update.py | 10-02 20:03 | 8d58d0db323d0d96f025067bf3dbf72c | no backup found |
| tools/build_cat_side_defaults_4.py | 10-02 20:03 | e4fc64c4a43be886f00026b5c3937807 | no backup found |
| tests/test_per_sym_store.py | 10-02 20:04 | aa5b204e0e49bf9c7f46b9036b6f8ba8 | NEW test |
| tests/test_broker_sync_exit_exempt.py | 10-02 22:27 | d583f6e98bd4dedd7cb4d184417fc039 | NEW test |
| tests/test_confirm_365d_gate_default.py | 10-02 22:36 | f63bcfb22f4ca651f39119b995f1dcec | NEW test |
| tools/opt/evaluate_v12.py | 10-02 23:52 | 7a167f7ccf4141b546f2c9023f8b0ad3 | backups/before_typegate_202610022349_evaluate_v12.py |
| tests/test_v15_type_gate.py | 10-02 23:52 | ee4fc400f774c0e4a984f2b59051e43e | NEW test |
| tests/test_v15_publish_coherence.py | 10-03 00:58 | 4401b219739e20273eecfaf3ab7b45a1 | NEW test |

Reapply untracked: current Mac file IS the after-image (md5 above, all match
s1). Before-image = listed backup (diff backup vs current to reapply forward
per Bible §24 — never copy old over new on Mac/live).

## C. Templates (4 files, all changed)

| Template | Handover size | Current size | Current md5 | Change points (backups) |
|---|---|---|---|---|
| TEMPLATE_CRYPTO_LONG | 1810370 | 1832900 | d224b77efbe07f72ff517e84d0767d38 | btfamily_rows 16:32, daily_update 18:07, topconfirm 21:53, switchrows 23:33 |
| TEMPLATE_CRYPTO_SHORT | 1701351 | 1707993 | 1cce893a59d3fe722d042863a8266ad8 | same 4 |
| TEMPLATE_STOCKS_LONG | 1894135 | 1907975 | f38ea1860256c112139f504767dd42df | same 4 |
| TEMPLATE_STOCKS_SHORT | 1788607 | 1805895 | 7ec65a4951aea3c33f0a80342576f68d | same 4 |

Before-images: `backups/before_btfamily_rows_202610021632_*`,
`before_daily_template_update_202610021819_*`,
`before_selltop_topconfirm_202610022153_*`,
`before_selltop_switchrows_202610022333_*` (+ `run_inv_202610021823/` mid state).

## D. EZ_MANAGE_THROTTLER_RATE finding (s5 revert target is VOID as stated)

Checked all 93 `backups/*CRYPTO_SHORT*.xlsx` (back to Sep 15) + oldest git blob
(Sep 16) + handover blob: the switch is present in EVERY one. It was NOT added
after handover — at handover it sat at ENTRY_REVERSAL_BOUNCE rows 35/36
(alt `0.0`/NO + bold `0`/YES), and the `v15_daily_template_update` run moved it
to rows 3/4 between 18:07Z and 18:21Z Oct 2 (worst-first reorder). Content
identical, position changed.

So "before EZ_MANAGE_THROTTLER_RATE was added to TEMPLATE_CRYPTO_SHORT.xlsx"
has NO determinable point in Mac backups or git (predates Sep 15). Nearest
sensible s5 target, if you want the throttler back where Claude left it: the
`before_daily_template_update_202610021819_*` templates (throttler at 35/36,
as at handover). Confirm before anyone touches s5.

## E. Server state at snapshot (~/binance-sandbox, read-only checks 01:04–01:10Z)

- s1/s2/s5 IDENTICAL on 9 scripts + 4 templates (same md5+mtime, §1 values).
- s2/s5 differ from s1 ONLY: cat_side_defaults.py OLD (c820066d, 00:53) and
  per_sym_store.py MISSING.
- All servers run ez_manage.py 2e385d67 (16:23); Mac's bb7e9f82 (22:36
  brokersync fix) was never deployed anywhere.
- s1 LEFT RUNNING as the known-current reference. s2/s5 reverts NOT executed
  (blocked: LOCKED_FILES.md needs explicit `unlock <file>` per file in the
  same message + NEVER-REVERT rules; throttler premise void per §D).
