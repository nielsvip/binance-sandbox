#!/usr/bin/env python3
"""Patch: Update compute_dc_width_sizing to use new dc_width_rank + dc_long_opp_rank/dc_short_opp_rank."""
import os
os.chdir("/home/niels/binance")

with open("ez_positions_quick.py", "r") as f:
    code = f.read()

old_func_start = "def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol=''):"
old_func_end = "    return final_mult, f'DC_WIDTH_IDX(rank={rank_pct:.0%},w_D={w_D:.1f}%,w_4h={w_4h:.1f}%,pb={avg_pb:.2f},mult={final_mult:.1f}x)'"

if old_func_start not in code or old_func_end not in code:
    print("FAILED - function not found")
    exit(1)

start_idx = code.index(old_func_start)
end_idx = code.index(old_func_end) + len(old_func_end)

new_func = """def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol=''):
    if not getattr(cfg, 'DC_WIDTH_SIZING_ENABLED', False) or htf_count < 3 or current_price <= 0 or not symbol: return 0.0, ''
    try:
        from ez_rankings import get_ranking_data
        rd = get_ranking_data(symbol)
    except Exception: rd = None
    if not rd: return 0.0, ''
    width_rank = rd.get('dc_width_rank', 0.0)
    if width_rank < 0.4: return 0.0, ''
    opp_rank = rd.get('dc_long_opp_rank', 0.0) if is_long else rd.get('dc_short_opp_rank', 0.0)
    pb_quality = rd.get('dc_long_pb', 0.0) if is_long else rd.get('dc_short_pb', 0.0)
    expansion = rd.get('dc_expansion', 0.0)
    combined = width_rank * 0.4 + opp_rank * 0.4 + min(expansion, 2.0) / 2.0 * 0.2
    if combined < 0.3: return 0.0, ''
    max_mult = getattr(cfg, 'DC_WIDTH_MAX_MULT', 8.0)
    final_mult = 1.0 + combined * (max_mult - 1.0)
    final_mult = max(2.0, min(max_mult, final_mult))
    if expansion > 1.2: final_mult = min(final_mult * 1.15, max_mult)
    w_D = rd.get('dc_width_D', 0)
    w_4h = rd.get('dc_width_4h', 0)
    htf_p = rd.get('dc_htf_pos', 0)
    ltf_p = rd.get('dc_ltf_pos', 0)
    return final_mult, f'DC_IDX(wr={width_rank:.0%},opp={opp_rank:.0%},pb={pb_quality:.2f},exp={expansion:.2f},D={w_D:.1f}%,4h={w_4h:.1f}%,htf_p={htf_p:.2f},ltf_p={ltf_p:.2f},mult={final_mult:.1f}x)'"""

code = code[:start_idx] + new_func + code[end_idx:]

with open("ez_positions_quick.py", "w") as f:
    f.write(code)
print("OK: compute_dc_width_sizing updated to use cross-TF/cross-symbol ranking")
