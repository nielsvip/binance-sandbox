#!/usr/bin/env python3
"""Apply the 3 remaining missing fixes on the server."""
import os, sys
os.chdir("/home/niels/binance")

# === FIX 3: sma_500_dist + ema_20_dist scoring ===
with open("ez_positions_quick.py", "r") as f:
    code = f.read()

anchor = '            if not is_long and _sma200d_dist < -0.12: return 0, "WAIT", f"BLOCKED_SMA200D_EXTREME_BELOW({_sma200d_dist:.1%})_backtest#1"'
if anchor in code and "SMA500_DEEP" not in code:
    new_scoring = anchor + '''
        # sma_500_dist + ema_20_dist: best indicators per sweep (D: 0.106, 4h: 0.129)
        _sma500_1h = safe_fetch_float(i.get('sma_500_1h'), 0.0)
        _ema20_4h = safe_fetch_float(i.get('ema_20_4h'), 0.0)
        if _sma500_1h > 0:
            _s500_dist = (current_price - _sma500_1h) / _sma500_1h
            if is_long and _s500_dist < -0.03: score += 15; reasons.append(f"SMA500_DEEP({_s500_dist:.1%},+15)")
            elif is_long and _s500_dist > 0.08: score -= 10; reasons.append(f"SMA500_OVER({_s500_dist:.1%},-10)")
            elif not is_long and _s500_dist > 0.03: score += 15; reasons.append(f"SMA500_ABOVE({_s500_dist:.1%},+15)")
            elif not is_long and _s500_dist < -0.08: score -= 10; reasons.append(f"SMA500_UNDER({_s500_dist:.1%},-10)")
        if _ema20_4h > 0:
            _e20_dist = (current_price - _ema20_4h) / _ema20_4h
            if is_long and _e20_dist < -0.02: score += 10; reasons.append(f"EMA20_4H_DEEP({_e20_dist:.1%},+10)")
            elif not is_long and _e20_dist > 0.02: score += 10; reasons.append(f"EMA20_4H_ABOVE({_e20_dist:.1%},+10)")'''
    code = code.replace(anchor, new_scoring)
    with open("ez_positions_quick.py", "w") as f:
        f.write(code)
    print("OK: sma_500_dist + ema_20_dist scoring added")
else:
    if "SMA500_DEEP" in code:
        print("SKIP: sma_500_dist already present")
    else:
        print("FAILED: anchor not found for sma_500_dist")

# === FIX 4: REENTRY max-retry limit ===
with open("ez_manage.py", "r") as f:
    code = f.read()

old_handler = '''    async def _handle_stuck_order(self, position_key: str):
        """Aggressively clear locks so the system doesn't freeze"""
        if position_key in self.pending_orders:
            order = self.pending_orders[position_key]
            self.logger.warning(f"[OrderMonitor] ☢️ NUKING LOCKS for {position_key} due to stuck {order['action']}")
            await self.trade_manager.force_clear_execution_lock(position_key)
            await self.trade_manager.clear_all_cooldowns_for_position(position_key)
            async with self.trade_manager.dedupe_lock:
                self.trade_manager.order_deduplication.pop(position_key, None)
            del self.pending_orders[position_key]'''

new_handler = '''    _stuck_order_counts = {}

    async def _handle_stuck_order(self, position_key: str):
        """Aggressively clear locks so the system doesn't freeze. Limits REENTRY retries."""
        if position_key in self.pending_orders:
            order = self.pending_orders[position_key]
            action = order.get('action', '')
            self._stuck_order_counts[position_key] = self._stuck_order_counts.get(position_key, 0) + 1
            count = self._stuck_order_counts[position_key]
            self.logger.warning(f"[OrderMonitor] ☢️ NUKING LOCKS for {position_key} due to stuck {action} (attempt #{count})")
            await self.trade_manager.force_clear_execution_lock(position_key)
            await self.trade_manager.clear_all_cooldowns_for_position(position_key)
            async with self.trade_manager.dedupe_lock:
                self.trade_manager.order_deduplication.pop(position_key, None)
            del self.pending_orders[position_key]
            if count >= 5 and 'REENTRY' in action.upper():
                self.logger.critical(f"[OrderMonitor] 🛑 REENTRY_STUCK_LIMIT: {position_key} failed {count}x — setting 10min cooldown to stop loop")
                await self.trade_manager.set_trade_cooldown(position_key, cooldown_seconds=600)
                self._stuck_order_counts[position_key] = 0'''

if old_handler in code:
    code = code.replace(old_handler, new_handler)
    with open("ez_manage.py", "w") as f:
        f.write(code)
    print("OK: REENTRY max-retry limit added")
elif "REENTRY_STUCK_LIMIT" in code:
    print("SKIP: REENTRY limit already present")
else:
    print("FAILED: _handle_stuck_order anchor not found")

# === FIX 7: Tradier buffer + stop_atr ===
with open("tradier_manage.py", "r") as f:
    code = f.read()
changed = False
if "_buf = 0.001" in code:
    code = code.replace("_buf = 0.001", "_buf = 0.0005  # Marathon winner: buffer=0.0005")
    changed = True
if "atr * 2.5" in code:
    code = code.replace("atr * 2.5", "atr * 2.0  # Marathon winner: stop_atr=2.0")
    changed = True
if changed:
    with open("tradier_manage.py", "w") as f:
        f.write(code)
    print(f"OK: Tradier params fixed")
else:
    print("SKIP: Tradier params already correct or pattern not found")

# === SYNTAX CHECK ===
print("\nSyntax checks:")
import py_compile
for f in ["ez_indicators.py", "ez_positions_quick.py", "config.py", "ez_manage.py", "tradier_manage.py"]:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {f}: {e}")
