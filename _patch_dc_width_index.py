#!/usr/bin/env python3
"""Patch: Add DC width index to ez_rankings.py and update ez_positions_quick.py to use it."""
import sys

def patch_rankings():
    with open("ez_rankings.py", "r") as f:
        code = f.read()

    # 1. Add DC width computation inside save_rankings_json, right before the final save
    old_save = '        rankings_dict = recursively_convert_np(rankings_dict)\n        async with aiofiles.open(RANKINGS_FILE, "w") as f:'
    new_save = '''        # === DC WIDTH INDEX: rank all symbols by multi-TF Donchian Channel width ===
        dc_widths = {}
        for sym in rankings_dict:
            ind = indicators_data.get(sym, {})
            if not ind: continue
            w_scores = []
            w_vals = {}
            for tf, weight in [('D', 0.50), ('4h', 0.30), ('1h', 0.20)]:
                dch = float(ind.get(f'dc_high_{tf}') or 0)
                dcl = float(ind.get(f'dc_low_{tf}') or 0)
                if dch > 0 and dcl > 0 and dch > dcl:
                    w_pct = ((dch - dcl) / dcl) * 100
                    w_vals[tf] = w_pct
                    w_scores.append(w_pct * weight)
                else:
                    w_scores.append(0.0)
            composite = sum(w_scores)
            if composite > 0:
                dc_widths[sym] = {'composite': composite, 'width_D': w_vals.get('D', 0.0), 'width_4h': w_vals.get('4h', 0.0), 'width_1h': w_vals.get('1h', 0.0)}
        if dc_widths:
            sorted_dc = sorted(dc_widths.items(), key=lambda x: x[1]['composite'], reverse=True)
            n = len(sorted_dc)
            for rank_idx, (sym, wdata) in enumerate(sorted_dc):
                rank_pct = (n - rank_idx) / n
                rankings_dict[sym]['dc_width_rank_pct'] = round(rank_pct, 4)
                rankings_dict[sym]['dc_width_composite'] = round(wdata['composite'], 2)
                rankings_dict[sym]['dc_width_D'] = round(wdata.get('width_D', 0), 2)
                rankings_dict[sym]['dc_width_4h'] = round(wdata.get('width_4h', 0), 2)
                rankings_dict[sym]['dc_width_1h'] = round(wdata.get('width_1h', 0), 2)
            logger.info(f"[DC_WIDTH_INDEX] Ranked {n} symbols. Top5: {', '.join(f'{s}({d[\\'composite\\']:.1f})' for s,d in sorted_dc[:5])}. Bot5: {', '.join(f'{s}({d[\\'composite\\']:.1f})' for s,d in sorted_dc[-5:])}")
        rankings_dict = recursively_convert_np(rankings_dict)
        async with aiofiles.open(RANKINGS_FILE, "w") as f:'''

    if old_save in code:
        code = code.replace(old_save, new_save, 1)
        with open("ez_rankings.py", "w") as f:
            f.write(code)
        print("ez_rankings.py: DC width index added to save_rankings_json OK")
        return True
    else:
        print("FAILED - save target not found in ez_rankings.py")
        return False


def patch_quick():
    with open("ez_positions_quick.py", "r") as f:
        code = f.read()

    # 1. Replace the old compute_dc_width_sizing with one that reads from rankings.json
    old_func_start = "def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count):"
    old_func_end = "    return final_mult, f'DC_WIDTH_AGG({w_str},pb={pb_str},mult={final_mult:.1f}x)'"

    if old_func_start not in code:
        print("SKIP - old compute_dc_width_sizing not found (may already be patched)")
        # Check if already patched
        if "def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol=''):" in code:
            print("Already has symbol param version")
        return True

    # Find full old function
    start_idx = code.index(old_func_start)
    end_idx = code.index(old_func_end, start_idx) + len(old_func_end)
    old_func = code[start_idx:end_idx]

    new_func = """def compute_dc_width_sizing(indicators, current_price, is_long, cfg, htf_count, symbol=''):
    if not getattr(cfg, 'DC_WIDTH_SIZING_ENABLED', False) or htf_count < 3 or current_price <= 0 or not symbol: return 0.0, ''
    try:
        from ez_rankings import get_ranking_data
        rd = get_ranking_data(symbol)
    except Exception: rd = None
    if not rd: return 0.0, ''
    rank_pct = rd.get('dc_width_rank_pct', 0.0)
    if rank_pct < 0.5: return 0.0, ''
    pb_scores = []
    for tf in ['D', '4h', '1h']:
        dch = safe_fetch_float(indicators.get(f'dc_high_{tf}'), 0.0)
        dcl = safe_fetch_float(indicators.get(f'dc_low_{tf}'), 0.0)
        dcb = safe_fetch_float(indicators.get(f'dc_basis_{tf}'), 0.0)
        if dch <= 0 or dcl <= 0 or dch <= dcl: continue
        if is_long:
            pb = (dch - current_price) / (dch - dcl)
            in_zone = dcl < current_price < dcb
        else:
            pb = (current_price - dcl) / (dch - dcl)
            in_zone = dcb < current_price < dch
        pb = max(0.0, min(1.0, pb))
        if pb < 0.15 or pb > 0.85: pq = 0.0
        elif 0.3 <= pb <= 0.7: pq = 1.0
        elif pb < 0.3: pq = (pb - 0.15) / 0.15
        else: pq = (0.85 - pb) / 0.15
        if in_zone: pq = min(pq * 1.3, 1.3)
        pb_scores.append(pq)
    if not pb_scores or max(pb_scores) < 0.2: return 0.0, ''
    avg_pb = sum(pb_scores) / len(pb_scores)
    max_mult = getattr(cfg, 'DC_WIDTH_MAX_MULT', 8.0)
    final_mult = 1.0 + rank_pct * avg_pb * (max_mult - 1.0)
    final_mult = max(2.0, min(max_mult, final_mult))
    w_D = rd.get('dc_width_D', 0)
    w_4h = rd.get('dc_width_4h', 0)
    return final_mult, f'DC_WIDTH_IDX(rank={rank_pct:.0%},w_D={w_D:.1f}%,w_4h={w_4h:.1f}%,pb={avg_pb:.2f},mult={final_mult:.1f}x)'"""

    code = code[:start_idx] + new_func + code[end_idx:]

    # 2. Update the fast-path call to pass symbol
    old_call = "_dcw_mult, _dcw_reason = compute_dc_width_sizing(indicators, current_price, is_long, config_obj, _htfc)"
    new_call = "_dcw_mult, _dcw_reason = compute_dc_width_sizing(indicators, current_price, is_long, config_obj, _htfc, symbol)"
    if old_call in code:
        code = code.replace(old_call, new_call, 1)
    else:
        print("WARNING - fast-path call not found")

    # 3. Remove the module-level _dc_width_index variables if they exist
    for line_to_remove in [
        "# Module-level DC width index: refreshed in RatingRegistry.refresh_rankings()\n",
        "_dc_width_index = {}  # {symbol: {'rank_pct': 0.0-1.0, 'width_D': float, 'width_4h': float, 'width_1h': float, 'composite': float}}\n",
        "_dc_width_index_ts = 0.0\n",
    ]:
        code = code.replace(line_to_remove, "")

    with open("ez_positions_quick.py", "w") as f:
        f.write(code)
    print("ez_positions_quick.py: compute_dc_width_sizing updated to use rankings.json OK")
    return True


if __name__ == "__main__":
    import os
    os.chdir("/home/niels/binance")
    ok1 = patch_rankings()
    ok2 = patch_quick()
    if ok1 and ok2:
        print("\n=== ALL PATCHES APPLIED ===")
    else:
        print("\n=== SOME PATCHES FAILED ===")
        sys.exit(1)
