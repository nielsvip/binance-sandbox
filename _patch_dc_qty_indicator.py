#!/usr/bin/env python3
"""Patch: Add 0dc_qty to _inject_dc_moment using cached rankings.json width_rank."""
import os, sys
os.chdir("/home/niels/binance")

with open("ez_indicators.py", "r") as f:
    code = f.read()

# Replace _inject_dc_moment to include dc_qty from cached rankings
old_method = '''    def _inject_dc_moment(self, symbol_data: Dict[str, Any]) -> None:
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
        symbol_data['0dc_ltf_pos'] = round(lp, 3)'''

new_method = '''    _dc_rankings_cache = {}
    _dc_rankings_ts = 0.0

    def _inject_dc_moment(self, symbol_data: Dict[str, Any]) -> None:
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
        # 0dc_qty = moment * width_rank from rankings.json (cross-symbol)
        now = time.time()
        if now - self.__class__._dc_rankings_ts > 60:
            try:
                rfile = Path(config.DATA_DIR) / 'rankings.json'
                if rfile.exists():
                    with open(rfile) as f: self.__class__._dc_rankings_cache = json.load(f)
                    self.__class__._dc_rankings_ts = now
            except Exception: pass
        sym = symbol_data.get('symbol', '')
        rd = self.__class__._dc_rankings_cache.get(sym, {})
        wr = float(rd.get('dc_width_rank', 0.5))
        symbol_data['0dc_qty'] = round(moment * wr, 1)'''

if old_method not in code:
    print("FAILED - old _inject_dc_moment not found")
    sys.exit(1)

code = code.replace(old_method, new_method, 1)

# Make sure json is imported (it likely is but let's be safe)
if "import json" not in code.split("\n")[0:30]:
    # json is already imported via orjson or json in this file
    pass

with open("ez_indicators.py", "w") as f:
    f.write(code)
print("ez_indicators.py: 0dc_qty added (reads width_rank from rankings.json, cached 60s)")
