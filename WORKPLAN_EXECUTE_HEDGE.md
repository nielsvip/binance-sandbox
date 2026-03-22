# Work Plan: execute_now / Webhook / HedgeEngine Overhaul

**Working from:** `ssh niels@157.180.125.52:/home/niels/binance/`
**Copy back to:** `/Users/niels/Documents/binance/` after each file is done
**Files to touch:** `ez_manage.py`, `ez_positions_quick.py`
**Always backup first** → `/home/niels/binance/backups/`
**Note:** `157.90.168.35` only fetches klines — NO scripts there

---

## 1. execute_now — Multiple Augment Leak (ez_manage.py)

### Root causes identified

The redis `execute_now:{key}:{side}` lock is acquired but **3 separate code paths bypass or re-trigger augments within the same lock window:**

| Path | Where | Problem |
|------|--------|---------|
| A | `place_maker_order` line ~11619 | Account error → calls `send_webhook` → returns `True` (maker "succeeded") |
| B | `place_maker_order` line ~11712 | Timeout → sends webhook with `order_ids_to_cancel` → returns `True` |
| C | `execute_now` line ~12099 | Maker "succeeded" (True) but verify fails → sends ANOTHER webhook as UNVERIFIED fallback |

Paths A+C and B+C can **both fire in sequence**, producing two augment orders.
Also: `augmented_positions` is only checked in `execute_trade_wrapper` (quick), **not** inside `execute_now` directly.

### Fixes

#### 1a. Add a single-fire augment guard inside `execute_now`
Add an in-memory flag `_aug_webhook_sent: Dict[str, float]` on the trade manager.
Before any augment webhook/fallback fires, check this flag.
Set it immediately on first send; clear on lock release.

```python
# In execute_now, before any augment webhook send:
aug_sent_key = f"aug_sent:{position_key}"
if self._aug_webhook_sent.get(aug_sent_key, 0) > time.time() - 120:
    logger.warning(f"[AUG_DEDUP] {position_key}: Augment already sent, skipping duplicate.")
    return "BLOCK_AUG_ALREADY_SENT"
self._aug_webhook_sent[aug_sent_key] = time.time()
```

#### 1b. Add `augmented_positions` check inside `execute_now`
Right after lock acquired, check `self.augmented_positions.get(position_key)`:

```python
if is_augment and position_key in self.augmented_positions:
    aug_dt = self.augmented_positions[position_key]
    if isinstance(aug_dt, datetime):
        mins = (datetime.now(timezone.utc) - aug_dt).total_seconds() / 60
        if mins < 15:  # same cooldown as execute_trade_wrapper
            return f"BLOCK_AUG_ALREADY_IN_AUGMENTED_POSITIONS ({mins:.1f}m ago)"
```

#### 1c. Consolidate maker fallback path
When `place_maker_order` returns `(False, 0.0)` (lock busy/account error), do NOT fall through to the "unverified" webhook path. Return `"BLOCK_MAKER_BUSY"` immediately.

```python
maker_success, executed_qty = await self.place_maker_order(...)
if not maker_success:
    return "BLOCK_MAKER_FAILED_NO_FALLBACK"  # NEW — was previously falling through
```

The account-error webhook inside `place_maker_order` should be **disabled for augments** (see section 3).

---

## 2. Order Verification Before Webhook (ez_manage.py)

### Current problem
`send_webhook` only cancels `order_ids_to_cancel` (tracked from current maker session), but:
- Stale orders from previous sessions may still be open
- After cancel, sleep is only 0.5s (not enough for Binance to process)
- Doesn't re-verify position state thoroughly before firing webhook

### Fix: `_safe_cancel_and_verify` helper (new method)

Add to `MultiAccountTradeManager`:
```python
async def _safe_cancel_and_verify(self, account_key, position_key, symbol, side, position_side, qty, positionAmt, timeout=8.0) -> tuple[bool, bool]:
    """
    Cancel ALL open orders for (symbol, side, positionSide).
    Wait for confirmation they're gone.
    Re-verify position state via websocket.
    Returns: (orders_cleared, already_filled)
    """
    try:
        account = self.accounts.get(account_key)
        client = account.client if account else None
        # 1. Cancel all open orders for this symbol+side
        if client:
            open_orders = await self.get_cached_open_orders(account_key, symbol)
            relevant = [o for o in open_orders
                        if o.get('side') == side
                        and o.get('positionSide') == position_side]
            for o in relevant:
                try:
                    await asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=o['orderId'])
                except Exception: pass
            # 2. Wait and confirm cancellation (up to 3s)
            deadline = time.time() + 3.0
            while time.time() < deadline:
                await asyncio.sleep(0.4)
                remaining = await self.get_cached_open_orders(account_key, symbol)
                if not any(o.get('side') == side for o in remaining):
                    break
        # 3. Re-verify position state
        already_filled = await verify_trade_via_websocket(
            self, account_key, position_key, qty,
            is_long=(position_side == 'LONG'),
            timeout_seconds=timeout,
            initial_positionAmt=positionAmt,
            action='AUGMENT' if side in ('BUY',) else 'REDUCE')
        return True, already_filled
    except Exception as e:
        logger.error(f"[CANCEL_VERIFY] {position_key}: {e}")
        return False, False
```

#### Call this from `send_webhook` for ALL cases (not just `order_ids_to_cancel`):
```python
# Replace the current order_ids_to_cancel block with:
orders_cleared, already_filled = await self._safe_cancel_and_verify(
    account_key, position_key, symbol, side, position_side, quantity, positionAmt)
if already_filled:
    logger.warning(f"[WEBHOOK_ABORT] {position_key}: Already filled after cancel. No webhook needed.")
    return True
# Only proceed to send webhook if not already filled
```

---

## 3. Disable Augment Webhooks in _service and _quick

### 3a. `ez_manage.py` — Disable augment webhooks in place_maker_order

**Account error path** (~line 11619):
```python
# BEFORE (fires augment webhook on account error):
await self.send_webhook(position_key, ..., f"{reason}_ACC_ERR", ...)
return True, qty_abs

# AFTER:
logger.error(f"[MAKER_ACC_ERR] {position_key}: Account error, blocking augment. No webhook.")
return False, 0.0
```

**Timeout fallback path** (~line 11712):
```python
# BEFORE:
await self.send_webhook(..., order_ids_to_cancel=tracked_order_ids)

# AFTER: only send for REDUCE, never for augment via this path
if not is_augment:
    await self.send_webhook(..., order_ids_to_cancel=tracked_order_ids)
else:
    logger.warning(f"[AUG_WEBHOOK_BLOCKED] {position_key}: Augment timeout, blocking fallback webhook.")
```

**Unverified fallback path in execute_now** (~line 12099):
```python
# BEFORE:
fallback_success = await self.send_webhook(..., f"{reason}_UNVERIFIED_SHADOW_QWH", ...)

# AFTER: hard block for augments
if is_augment:
    logger.warning(f"[AUG_WEBHOOK_BLOCKED] {position_key}: Unverified augment, NOT sending fallback webhook.")
    return "BLOCK_UNVERIFIED_AUG_NO_WEBHOOK"
```

### 3b. `ez_positions_quick.py` — Disable augment webhook in execute_trade_wrapper

Around line 5894 (augment success path):
```python
# BEFORE:
success = await tracker_manager.send_webhook(position_key, real_amt, side, current_price, qty, False, reason)

# AFTER: block augment webhooks completely from _quick
# success = await tracker_manager.send_webhook(...)  ← DISABLED
logger.info(f"[AUG_WEBHOOK_DISABLED] {position_key}: Augment webhook suppressed in _quick.")
```

The REDUCE webhook paths (lines 5865, 6310, 6377) stay enabled — those are fine.

---

## 4. RatingRegistry — Strict Hedge Qualification Filter

### Current problem
`get_hottest_hedge` returns any symbol with `score >= 10` from `top_longs/top_shorts`.
No check for:
- Is sma_200_15m pointing UP (for longs)?
- Is price above sma_200_15m (for longs)?
- Is price above dc_basis_15m (for longs)?
(Mirror rules for shorts)

### Fix: Add structural filter in `get_hottest_hedge`

Lookup raw indicators from `self.data_manager._cold_data` during candidate iteration:

```python
def get_hottest_hedge(self, account_key, target_side, exclude_symbols=None):
    candidates = self.top_longs if target_side == "LONG" else self.top_shorts
    allowed_symbols = set(self.trade_manager.symbols_active)
    results = []
    cold = getattr(self.data_manager, '_cold_data', {}) or {}  # raw indicator snapshot

    for sym, score, price in candidates:
        if exclude_symbols and sym in exclude_symbols: continue
        if sym not in allowed_symbols: continue
        if self.hedge_usage.get(f"{account_key}:{sym}", 0) >= 1: continue

        # ── STRUCTURAL FILTER ──────────────────────────────────────────
        ind = cold.get(sym, {})
        sma200 = float(ind.get('sma_200_15m', 0) or 0)
        sma200_prev = float(ind.get('sma_200_15m_prev', 0) or 0)
        dc_basis = float(ind.get('dc_basis_15m', 0) or 0)

        if target_side == 'LONG':
            # Must be above 200SMA, 200SMA must point UP, must be above DC basis
            if sma200 > 0 and price < sma200:
                continue  # price below 200SMA — structural downtrend, skip
            if sma200 > 0 and sma200_prev > 0 and sma200 <= sma200_prev:
                continue  # 200SMA pointing down — skip
            if dc_basis > 0 and price < dc_basis:
                continue  # below DC basis mid — skip
        else:  # SHORT
            # Must be below 200SMA, 200SMA must point DOWN, must be below DC basis
            if sma200 > 0 and price > sma200:
                continue  # price above 200SMA — skip
            if sma200 > 0 and sma200_prev > 0 and sma200 >= sma200_prev:
                continue  # 200SMA pointing up — skip
            if dc_basis > 0 and price > dc_basis:
                continue  # above DC basis mid — skip
        # ──────────────────────────────────────────────────────────────

        # Check no open position
        pos_key_l = construct_position_key(account_key, sym, 'LONG')
        pos_key_s = construct_position_key(account_key, sym, 'SHORT')
        pos_l = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(pos_key_l)
        pos_s = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(pos_key_s)
        if abs(safe_fetch_float(getattr(pos_l, 'positionAmt', 0))) > 0: continue
        if abs(safe_fetch_float(getattr(pos_s, 'positionAmt', 0))) > 0: continue

        if score >= 10:
            results.append((sym, score))
            if len(results) >= 10: break
    return results
```

### Also: Raise minimum score threshold
Change `if score >= 10:` to `if score >= 14:` — only genuinely hot candidates qualify for hedge duty.

### Also: Add same filter to `refresh_rankings` scoring
In `refresh_rankings`, when building `temp_longs`, apply the same structural check:
```python
sma200 = float(data.get('sma_200_15m', 0) or 0)
sma200_prev = float(data.get('sma_200_15m_prev', 0) or 0)
dc_basis = float(data.get('dc_basis_15m', 0) or 0)
# Only add to top_longs if structurally sound
if score_l >= 14 and (sma200 <= 0 or price >= sma200) and (sma200_prev <= 0 or sma200 >= sma200_prev) and (dc_basis <= 0 or price >= dc_basis):
    temp_longs.append((symbol, score_l, price))
# Only add to top_shorts if structurally sound (inverted)
if score_s >= 14 and (sma200 <= 0 or price <= sma200) and (sma200_prev <= 0 or sma200 <= sma200_prev) and (dc_basis <= 0 or price <= dc_basis):
    temp_shorts.append((symbol, score_s, price))
```

---

## 5. HedgeEngine: No Valid Candidates → Reduce Losing Position

### Current problem
When no external candidates qualify, `execute_dual_hedge` does:
1. Falls back to **same-symbol opposite-side hedge** ("self-hedge") at ratio=1.0
2. This is a hopeless hedge in a trending market — exactly what the user said NOT to do

There IS a final fallback reduce (35%) — but only triggered if BOTH elected AND actual hedge fail.

### Fix: Skip self-hedge when no candidates, go straight to reducing

In `execute_dual_hedge`, when `not candidates` OR after all external candidates fail:

```python
if not candidates:
    logger.warning(f"[HEDGE_NOCANDIDATE] {losing_symbol}: No structurally qualified candidates. Skipping hedge, reducing losing position.")
    await self._emergency_reduce_losing(account_key, losing_position_key, losing_symbol, losing_side)
    results['overall_status'] = 'reduced_no_hedge'
    return results
```

Add new method `_emergency_reduce_losing`:
```python
async def _emergency_reduce_losing(self, account_key, losing_key, losing_symbol, losing_side):
    """When no hedge candidate qualifies, reduce the losing position directly."""
    losing_pos = await self.tracker_manager.get_position(losing_key)
    if not losing_pos:
        losing_pos = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(losing_key)
    if not losing_pos:
        return
    current_amt = abs(safe_fetch_float(getattr(losing_pos, 'positionAmt', 0.0), 0.0))
    if current_amt <= 0:
        return
    current_price, _ = await self.data_manager.get_fresh_price(losing_symbol)
    if current_price <= 0:
        current_price, _ = await get_current_price(losing_symbol)
    # Reduce 40% of position — enough to materially cut exposure without full panic close
    reduce_qty = current_amt * 0.40
    logger.warning(f"🛡️ [HEDGE_FALLBACK_REDUCE] No hedge candidates for {losing_key}. Reducing {reduce_qty:.6f} ({40:.0f}%) @ ${current_price:.4f}")
    side = 'SELL' if losing_side == 'LONG' else 'BUY'
    success, result = await execute_trade_wrapper(
        trade_manager=self.trade_manager,
        tracker_manager=self.tracker_manager,
        hedge_engine=self,
        account_key=account_key,
        position_key=losing_key,
        positionAmt=current_amt,
        action='REDUCE',
        current_price=current_price,
        qty=reduce_qty,
        reason=f"HEDGE_NO_CANDIDATE_EMERGENCY_REDUCE",
        already_locked=False,
        is_hedge=False,   # Don't flag as hedge — allows STRICT_NO_LOSS bypass via reason
        override_qty=reduce_qty,
        data_manager=self.data_manager
    )
    logger.warning(f"🛡️ [HEDGE_FALLBACK_REDUCE] Result: {result}")
```

Also update the existing fallback at bottom of `execute_dual_hedge` (when both elected+actual fail):
```python
# Change 35% to 45% and use force reason
target_reduce = current_amt * 0.45
reason = "HEDGE_ALL_FAILED_EMERGENCY_REDUCE"
```

---

## Implementation Order

1. **Backup files first** on server
2. `ez_manage.py` — fixes 1a, 1b, 1c (multi-augment guard)
3. `ez_manage.py` — fix 2 (add `_safe_cancel_and_verify`, update `send_webhook`)
4. `ez_manage.py` — fix 3a (disable augment webhooks in place_maker_order + execute_now)
5. `ez_positions_quick.py` — fix 3b (disable augment webhook in execute_trade_wrapper)
6. `ez_positions_quick.py` — fix 4 (RatingRegistry structural filter)
7. `ez_positions_quick.py` — fix 5 (HedgeEngine no-candidate → reduce)
8. **Restart services**, monitor logs for 30min
9. **rsync back to local** after all changes confirmed stable

## Restart commands
```bash
sudo systemctl restart ez-positions-quick@ang ez-positions-quick@inf ez-positions-quick@flz ez-positions-quick@men ez-positions-quick@fin
sudo systemctl restart ez-manage@ang ez-manage@inf ez-manage@flz ez-manage@men ez-manage@fin
```

## Watch logs
```bash
ssh niels@157.180.125.52 "tail -f /home/niels/logs/ez_positions_quick_general_ang.log | grep -E 'AUG_DEDUP|AUG_WEBHOOK|CANCEL_VERIFY|HEDGE_NOCANDIDATE|STRUCTURAL'"
```
