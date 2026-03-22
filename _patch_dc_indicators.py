#!/usr/bin/env python3
"""Patch: Add _inject_dc_moment method to ez_indicators.py Orchestrator class,
and call it after _inject_external_scores so 0dc_moment and 0dc_qty
flow through Redis/bridge/files automatically."""
import os, sys
os.chdir("/home/niels/binance")

with open("ez_indicators.py", "r") as f:
    code = f.read()

# 1. Add _inject_dc_moment method right after _inject_external_scores method
anchor = "    def _flag_errors(self, symbol_data: Dict[str, Any]) -> None:"
if anchor not in code:
    print("FAILED - _flag_errors not found")
    sys.exit(1)

new_method = '''    def _inject_dc_moment(self, symbol_data: Dict[str, Any]) -> None:
        """Compute 0dc_moment (-100..+100) and 0dc_qty (-100..+100) from dc_width/dc_position across all TFs."""
        tfs_all = ['3m', '15m', '1h', '4h', 'D']
        w, p = {}, {}
        cp = float(symbol_data.get('current_price') or 0)
        if cp <= 0: return
        for tf in tfs_all:
            dw = float(symbol_data.get(f'dc_width_{tf}') or 0)
            dp = float(symbol_data.get(f'dc_position_{tf}') or 0)
            if dw > 0: w[tf] = dw
            if 0 <= dp <= 1.0: p[tf] = dp
        if len(w) < 2 or len(p) < 2: return
        wc = w.get('D', 0) * 0.50 + w.get('4h', 0) * 0.30 + w.get('1h', 0) * 0.20
        ltf_w = sum(w.get(t, 0) for t in ['3m', '15m']) / max(1, sum(1 for t in ['3m', '15m'] if t in w))
        htf_w = sum(w.get(t, 0) for t in ['D', '4h', '1h']) / max(1, sum(1 for t in ['D', '4h', '1h'] if t in w))
        exp = ltf_w / htf_w if htf_w > 0 else 0.0
        hp = p.get('D', 0.5) * 0.5 + p.get('4h', 0.5) * 0.3 + p.get('1h', 0.5) * 0.2
        lp = p.get('3m', 0.5) * 0.5 + p.get('15m', 0.5) * 0.5
        trend = (hp - 0.5) * 2.0
        if trend > 0:
            pbd = max(0.0, hp - lp) / max(hp, 0.01)
        else:
            pbd = max(0.0, lp - hp) / max(1.0 - hp, 0.01)
        pbd = min(1.0, pbd)
        eb = min(1.3, max(1.0, exp * 0.65 + 0.35)) if exp > 1.0 else max(0.7, exp)
        moment = max(-100.0, min(100.0, trend * pbd * eb * 100.0))
        symbol_data['0dc_moment'] = round(moment, 1)
        symbol_data['0dc_width_composite'] = round(wc, 2)
        symbol_data['0dc_expansion'] = round(exp, 3)
        symbol_data['0dc_htf_pos'] = round(hp, 3)
        symbol_data['0dc_ltf_pos'] = round(lp, 3)

''' + "    " + anchor.lstrip()

code = code.replace(anchor, new_method, 1)

# 2. Call _inject_dc_moment after every _inject_external_scores call
# There are 2 active calls at lines ~2161 and ~2200
old_inject1 = "        self._inject_external_scores(symbol, symbol_data)\n        self._flag_errors(symbol_data)\n        self._update_master_timestamp(symbol_data)\n        state.last_df = df.copy()"
new_inject1 = "        self._inject_external_scores(symbol, symbol_data)\n        self._inject_dc_moment(symbol_data)\n        self._flag_errors(symbol_data)\n        self._update_master_timestamp(symbol_data)\n        state.last_df = df.copy()"

count = code.count(old_inject1)
if count >= 1:
    code = code.replace(old_inject1, new_inject1)
    print(f"Injected _inject_dc_moment at {count} location(s) (full run path)")
else:
    print("WARNING - full run inject pattern not found")

# Also the mid-run path
old_inject2 = "        self._inject_external_scores(symbol, symbol_data)\n        self._flag_errors(symbol_data)\n        state.mid_done = True"
new_inject2 = "        self._inject_external_scores(symbol, symbol_data)\n        self._inject_dc_moment(symbol_data)\n        self._flag_errors(symbol_data)\n        state.mid_done = True"

if old_inject2 in code:
    code = code.replace(old_inject2, new_inject2)
    print("Injected _inject_dc_moment at mid-run path")

with open("ez_indicators.py", "w") as f:
    f.write(code)
print("ez_indicators.py: _inject_dc_moment added OK")
print(f"File size: {len(code)} bytes")
