# RESTORE TO CURRENT STATE — snapshot 2026-10-03 01:10Z

Snapshot of the CURRENT (pre-revert-experiment) state. If s2/s5 get reverted for
diagnosis, this file puts everything back exactly.

Handover reference: Claude left off 2026-10-02 02:39Z. Nearest Mac autosave:
`0ea8cb03d` (02:25:37Z). HEAD at snapshot: `424c4be7c` (00:53:09Z Oct 3) plus
uncommitted work (v15_pilot.py +99 lines coherence edit, deployed to all 3
servers at 00:58:20Z but not yet autosaved).

s1 was LEFT UNTOUCHED and still runs the current state below (the "known crap"
reference). Mac also untouched. Only s2/s5 are candidates for revert.

## 1. Current md5 (Mac working tree = s1 ~/binance-sandbox)

| File | md5 | Mac mtime (UTC) |
|---|---|---|
| v12_quick_engine.py | 0b43055f66e992692375882a3e45dca0 | 10-02 23:37 |
| v15_pilot.py | e6142befe3741b1214d7c1aa6776f031 | 10-03 00:58 |
| config.py | 8eee5c6574964cab5d80b4ed9b5c3ba4 | 10-02 23:37 |
| config_tradier.py | 07ab7dd2b00bdadee7c46f6dda146031 | 10-02 23:37 |
| tradier_manage.py | 1515eca6cee791cbee41325ab6437c4c | 10-02 16:25 |
| ez_positions_quick.py | 41f684960ba68e101b320f55a7acec97 | 10-02 23:37 |
| ez_indicators.py | bbd31d0eb5dbf38b1a1595b6e3d9a4b4 | 10-02 17:54 |
| tradier_indicators.py | f786be9239fbd4e4e3ff1cbf89c0e186 | 10-02 17:54 |
| cat_side_defaults.py | 51304a649e3f24472bd06e06527276ef | 10-02 20:01 |
| per_sym_store.py | 51d3308d50df37a9e2e7ab7e57dd5c83 | 10-02 20:02 |
| tools/v15_gain_pusher.py | 0bce3bdf76e00bb87675e500651c9278 | 10-02 20:02 |
| tools/opt/evaluate_v12.py | 7a167f7ccf4141b546f2c9023f8b0ad3 | 10-02 23:52 |
| tools/v15_daily_template_update.py | 8d58d0db323d0d96f025067bf3dbf72c | 10-02 20:03 |
| tools/build_cat_side_defaults_4.py | e4fc64c4a43be886f00026b5c3937807 | 10-02 20:03 |
| tools/v15_npz_augment.py | c49d36b243df052a2ec28f3071a22383 | 10-02 17:54 |
| tools/v15_template_universe.py | f548e08f263bfb27d4907e74f3dfdb62 | 10-02 05:48 |
| tools/backfill_per_sym_store.py | ef83b85a1cd58ed3d877fbe2321f74c7 | 10-02 03:26 |
| tools/mac_pull_sheets_charts.sh | 57d3759b609b48087c6cee1fea6b1a8b | 10-02 03:36 |
| vec_decisions/ported_entry.py | b45f93c82efabdbfc97f35c6e90f5358 | 10-02 16:00 |
| vec_decisions/ported_exit.py | 69eb9f312f008cab18aeb0f1fbda73f1 | 10-02 16:22 |
| vec_decisions/bottom_top_signals.py | abb55283c5c319a9f74b43602b3982c1 | 10-02 18:37 |
| SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx | d224b77efbe07f72ff517e84d0767d38 | 10-02 23:33 |
| SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx | 1cce893a59d3fe722d042863a8266ad8 | 10-02 23:33 |
| SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx | f38ea1860256c112139f504767dd42df | 10-02 23:33 |
| SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx | 7ec65a4951aea3c33f0a80342576f68d | 10-02 23:33 |

## 2. Known divergences at snapshot time (do NOT "fix" these on restore)

- Mac `ez_manage.py` = `bb7e9f82a92561512ac32d3dc3067726` (22:36, includes
  brokersync_exitfix, committed c465b7c80) — NEVER DEPLOYED. All 3 servers run
  `2e385d67b381553dd412ef87814d6f7a` (16:23 bottomtop). Restore keeps servers on
  `2e385d67` unless you explicitly decide to deploy the brokersync fix.
- s2/s5 `cat_side_defaults.py` = `c820066db71444c9d91511c4c3816a9d`
  (00:53, OLD). s1/Mac = `51304a64...` (20:01). Restore pushes `51304a64` to
  s2/s5 to match s1.
- s2/s5 `per_sym_store.py` = MISSING. s1/Mac = `51d3308d...`. Restore pushes it.
- Mac `v15_pilot.py` working tree has 99 uncommitted lines vs HEAD 424c4be7c;
  that working tree (e6142bef) is what all 3 servers run. Next autosave will
  commit it. Restore uses the working-tree file, not HEAD.

## 3. Restore commands (Mac → s2/s5, run from /Users/niels/Documents/binance)

```bash
cd /Users/niels/Documents/binance
for H in s2 s5; do
  rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new" \
    v12_quick_engine.py v15_pilot.py config.py config_tradier.py \
    ez_manage.py tradier_manage.py ez_positions_quick.py \
    ez_indicators.py tradier_indicators.py cat_side_defaults.py per_sym_store.py \
    tools/v15_gain_pusher.py tools/opt/evaluate_v12.py \
    tools/v15_daily_template_update.py tools/build_cat_side_defaults_4.py \
    tools/v15_npz_augment.py tools/v15_template_universe.py \
    tools/backfill_per_sym_store.py tools/mac_pull_sheets_charts.sh \
    vec_decisions/ported_entry.py vec_decisions/ported_exit.py \
    vec_decisions/bottom_top_signals.py \
    SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx \
    SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx \
    $H:~/binance-sandbox/
done
# NOTE: this pushes Mac ez_manage.py (bb7e9f82, brokersync fix) over the servers'
# 2e385d67. To restore servers EXACTLY as they were, exclude ez_manage.py from
# the list above (it was never deployed).
for H in s2 s5; do
  ssh $H "cd ~/binance-sandbox && md5sum v12_quick_engine.py v15_pilot.py config.py \
    config_tradier.py tradier_manage.py ez_positions_quick.py per_sym_store.py \
    SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx"
done
# expect: 0b43055f / e6142bef / 8eee5c65 / 07ab7dd2 / 1515ecae / 41f68496 /
# 51d3308d / 1cce893a — identical on s1, s2, s5.
```

Engine-cut warning (Bible §24/§25): pushing `v12_quick_engine.py` to a box
running the herd is an ENGINE CUT. In-flight pilots keep the old engine, new
launches take the new one. Never resume a chain across a cut — archive
pre-cut progress JSONs and let chains re-baseline.

## 4. Full change inventory (for selective reapply)

See `CHANGES_SINCE_HANDOVER_20261003.md` — every script change since the
2026-10-02 02:39Z handover with git commits (tracked files) and
`backups/before_*` sources (untracked tools/vec_decisions/tests).
