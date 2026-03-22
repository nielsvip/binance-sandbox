# System Update Log: 2026-03-01
## Objective: Sentiment Ratio Enforcement & Quick-In-Quick-Out Optimization

This document records the critical architectural and logic changes made to enforce account-wide long/short ratios based on market sentiment and to maximize execution speed.

### 1. Global Sentiment & Ratio Enforcement
**File:** `ez_positions_quick.py` (Class: `SentimentExposureManager`)
- **Per-Account Rebalancing:** Rewrote `process_portfolio` to group positions by account.
- **Dynamic Target Ratios:**
  - Sentiment > 50: Target 1.5x Longs vs Shorts.
  - Sentiment < -50: Target 1.5x Shorts vs Longs.
  - Sentiment > 25: Target 1.35x.
- **Active Rebalancing:** If USD imbalance > $50, the system automatically augments profitable positions on the favored side.
- **Force Entry:** Added `_force_fresh_entry`. If an account is flat on the favored side and sentiment > 30 (or < -30), the system forces open a new position immediately.

### 2. "QUICK IN QUICK OUT" Speed Optimizations
**File:** `ez_positions_quick.py`
- **Loop Frequencies:**
  - `SentimentExposureManager`: 10s -> **5s**.
  - `HedgeEngine.monitor_hedge_health_loop`: 10s -> **5s**.
  - `quick_scalp_monitor_loop`: 10s -> **5s**.
- **Throttling:**
  - `TRADE_COOLDOWN_SECONDS`: 60s -> **15s**.
- **Neutral Bypass:** Allowed processing even when indicators are at 50 (neutral) to prevent stalling.

### 3. Aggressive Re-Entry (The Chase Logic)
**File:** `ez_positions_quick.py` & `TrackerManager`
- **Exit Tracking:** Added `last_exit_prices` registry. Automatically saves the price level of every `CLOSE` or `REDUCE`.
- **Chase Trigger:** If price moves **PAST** the last exit level (above for Longs, below for Shorts) within 1 hour, the system **re-enters no matter what** to catch the continuation move.

### 4. Profit Protection & Pyramiding
**File:** `ez_positions_quick.py`
- **Breakeven Enforcement:** If a position's `max_gain` reached 0.12% but current gain drops to 0.05%, it is **closed instantly** to prevent winners turning into losers.
- **Aggressive Pyramiding:** 
  - Starts adding to winners at **0.5% gain**.
  - **Moon Chase:** Adds 1.0x base size if gain > 2.5% and trend is strong.
  - **Ultra Runner:** Adds 1.5x base size if gain > 5.0% without waiting for pullbacks.

### 5. STRICT NO LOSS Mandatory Firewall
**Files:** `config.py`, `utils.py`, `ez_manage.py`, `ez_positions_service.py`
- **Protected Accounts:** `inf`, `men`, `fin`.
- **Execution Firewall:** Blocked all `CLOSE`/`REDUCE` orders in `execute_now` and `execute_trade_wrapper` if gain < -0.01%.
- **Service Override:** Blocked `EMERGENCY_EXIT` (Bleed Detection) and `HEDGE_FAILURE_KILL` for these accounts. They MUST be hedged rather than closed at a loss.

### 6. Hedge Engine Safety
**File:** `ez_positions_quick.py`
- Paired Exit: Never close a winning original position if the hedge is currently at a loss for strict accounts. Keep both until safe or combined profit is reached.
- Orphan Fix: Added `SENTIMENT_ORPHAN_FIX` to force-hedge losing positions that are opposite to the current market regime.

### 7. BASIS_CONDITION (The Ultimate Filter)
**File:** `config.py`, `ez_positions_quick.py`
- **Strict Basis Check:** Enabled via `BASIS_CONDITION = True`.
- **Opening Boycott:** NO Long can be opened unless price is ABOVE `dc_basis` for 15m, 1h, 4h, and D. NO Short can be opened unless price is BELOW all of them.
- Forced Liquidation: Any open position that crosses its basis level (Price < Basis for Long, Price > Basis for Short) on ANY of the 4 timeframes is **instantly closed**.
- Execution Level: Applied at both the Signal Rater (rating level) and Execution Wrapper (trade level).

### 8. SCALP MOMENTUM BOYCOTT
**File:** `ez_positions_quick.py`
- **Strict 1m Direction Check:** In `SCALP_MODE`, NO position can be opened or augmented if the 1-minute stochastic (`k_1m`) is moving in the wrong direction.
- **Rules:**
  - LONG: Blocked if `k_1m < k_1m_prev`.
  - SHORT: Blocked if `k_1m > k_1m_prev`.
- **Reason:** Ensures scalps only enter when micro-momentum is actively confirming the move.



---
**Verification:** All files compiled successfully with `python3 -m py_compile`. Logs confirm 5s heartbeat.
