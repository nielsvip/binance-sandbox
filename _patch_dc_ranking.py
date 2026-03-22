#!/usr/bin/env python3
"""Patch: Replace naive DC width ranking in ez_rankings.py with proper
cross-TF relationship analysis + cross-symbol ranking."""
import os
os.chdir("/home/niels/binance")

with open("ez_rankings.py", "r") as f:
    code = f.read()

# Find the DC WIDTH INDEX block and replace it with proper cross-TF + cross-symbol logic
old_start = "        # === DC WIDTH INDEX: rank all symbols by multi-TF Donchian Channel width ==="
old_end = """            logger.info(f"[DC_WIDTH_INDEX] Ranked {n} symbols. Top5: {_top5}. Bot5: {_bot5}")"""

if old_start not in code or old_end not in code:
    print("FAILED - DC WIDTH INDEX block not found")
    exit(1)

start_idx = code.index(old_start)
end_idx = code.index(old_end) + len(old_end)

new_block = '''        # === DC WIDTH + POSITION INDEX: cross-TF relationships + cross-symbol ranking ===
        dc_analysis = {}
        tfs_htf = ['D', '4h', '1h']
        tfs_ltf = ['3m', '15m']
        tfs_all = ['3m', '15m', '1h', '4h', 'D']
        for sym in rankings_dict:
            ind = indicators_data.get(sym, {})
            if not ind: continue
            widths, positions = {}, {}
            for tf in tfs_all:
                w = float(ind.get(f'dc_width_{tf}') or 0)
                p = float(ind.get(f'dc_position_{tf}') or 0)
                if w <= 0:
                    dch = float(ind.get(f'dc_high_{tf}') or 0)
                    dcl = float(ind.get(f'dc_low_{tf}') or 0)
                    cp = float(ind.get('current_price') or 0)
                    if dch > 0 and dcl > 0 and dch > dcl:
                        w = ((dch - dcl) / dcl) * 100
                        p = max(0.0, min(1.0, (cp - dcl) / (dch - dcl)))
                if w > 0: widths[tf] = w
                if 0 < p <= 1.0: positions[tf] = p
            if len(widths) < 2: continue
            # Composite width (D=50%, 4h=30%, 1h=20%)
            w_composite = widths.get('D', 0) * 0.50 + widths.get('4h', 0) * 0.30 + widths.get('1h', 0) * 0.20
            # Expansion ratio: are short TFs wider than long TFs? (new mover detection)
            ltf_avg = sum(widths.get(tf, 0) for tf in tfs_ltf) / max(1, sum(1 for tf in tfs_ltf if tf in widths))
            htf_avg = sum(widths.get(tf, 0) for tf in tfs_htf) / max(1, sum(1 for tf in tfs_htf if tf in widths))
            expansion = ltf_avg / htf_avg if htf_avg > 0 else 0.0
            # Position analysis: HTF trend vs LTF pullback
            pos_D = positions.get('D', 0.5)
            pos_4h = positions.get('4h', 0.5)
            pos_1h = positions.get('1h', 0.5)
            pos_15m = positions.get('15m', 0.5)
            pos_3m = positions.get('3m', 0.5)
            htf_pos = pos_D * 0.5 + pos_4h * 0.3 + pos_1h * 0.2
            ltf_pos = pos_3m * 0.5 + pos_15m * 0.5
            # LONG pullback: HTF trending up (pos > 0.5) + LTF pulled back (pos < 0.4)
            long_pb = max(0.0, (htf_pos - 0.4)) * max(0.0, (0.5 - ltf_pos)) * 4.0
            long_pb = min(1.0, long_pb)
            # SHORT pullback: HTF trending down (pos < 0.5) + LTF bounced (pos > 0.6)
            short_pb = max(0.0, (0.6 - htf_pos)) * max(0.0, (ltf_pos - 0.5)) * 4.0
            short_pb = min(1.0, short_pb)
            dc_analysis[sym] = {'w_composite': w_composite, 'expansion': round(expansion, 3), 'htf_pos': round(htf_pos, 3), 'ltf_pos': round(ltf_pos, 3), 'long_pb': round(long_pb, 3), 'short_pb': round(short_pb, 3), 'widths': widths, 'positions': positions}
        if dc_analysis:
            # Cross-symbol ranking by composite width
            sorted_by_width = sorted(dc_analysis.items(), key=lambda x: x[1]['w_composite'], reverse=True)
            n = len(sorted_by_width)
            for rank_idx, (sym, data) in enumerate(sorted_by_width):
                width_rank = (n - rank_idx) / n
                data['width_rank'] = round(width_rank, 4)
            # Cross-symbol ranking by long pullback quality
            sorted_by_long = sorted(dc_analysis.items(), key=lambda x: x[1]['long_pb'] * x[1].get('width_rank', 0), reverse=True)
            for rank_idx, (sym, data) in enumerate(sorted_by_long):
                data['long_opp_rank'] = round((n - rank_idx) / n, 4)
            # Cross-symbol ranking by short pullback quality
            sorted_by_short = sorted(dc_analysis.items(), key=lambda x: x[1]['short_pb'] * x[1].get('width_rank', 0), reverse=True)
            for rank_idx, (sym, data) in enumerate(sorted_by_short):
                data['short_opp_rank'] = round((n - rank_idx) / n, 4)
            # Write to rankings_dict
            for sym, data in dc_analysis.items():
                if sym not in rankings_dict: continue
                rankings_dict[sym]['dc_width_rank'] = data.get('width_rank', 0)
                rankings_dict[sym]['dc_width_composite'] = round(data['w_composite'], 2)
                rankings_dict[sym]['dc_expansion'] = data['expansion']
                rankings_dict[sym]['dc_htf_pos'] = data['htf_pos']
                rankings_dict[sym]['dc_ltf_pos'] = data['ltf_pos']
                rankings_dict[sym]['dc_long_pb'] = data['long_pb']
                rankings_dict[sym]['dc_short_pb'] = data['short_pb']
                rankings_dict[sym]['dc_long_opp_rank'] = data.get('long_opp_rank', 0)
                rankings_dict[sym]['dc_short_opp_rank'] = data.get('short_opp_rank', 0)
                for tf in tfs_all:
                    if tf in data['widths']: rankings_dict[sym][f'dc_width_{tf}'] = round(data['widths'][tf], 2)
                    if tf in data['positions']: rankings_dict[sym][f'dc_pos_{tf}'] = round(data['positions'][tf], 4)
            top_long = [(s, d['long_pb'], d.get('width_rank',0)) for s, d in sorted_by_long[:5] if d['long_pb'] > 0]
            top_short = [(s, d['short_pb'], d.get('width_rank',0)) for s, d in sorted_by_short[:5] if d['short_pb'] > 0]
            expanding = [(s, d['expansion']) for s, d in sorted(dc_analysis.items(), key=lambda x: x[1]['expansion'], reverse=True)[:5] if d['expansion'] > 1.0]
            logger.info(f"[DC_INDEX] {n} symbols | TopLong: {[(s, round(pb,2)) for s,pb,_ in top_long[:3]]} | TopShort: {[(s, round(pb,2)) for s,pb,_ in top_short[:3]]} | Expanding: {[(s, round(e,2)) for s,e in expanding[:3]]}")'''

code = code[:start_idx] + new_block + code[end_idx:]

with open("ez_rankings.py", "w") as f:
    f.write(code)
print("OK: DC WIDTH + POSITION cross-TF + cross-symbol ranking applied")
