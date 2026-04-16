# Server S2 Inventory Report — 2026-04-16

**Date**: 2026-04-16 20:57 UTC  
**Status**: LIVE TRADIER SWEEP RUNNING (v8_quick_sweep.py, 6 workers, PID 888517+)  
**Lock File**: /home/niels/SWEEP_RUNNING contains TRADIER_SWEEP

---

## Summary

| Category | Count | Disposition |
|----------|-------|-------------|
| **Protected (LOCKED)** | 34 | DO NOT MOVE — core live + recent fixes |
| **Live Referenced** | 60 | Actively used in V8 sweep, config, backtest |
| **Orphan Obsolete** | 105 | All from 2026-04-01 or pre-2026-04-10 (analysis, patches, old frameworks) |
| **Diamond Candidates** | 7 | Recent (2026-04-08+): strategy pilots, new exit mechanisms, signal discovery |
| **Total .py** | 206 | (excluding old/, backups/, logs/, indicators/, __pycache__, TRADIER_SWEEP/) |

---

## Protected Files (LOCKED_FILES.md)

34 files locked per LOCKED_FILES.md — core live trading, delta engine, position management, indicators, and recent bug fixes.

Protected: ez_manage.py, ez_positions_quick.py, ez_positions_service.py, wt_dc_delta.py, tradier_manage.py, backtest_v8_engine.py, backtest_v8_precompute.py, config.py, utils.py, ez_indicators.py, ez_rankings.py, tradier_rankings.py, tradier_positions.py, et al.

---

## Live Referenced (60 files)

V8 sweep core + active tools. Recent updates to backtest_v8_engine.py, config.py, config_tradier.py, ez_manage.py, tradier_manage.py, v8_quick_sweep.py, all sweep variants.

---

## Orphan Obsolete (105 files)

All mtime = 2026-04-01 or pre-2026-04-10. Candidates for old/inventory_2026-04-16/:
- 33 _patch_*.py files (frozen 2026-03-25 through 2026-04-01)
- 3 ablation_*.py (legacy ablation studies)
- 5+ analysis_*.py (analyzed data, not live)
- 10+ download/klines tools (superseded or one-time use)
- Legacy strategies (bitget_scraper, btc_crash_safety, spike_fade, grid_sweep, rsi_filter, stoch)
- Infrastructure (bridge, btc_ticker, continuous_param_optimizer, daily_performance, export_conversations, wallpaper_daemon, weekend_grid_scheduler)

---

## Diamond Candidates (7 files, Recent Development)

### 1. htf_breakout_scalper.py (2026-04-16, 17.3 KB)
Finding: SCALP_V2 mechanism. ONLY fires when price breaks HTF Donchian Channel. 8 exit variants (V1–V8, V1_WT_CONFIRM live with Sharpe=107).

Config Status: ACTIVE in config.py lines 94–109
- SCALP_V2_VARIANT = V1_WT_CONFIRM (Sharpe=107)
- SCALP_V2_ISOLATE = True (2026-04-16: V2 positions use ONLY V2 exits)
- MAX_HOLD_MINUTES = 15.0, DC_HTF_LIST = [15m, 1h]

Proposed Archive: SCALP_V2_* (after 7d live stability)
Status: ACTIVE & INTEGRATED

### 2. backtest_redzone_perpetual.py (2026-04-11, 12.8 KB)
Finding: RED ZONE perpetual sweep. Cycles config grid deterministically, SQLite resume cache, 7200s timeout, resumable on crash.

Config Status: ACTIVE
- RZ_EXIT_ENABLED = True
- DELTA_EXIT_DOM_TF_ENABLED = True (wt_dc_delta.py delta engine)
- Thresholds: rz_baseline_tol, rz_ltf_micro (3m crypto/5m stocks)

Proposed Archive: RZ_* (after Part 15 sweep complete)
Status: CORE TO CURRENT DELTA ENGINE

### 3. backtest_redzone_stock_sweep.py (2026-04-09, 5.7 KB)
Finding: Stock RZ backtest on 128 stocks over 25mo (2024-01 to 2026-01).

Config Status: ACTIVE in config_tradier.py (RZ_EXIT_ENABLED, DELTA gating)

Proposed Archive: Tradier RZ_* (once parent stable)
Status: ACTIVE IN SWEEP

### 4. analyze_v8_pnl.py (2026-04-09, 5.0 KB)
Finding: PnL analyzer for V8 output. Pure analysis tool, no algorithmic content.

Config Status: No live dependencies
Proposed Archive: Immediate
Status: PURE ANALYSIS

### 5. backtest_v8_positions.py (2026-04-08, 11.6 KB)
Finding: V8 position data model. Required by backtest_v8_harness.py.

Config Status: Imported by harness (PROTECTED)
Proposed Archive: DO NOT MOVE (required dependency)
Status: REQUIRED

### 6. vec_mass_scan.py (2026-04-16, 13.2 KB)
Finding: Vectorized mass signal scan. No position simulation. Tests millions of param combos. Forward-return scoring at 8 horizons (4/8/16/32/64/128/256 bars). Crypto vs tradier condition banks.

Config Status: No live integration yet. Standalone R&D tool.
Proposed Archive: None
Status: ACTIVE R&D — keep live until signal discovery phase complete

### 7. backtest_fill_kline_gaps.py (2026-04-04, 8.5 KB)
Finding: Kline gap filler. Repairs sparse kline data before backtest.

Config Status: May be in prep pipeline
Proposed Archive: Keep if used in pipeline; archive if superseded
Status: UTILITY

---

## Critical Issues

1. **import argparse.py (1.3 MB)**: GARBAGE FILE — wrongly named Python system library. SHOULD REMOVE IMMEDIATELY.
2. **push3.py (0 bytes)**: Empty stub. SHOULD REMOVE.

---

## Running Processes (DO NOT KILL)

- PID 833220: sweep_autochain.sh s2 (3h+ uptime)
- PID 888517–888532: v8_quick_sweep.py --mode tradier (6 workers, 20:56 UTC)
- Screen session: full_tradier
- Lock: /home/niels/SWEEP_RUNNING = TRADIER_SWEEP

---

Report Generated: 2026-04-16 20:57 UTC
Prepared By: S2 Inventory Agent (read-only scan)
