#!/usr/bin/env python3
"""Single patch: Apply ALL DC moment/qty changes to ez_rankings.py and ez_positions_quick.py.
Run from /home/niels/binance/"""
import os, sys
os.chdir("/home/niels/binance")

# =====================================================================
# 1. PATCH ez_rankings.py — add dc_moment/dc_qty to save_rankings_json
# =====================================================================
with open("ez_rankings.py", "r") as f:
    code = f.read()

# Find the anchor: right before "rankings_dict = recursively_convert_np"
anchor = "        rankings_dict = recursively_convert_np(rankings_dict)\n        async with aiofiles.open(RANKINGS_FILE, \"w\") as f:"
if anchor not in code:
    print("FAILED - anchor not found in ez_rankings.py")
    sys.exit(1)

dc_block = '''        # === DC MOMENT + QTY: cross-TF analysis + cross-symbol ranking ===
        _dc = {}
        _tfs_all = ['3m', '15m', '1h', '4h', 'D']
        for _sym in rankings_dict:
            _ind = indicators_data.get(_sym, {})
            if not _ind: continue
            _w, _p = {}, {}
            _cp = float(_ind.get('current_price') or 0)
            if _cp <= 0: continue
            for _tf in _tfs_all:
                _dw = float(_ind.get(f'dc_width_{_tf}') or 0)
                _dp = float(_ind.get(f'dc_position_{_tf}') or 0)
                if _dw <= 0:
                    _dch = float(_ind.get(f'dc_high_{_tf}') or 0)
                    _dcl = float(_ind.get(f'dc_low_{_tf}') or 0)
                    if _dch > 0 and _dcl > 0 and _dch > _dcl:
                        _dw = ((_dch - _dcl) / _dcl) * 100
                        _dp = max(0.0, min(1.0, (_cp - _dcl) / (_dch - _dcl)))
                if _dw > 0: _w[_tf] = _dw
                if 0 <= _dp <= 1.0: _p[_tf] = _dp
            if len(_w) < 2: continue
            _wc = _w.get('D', 0) * 0.50 + _w.get('4h', 0) * 0.30 + _w.get('1h', 0) * 0.20
            _ltf_w = sum(_w.get(t, 0) for t in ['3m', '15m']) / max(1, sum(1 for t in ['3m', '15m'] if t in _w))
            _htf_w = sum(_w.get(t, 0) for t in ['D', '4h', '1h']) / max(1, sum(1 for t in ['D', '4h', '1h'] if t in _w))
            _exp = _ltf_w / _htf_w if _htf_w > 0 else 0.0
            _hp = _p.get('D', 0.5) * 0.5 + _p.get('4h', 0.5) * 0.3 + _p.get('1h', 0.5) * 0.2
            _lp = _p.get('3m', 0.5) * 0.5 + _p.get('15m', 0.5) * 0.5
            _trend = (_hp - 0.5) * 2.0
            if _trend > 0:
                _pbd = max(0.0, _hp - _lp) / max(_hp, 0.01)
            else:
                _pbd = max(0.0, _lp - _hp) / max(1.0 - _hp, 0.01)
            _pbd = min(1.0, _pbd)
            _eb = min(1.3, max(1.0, _exp * 0.65 + 0.35)) if _exp > 1.0 else max(0.7, _exp)
            _moment = max(-100.0, min(100.0, _trend * _pbd * _eb * 100.0))
            _dc[_sym] = {'wc': _wc, 'exp': round(_exp, 3), 'hp': round(_hp, 3), 'lp': round(_lp, 3), 'moment': round(_moment, 1), 'w': _w, 'p': _p}
        if _dc:
            _sorted = sorted(_dc.items(), key=lambda x: x[1]['wc'], reverse=True)
            _n = len(_sorted)
            for _ri, (_s, _d) in enumerate(_sorted):
                _wr = (_n - _ri) / _n
                _d['wr'] = round(_wr, 4)
                _d['qty'] = round(_d['moment'] * _wr, 1)
            for _s, _d in _dc.items():
                if _s not in rankings_dict: continue
                rankings_dict[_s]['dc_moment'] = _d['moment']
                rankings_dict[_s]['dc_qty'] = _d.get('qty', 0)
                rankings_dict[_s]['dc_width_rank'] = _d.get('wr', 0)
                rankings_dict[_s]['dc_width_composite'] = round(_d['wc'], 2)
                rankings_dict[_s]['dc_expansion'] = _d['exp']
                rankings_dict[_s]['dc_htf_pos'] = _d['hp']
                rankings_dict[_s]['dc_ltf_pos'] = _d['lp']
                for _tf in _tfs_all:
                    if _tf in _d['w']: rankings_dict[_s][f'dc_width_{_tf}'] = round(_d['w'][_tf], 2)
                    if _tf in _d['p']: rankings_dict[_s][f'dc_pos_{_tf}'] = round(_d['p'][_tf], 4)
            _best_l = [(_s, _d['moment'], _d.get('qty',0)) for _s, _d in _sorted if _d['moment'] > 10][:3]
            _best_s = [(_s, _d['moment'], _d.get('qty',0)) for _s, _d in reversed(_sorted) if _d['moment'] < -10][:3]
            logger.info(f"[DC_INDEX] {_n} syms | L: {_best_l} | S: {_best_s}")
'''

code = code.replace(anchor, dc_block + "        " + anchor.lstrip())

with open("ez_rankings.py", "w") as f:
    f.write(code)
print(f"ez_rankings.py: dc_moment/dc_qty block inserted ({len(dc_block)} chars)")

# =====================================================================
# 2. PATCH ez_positions_quick.py — simplify to use dc_moment/dc_qty
# =====================================================================
with open("ez_positions_quick.py", "r") as f:
    code = f.read()

# Find the function
marker = "def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol='')"
if marker not in code:
    # Try without symbol param
    marker = "def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count)"
if marker not in code:
    print("FAILED - compute_dc_width_sizing not found")
    sys.exit(1)

# Find end of function (next def or next line at col 0 that's not indented)
start = code.index(marker)
# Find the return statement
ret_markers = ["    return final_mult, f'DC_IDX(", "    return final_mult, f'DC_WIDTH_IDX(", "    return final_mult, f'DC_WIDTH_AGG("]
end = -1
for rm in ret_markers:
    idx = code.find(rm, start)
    if idx > 0:
        end = code.index("\n", idx) + 1
        break
if end < 0:
    # fallback: find next def
    next_def = code.find("\ndef ", start + 10)
    end = next_def if next_def > 0 else start + 500

new_func = """def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol=''):
    if not getattr(cfg, 'DC_WIDTH_SIZING_ENABLED', False) or htf_count < 3 or current_price <= 0 or not symbol: return 0.0, ''
    try:
        from ez_rankings import get_ranking_data
        rd = get_ranking_data(symbol)
    except Exception: rd = None
    if not rd: return 0.0, ''
    dc_moment = rd.get('dc_moment', 0.0)
    dc_qty = rd.get('dc_qty', 0.0)
    if is_long and dc_qty <= 10: return 0.0, ''
    if not is_long and dc_qty >= -10: return 0.0, ''
    strength = abs(dc_qty) / 100.0
    max_mult = getattr(cfg, 'DC_WIDTH_MAX_MULT', 8.0)
    final_mult = max(2.0, min(max_mult, 1.0 + strength * (max_mult - 1.0)))
    return final_mult, f'DC(m={dc_moment:.0f},q={dc_qty:.0f},x={final_mult:.1f})'
"""

code = code[:start] + new_func + code[end:]

# Make sure the call passes symbol
old_call = "_dcw_mult, _dcw_reason = compute_dc_width_sizing(indicators, current_price, is_long, config_obj, _htfc)"
new_call = "_dcw_mult, _dcw_reason = compute_dc_width_sizing(indicators, current_price, is_long, config_obj, _htfc, symbol)"
code = code.replace(old_call, new_call)

with open("ez_positions_quick.py", "w") as f:
    f.write(code)
print("ez_positions_quick.py: compute_dc_width_sizing simplified to dc_moment/dc_qty")
print("\n=== ALL PATCHES APPLIED ===")
