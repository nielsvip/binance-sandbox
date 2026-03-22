# TF_FOCUS_SPEC: Long/Short Ratio Enforcement Rollout

## Progress Tracking (binance-sandbox)

- [x] Research current ratio calculation and enforcement mechanisms.
- [x] Identify failure points: `ratio_rebalance_loop` blocked by `STRICT_NO_LOSS_BLOCK`, and entry sizing not using live ratio.
- [x] Create "Before" backups in `binance/backups/`:
    - `ez_manage_BEFORE_ratio_fix_20260310.py`
    - `ez_positions_quick_BEFORE_ratio_fix_20260310.py`
- [x] **Apply Fix to `ez_positions_quick.py`**:
    - Update `calculate_dynamic_quantity` signature to accept `account_key`.
    - Implement aggressive `ratio_mult` (up to 5x) based on live account skew.
    - Update internal call site in `ez_positions_quick.py`.
- [x] **Apply Fix to `ez_manage.py`**:
    - Update `RATIO_GATE` to bypass blocks when the side is needed to rebalance skew (> 60% on opposite side).
    - Update `ratio_rebalance_loop` to monitor/log skew instead of attempting blocked reductions.
- [x] Verify consistency across account-specific realtime scripts (`ez_positions_realtime_*.py`).
- [x] Final verification and syntax checks.

## Key Logic Changes
- **Organic Rebalancing**: Instead of cutting losers (forbidden), we now "enter big" on the underweight side.
- **Dynamic Multiplier**: `ratio_mult` scales from 0.2x (overweight) to 5.0x (underweight) based on live `%LONG` vs `%SHORT`.
- **Gate Intelligence**: The ratio gate now recognizes when an entry is a "rescue mission" for the account balance and lets it through regardless of market trend.
