#!/usr/bin/env python3
"""Patch: Add dc_moment and dc_qty to the DC analysis block in ez_rankings.py,
and simplify compute_dc_width_sizing in ez_positions_quick.py to use them."""
import os
os.chdir("/home/niels/binance")

# ==================== PATCH ez_rankings.py ====================
with open("ez_rankings.py", "r") as f:
    code = f.read()

# Add dc_moment and dc_qty computation inside the dc_analysis loop,
# right after short_pb is computed
old_analysis = """            dc_analysis[sym] = {'w_composite': w_composite, 'expansion': round(expansion, 3), 'htf_pos': round(htf_pos, 3), 'ltf_pos': round(ltf_pos, 3), 'long_pb': round(long_pb, 3), 'short_pb': round(short_pb, 3), 'widths': widths, 'positions': positions}"""

new_analysis = """            # dc_moment: -100 (perfect short) to +100 (perfect long)
            # Trend direction from HTF position (0.5 = neutral)
            trend = (htf_pos - 0.5) * 2.0  # [-1.0, +1.0]
            # Pullback quality: how much LTF diverges from HTF in the profitable direction
            if trend > 0:  # bullish: want LTF below HTF (pullback)
                pb_depth = max(0.0, htf_pos - ltf_pos) / max(htf_pos, 0.01)
            else:  # bearish: want LTF above HTF (bounce)
                pb_depth = max(0.0, ltf_pos - htf_pos) / max(1.0 - htf_pos, 0.01)
            pb_depth = min(1.0, pb_depth)
            # Expansion bonus: new movers get amplified (1.0-1.3x)
            exp_bonus = min(1.3, max(1.0, expansion * 0.65 + 0.35)) if expansion > 1.0 else max(0.7, expansion)
            dc_moment = trend * pb_depth * exp_bonus * 100.0
            dc_moment = max(-100.0, min(100.0, dc_moment))
            # dc_qty computed after cross-symbol ranking (needs width_rank)
            dc_analysis[sym] = {'w_composite': w_composite, 'expansion': round(expansion, 3), 'htf_pos': round(htf_pos, 3), 'ltf_pos': round(ltf_pos, 3), 'long_pb': round(long_pb, 3), 'short_pb': round(short_pb, 3), 'dc_moment': round(dc_moment, 1), 'widths': widths, 'positions': positions}"""

if old_analysis not in code:
    print("FAILED - dc_analysis assignment not found in ez_rankings.py")
    exit(1)
code = code.replace(old_analysis, new_analysis, 1)

# Add dc_qty computation after width_rank is assigned (needs the rank)
old_width_rank = """                data['width_rank'] = round(width_rank, 4)"""
new_width_rank = """                data['width_rank'] = round(width_rank, 4)
                # dc_qty: dc_moment scaled by width rank (-100 to +100)
                data['dc_qty'] = round(data['dc_moment'] * width_rank, 1)"""

if old_width_rank not in code:
    print("FAILED - width_rank assignment not found")
    exit(1)
code = code.replace(old_width_rank, new_width_rank, 1)

# Add dc_moment and dc_qty to the rankings_dict output
old_write = """                rankings_dict[sym]['dc_long_opp_rank'] = data.get('long_opp_rank', 0)
                rankings_dict[sym]['dc_short_opp_rank'] = data.get('short_opp_rank', 0)"""
new_write = """                rankings_dict[sym]['dc_long_opp_rank'] = data.get('long_opp_rank', 0)
                rankings_dict[sym]['dc_short_opp_rank'] = data.get('short_opp_rank', 0)
                rankings_dict[sym]['dc_moment'] = data.get('dc_moment', 0)
                rankings_dict[sym]['dc_qty'] = data.get('dc_qty', 0)"""

if old_write not in code:
    print("FAILED - rankings_dict write block not found")
    exit(1)
code = code.replace(old_write, new_write, 1)

# Update the log line to show dc_moment/dc_qty
old_log = """            logger.info(f"[DC_INDEX] {n} symbols | TopLong: {[(s, round(pb,2)) for s,pb,_ in top_long[:3]]} | TopShort: {[(s, round(pb,2)) for s,pb,_ in top_short[:3]]} | Expanding: {[(s, round(e,2)) for s,e in expanding[:3]]}")"""
new_log = """            top_moment = sorted(dc_analysis.items(), key=lambda x: x[1].get('dc_qty', 0), reverse=True)
            best_long = [(s, d.get('dc_moment',0), d.get('dc_qty',0)) for s, d in top_moment[:3] if d.get('dc_moment',0) > 0]
            best_short = [(s, d.get('dc_moment',0), d.get('dc_qty',0)) for s, d in top_moment[-3:] if d.get('dc_moment',0) < 0]
            logger.info(f"[DC_INDEX] {n} symbols | BestLong(moment,qty): {[(s, m, q) for s,m,q in best_long]} | BestShort: {[(s, m, q) for s,m,q in best_short]}")"""

if old_log not in code:
    print("WARNING - log line not found, skipping log update")
else:
    code = code.replace(old_log, new_log, 1)

with open("ez_rankings.py", "w") as f:
    f.write(code)
print("ez_rankings.py: dc_moment + dc_qty added OK")


# ==================== PATCH ez_positions_quick.py ====================
with open("ez_positions_quick.py", "r") as f:
    code = f.read()

old_func_start = "def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol=''):"
old_func_end = "    return final_mult, f'DC_IDX(wr={width_rank:.0%},opp={opp_rank:.0%},pb={pb_quality:.2f},exp={expansion:.2f},D={w_D:.1f}%,4h={w_4h:.1f}%,htf_p={htf_p:.2f},ltf_p={ltf_p:.2f},mult={final_mult:.1f}x)'"

if old_func_start not in code or old_func_end not in code:
    print("FAILED - compute_dc_width_sizing not found in quick")
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
    dc_moment = rd.get('dc_moment', 0.0)
    dc_qty = rd.get('dc_qty', 0.0)
    if is_long and dc_qty <= 10: return 0.0, ''
    if not is_long and dc_qty >= -10: return 0.0, ''
    strength = abs(dc_qty) / 100.0
    max_mult = getattr(cfg, 'DC_WIDTH_MAX_MULT', 8.0)
    final_mult = 1.0 + strength * (max_mult - 1.0)
    final_mult = max(2.0, min(max_mult, final_mult))
    return final_mult, f'DC_IDX(moment={dc_moment:.0f},qty={dc_qty:.0f},mult={final_mult:.1f}x)'"""

code = code[:start_idx] + new_func + code[end_idx:]

with open("ez_positions_quick.py", "w") as f:
    f.write(code)
print("ez_positions_quick.py: simplified to dc_moment/dc_qty OK")
print("\n=== ALL PATCHES APPLIED ===")
