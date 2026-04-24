# LOCKED FILES — DO NOT TOUCH WITHOUT EXPLICIT PERMISSION

## How This Works

- If a file is listed here as **LOCKED**, Claude MUST NOT edit it unless the user says "unlock X" or explicitly permits the edit in that message.
- Even then, confirm before touching.
- To lock a file: add a row below. To unlock: user says "unlock <file>" and row gets removed.
- Locks apply to BOTH local and server copies.

---

## Currently Locked Files

| File | Locked Since | Reason / What Is Working | Who Locked |
|------|-------------|--------------------------|-----------|
| `autonomous_search.py` | 2026-04-24 | Stoch entry threshold caps: TRADIER_STOCH_ENTRY_LONG_TRADIER capped at 60 (k_5m < threshold gate — 90 means "almost always", wrong for longs), TRADIER_STOCH_ENTRY_SHORT_TRADIER floored at 40. MD5: 9eb40a7d95220ffab4a94b57a5dcffcb on all 3 machines. | user |
| `run_with_watchdog.sh` | 2026-04-03 | Singleton guard + SIGTERM restart + --accounts flag parsing. Fixed tra dying permanently after SIGTERM. | user |
| `ez_mem_watchdog.py` | 2026-03-25 | DISABLED — was causing 14k+ restarts/day by killing "duplicates" without stopping parent watchdog. LaunchAgent removed. DO NOT re-enable or re-launch. | user |
| `ez_prices.py` | 2026-03-13 | WebSocket price feeds stable, no issues reported | user |
| ~~`ez_klines.py`~~ | RELOCKED 2026-03-27 | See entry below | user |
| `ez_indicators.py` | RELOCKED 2026-04-20 19:50 | Prior: CWD fallback removed 2026-04-08. **2026-04-20 NEW**: `_calculate_15m_from_3m()` two-path logic: (1) native 15m exists → load from disk + per-bar gap-fill for missing last-2h bars from 3m only; (2) no native file → wholesale resample from 3m (synthetic fragment, always better than None). Backup: before_15m_gapfill_fix_202604201750.py. DO NOT remove synthetic fallback. DO NOT revert to wholesale replacement when native exists. | user |
| `ez_indicators_merger.py` | 2026-03-14 | Indicator merging pipeline — infrastructure | user |
| `ez_market_data.py` | 2026-03-26 | WT 1m/3m hot_metrics + klines_cache_writeback (60s cycle, merges composed candles to disk). DO NOT revert. | user |
| `ez_positions.py` | 2026-03-13 | Core position data structures — fundamental, only touch if data model changes. Log path fixed to ~/logs 2026-04-08. | user |
| ~~`ez_positions_service.py`~~ | RELOCKED 2026-03-27 | See entry below | user |
| `ez_positions_realtime.py` | 2026-03-13 | Real-time monitor stable (all account variants) | user |
| `ez_positions_watchdog.py` | 2026-03-14 | Process watchdog — infrastructure | user |
| ~~`ez_rankings.py`~~ | RELOCKED 2026-03-27 | See entry below | user |
| `ez_mark_prices.py` | 2026-03-13 | Mark price tracking stable | user |
| `ez_share_ind.py` | 2026-03-13 | Shared indicator server stable, heartbeat working | user |
| `ez_gain_protector.py` | 2026-03-13 | Trailing stop logic stable | user |
| `ez_gap_filler.py` | 2026-03-13 | Gap-fill entry logic stable | user |
| `ez_crosses.py` | 2026-03-13 | Cross-detection stable | user |
| `ez_double.py` | 2026-03-13 | Double-down logic stable | user |
| `ez_prices.py` | 2026-03-13 | WebSocket price feeds stable | user |
| `ez_prices_ws.py` | 2026-03-14 | WebSocket price feed (ws variant) — infrastructure | user |
| ~~`ez_klines.py`~~ | UNLOCKED 2026-03-16 | (duplicate entry) Unlocked to fix klines deletion | user |
| `tradier_api.py` | 2026-03-13 | Tradier API client stable — any breakage kills all stock trading | user |
| `tradier_prices.py` | 2026-03-13 | Stock price feeds stable | user |
| `tradier_positions.py` | 2026-03-13 | Stock position management stable | user |
| ~~`tradier_indicators.py`~~ | UNLOCKED 2026-03-16 | Unlocked to fix klines retention | user |
| ~~`tradier_rankings.py`~~ | UNLOCKED 2026-03-18 | Unlocked to apply backtest ranking changes (BACKTEST_CHANGE_T39-T42) | user |
| `tradier_webhook_bridge.py` | 2026-03-13 | Webhook integration stable | user |
| `utils.py` | 2026-03-13 | Helpers (pk_is_long, pk_is_short, parse_position_key) stable — touching breaks everything | user |
| ~~`tradier_indicators.py`~~ | UNLOCKED 2026-04-03 | Unlocked to integrate wt_composite for complete backtest NPZ | user |
| `tradier_rankings.py` | 2026-03-23 | Rankings pipeline stable | user |
| `tradier_positions.py` | 2026-03-23 | Position management stable + option gain/price fix. NEVER mutilate sync logic. | user |
| `tradier_positions.py` | 2026-03-30 | DOUBLE-LOCKED: Position sync was broken for 5+ weeks (phantom positions, merge-not-replace, 1s disk reload race). Fixed 2026-03-30. DO NOT touch sync_real_positions_from_api, save_all_positions, sync_positions_loop in tradier_manage.py either. | user |
| `tradier_prices.py` | 2026-03-23 | Price feeds stable | user |
| `ez_rankings.py` | 2026-03-27 | Rankings pipeline stable — data pipeline, not trading logic | user |
| `ez_klines.py` | 2026-03-27 | Kline fetching/caching stable — data pipeline | user |
| `wt_composite.py` | 2026-03-27 | Cross-TF WaveTrend composite scoring — core signal system | user |
| `wt_dc_delta.py` | RELOCKED 2026-04-11 19:50 | **SMART_RZ_EXIT v2**: k_15m>90 + red zone + (MFI OR lower_low OR lower_high OR delta_decel OR wt_vel_neg OR dc_falling OR prev_low_break). Sets exit_pending → Phase 2 sells at next bar high. reentry_if_momentum flag. Renamed all tags to SMART_RZ_*. Backup: backups/before_rz_rename_202604111950.py. DO NOT revert to flat K threshold. DO NOT remove slowdown requirement. | user |
| `ez_positions_service.py` | RELOCKED 2026-04-08 | CWD fallback removed, logs to ~/logs only. Phantom kill after API fetch done. | user |

---

| `backtest_v8_precompute.py` | 2026-04-04 | Full indicator precompute with fabricate_3m/5m, HTF resample, 404 fields. DO NOT remove fabricate_3m. DO NOT change base_tf. | user |
| `backtest_v8_harness.py` | 2026-04-04 | NPZ loader with HTF forward-fill. DO NOT remove ffill logic. | user |
| ~~`backtest_v8_engine.py`~~ | UNLOCKED 2026-04-08 | Unlocked to run V8 backtest with delta engine and compare vs fake vectorized results. | user |
| `ez_manage.py` | RELOCKED 2026-04-10 23:10 | Prior: all earlier patches. **2026-04-10 23:10 NEW**: UNIVERSAL_NOLOSS_GATE at line ~13455 in execute_now — replaces Finandy's external NO_LOSS. Calculates REAL gain from entry_price (not tracker .gain). Blocks ALL reduce/close at loss except: is_hedge=True, STRUCTURAL_RANGE_SHIFT, LIQUIDATION. NO delta_exit bypass (that was the leak). Also added config toggle gates: EXIT_MARKET_SPIKE_REDUCE_ENABLED, EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED, EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED, EXIT_HARD_MAX_LOSS_CAP_ENABLED. Backups at backups/before_universal_noloss_*. DO NOT remove UNIVERSAL_NOLOSS_GATE. DO NOT add delta_exit bypass. DO NOT touch tradeable_keys, sync_real_positions_from_api. | user |
| `ez_positions_quick.py` | RELOCKED 2026-04-21 | **2026-04-21 SIZING FIXES**: (1) `_re_amount` extracted from S7 `_re_data.get('reentry_amount')` (fallback: `position.max_quantity`) — forwarded as floor in TIER1/TIER2 sizing so accumulated partial-reduce qty drives reentry size. (2) TIER1: `qty = max(qty, SPS/price * TIER1_SIZE_MULT, _re_amount)`. (3) TIER2/TIER2_FORCED: floor is `SPS/price` (NOT `SPS*0.8/price`) — reentry can NEVER be below START_POSITION_SIZE. (4) Guaranteed-reentry loop enforces `max(override_qty, SPS/price)` floor. DO NOT allow TIER2 reentries below SPS. DO NOT remove `_re_amount` flow. **2026-04-21 EARLIER NEW**: (1) WT_CROSS_EXIT_3M_VETO — if 3m WT is still with the position and age<30m, block WT_CROSS_EXIT. Config: `WT_CROSS_EXIT_3M_VETO_MAX_AGE=30.0`. (2) MANDATORY_REENTRY_PRICE_CROSS — when price >= exit price, NO TIMER GATE, forces reentry with ≥1 WT TF (+30 score) or 0.3% strong cross (+20). REENTRY IS A RIGHT. **Prior 2026-04-20 NEW**: HEDGE_WT_KILL now requires BOTH loser's LTF (3m) AND 1h WT to recover before killing hedge — `HEDGE_WT_KILL_CONFIRM_TF='1h'` default, swept and validated (1h: Sharpe pool=0.274 WR=62%; none/15m: pool=0.135 WR=50%). DO NOT change default to 'none' or '15m'. **Prior**: Hedge vintage gain-based close paths gated OFF by default: HEDGE_BANDAID_OFF_ENABLED, HEDGE_DECAY_NUKE_ENABLED (×2), HEDGE_RECOVERY_CLOSE_ENABLED, HEDGE_PROFIT_PROTECT_ENABLED, HEDGE_LOSS_KILL_ENABLED, HEDGE_MAX_AGE_HOURS→0. ONLY ACTIVE closes: HEDGE_CLOSE_WT3M1H_ABS + HEDGE_WT_KILL + HEDGE_ORPHAN_HEALTH_KILL + CLEANUP_STALE_HEDGE_24H. DO NOT re-enable gain-based paths without sweep proof. DO NOT remove HEDGE_CLOSE_WT3M1H_ABS. DO NOT revert PEAK_GIVEBACK below 0.08. DO NOT remove delta_sig, tracker sync, hedge engine, WT_GATE_BYPASS_RZ, PARABOLIC_EXHAUSTION_EXIT, _hedge_entry_is_valid, GAIN_EROSION, STRUCTURAL_RANGE_SHIFT_EXIT cascade, PREEMPTIVE_BREAKEVEN toggle. | user |
| `ez_positions_service.py` | RELOCKED 2026-04-20 | **2026-04-20 NEW**: HEDGE_PAIR_CLEANUP grace period for inf — `elif not long_open` / `elif not short_open` now checks `PERSIST_INF` hours before discarding the NON-hedge closed side. Hedge side (in global_known_hedges_history) still discarded immediately. Config: `PERSIST_INF=4.0` hours. Backups: before_inf_persist_grace_svc_*. DO NOT remove `_long_is_hedge` / `_short_is_hedge` checks. DO NOT revert HEDGE_CLEANUP_GRACE logic. | user |
| `config.py` | RELOCKED 2026-04-20 | **2026-04-20 NEW**: Added `PERSIST_INF = 4.0` (hours) — grace period for inf in HEDGE_PAIR_CLEANUP. Tune to change how long a closed non-hedge key stays tradeable after rankings remove it. **Prior (2026-04-20 20:05)**: Added 'GAIN_EROSION' to LOSS_EXIT_TECHNICAL_BYPASS (bypasses NOLOSS_DC4H_GATE → DC_LOW4_3M closes at loss instead of hedging) AND to UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS (bypasses execute_now gate). Backup: before_gain_erosion_bypass_*. DO NOT remove GAIN_EROSION from either list without sweep proof — DC_LOW4 was effectively off for negative-gain positions before this. **Prior (2026-04-19 21:03)**: CT_WT_VELOCITY_1H_MIN=8.0, RZ_EXIT_ENABLED=False, V8Q_MIN_HOLD_BARS=250, V8Q_WT_EXIT_MIN_TFS=3. DO NOT re-enable HARD_MAX_LOSS_CAP. DO NOT re-enable RZ_EXIT without sweep proof. | user |

| `tradier_manage.py` | LOCKED 2026-04-10 17:30 | Prior: all earlier edits (see git). **2026-04-10 17:30 APPLIED**: STRUCTURAL_RANGE_SHIFT_EXIT in evaluate_stop — if entry outside [dc_low_4h, dc_high_4h], close LONG at dc_high_4h / SHORT at dc_low_4h (0.1% tolerance). Inserted before delta exit. Backup: `backups/before_srs_tradier_202604101730.py`. DO NOT revert SRS block. DO NOT touch sync_real_positions_from_api or tradeable_keys. | user |
| `config_tradier.py` | RELOCKED 2026-04-10 23:25 | Prior: SRS. **2026-04-10 23:25 NEW**: EXIT_SCORER_MIN_CONDITIONS/K_EXTREME/DC_EXTREME/PARTIAL_SCORE/FULL_SCORE (configurable exit scorer params). UNIVERSAL_NOLOSS_GATE=True, EXIT_HARD_MAX_LOSS_CAP_ENABLED=True, HARD_MAX_LOSS_PCT=-5.0. Backup: backups/before_scorer_config_ct_202604102320.py. DO NOT change START_POSITION_SIZE or WT_DC_ENTRY_THRESHOLD without sweep proof. | user |
| `wt_dc_exit_scorer.py` | LOCKED 2026-04-10 23:25 | **2026-04-10 23:25 NEW**: score_exit() now accepts cfg param. Reads EXIT_SCORER_MIN_CONDITIONS (3-5), EXIT_SCORER_K_EXTREME (65-85), EXIT_SCORER_DC_EXTREME (0.70-0.90) from config. Defaults match previous hardcoded values. Backup: backups/before_scorer_config_202604102320.py. DO NOT revert to hardcoded params. | user |
| `tradier_manage.py` | RELOCKED 2026-04-10 23:25 | Prior: SRS. **2026-04-10 23:25 NEW**: score_exit() call now passes cfg=config. Backup: backups/before_scorer_config_tm_202604102320.py. DO NOT revert cfg= param. | user |
| `tradier_manage.py` | RELOCKED 2026-04-20 21:05 | **2026-04-20 21:05 NEW**: (1) LOCAL_EXTREMES_MIN_SCORE entry gate at line ~1444 — skips entry if LE score < 45.0 (LE_MIN_SCORE_SKIP reason). (2) DYNAMIC_SCORE_COUNTER_EXIT at line ~4088 in evaluate_stop() — exits if opposite-direction LE score >= 55.0, fires after STOCK_MIN_HOLD, before NOLOSS gate. Both gated by config. Backup: before_le_winner_wire_*. DO NOT remove either gate without sweep proof. **Prior (2026-04-20 20:15)**: NOLOSS_BB1H_GATE + PEAK_GIVEBACK. | user |
| `tradier_manage.py` | RELOCKED 2026-04-20 22:32 | **2026-04-20 22:32 NEW**: RZ_BREAKOUT_ENTRY_ENABLED entry path wired at line ~1368 (third path after delta+WT/DC, before variance_fix). Band approach: LONG fires if bb_pctb_1h in [rz_bot, rz_bot+band]; SHORT if [rz_top-band, rz_top]. Defaults False. Backup: before_rz_breakout_entry_tradier_*. DO NOT remove RZ_BREAKOUT block. Enable only after sweep proof (rz_breakout_tradier tier). | user |
| `config_tradier.py` | RELOCKED 2026-04-20 21:00 | **2026-04-20 21:00 NEW**: LOCAL_EXTREMES_MIN_SCORE=45.0, DYNAMIC_SCORE_COUNTER_EXIT_ENABLED=True, DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD=55.0, TRADIER_MIN_HOLD_MINUTES=100 (was 240). le_dynamic_tradier_v2 winner (262sym Sharpe 3.5479). Needs tradier_manage.py unlock to wire entry gate + counter-exit. **Prior (2026-04-20 20:15)**: NOLOSS_BB1H_GATE_ENABLED=True. **Prior (2026-04-20 20:05)**: GAIN_EROSION in LOSS_EXIT_TECHNICAL_BYPASS. DO NOT disable NOLOSS_BB1H_GATE, PEAK_GIVEBACK, or LOCAL_EXTREMES_MIN_SCORE without sweep proof. | user |
| `ez_manage.py` | RELOCKED 2026-04-20 | **2026-04-20 NEW**: MOMENTUM_TP k-thresholds tightened — k_15m >90→>95, <10→<5; k_15m in K3M_BOUNCE_TURN >80→>95; k_1h in K3M_BOUNCE_TURN >80→>85; SHORT mirror: k_15m <20→<5, k_1h <20→<15. Backups: before_k_threshold_ez_*. DO NOT re-loosen below 95/5 (k_15m) or 85/15 (k_1h). **Prior (2026-04-14)**: SRS bypass added to IMMEDIATE_REDUCE_BLOCKED_NOLOSS. DO NOT remove SRS bypass. | user |
| `ez_manage.py` | RELOCKED 2026-04-20 22:32 | **2026-04-20 22:32 NEW**: RZ_BREAKOUT_ENTRY_ENABLED early-path in evaluate_technical_indicator_signals (line ~17149) — fires before alignment/HTF/trigger gates when bb_pctb_1h in breakout band. Defaults False. Backup: before_rz_breakout_entry_ez_*. DO NOT remove RZ_BREAKOUT block. Enable only after sweep proof. | user |
| `tradier_api.py` | LOCKED 2026-03-13 | Tradier API client stable — any breakage kills all stock trading | user |
| `tradier_positions.py` | DOUBLE-LOCKED 2026-03-30 | Position sync fixed after 5-week break. DO NOT touch sync_real_positions_from_api. | user |

## Actively Editable (Hot Zone — Requires Care)

These files are currently being worked on. They are NOT locked but treat every edit as high risk.

| File | Why It's Active | Last Known Issue |
|------|----------------|-----------------|
| `ez_manage.py` | Main orchestrator — trading decisions, constant tuning | Hedge execution, reduce fill verification |
| `ez_positions_quick.py` | Scalp/hedge execution — constant tuning | Ratio recovery, staleness gate |
| `ez_news_scanner.py` | News scanner — active dev | Free sources v4 |

---

## Rules For Claude When This File Exists

1. **Before editing ANY file** — check this list.
2. If the file is in the **Locked** table → STOP. Tell the user the file is locked and ask for explicit unlock.
3. If a fix to a Hot Zone file would require touching a Locked file → raise it explicitly, get permission first.
4. When a Hot Zone file is confirmed stable and the user says "lock it" → add it to the Locked table immediately.
5. Never silently edit a locked file "just this one import" or "just one line" — the lock is absolute.
