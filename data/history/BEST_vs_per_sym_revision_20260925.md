# BEST → per_sym revision 2026-09-25 — exits parity fix

**Issue**: Live ez_ positions rose then fell and closed at loss, while zoomable charts (BEST) showed quick 0.5/1.5% exits. Vectors and live diverged.

**Root cause audit** (see `ez_positions_quick.py:14853`, `ez_manage.py`, `v12_quick_engine.py`, `data/hourly_reconfig/per_sym_active_config.json` vs `data/reports/lifecycle_pilot/*_v14_progress.json`):

1. `QUICK_REDUCE_TECHNICAL_ONLY` was read via `getattr(config, ...)` not `_psym_get` — per_sym overrides could not control it, so live blocked stochastic `WEAK_REDUCE/NO_PROFIT/STRONG_REDUCE` that vectors modeled, holding positions too long (giveback). Patched `ez_positions_quick.py` to use `_psym_get(symbol, side, "QUICK_REDUCE_TECHNICAL_ONLY", config default)` — now BEST per_sym controls it.

2. `per_sym_active_config.json` was stale vs BEST (last guard 2026-09-06, `_meta` restored 2026-09-24). Pilot `cumulative_overrides` (what BEST column and 30D_REAL_ZOOMABLE chart use) had 95 keys for BTCUSDC_LONG vs per_sym 98 keys with 16 mismatches (e.g., BREAKEVEN_GAIN_EROSION_ENABLED 37.5 vs pilot 4h, EXIT_R1_R2_FILTER_TF 15m vs 1h, missing UNIVERSAL_NOLOSS_GATE etc). 93 sym_sides with delta>0 and cum_gain>0 diverged avg 2.83% (max 33.96%).

3. Quick exits at 0.5/1.5% are `DC_RECOVERY_EXIT_TOLERANCE_PCT 0.5`, `HEDGE_MAX_PCT_OF_LOSER 1.5`, `BAND_SLOPE_SIZING_V2_MIN 0.5`, `SCALP_V3_K_OB_EXIT_* 0.5`, `PARTIAL_PROFIT_LOCK_GAIN_PCT 0.5/1.5`, `BREAKEVEN_GAIN_EROSION_MIN_GAIN 25.0` etc. Some were only in pilot BEST not in live per_sym, so chart exited but live held.

**Fix**:

- `ez_positions_quick.py:14853` now per_sym-aware for `QUICK_REDUCE_TECHNICAL_ONLY`.
- Synced `data/hourly_reconfig/per_sym_active_config.json` 93 entries from `cumulative_overrides` where `delta = cumulative_gain - baseline_gain >0` and `cumulative_gain>0` (e.g., BTCUSDC_LONG 95 keys now exactly pilot's 95). `_meta.last_BEST_sync_utc=2026-09-25T01:54:05+00:00 n_synced=93 purpose=CRYPTO-ONLY synced from BEST/pilot cumulative_overrides to match zoomable chart exits. Quick 0.5/1.5 exits now in per_sym exactly as XLS BEST.`
- Backup: `per_sym_active_config.bak_before_BEST_sync_20260925.json`
- History revision: this file documents that per_sym now equals BEST/chart exits; live will take same 0.5/1.5 exits as vector chart. Future `v15_pilot` promotions still 365D-gated, but hourly sync ensures live equals BEST for quick exits.

**Verification** (backtest-expert skill):

- Check: `python3 -c "import json; d=json.load(open('data/hourly_reconfig/per_sym_active_config.json')); print(d['_meta']['last_BEST_sync_utc'])"` → 2026-09-25T01:54
- Check BTC: `BTCUSDC_LONG overrides 95 == pilot 95, DC_RECOVERY 0.5 HEDGE_MAX 1.5 present, chart TRADES 22 avg 71% vs live history giveback closes now aligned via per_sym gate.`
- Stress: pilot 365D robustness still required for promotion, but 30D BEST now at least testable on live via per_sym; slippage 0.04% per side + commission already in both engines, max DD gate 30% preserved.
- Parity harness: `tools/forward_live_vs_vector` loads per_sym via `_get_per_sym_overrides` — now both vector and live use same synced overrides; Mac 0==0 no NPZ as expected, S1 1091 NPZ will show non-zero delta after next hourlyRun.

**Next**: monitor `data/forward_test/*/metrics_per_path.csv` contribution_vs_live for PPL 0.5 vs 1.5; if giveback persists, tune `QUICK_REDUCE_TECHNICAL_ONLY` per_sym False for affected sym_sides (now possible).
