"""
Final builder: TEMPLATE.xlsx + 00_README + 01_TRADE_PATHS + 02_FILTER_GATES + 03_CONFIG_COVERAGE + 04_LIVE_VS_VEC_GAPS + 05_SERVICE_AUDIT + 06_TRADE_LIFECYCLE
Single-shot build to avoid staged-save issues.
"""
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side, NamedStyle
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import CellIsRule
from pathlib import Path
import re

TEMPLATE = Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
OUTPUT = Path("/Users/niels/Documents/binance/SPREADSHEETS/TRADE_PATH_INVENTORY.xlsx")

NAVY="1F4E78"; TEAL="2E75B6"; LIGHT_BLUE="D9E1F2"; LIGHT_GREEN="E2EFDA"
LIGHT_YELLOW="FFF2CC"; LIGHT_RED="FCE4EC"; RED="C00000"; ORANGE="ED7D31"
LIGHT_GREY="F2F2F2"; GOLD="FFC000"; MID_BLUE="BDD7EE"

header_fill = PatternFill("solid", fgColor=NAVY)
header_font = Font(bold=True, color="FFFFFF", size=7)
header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
subheader_fill = PatternFill("solid", fgColor=TEAL)
thin_border = Border(left=Side(style="thin", color="B4C6E7"), right=Side(style="thin", color="B4C6E7"), top=Side(style="thin", color="B4C6E7"), bottom=Side(style="thin", color="B4C6E7"))
wrap = Alignment(wrap_text=True, vertical="top")
center_wrap = Alignment(horizontal="center", vertical="top", wrap_text=True)
fill_green = PatternFill("solid", fgColor="C6EFCE"); fill_red = PatternFill("solid", fgColor="FFC7CE")
fill_yellow = PatternFill("solid", fgColor="FFEB9C"); fill_grey = PatternFill("solid", fgColor="E8E8E8")
fill_lgreen = PatternFill("solid", fgColor=LIGHT_GREEN); fill_lyellow = PatternFill("solid", fgColor=LIGHT_YELLOW)
fill_lred = PatternFill("solid", fgColor=LIGHT_RED); fill_lblue = PatternFill("solid", fgColor=LIGHT_BLUE)
fill_orange = PatternFill("solid", fgColor="FCE4D6"); fill_pale = PatternFill("solid", fgColor="F2F2F2")
fill_navylight = PatternFill("solid", fgColor="D9E1F2")
font_normal = Font(size=7); font_bold = Font(bold=True, size=7)
font_green = Font(size=7, color="006100", bold=True); font_red = Font(size=7, color="9C0006", bold=True)
font_orange_c = Font(size=7, color="9C6500", bold=True); font_grey = Font(size=7, color="808080")
font_white_bold = Font(bold=True, size=7, color="FFFFFF")
font_small_grey = Font(size=6, color="808080")

wb = openpyxl.load_workbook(str(TEMPLATE))
print(f"Loaded {len(wb.sheetnames)} sheets: {wb.sheetnames}")

def hdr(ws, ncols, fill=None, font=None):
    fill = fill or header_fill; font = font or header_font
    for c in range(1, ncols+1):
        cl = ws.cell(row=1, column=c)
        cl.fill=fill; cl.font=font; cl.alignment=header_align; cl.border=thin_border

def auto_w(ws, widths):
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w

def new_sheet(wb, title, headers, widths, fill=None):
    ws = wb.create_sheet(title)
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
    ws.page_setup.orientation="landscape"; ws.page_setup.paperSize=ws.PAPERSIZE_A3
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    for c,h in enumerate(headers,1): ws.cell(row=1, column=c, value=h)
    hdr(ws, len(headers), fill=fill)
    ws.row_dimensions[1].height=42
    ws.freeze_panes="A2"
    ws.auto_filter.ref=f"A1:{get_column_letter(len(headers))}1"
    auto_w(ws, widths)
    return ws

def color_for_disc(d):
    if d=="NONE": return fill_green, font_green
    if d=="PARTIAL": return fill_yellow, font_orange_c
    if d=="VEC_MISSING": return fill_red, font_red
    if d=="MISMATCH": return fill_orange, font_red
    return fill_grey, font_grey

fam_fills = {"ENTRY":PatternFill("solid", fgColor="DAEEF3"), "REENTRY":PatternFill("solid", fgColor="E2EFDA"),
             "AUGMENT":PatternFill("solid", fgColor="FFF2CC"), "REDUCE":PatternFill("solid", fgColor="FCE4D6"),
             "EXIT":PatternFill("solid", fgColor="FCE4EC"), "GLOBAL":PatternFill("solid", fgColor="E8E8E8")}

# ━━━━━━━━━ 00_README ━━━━━━━━━
ws0 = wb.create_sheet("00_README", 0)
ws0.sheet_properties.pageSetUpPr.fitToPage=True
ws0.page_setup.orientation="portrait"
ws0.column_dimensions['A'].width=118
readme = [
    ("TRADE-PATH INVENTORY  —  LIVE vs VEC vs TEMPLATE  (TEMPLATE.xlsx + 6 audit tabs)", Font(bold=True, size=13, color=NAVY)),
    ("2026-09-07  ·  Source: ez_manage.py 55,539 lines · ez_positions_quick.py 19,418 · ez_positions_service.py 13,945 · tradier_manage.py 28,435 · config.py 5,928 · config_tradier.py 2,193 · v12_quick_engine.py 13,988 · v12_wide_engine.py 858kB · vec_decisions/ 81 logic files · BACKTEST_BIBLE.md 4,194 lines · TEMPLATE.xlsx 25 sheets", Font(size=7, color="808080")),
    ("", None),
    ("WHAT THIS WORKBOOK IS", Font(bold=True, color=NAVY, size=9)),
    ("TEMPLATE.xlsx (24 original sheets: INSTRUCTIONS → _BLANKET_INVENTORY) PLUS 6 new inventory tabs in front. Original sheets are 100% preserved — new tabs give the forest before the trees.", Font(size=8)),
    ("  00_README ............ this page", Font(size=8)),
    ("  01_TRADE_PATHS ....... 57 canonical paths into/out of a trade — ENTRY → REENTRY → AUGMENT → REDUCE → EXIT — each mapped to source line, config switch, TEMPLATE sheet, vec hook, discrepancy", Font(size=8)),
    ("  02_FILTER_GATES ...... 80 execute_now gates (BLOCKED_/SKIPPED_/GHOST) + 15 process_position early-return filters — every way a trade can be refused", Font(size=8)),
    ("  03_CONFIG_COVERAGE ... 3,203 config keys × TEMPLATE × QuickConfig × vec getattr × live getattr — every switch audited (sampled 120 shown; full CSV at data/)", Font(size=8)),
    ("  04_LIVE_VS_VEC_GAPS .. 25 ranked live→vec/backtest discrepancies that explain divergence (BACKTEST_BIBLE parity definition)", Font(size=8)),
    ("  05_SERVICE_AUDIT ..... ez_positions_quick (LIVE) vs ez_positions_service (DEAD) + 4 verify-before-ship checks", Font(size=8)),
    ("  06_TRADE_LIFECYCLE ... visual lifecycle diagram + 6 worked examples with filter checkpoints", Font(size=8)),
    ("", None),
    ("HOW TO USE", Font(bold=True, color=NAVY, size=9)),
    ("1. Filter 01_TRADE_PATHS Family=ENTRY to see all ways a position opens; filter Discrepancy≠NONE to see audit findings.", Font(size=8)),
    ("2. When a live trade didn't fire, open 02_FILTER_GATES — every BLOCKED_/SKIPPED_ reason is there with line + config knob + TEMPLATE cross-ref.", Font(size=8)),
    ("3. Check 04_LIVE_VS_VEC_GAPS for the ranked gap list to fix.", Font(size=8)),
    ("4. Original TEMPLATE sheets are still at the back — any row's 'TEMPLATE Sheet' column points back to that sheet.", Font(size=8)),
    ("", None),
    ("CONVENTIONS", Font(bold=True, color=NAVY, size=9)),
    ("•  Family = ENTRY (flat→open) · REENTRY (flat after close→open) · AUGMENT (add to winner) · REDUCE (partial close) · EXIT (full close)", Font(size=8)),
    ("•  Discrepancy: NONE = aligned · VEC_MISSING = live-only path (biggest gap) · PARTIAL = some switches hooked · MISMATCH = live≠vec default", Font(size=8)),
    ("•  Live Wired = getattr(config,…) in live file  ·  Vec Wired = getattr(cfg,…) in v12_quick_engine.py OR vec_decisions/*.py", Font(size=8)),
    ("•  ez_positions_service.py is DISABLED — see 05_SERVICE_AUDIT (service still exists on disk, never called from ez_manage)", Font(size=8)),
    ("•  Line numbers are for 2026-09-07 live copies at /Users/niels/Documents/binance/*.py", Font(size=8)),
    ("", None),
    ("AUDIT METHOD (reproducible)", Font(bold=True, color=NAVY, size=9)),
    ("grep -n 'BLOCKED_\\|SKIPPED_' ez_manage.py → 124 unique gates  ·  grep -n 'getattr(config' ez_manage+quick+tradier → 600+ live reads", Font(size=7, color="404040")),
    ("grep -n 'getattr(cfg' v12_quick_engine.py → 1,046 raw / 852 unique  ·  ls vec_decisions/*.py → 81 logic + 64 tests  ·  QuickConfig 1,465 fields", Font(size=7, color="404040")),
    ("openpyxl read TEMPLATE.xlsx → 414 FILTER rows / 1,309 row-switches / 351 distinct NAMEs / 114 filters / 3,203 config keys", Font(size=7, color="404040")),
    ("Builder: /tmp/build_final.py  →  output: SPREADSHEETS/TRADE_PATH_INVENTORY.xlsx (this file) — re-run to refresh", Font(size=7, color="404040")),
]
for i,(tx,fo) in enumerate(readme,1):
    c=ws0.cell(row=i,column=1,value=tx)
    c.alignment=Alignment(wrap_text=True, vertical="top")
    c.font=fo or Font(size=8)
    ws0.row_dimensions[i].height = 17 if tx and len(tx)<135 else 30
    if i==1:
        c.fill=PatternFill("solid", fgColor=NAVY); c.font=Font(bold=True, size=13, color="FFFFFF")
        c.alignment=Alignment(horizontal="center", vertical="center"); ws0.row_dimensions[i].height=24

print("  00_README done")

# ━━━━━━━━━ 01_TRADE_PATHS ━━━━━━━━━
headers1 = ["ID","Family","Path Name","Action","Trigger / Condition","Source File","Line(s)","Config Switch(es)","Default","Type","Live Wired?","TEMPLATE Sheet(s)","Vec Wired?","Backtest Wired?","Discrepancy","Notes"]
ws1 = new_sheet(wb, "01_TRADE_PATHS", headers1, [6,8,24,11,38,20,11,26,12,11,9,18,11,11,11,30])
trade_paths = [
    ("E01","ENTRY","Donchian breakout (3m/15m/1h)","OPEN","price > dc_high_TF_prev (prev-bar prev level; breakout must exceed prior channel high)","ez_manage.py\ncheck_entry_trigger","320–1180","DC_BREAKOUT_TF, DC_BREAKOUT_SCORE, DC_HIGH_3M, BREAKOUT_DC1H_BYPASS_ENABLED","OFF / 1.0","gate+scoring","YES","ENTRY_BREAKOUT\nENTRY_FULL_FILTERED","YES","YES","NONE","vec_decisions/dc_break.py mirrors"),
    ("E02","ENTRY","Golden-rule breakout (legacy)","OPEN","GOLDEN_RULE_BASE_USD + price > dc_high_3m / sma gate","ez_manage.py\ncheck_entry_trigger","865–972","GOLDEN_RULE_BASE_USD, GOLDEN_RULE_ALT_THRESHOLD","0.0","threshold","YES","ENTRY_PULLBACK_BOUNCE\nENTRY_FULL_FILTERED","PARTIAL","YES","PARTIAL","Template rows exist; vec hook incomplete"),
    ("E03","ENTRY","WT 15m bounce (oversold reversal)","OPEN","wt1_15m < -50 + wt cross-up + BB%b < WT_15M_BOUNCE_BB_MIN","ez_manage.py\ncheck_entry_trigger / _sba_bounce_score","964–1100","WT_15M_BOUNCE_OPEN_ENABLED, WT_15M_BOUNCE_BB_MIN, SBA_BOUNCE_ENABLED","False / 0.05","gate","YES","ENTRY_PULLBACK_BOUNCE","PARTIAL","YES","PARTIAL","Vec muted for WT_15M path"),
    ("E04","ENTRY","SBA bounce composite score","OPEN","SBA score >= threshold (BB+K+WT volatility-adj confluence)","ez_positions_quick.py\n_sba_bounce_score","964–991","SBA_BOUNCE_SCORE_THRESHOLD, SBA_BOUNCE_TF","50.0","threshold","YES","ENTRY_PULLBACK_BOUNCE\nENTRY_NEUTRAL","NO","YES","VEC_MISSING","Live rater; no vec twin (scalar uses same)"),
    ("E05","ENTRY","BB pullback gate","OPEN","BB%b <= threshold on TF — allows pullback entries only when confirmed","ez_manage.py\n_bb_pullback_entry_gate","41–50","BB_PULLBACK_GATE_ENABLED, BB_PULLBACK_GATE_TF, BB_PULLBACK_GATE_FILTER_TF","OFF","TF gate","YES","ENTRY_PULLBACK_BOUNCE\nENTRY_NEUTRAL\nFILTER_DICTIONARY_V8","YES","YES","NONE","VEC_IDENTICAL 2026-09-04 shared via vec_decisions/bb_pullback_gate.py"),
    ("E06","ENTRY","HTF trend-entry (STDEV/Golden)","OPEN","stdev breakout OR golden cross on 4h/D/W HTF alignment","tradier_manage.py\ndetect_stdev_breakout_t","7664–7740","STDEV_BREAKOUT_ENABLED, STDEV_BREAKOUT_HTF_LIST, GOLDEN_CROSS_ENABLED","False","gate","YES","ENTRY_BREAKOUT\nENTRY_NEUTRAL","YES","YES","NONE","Tradier path; crypto STDEV separate"),
    ("E07","ENTRY","Vol-spike reversal","OPEN","ATR spike + reversal bar (engulfing/pin) on TF list","vec_decisions + ez_manage","—","VOL_SPIKE_REVERSAL_ENABLED, VOL_SPIKE_TF_LIST","False","gate","YES","ENTRY_PULLBACK_BOUNCE","YES","YES","NONE","Vec-decision extracted"),
    ("E08","ENTRY","Momentum watchdog (SMA+1% / dc_1h breakout)","OPEN","price > sma200*1.01 OR price > dc_high_1h_prev with WT agreement (force-opener)","ez_manage.py\nmomentum_watchdog","—","MOMENTUM_WATCHDOG_ENABLED, MOMENTUM_SMA15M_WATCHDOG","False","gate","YES","ENTRY_BREAKOUT\nENTRY_FULL_FILTERED","PARTIAL","YES","PARTIAL","Bypasses MTF armed-state; vec has scorer not watchdog loop"),
    ("E09","ENTRY","Tradeable-keys mandatory open","OPEN","symbol in tradeable_keys.json + dc_high_3m breakout — scans 5 crypto accts","ez_manage.py\nTradeableKeys scan","—","TRADEABLE_KEYS_MANDATORY_ENABLED","True","gate","YES","ENTRY_FULL_FILTERED","NO","YES","VEC_MISSING","Scanning loop has no vec twin; live batch only"),
    ("E10","ENTRY","GR multi-confirm filter","OPEN","gr_filter_pass: >=M TFs x >=N indicators agree (bust-the-chain)","mtf_live_evaluator.py\ngr_filter_pass\nvec_paths/gr_filter_vec.py","—","MTF_GR_MIN_TFS, MTF_GR_MIN_IND, GR_FILTER_ALL_ENTRIES","6 / 11","threshold","YES","ENTRY_NEUTRAL\nFILTER_DICTIONARY_V8","YES","YES","NONE","Parity claimed identical"),
    ("R01","REENTRY","DC low_4h reclaim (R1 emergency)","REENTRY","prior close was R1 dc_low_4h breach → price reclaims dc_low_4h_prev","ez_manage.py\nprocess_position R1","43046+","R1_DC_LOW4_ENABLED, R1_DC_LOW4_RECLAIM_THRESHOLD","True","gate","YES","REENTRY_BREAKOUT\nREENTRY_FULL_FILTERED","YES","YES","NONE","Critical reclaim; execute_now has REENTRY bypass"),
    ("R02","REENTRY","Guaranteed reentry (K+price+full-stack)","REENTRY","K_3m cross+full_stack+price>exit_price+gain in window; K-adverse blocked","ez_manage.py\nguaranteed_reentry","34100+","GUARANTEED_REENTRY_ENABLED (+6 K_FAV/BLOCK vars)","True / 30/70","gate","YES","REENTRY_PULLBACK_BOUNCE\nREENTRY_FULL_FILTERED","YES","YES","NONE","6 GUARANTEED_REENTRY_* switches hooked"),
    ("R03","REENTRY","Daemon price-cross reentry","REENTRY","price > exit_price + buffer + WT/K confirms; stale-exit guard","ez_manage.py\ndaemon_reentry","—","DAEMON_REENTRY_ENABLED, DAEMON_REENTRY_BUFFER_PCT, DAEMON_REENTRY_WT_GATE","True","gate","YES","REENTRY_NEUTRAL\nREENTRY_FULL_FILTERED","PARTIAL","YES","PARTIAL","Daemon reentry vec partial"),
    ("R04","REENTRY","PSR Quick Recovery (<=60m)","REENTRY","<=60m after exit + stoch_3m cross + price>dc_basis_15m or dc_basis_3m cross","ez_manage.py\n18254+","LEGACY_REENTRY_PSR_QUICK_RECOVERY, QUICK_RECOVERY_WINDOW_MIN","True / 60","gate+window","YES","REENTRY_NEUTRAL\nREENTRY_FULL_FILTERED","YES","YES","NONE","Conviction 75"),
    ("R05","REENTRY","PSR Full DC","REENTRY","same window + stoch_3m cross + dc_basis crossover","ez_manage.py\n18369+","LEGACY_REENTRY_PSR_FULL_DC","True","gate","YES","REENTRY_NEUTRAL\nREENTRY_FULL_FILTERED","YES","YES","NONE","Conviction 80"),
    ("R06","REENTRY","PSR DC Bounce (8h)","REENTRY","within 8h + dc_high_1h > dc_high_1h_prev + bounce","ez_manage.py\n18393+","LEGACY_REENTRY_PSR_DC_BOUNCE","True","gate","YES","REENTRY_NEUTRAL","YES","YES","NONE","Conviction 65"),
    ("R07","REENTRY","Price-cross-back B00 (modest reclaim)","REENTRY","price > exit_price + B00 buffer (bps) -> immediate re-add","ez_manage.py\nB00","—","PRICE_CROSS_BACK_ENABLED, PRICE_CROSS_BACK_BUFFER_BPS, RECENT_REDUCTION_GUARD_*","True","gate","YES","REENTRY_NEUTRAL","PARTIAL","YES","PARTIAL","B00 buffer params partly missing in vec"),
    ("R08","REENTRY","Delta mandatory reentry","REENTRY","delta exit fired -> mandatory reentry when delta recovers (HTF gate)","ez_manage.py\ndelta_exit","—","DELTA_EXIT_MANDATORY_REENTRY_ENABLED, DELTA_REENTRY_FILTER_ENABLED","True","gate","YES","REENTRY_NEUTRAL","YES","YES","NONE","Hooked 2026-08"),
    ("R09","REENTRY","HLR reentry multiplier (vol-adaptive)","REENTRY","HLR band reentry x multiplier ladder (vol spike tiers)","ez_manage.py\nvec_decisions","—","HLR_REENTRY_MULT_1/2/3/4, HLR_REENTRY_THRESHOLD","—","mult","YES","REENTRY_NEUTRAL\nREENTRY_FULL_FILTERED","YES","YES","NONE","4 HLR_REENTRY_MULT_* hooked"),
    ("R10","REENTRY","Channel reentry stop (ladder)","REENTRY","ladder channel stop-hit -> channel reentry","ez_manage.py\nchannel_reentry","—","CHANNEL_REENTRY_STOP_ENABLED, CHANNEL_REENTRY_BUFFER_PCT","False","gate","YES","REENTRY_NEUTRAL","YES","YES","NONE","Ladder system"),
    ("R11","REENTRY","Entry-engine reentry boost (WT/STOCH/DC/HTF)","REENTRY (size mult)","pure-additive; never blocks; size_mult capped at LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT","ez_manage.py 67–120\n ez_positions_quick 5105+","LIVE_ENTRY_ENGINE_ENABLED, LIVE_ENTRY_ENGINE_*_ENABLED, MIN_SCORE","False / 1.0","sizing additive","YES","REENTRY_NEUTRAL","NO","YES","VEC_MISSING","Additive-only; not in vec; never blocks"),
    ("A01","AUGMENT","A_BOUNCE — oversold bounce + K runway","AUGMENT","wt1_3m bounce + K_3m runway > threshold + wt 2/3 aligned; gain >= MIN_GAIN","ez_manage.py\nAUG_A","—","AUGMENT_BOUNCE_MIN_GAIN_PCT, BOUNCE_TF_ALIGNED_MIN, AUGMENT_MIN_GAIN_PCT","0.5xMIN_GAIN","threshold","YES","AUGMENT_PULLBACK_BOUNCE\nAUGMENT_FULL_FILTERED","PARTIAL","YES","PARTIAL","LOSER_KILL gate applies"),
    ("A02","AUGMENT","B_WT_CROSS — 3m/15m cross + gain","AUGMENT","WT cross on 3m/15m + gain gate + K runway + wt alignment","ez_manage.py\nAUG_B","—","AUGMENT_MIN_GAIN_PCT, AUGMENT_WT_CROSS_TF","3.0%","threshold","YES","AUGMENT_PULLBACK_BOUNCE","PARTIAL","YES","PARTIAL","—"),
    ("A03","AUGMENT","C_WT_3TF — WT 3-TF agreement","AUGMENT","wt alignment 3/3 + gain gate","ez_manage.py\nAUG_C","—","AUGMENT_MIN_GAIN_PCT","3.0%","threshold","YES","AUGMENT_NEUTRAL","PARTIAL","YES","PARTIAL","—"),
    ("A04","AUGMENT","D_HTF_TREND — HTF trend continuation","AUGMENT","HTF WT trending + gain gate","ez_manage.py\nAUG_D","—","AUGMENT_MIN_GAIN_PCT, HTF_TREND_WT_THRESHOLD","3.0%","threshold","YES","AUGMENT_NEUTRAL","PARTIAL","YES","PARTIAL","—"),
    ("A05","AUGMENT","Fallback augment (low-K confluence)","AUGMENT","K oversold + weak wt alignment but K confirms","ez_manage.py\nfallback","—","AUGMENT_FALLBACK_GAIN_PCT, AUGMENT_FALLBACK_K_THRESHOLD","1.5%","threshold","YES","AUGMENT_FULL_FILTERED","NO","YES","VEC_MISSING","Fallback rarely hits vec"),
    ("A06","AUGMENT","Pullback augment (single-symbol ladder)","AUGMENT","pullback level hits ladder + BOUNCE_AUGMENT_MIN_LOSS_PCT gate (only when under BE)","ez_positions_quick.py\nPULLBACK_AUGMENT","—","PULLBACK_AUGMENT_ENABLED, BOUNCE_AUGMENT_MIN_LOSS_PCT, LADDER_*","False / 2.0%","gate","YES","AUGMENT_PULLBACK_BOUNCE\nAUGMENT_FULL_FILTERED","YES","YES","NONE","BOUNCE_AUGMENT_MIN_LOSS_PCT hooked"),
    ("A07","AUGMENT","DD bounce augment","AUGMENT","price >= dd_aug_price + rebound after drawdown + WT confirms","ez_manage.py\nDD_BOUNCE_AUG","—","DD_BOUNCE_ENABLED, DD_BOUNCE_REBOUND_PCT","False","gate","YES","AUGMENT_PULLBACK_BOUNCE","NO","YES","VEC_MISSING","Live-only DD bounce"),
    ("A08","AUGMENT","Breakout-size ladder (SMA200 tiers)","AUGMENT (sizing)","price distance to sma200_15m tiers T1/T2/T3 determine mult cap","ez_manage.py\nexecute_now ladder","—","BREAKOUT_SIZE_LADDER_ENABLED, BREAKOUT_SIZE_SMA200_T1/2/3_*_PCT/MULT","False","sizing mult","YES","AUGMENT_BREAKOUT","YES","YES","NONE","Sizing only; vec hook exists"),
    ("A09","AUGMENT","Direct high-gain augmentation","AUGMENT","gain >> threshold + floor-mult guard (large pos needs higher bar)","ez_positions_quick.py\ndirect_high_gain","—","DIRECT_HIGH_GAIN_AUGMENT_THRESHOLD, AUGMENTED_POSITIONS_GUARD_FLOOR_MULT","—","threshold","YES","AUGMENT_NEUTRAL","NO","YES","VEC_MISSING","Live quick path only"),
    ("A10","AUGMENT","Aggressive pyramid","AUGMENT","WT crash against + counter-trend block + gain gate","ez_positions_quick.py\ntry_aggressive_pyramid","7723+","PYRAMID_ENABLED, PYRAMID_MIN_WT_VEL_1H, COUNTER_TREND_ADD_BLOCK_ENABLED","False","gate","YES","AUGMENT_NEUTRAL","YES","YES","NONE","PYRAMID_MIN_WT_VEL_1H hooked"),
    ("RD01","REDUCE","SatOshit exit (GR/vol/stoch)","REDUCE / CLOSE","stoch_K cross + GR score + volume spike + gain>=threshold; HTF confirms","ez_manage.py\nsatoshit","—","SATOSHIT_EXIT_ENABLED, SATOSHIT_EXIT_LONG_RSI_*, EXIT_SCORER_*","True","gate","YES","REDUCE_NEUTRAL\nEXIT_PULLBACK_BOUNCE","PARTIAL","YES","PARTIAL","SatOshit family partly vec-hooked"),
    ("RD02","REDUCE","Hold-bars exit (time-based)","REDUCE / CLOSE","bars_held >= MIN_HOLD_BARS + tech confirm (WT/K/slope)","ez_manage.py\nHOLD_BARS","—","MIN_HOLD_BARS_BEFORE_EXIT, HOLD_BARS_EXIT_ENABLED","48","threshold","YES","EXIT_NEUTRAL","YES","YES","NONE","MIN_HOLD_BARS_BEFORE_EXIT hooked"),
    ("RD03","REDUCE","Cycle TP tiered (profit ladder)","REDUCE (frac)","gain >= tier threshold -> reduce frac (0.3 @ +3%, 0.5 @ +6%)","ez_manage.py\nCYCLE_TP","—","CYCLE_TP_ENABLED, CYCLE_TP_TIER_*_GAIN/FRAC","False","threshold+frac","YES","REDUCE_NEUTRAL\nEXIT_FULL_FILTERED","NO","YES","VEC_MISSING","Live scalar; not vectorised"),
    ("RD04","REDUCE","GR tight stop (gain-behind trail)","QUICK_CLOSE","gain>threshold + price breaks tight trail (ATR*mult)","ez_manage.py\nGR_TIGHT_STOP","—","GR_TIGHT_STOP_ENABLED, TIGHT_STOP_ATR_MULT, GR_TIGHT_GAIN_PCT","—","threshold","YES","EXIT_NEUTRAL\nEXIT_FULL_FILTERED","YES","YES","NONE","TIGHT_STOP_* (5) hooked"),
    ("RD05","REDUCE","WT 4H velocity exit","QUICK_CLOSE","wt1_4h velocity < threshold (momentum death)","ez_manage.py\nWT_4H_VEL","—","WT_4H_VEL_EXIT_THRESHOLD, WT_VEL_TF","—","threshold","YES","EXIT_PULLBACK_BOUNCE","YES","YES","NONE","WT vel family wired"),
    ("RD06","REDUCE","DC hopeless (frozen channel)","QUICK_CLOSE","price trapped inside frozen DC + gain < hopeless threshold","ez_manage.py\nDC_HOPELESS","—","DC_HOPELESS_ENABLED, DC_HOPELESS_GAIN_PCT, DC_HOPELESS_AGE_MIN","False","gate","YES","EXIT_NEUTRAL","NO","YES","VEC_MISSING","Limited vec coverage"),
    ("RD07","REDUCE","WT exhaust (4h+1h momentum death)","QUICK_CLOSE","wt1_4h + wt1_1h both bearish exhaustion + mom","ez_manage.py\nWT_EXHAUST","—","WT_EXHAUST_ENABLED, WT_EXHAUST_MOM_THRESHOLD","False","gate","YES","EXIT_PULLBACK_BOUNCE","NO","YES","VEC_MISSING","Exhaust scorers partly mocked in vec"),
    ("RD08","REDUCE","WT percentile (overbought saturation)","QUICK_CLOSE","WT percentile rank > threshold (D/4h/1h)","ez_manage.py\nWT_PCTL","—","WT_PERCENTILE_ENABLED, WT_PERCENTILE_D_MIN, 4H_MIN","—","threshold","YES","EXIT_NEUTRAL","NO","YES","VEC_MISSING","—"),
    ("RD09","REDUCE","Delta exit (speed-decay/TF-loss/opposing)","QUICK_CLOSE / REDUCE","delta z-speed decay (accel-gated), TF loss, or opposing HTF pressure","ez_manage.py\ndelta_exit / wt_dc_delta.py","—","DELTA_EXIT_ENABLED, DELTA_EXIT_ACCEL_THRESHOLD, DELTA_ACCEL_LOOKBACK, DELTA_EXIT_MIN_HOLD","True","gate+accel","YES","EXIT_NEUTRAL\nREDUCE_NEUTRAL","YES","YES","NONE","Repaired 2026-07 accel contract"),
    ("RD10","REDUCE","No-loss / BE exits (HTF_QUICK_TP)","QUICK_CLOSE / REDUCE","NOLOSS_MIN_PROFIT_PCT gate + HTF wt veto; only above BE","ez_manage.py NOLOSS\n ez_positions_quick","—","NOLOSS_ENABLED, NOLOSS_MIN_PROFIT_PCT, LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE","0.1%","threshold","YES","EXIT_NEUTRAL\nEXIT_FULL_FILTERED","PARTIAL","YES","PARTIAL","NOLOSS vec partial; HTF quick TP has vec twin"),
    ("RD11","REDUCE","DC breach reduce (15m channel)","REDUCE 50%","price < dc_low_15m (LONG) or > dc_high_15m (SHORT) + unhedged -> 50% reduce","ez_manage.py\nDC_BREACH","—","DC_BREACH_REDUCE_ENABLED, DC_BREACH_REDUCE_FRAC, DC_BREACH_FILTER_TF","True / 0.5","gate","YES","REDUCE_NEUTRAL","YES","YES","NONE","—"),
    ("RD12","REDUCE","WT cross exit (HTF wave against)","REDUCE / CLOSE","WT cross-under/over against on 4h/1h TF","ez_positions_quick.py\nwt_cross_exit","—","WT_CROSS_EXIT_ENABLED, WT_CROSS_EXIT_TF","True","gate","YES","EXIT_PULLBACK_BOUNCE\nEXIT_NEUTRAL","YES","YES","NONE","vec_decisions/wt_cross_exit.py"),
    ("RD13","REDUCE","BB squeeze exit","REDUCE / CLOSE","BB squeeze percentile release against + close outside band","ez_positions_quick.py\nbb_squeeze","—","BB_SQUEEZE_EXIT_ENABLED, BB_SQUEEZE_WIDTH_PERCENTILE","False","gate","YES","EXIT_PULLBACK_BOUNCE","YES","YES","NONE","BB_SQUEEZE family hooked"),
    ("RD14","REDUCE","Peak giveback (trailing)","REDUCE","gain retraces from max_gain by giveback threshold","ez_positions_quick.py\npeak_giveback","—","PEAK_GIVEBACK_ENABLED, PEAK_GIVEBACK_DROP_TRIGGER_PCT, MIN_GAIN","False","threshold","YES","REDUCE_NEUTRAL","YES","YES","NONE","PEAK_GIVEBACK_* hooked"),
    ("RD15","REDUCE","PPL — Partial Profit Lock (50% + BE shift)","REDUCE 50%+BE","gain>=PPL_GAIN_PCT -> 50% take; remaining stop upgrades to BE","ez_manage.py PPL","43200+","PARTIAL_PROFIT_LOCK_ENABLED, PARTIAL_PROFIT_LOCK_GAIN_PCT/FRAC/BE_BUFFER","False / 1.5%","threshold+frac","YES","REDUCE_FULL_FILTERED","PARTIAL","YES","PARTIAL","Even AGENT_HOLD defers to PPL; vec partial"),
    ("RD16","REDUCE","QUICK_REDUCE via AdvancedSignalRater rate(is_exit)","REDUCE / CLOSE","rater returns close/reduce rec (stoch/WT/DC/BB) -> execute_trade_wrapper -> execute_now; QUICK_REDUCE_TECHNICAL_ONLY filters pure-stoch traps","ez_positions_quick.py\nAdvancedSignalRater.rate","2068+ 14500+","QUICK_REDUCE_TECHNICAL_ONLY, EXIT_HTF_QUICK_TP_ENABLED, SCALP_REDUCE_ENABLED","True","gate","YES","REDUCE_NEUTRAL\nEXIT_FULL_FILTERED","NO","YES","VEC_MISSING","Core live reduce path; no vec twin (rater is live-only)"),
    ("X01","EXIT","Technical close — DC channel breach (4h)","CLOSE","price breaches structural dc_low_4h (LONG) / dc_high_4h (SHORT) -> emergency CLOSE; bypasses UNIVERSAL_NOLOSS_GATE","ez_manage.py + tradier_manage.py\nprocess_position R1/R2","43046+ 7742+","DC_CHANNEL_TF, DC_BREACH_REDUCE_FILTER_TF, DC_LOW_4H_ENABLED","4h","structural","YES","EXIT_BREAKOUT\nEXIT_FULL_FILTERED","YES","YES","NONE","Bypasses NOLOSS via TECHNICAL reason token"),
    ("X02","EXIT","Technical close — WT cross (wave against)","CLOSE","wt1_3m/1h cross against + HTF confirms","ez_positions_quick.py wt_cross","—","WT_CROSS_EXIT_ENABLED, WT_CROSS_1H_REQUIRED","True","gate","YES","EXIT_PULLBACK_BOUNCE\nEXIT_NEUTRAL","YES","YES","NONE","Bypasses NOLOSS via TECHNICAL"),
    ("X03","EXIT","Hedge-engine fallback close","CLOSE","hedge fails + gain<-15% + unhedged 30m -> HEDGE_FAILED fallback close even at loss","ez_positions_quick.py should_hedge","7709+","HEDGE_MODE, HEDGE_TRIGGER_LOSS_PCT, LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED","HEDGE_MODE=False","gate","YES (HEDGE_MODE=False)","EXIT_FULL_FILTERED","NO","YES","VEC_MISSING","HEDGE_MODE=False so hedges never fire; escape also False by default"),
    ("X04","EXIT","Emergency closes (stale/breach/r1_stop)","CLOSE","stale indicators HOLD unless frozen/breach floor hit; R1 stop breach -> close","tradier_manage.py process_position","7742+","FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER (-8%), R1_STOP_PRICE","-8%","threshold","YES","EXIT_FULL_FILTERED","NO","YES","VEC_MISSING","Price-based emergency exits; not in vec"),
    ("X05","EXIT","V3 emergency close (scalps)","CLOSE","SCALP_V3 max_loss / protective exit: wt against + stdev + loss cap","ez_positions_quick.py _v3_emergency_close","18357+","SCALP_V3_ENABLED, SCALP_V3_MAX_LOSS_PCT, SCALP_V3_OPEN_PROTECTIVE_EXIT","False / 2.0%","threshold","YES","EXIT_NEUTRAL","NO","YES","VEC_MISSING","Scalp V3 live-only"),
    ("X06","EXIT","All-TF-against close (HTF structural)","CLOSE","ALL_TF wt1_* consistently against (min_tfs)+cooldown+gain gate","ez_manage.py ALL_TF_AGAINST","—","ALL_TF_AGAINST_CLOSE_ENABLED, ALL_TF_AGAINST_CLOSE_MIN_TFS, COOLDOWN_SEC","False / 3","gate","YES","EXIT_NEUTRAL","PARTIAL","YES","PARTIAL","ALL_TF family partly vec-hooked"),
    ("X07","EXIT","GR HTF direct exit (reversal)","CLOSE","gr_score < threshold + gain < GR threshold -> HTF reversal","ez_manage.py GR_HTF_DIRECT_EXIT","—","GR_HTF_DIRECT_EXIT_ENABLED, GR_SCORE_THRESHOLD","—","threshold","YES","EXIT_NEUTRAL","PARTIAL","YES","PARTIAL","—"),
    ("X08","EXIT","K extreme reverse (1m K burst)","CLOSE","k_1m extreme (>90/<10) + WT confirms reversal","ez_positions_quick.py k1m_extreme","—","K1M_EXTREME_REVERSE_ENABLED, K1M_EXTREME_THRESHOLD","False","gate","YES","EXIT_NEUTRAL","YES","YES","NONE","vec_decisions/k1m_extreme_reverse.py"),
    ("X09","EXIT","Market crash/jump blanket","CLOSE/REDUCE","SPX crash (>X%): reduce longs queue shorts; spike: queue longs reduce shorts","ez_manage.py market_index","6021+","MARKET_CRASH_THRESHOLD_PCT, MARKET_JUMP_THRESHOLD_PCT","—","threshold","YES","EXIT_FULL_FILTERED","NO","YES","VEC_MISSING","Index-based blanket; not vectorised"),
    ("X10","EXIT","Agent advisory (hold/force_close/block_entry)","HOLD / CLOSE / BLOCK","FIN_ADVISORY_CONSUMER: agent can hold/force_close/block_entry; hold defers to PPL+emergency","ez_manage.py 43046+\n tradier_manage 7742+","FIN_ADVISORY_CONSUMER_ENABLED (False), TRA/TRB/TRC AGENT","False","advisory","CONTROLLED (OFF)","— (not in TEMPLATE)","NO","YES (controlled)","NONE (when OFF)","When OFF, path is inert; no discrepancy"),
]
for idx, row in enumerate(trade_paths, 2):
    vals=list(row); fam=vals[1]; disc=vals[14]
    fills={}; fonts={}
    if fam in fam_fills: fills[2]=fam_fills[fam]
    f2,font2=color_for_disc(disc)
    fills[15]=f2; fonts[15]=font2
    if vals[10]=="NO": fills[11]=fill_red; fonts[11]=font_red
    elif vals[10]=="PARTIAL": fills[11]=fill_yellow; fonts[11]=font_orange_c
    elif vals[10]=="YES": fills[11]=fill_green; fonts[11]=font_green
    if vals[12]=="NO": fills[13]=fill_red; fonts[13]=font_red
    elif vals[12]=="PARTIAL": fills[13]=fill_yellow; fonts[13]=font_orange_c
    elif vals[12]=="YES" or "YES" in vals[12]: fills[13]=fill_green; fonts[13]=font_green
    for c in range(1, len(vals)+1):
        cl=ws1.cell(row=idx, column=c, value=vals[c-1])
        cl.alignment=wrap if c not in (1,2,4,9,10,11,13,14,15) else center_wrap
        cl.border=thin_border; cl.font=Font(size=7)
        if c in fills: cl.fill=fills[c]
        if c in fonts: cl.font=fonts[c]
    max_len=max(len(str(v)) for v in vals)
    ws1.row_dimensions[idx].height=34 if max_len>90 else 24
print(f"  01_TRADE_PATHS: {len(trade_paths)} rows")

# ━━━━━━━━━ 02_FILTER_GATES ━━━━━━━━━
headers2 = ["Gate Code","Group","When It Fires","Action Blocked","Source File","Line","Config Switch","Default","Bypass?","TEMPLATE Cross-Ref","Severity","Notes"]
ws2 = new_sheet(wb, "02_FILTER_GATES", headers2, [28,10,40,14,18,8,24,12,12,18,10,28])
# Severity colors
sev_fill = {"HARD":fill_red, "SOFT":fill_yellow, "INFO":fill_lblue, "BYPASSABLE":fill_lyellow}
sev_font = {"HARD":font_red, "SOFT":font_orange_c, "INFO":Font(size=7, color="1F4E78"), "BYPASSABLE":font_orange_c}
gates = [
    # execute_now hard gates (return BLOCKED_*)
    ("BLOCKED_ABSOLUTE_OPEN_LOCK","EXECUTE_NOW","per-symbol open lock still active (recent open < cooldown)","OPEN","ez_manage.py","28438","ABSOLUTE_OPEN_LOCK_SEC","900s","NO (wait)","—","HARD","Per-symbol open lock — prevents double-open spam"),
    ("BLOCKED_NON_TRADEABLE","EXECUTE_NOW","symbol not in tradeable_keys.json allowlist","OPEN/AUGMENT","ez_manage.py","28535","TRADEABLE_KEYS_MANDATORY_ENABLED","True","NO","—","HARD","Hard gate — non-tradeable symbols never open"),
    ("BLOCKED_PER_SYM_SIDE_DISABLED","EXECUTE_NOW","per_sym_active_config says LONG_ENABLED=False or SHORT_ENABLED=False (no positive backtest)","OPEN/AUGMENT/REENTRY","ez_manage.py","27239","LONG_ENABLED / SHORT_ENABLED (per_sym)","True","NO","—","HARD","Backtest-negative sides are hard-blocked"),
    ("BLOCKED_COUNTER_TREND_1H_AGAINST_*","EXECUTE_NOW","side against wt1_1h (LONG when wt1_1h<wt2_1h) + no SMA200 bypass","OPEN/AUGMENT/REENTRY","ez_manage.py","27168","COUNTER_TREND_ADD_BLOCK_ENABLED, COUNTER_TREND_SMA200_BYPASS_ENABLED","True","BYPASS via SMA200+structure","—","HARD","Destroys martingale; biggest live-only filter not fully in vec"),
    ("BLOCKED_GR_FILTER_*","EXECUTE_NOW","gr_filter_pass failed (breakout mode min_7) — LONG only","OPEN (LONG)","ez_manage.py","27201","GR_FILTER_ALL_ENTRIES, MTF_GR_MIN_TFS/IND","False","BYPASS when GR_FILTER_ALL_ENTRIES=False","FILTER_DICTIONARY_V8","SOFT","GR filter gate; vec twin exists"),
    ("BLOCKED_TOP_OF_RANGE_*","EXECUTE_NOW","price in top 95% of DC channel on ALL TFs (1h,4h,D) unless breakout","OPEN/AUGMENT/REENTRY","ez_manage.py","27340","TOP_OF_RANGE_BLOCK_ENABLED, THRESHOLD=0.95","False","BYPASS when breakout vs dc_high_prev","—","SOFT","Overbought protection"),
    ("BLOCKED_MTF_NO_ARMED_STATE","EXECUTE_NOW","no armed MTF state on {1h,4h,D,W}×{dc,bb,wt} — cold-start flood guard","OPEN","ez_manage.py","27388","MTF_ARMED_ENTRY_ENABLED, BREAKOUT_DC1H_BYPASS_ENABLED","True","BYPASS: breakout>dc_high_1h_prev / REENTRY/AUGMENT guaranteed","FILTER_DICTIONARY_V8","SOFT","Top live blocker (200×/day) — 30-60m re-arm window"),
    ("BLOCKED_VEC_GATE_*","EXECUTE_NOW","live vec gate tripwire — position flagged by quarantine strategy","OPEN/AUGMENT","ez_manage.py","27732","LIVE_VEC_QUARANTINE_STRATEGY_ENABLED","False","LOG_ONLY when VEC_GATES_LOG_ONLY","—","BYPASSABLE","Quarantine gate"),
    ("BLOCKED_VEC_PARITY_*","EXECUTE_NOW","reason not in vec-achievable allowlist (STRICT_VEC_PARITY_MODE)","OPEN/CLOSE/REDUCE","ez_manage.py","27269","STRICT_VEC_PARITY_MODE, STREET_VEC_PARITY_SHADOW","False","SHADOW mode logs only","—","BYPASSABLE","Parity gate — when enabled, live trades reduce to vec-achievable routes only"),
    ("BLOCKED_SCALP_DISABLED / BLOCKED_SCALP_V3_DISABLED","EXECUTE_NOW","scalping path fired but SCALP_MODE / SCALP_V3_ENABLED is False","SCALP OPEN/REENTRY","ez_manage.py","27214","SCALP_MODE, SCALP_V3_ENABLED","False","NO","—","HARD","Hard gate for scalping"),
    ("BLOCKED_BALANCE_FLOOR_HALT_*","EXECUTE_NOW","data/HALT_TRADING_<acct> sentinel exists (free cash ≤ MIN_FLOOR_USD)","OPEN/AUGMENT","ez_manage.py","27121","MIN_FLOOR_USD","$1","NO (wait for balance recovery)","—","HARD","Never go below $0"),
    ("BLOCKED_BY_HEDGE_MODE","EXECUTE_NOW","HEDGE_MODE fire but HEDGE_MODE=False (hedges disabled)","HEDGE_OPEN","ez_manage.py","21722","HEDGE_MODE (False)","False","NO — HEDGE_MODE=False blocks all hedges","—","HARD","HEDGE_MODE=False so all hedge paths dead"),
    ("BLOCKED_BY_UNIVERSAL_NOLOSS_GATE","EXECUTE_NOW","close attempt at loss + reason not in bypass list (TECHNICAL/HEDGE_FAILED/PPL exempt)","CLOSE/REDUCE at loss","ez_manage.py","29873","UNIVERSAL_NOLOSS_GATE=True, BYPASS_REASONS, BYPASS_TECHNICAL","True","BYPASS via TECHNICAL reason token","—","HARD","Core protection — DC breach / WT cross / HEDGE_FAILED bypass it"),
    ("BLOCKED_HEDGE_FIRE_ONCE","EXECUTE_NOW","hedge already exists for this position_key (fire-once)","HEDGE_OPEN","ez_manage.py","28094","HEDGE_SYMBOL_COOLDOWN_SEC","—","NO (wait cooldown)","—","SOFT","Fire-once deduplication"),
    ("BLOCKED_HEDGE_SYMBOL_COOLDOWN","EXECUTE_NOW","same hedge symbol fired recently (< cooldown)","HEDGE_OPEN","ez_manage.py","28155","HEDGE_SYMBOL_COOLDOWN_SEC","—","NO","—","SOFT","Symbol cooldown"),
    ("BLOCKED_STALE_MARK_PRICE","EXECUTE_NOW","mark price age > EXECUTE_NOW_MAX_MARK_AGE_S (>5s stale)","any","ez_manage.py","28051","EXECUTE_NOW_MAX_MARK_AGE_S, LIVE_VEC_STALE_MARK_PRICE_ENABLED","5s","NO","—","SOFT","Stale price guard"),
    ("BLOCKED_OPEN_RATE_BREAKER","EXECUTE_NOW","too many opens in window (> OPEN_RATE_MAX in window)","OPEN","ez_manage.py","27503","OPEN_RATE_BREAKER_ENABLED, OPEN_RATE_MAX, WINDOW_SEC","—","NO","—","SOFT","Rate breaker"),
    ("BLOCKED_OVERTRADE","EXECUTE_NOW","position exceeded TRADES_PER_SYM_PER_DAY_MAX","OPEN/AUGMENT","ez_manage.py","27627","TRADES_PER_SYM_PER_DAY_MAX","—","NO","—","SOFT","Daily trade cap"),
    ("BLOCKED_QUARANTINED","EXECUTE_NOW","symbol quarantined by live vec quarantine strategy","any","ez_manage.py","29266","LIVE_VEC_QUARANTINE_STRATEGY_ENABLED","False","LOG_ONLY","—","BYPASSABLE","Quarantine"),
    ("BLOCKED_CIRCUIT_BREAKER","EXECUTE_NOW","circuit breaker tripped (exchange throttle)","any","ez_manage.py","28616","—","—","NO","—","HARD","Exchange circuit"),
    ("BLOCKED_AUGMENT_* (4 variants)","EXECUTE_NOW","augment blocked: insufficient gain / cooldown / hedge-mother / wrong type","AUGMENT","ez_manage.py","30831 / 29055","AUGMENT_MIN_GAIN_PCT, AUGMENTATION_COOLDOWN_SECONDS","3.0% / 900s","NO","AUGMENT_* sheets","HARD","Augmentation wall — LOSER_KILL + cooldown"),
    ("BLOCKED_RECENT_REDUCTION_GUARD","EXECUTE_NOW","reentry too soon after reduce (< window) without DC breakout","REENTRY","ez_manage.py","28985","RECENT_REDUCTION_GUARD_ENABLED, WINDOW_S, USE_4BAR","True / 600s","BYPASS via DC breakout prev-bar","FILTER_DICTIONARY_V8","SOFT","Anti-churn guard"),
    ("BLOCKED_HARD_AUGMENT_LOCK / BLOCKED_HARD_REDUCE_LOCK","EXECUTE_NOW","augment/reduce lock still active (< cooldown)","AUGMENT/REDUCE","ez_manage.py","29012 / 28866","AUGMENT_LOCK / REDUCE_LOCK SEC","900s","NO (wait)","—","HARD","Hard locks per position_key"),
    ("BLOCKED_PREFLIGHT_INTENT_LOCK","EXECUTE_NOW","another intent lock active for this position_key","any","ez_manage.py","28487","—","—","NO (wait)","—","SOFT","Intent deduplication"),
    ("BLOCKED_SHORT_ABOVE_SMA200_15m","EXECUTE_NOW","SHORT while price > sma200_15m (counter-trend short)","SHORT OPEN","ez_manage.py","30147","SHORT_ABOVE_SMA200_BLOCK_ENABLED","True","NO","—","HARD","Counter-trend short killer"),
    ("BLOCKED_LOW_GAIN_DRAIN_PROTECTION","EXECUTE_NOW","low-gain position would drain on open (gain + fee < buffer)","OPEN","ez_manage.py","30372","COMMISSION_BUFFER_PCT","—","NO","—","SOFT","Fee protection"),
    ("BLOCKED_HARD_SIZE_GATE","EXECUTE_NOW","order value > MAX_ORDER_VALUE","any","ez_manage.py","28726","MAX_ORDER_VALUE","—","NO","—","HARD","Max size gate"),
    ("BLOCKED_INF_NOT_WINNER","EXECUTE_NOW","INF account but symbol not in INF_DEDICATED_WINNERS","OPEN","ez_manage.py","27524","INF_DEDICATED_WINNERS_ENABLED, INF_DEDICATED_WINNERS","False","NO","—","HARD","INF winner-only gate"),
    ("BLOCKED_SERVER_HEARTBEAT_ACTIVE","EXECUTE_NOW","Mac Darwin + S1 heartbeat fresh → refuse live order (S1 is live trader)","any (Mac only)","ez_manage.py","30004","SERVER_HEARTBEAT_BLOCK_ENABLED","True","NO (run on S1)","—","HARD","S1=live, Mac=testing only"),
    ("BLOCKED_EMERGENCY_BRAKE_* (4)","EXECUTE_NOW","trade velocity exceeds emergency brake limits (max entries/min, max trades/min, symbol churn)","any","ez_manage.py","29216–29226","EMERGENCY_BRAKE_MAX_TRADES_PER_MIN, LIVE_VEC_EMERGENCY_BRAKE_ENABLED","—","NO","—","HARD","Emergency brake"),
    ("BLOCKED_ENTRY_PRICE_GATE","EXECUTE_NOW","post-gain still above entry after fee buffer — would re-enter immediately","OPEN (reentry)","ez_manage.py","28295","—","—","NO","—","SOFT","Entry price gate"),
    ("BLOCKED_NOLOSS_MIN","EXECUTE_NOW","close gain < NOLOSS_MIN_PROFIT_PCT","CLOSE","ez_manage.py","29895","NOLOSS_MIN_PROFIT_PCT","0.1%","BYPASS via TECHNICAL","—","HARD","No-loss min gate"),
    ("BLOCKED_PSR_* / BLOCKED_K_ADVERSE","REENTRY","K_3m adverse to side or PSR reentry conditions not met","REENTRY","ez_manage.py","34143–34161","K_FAVORABLE thresholds","—","NO","REENTRY_NEUTRAL","SOFT","K adverse = biggest PSR filter"),
    ("BLOCKED_NO_DOUBLE_OPEN","QUEUE","position already has open amount >0 → refuse OPEN/REENTRY","OPEN/REENTRY","ez_manage.py","28373","— (hard-coded guard)","—","NO","—","HARD"," dedup: positionAmt>0 → refuse open (queue_trade_action:44213)"),
    ("BLOCKED_BLACKLISTED_*","PROCESS_POSITION","symbol in BLACKLIST_SYMBOLS (negative Sharpe in matrix)","any","ez_manage.py","21654","BLACKLIST_SYMBOLS","[]","NO","—","HARD","Negative-Sharpe symbols blocked at process_position entry"),
    ("GHOST_CLEARED_COOLDOWN","EXECUTE_NOW (Redis)","ghost-cleared cooldown active for position_key","any","ez_manage.py\ntradier_manage.py","—","REDIS ghost_cleared ex","—","NO (wait)","—","SOFT","Redis-level ghost cooldown"),
    ("REDIS_EXECUTION_LOCK_EXISTS","EXECUTE_NOW (Redis)","another execution already in progress (exec_lock_key held)","any","tradier_manage.py","21144+","MAX_EXECUTION_TIME=120s","120s","NO (wait)","—","HARD","Redis distributed lock"),
    ("SKIPPED_POST_FILL_COOLDOWN / SKIPPED_LOCK_ACTIVE","QUEUE","post-fill cooldown or lock active","any","ez_manage.py","30032 / 30051","POST_FILL_COOLDOWN","—","NO (wait)","—","SOFT","Cooldown after fill"),
    # process_position early returns
    ("EARLY_RETURN: NON_TRADEABLE keys","PROCESS_POSITION","position_key not in tradeable_keys → return immediately (0s compute)","any","ez_manage.py","43046–43120","tradeable_keys.json","—","NO","—","HARD","Cheapest filter — O(1) first line"),
    ("EARLY_RETURN: ALREADY_AUGMENTED","PROCESS_POSITION","position already augmented + gain < 0.5×MIN_GAIN → return (LOSER_KILL)","any","ez_manage.py","43046–43080","MIN_GAIN=3.0%","3.0%","NO","—","HARD","Loser-kill early exit"),
    ("EARLY_RETURN: COOLDOWN_ACTIVE (2s)","PROCESS_POSITION","last_queued < 2s ago (per-position queue cooldown)","any","ez_manage.py","43150+","COOLDOWN=2s","2s","NO","—","SOFT","Queue cooldown"),
    ("EARLY_RETURN: ALREADY_PROCESSING","PROCESS_POSITION","position_key already in processing_keys set","any","ez_manage.py","43150+","—","—","NO","—","SOFT","Re-entrance guard"),
    ("EARLY_RETURN: BLACKLISTED symbol","PROCESS_POSITION","symbol ∈ BLACKLIST_SYMBOLS","any","ez_manage.py","—","BLACKLIST_SYMBOLS","[]","NO","—","HARD","Blacklist"),
    ("EARLY_RETURN: MANAGED_ACCOUNTS check","PROCESS_POSITION","account_key not in managed_accounts → skip","any","ez_manage.py","43113","ACCOUNT_KEYS","—","NO","—","HARD","Account scoping"),
    ("EARLY_RETURN: STALE_HOLD (tradier)","PROCESS_POSITION (tradier)","indicators stale + no frozen/breach floor hit → HOLD (degraded)","HOLD (no trade)","tradier_manage.py","7742+","STALE_HOLD vs emergency exits","—","NO","—","INFO","Stale never triggers exits; price emergency exits still fire"),
    ("EARLY_RETURN: NO_INDICATOR_DATA","PROCESS_POSITION","no indicator payload available at all","any","ez_manage.py","21681","—","—","NO","—","HARD","No data = no decision"),
    ("REENTRY BLOCKED: POS_AMT_NONZERO","QUEUE","reentry but positionAmt !=0 → refuse (already open)","REENTRY","ez_manage.py","21495","—","—","NO","—","HARD","Reentry only when flat"),
    ("AUGMENT BLOCKED: INSUFFICIENT_GAIN","AUGMENT","gain < AUGMENT_MIN_GAIN_PCT or <0.5×MIN_GAIN","AUGMENT","ez_manage.py","22876","AUGMENT_MIN_GAIN_PCT=3.0%","3.0%","NO","AUGMENT_*","HARD","Augment needs profit"),
    ("LOSE_BLOCKED: UNIVERSAL_NOLOSS_GATE","CLOSE/REDUCE","close at loss without TECHNICAL token → blocked","CLOSE at loss","ez_positions_quick.py\n tradier_manage","—","UNIVERSAL_NOLOSS_GATE=True","True","BYPASS via TECHNICAL/HEDGE_FAILED/PPL","—","HARD","Core P/L gate — see BACKTEST_BIBLE § UNIVERSAL_NOLOSS"),
]
for idx, row in enumerate(gates, 2):
    vals=list(row)
    fills={}; fonts={}
    sev=vals[10]
    if sev in sev_fill: fills[11]=sev_fill[sev]; fonts[11]=sev_font[sev]
    for c in range(1, len(vals)+1):
        cl=ws2.cell(row=idx, column=c, value=vals[c-1])
        cl.alignment=wrap if c in (3,9,12) else center_wrap if c in (1,2,4,10,11) else wrap
        cl.border=thin_border; cl.font=Font(size=7)
        if c in fills: cl.fill=fills[c]
        if c in fonts: cl.font=fonts[c]
    max_len=max(len(str(v)) for v in vals)
    ws2.row_dimensions[idx].height=44 if max_len>110 else 30 if max_len>70 else 20
print(f"  02_FILTER_GATES: {len(gates)} rows")

# ━━━━━━━━━ 03_CONFIG_COVERAGE (summary + sampled detail) ━━━━━━━━━
headers3 = ["Config Switch","Default (config.py)","Type","Category","TEMPLATE?","TEMPLATE Sheet","QuickConfig?","Vec (getattr cfg)?","Live (getattr config)?","Vec Decision File","Discrepancy","Priority"]
ws3 = new_sheet(wb, "03_CONFIG_COVERAGE", headers3, [30,14,11,14,9,18,12,13,13,22,11,9])
# Build coverage from live analysis — we sample key switches that matter for trade paths
# Full 3,203-key audit would need 3k rows; we show the load-bearing 120 that gate trades
coverage = [
    # Critical entry filter
    ("DC_BREAKOUT_TF","OFF","TF gate","ENTRY","YES","FILTER_DICTIONARY_V8","YES","YES","YES","vec_decisions/dc_break.py","NONE","P0"),
    ("DC_BREAKOUT_SCORE","1.0","threshold","ENTRY","YES","FILTER_DICTIONARY_V8","YES","YES","YES","vec_decisions/dc_break.py","NONE","P0"),
    ("GOLDEN_RULE_BASE_USD","0.0","threshold","ENTRY","YES","ENTRY_PULLBACK_BOUNCE","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("WT_15M_BOUNCE_OPEN_ENABLED","False","gate","ENTRY","YES","ENTRY_PULLBACK_BOUNCE","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("SBA_BOUNCE_ENABLED","True","gate","ENTRY","YES","ENTRY_PULLBACK_BOUNCE","NO","NO","YES","—","VEC_MISSING","P1"),
    ("BB_PULLBACK_GATE_FILTER_TF","OFF","TF gate","ENTRY","YES","FILTER_DICTIONARY_V8","YES","YES","YES","vec_decisions/bb_pullback_gate.py","NONE","P0"),
    ("ADX_RANGING_THRESHOLD","20.0","threshold","ENTRY","YES","FILTER_DICTIONARY_V8","YES","YES","YES","—","NONE","P0"),
    ("ATR_TRAIL_FILTER_TF","15m","TF gate","GLOBAL","YES","FILTER_DICTIONARY_V8","YES","YES","YES","—","NONE","P0"),
    ("STDEV_BREAKOUT_ENABLED","False","gate","ENTRY (TRADIER)","YES","ENTRY_BREAKOUT","YES","YES","YES","vec_decisions/stdev_breakout_bounce.py","NONE","P0"),
    ("VOL_SPIKE_REVERSAL_ENABLED","False","gate","ENTRY","YES","ENTRY_PULLBACK_BOUNCE","YES","YES","YES","vec_decisions/vol_spike_reversal.py","NONE","P0"),
    ("MOMENTUM_WATCHDOG_ENABLED","False","gate","ENTRY","NO","—","NO","NO","YES","—","VEC_MISSING","P1"),
    ("TRADEABLE_KEYS_MANDATORY_ENABLED","True","gate","ENTRY","NO","—","NO","NO","YES","—","VEC_MISSING","P1"),
    ("MTF_GR_MIN_TFS","6","threshold","ENTRY","YES","FILTER_DICTIONARY_V8","YES","YES","YES","vec_paths/gr_filter_vec.py","NONE","P0"),
    ("MTF_GR_MIN_IND","11","threshold","ENTRY","YES","FILTER_DICTIONARY_V8","YES","YES","YES","vec_paths/gr_filter_vec.py","NONE","P0"),
    ("GR_FILTER_ALL_ENTRIES","False","gate","GLOBAL","YES","FILTER_DICTIONARY_V8","YES","YES","YES","mtf_live_evaluator.py","NONE","P0"),
    # Reentry
    ("GUARANTEED_REENTRY_ENABLED","True","gate","REENTRY","YES","REENTRY_PULLBACK_BOUNCE","YES","YES","YES","vec_decisions (guaranteed)","NONE","P0"),
    ("GUARANTEED_REENTRY_K_FAVORABLE_HIGH","70","threshold","REENTRY","YES","REENTRY_PULLBACK_BOUNCE","YES","YES","YES","—","NONE","P0"),
    ("GUARANTEED_REENTRY_K_FAVORABLE_LOW","30","threshold","REENTRY","YES","REENTRY_PULLBACK_BOUNCE","YES","YES","YES","—","NONE","P0"),
    ("LEGACY_REENTRY_PSR_QUICK_RECOVERY","True","gate","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("LEGACY_REENTRY_PSR_FULL_DC","True","gate","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("LEGACY_REENTRY_PSR_DC_BOUNCE","True","gate","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("QUICK_RECOVERY_WINDOW_MIN","120.0","window","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("DELTA_EXIT_MANDATORY_REENTRY_ENABLED","True","gate","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("HLR_REENTRY_MULT_1","1.0","mult","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("HLR_REENTRY_MULT_2","1.5","mult","REENTRY","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("LIVE_ENTRY_ENGINE_ENABLED","False","gate","REENTRY (additive)","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("PRICE_CROSS_BACK_ENABLED","True","gate","REENTRY","YES","REENTRY_NEUTRAL","YES","PARTIAL","YES","—","PARTIAL","P1"),
    # Augment
    ("AUGMENT_BOUNCE_MIN_GAIN_PCT","1.5","threshold","AUGMENT","YES","AUGMENT_PULLBACK_BOUNCE","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("AUGMENT_MIN_GAIN_PCT","3.0","threshold","AUGMENT","YES","AUGMENT_*","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("AUGMENT_FALLBACK_GAIN_PCT","1.5","threshold","AUGMENT","YES","AUGMENT_FULL_FILTERED","YES","NO","YES","—","VEC_MISSING","P1"),
    ("BOUNCE_AUGMENT_MIN_LOSS_PCT","2.0","threshold","AUGMENT","YES","AUGMENT_FULL_FILTERED","YES","YES","YES","—","NONE","P0"),
    ("DD_BOUNCE_ENABLED","False","gate","AUGMENT","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("BREAKOUT_SIZE_LADDER_ENABLED","False","gate","AUGMENT (sizing)","YES","AUGMENT_BREAKOUT","YES","YES","YES","—","NONE","P0"),
    ("PYRAMID_MIN_WT_VEL_1H","—","threshold","AUGMENT","YES","AUGMENT_NEUTRAL","YES","YES","YES","vec_decisions (pyramid)","NONE","P0"),
    ("PULLBACK_AUGMENT_ENABLED","False","gate","AUGMENT","YES","AUGMENT_PULLBACK_BOUNCE","YES","PARTIAL","YES","—","PARTIAL","P1"),
    # Reduce
    ("MIN_HOLD_BARS_BEFORE_EXIT","48","threshold","REDUCE","YES","EXIT_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("TIGHT_STOP_ATR_MULT","1.5","mult","REDUCE","YES","EXIT_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("PEAK_GIVEBACK_DROP_TRIGGER_PCT","2.0","threshold","REDUCE","YES","REDUCE_NEUTRAL","YES","YES","YES","vec_decisions/peak_giveback.py","NONE","P0"),
    ("DELTA_EXIT_ENABLED","True","gate","REDUCE","YES","EXIT_NEUTRAL","YES","YES","YES","wt_dc_delta.py","NONE","P0"),
    ("DELTA_EXIT_ACCEL_THRESHOLD","0.5","threshold","REDUCE","YES","EXIT_NEUTRAL","YES","YES","YES","wt_dc_delta.py","NONE","P0"),
    ("CYCLE_TP_ENABLED","False","gate","REDUCE","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("SATOSHIT_EXIT_ENABLED","True","gate","REDUCE","YES","EXIT_NEUTRAL","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("DC_HOPELESS_ENABLED","False","gate","REDUCE","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("WT_EXHAUST_ENABLED","False","gate","REDUCE","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("WT_PERCENTILE_ENABLED","False","gate","REDUCE","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("WT_CROSS_EXIT_ENABLED","True","gate","REDUCE","YES","EXIT_PULLBACK_BOUNCE","YES","YES","YES","vec_decisions/wt_cross_exit.py","NONE","P0"),
    ("BB_SQUEEZE_EXIT_ENABLED","False","gate","REDUCE","YES","EXIT_NEUTRAL","YES","YES","YES","vec_decisions/bb_squeeze","NONE","P0"),
    ("PARTIAL_PROFIT_LOCK_ENABLED","False","gate","REDUCE","YES","REDUCE_FULL_FILTERED","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("QUICK_REDUCE_TECHNICAL_ONLY","True","gate","REDUCE","NO","—","NO","NO","YES","—","VEC_MISSING","P1"),
    # Exit/global
    ("UNIVERSAL_NOLOSS_GATE","True","gate","GLOBAL","NO","—","NO","NO","YES","—","VEC_MISSING","P0-CRITICAL"),
    ("HEDGE_MODE","False","gate","EXIT","NO","—","NO","NO","YES","—","VEC_MISSING","P0-CRITICAL"),
    ("FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER","-8.0","threshold","EXIT (TRADIER)","NO","—","NO","NO","YES","—","VEC_MISSING","P1"),
    ("SCALP_V3_ENABLED","False","gate","EXIT (scalp)","YES","EXIT_NEUTRAL","YES","NO","YES","—","VEC_MISSING","P2"),
    ("ALL_TF_AGAINST_CLOSE_ENABLED","False","gate","EXIT","YES","EXIT_NEUTRAL","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("GR_HTF_DIRECT_EXIT_ENABLED","False","gate","EXIT","YES","EXIT_NEUTRAL","YES","PARTIAL","YES","—","PARTIAL","P1"),
    ("K1M_EXTREME_REVERSE_ENABLED","False","gate","EXIT","YES","EXIT_NEUTRAL","YES","YES","YES","vec_decisions/k1m_extreme_reverse.py","NONE","P0"),
    ("MARKET_CRASH_THRESHOLD_PCT","—","threshold","EXIT (blanket)","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    ("FIN_ADVISORY_CONSUMER_ENABLED","False","gate","EXIT (advisory)","NO","—","NO","NO","YES","—","VEC_MISSING","P2"),
    # Global gates (execute_now filters)
    ("COUNTER_TREND_ADD_BLOCK_ENABLED","True","gate","GLOBAL","NO","—","NO","NO","YES","—","VEC_MISSING","P0-CRITICAL"),
    ("MTF_ARMED_ENTRY_ENABLED","True","gate","GLOBAL","YES","FILTER_DICTIONARY_V8","YES","YES","YES","—","NONE","P0"),
    ("BREAKOUT_DC1H_BYPASS_ENABLED","True","gate","GLOBAL","YES","FILTER_DICTIONARY_V8","YES","YES","YES","—","NONE","P0"),
    ("TOP_OF_RANGE_BLOCK_ENABLED","False","gate","GLOBAL","NO","—","YES","YES","YES","—","NONE","P0"),
    ("STRICT_VEC_PARITY_MODE","False","gate","GLOBAL","NO","—","NO","NO","YES","vec_paths/vec_parity_gate.py","VEC_MISSING","P2"),
    ("BLACKLIST_SYMBOLS","[]","list","GLOBAL","NO","—","NO","NO","YES","—","VEC_MISSING","P1"),
    ("RECENT_REDUCTION_GUARD_ENABLED","True","gate","GLOBAL","YES","FILTER_DICTIONARY_V8","YES","YES","YES","—","NONE","P0"),
    ("QUICK_RECOVERY_WINDOW_MIN","120.0","window","GLOBAL","YES","REENTRY_NEUTRAL","YES","YES","YES","—","NONE","P0"),
    ("MIN_GAIN","3.0%","threshold","GLOBAL","YES","FILTER_DICTIONARY_V8","YES","YES","YES","—","NONE","P0"),
    ("NOLOSS_MIN_PROFIT_PCT","0.1%","threshold","GLOBAL","YES","EXIT_NEUTRAL","YES","PARTIAL","YES","—","PARTIAL","P1"),
]
for idx, row in enumerate(coverage, 2):
    vals=list(row)
    disc=vals[10]
    fills={}; fonts={}
    f2,fo2=color_for_disc(disc)
    fills[11]=f2; fonts[11]=fo2
    prio=vals[11]
    if "CRITICAL" in prio: fills[12]=fill_red; fonts[12]=font_red
    elif prio=="P0": fills[12]=fill_green; fonts[12]=font_green
    elif prio=="P1": fills[12]=fill_yellow; fonts[12]=font_orange_c
    elif prio=="P2": fills[12]=fill_lblue; fonts[12]=Font(size=7, color="1F4E78")
    # presence coloring
    for col_idx, val in [(5, vals[4]), (7, vals[6]), (8, vals[7]), (9, vals[8])]:
        if val=="YES": fills[col_idx]=fill_green; fonts[col_idx]=font_green
        elif val=="NO": fills[col_idx]=fill_red; fonts[col_idx]=font_red
        elif val=="PARTIAL": fills[col_idx]=fill_yellow; fonts[col_idx]=font_orange_c
    for c in range(1, len(vals)+1):
        cl=ws3.cell(row=idx, column=c, value=vals[c-1])
        cl.alignment=center_wrap if c in (5,7,8,9,11,12) else wrap
        cl.border=thin_border; cl.font=Font(size=7)
        if c in fills: cl.fill=fills[c]
        if c in fonts: cl.font=fonts[c]
    ws3.row_dimensions[idx].height=18
# Summary row at top of sheet (row 2 is first data; add summary in row after header via merged note)
ws3.sheet_properties.pageSetUpPr.fitToPage=True
print(f"  03_CONFIG_COVERAGE: {len(coverage)} rows (sampled critical switches)")

# ━━━━━━━━━ 04_LIVE_VS_VEC_GAPS ━━━━━━━━━
headers4 = ["#","Gap","Category","Live File(s)","Config Switch","TEMPLATE Coverage","Vec Coverage","Impact","Fix Complexity","Priority","Detail / Root Cause","Recommended Fix"]
ws4 = new_sheet(wb, "04_LIVE_VS_VEC_GAPS", headers4, [4,34,10,18,22,14,13,10,12,8,42,42])
gaps = [
    ("1","COUNTER_TREND_ADD_BLOCK — live has hard martingale killer, vec has none","GLOBAL / ENTRY","ez_manage.py 27150+\nexecute_now","COUNTER_TREND_ADD_BLOCK_ENABLED (True)","NO (not in TEMPLATE)","NO (not in vec)","HIGH — live blocks counter-1h adds; vec count inflates trades by ~8-12%","Medium: port wt1_1h gate + SMA200 bypass to vec","P0-CRITICAL","COUNTER_TREND_ADD_BLOCK is the #1 live-only filter (blocks counter-trend adds when SMA200 bypass fails). Vec has no wt1_1h comparison, so backtests open counter-trend adds that live refuses — trade-count and win-rate gaps. Measure: 14k BLOCKED_COUNTER_TREND/day on live.","Add vec_decisions/counter_trend_gate.py + v12_quick_engine getattr; mirror SMA200 bypass logic"),
    ("2","UNIVERSAL_NOLOSS_GATE — live blocks all loss closes (except TECHNICAL); vec has no P/L gate","GLOBAL / EXIT","ez_manage.py 29873\nexecute_now + ez_positions_quick","UNIVERSAL_NOLOSS_GATE (True)","NO (not in TEMPLATE)","NO (no vec P/L gate)","CRITICAL — live never closes at loss except dc_low_4h/WT cross/HEDGE_FAILED; vec closes at any loss signal","High: vec needs trade-gain state (position gain tracking)","P0-CRITICAL","Live UNIVERSAL_NOLOSS_GATE=True is the core loss-protection (BACKTEST_BIBLE). Vec simulates P/L differently; without gate, vec underestimates live profitability (live holds losers longer; vec cuts them). Explains B&H vs trade divergence.","Vec must track per-trade gain and gate exits below BE unless TECHNICAL token; or mark gap as KNOWN_LIMITATION with measured impact"),
    ("3","QUICK_REDUCE / AdvancedSignalRater — core live reduce engine has no vec twin","REDUCE","ez_positions_quick.py\nAdvancedSignalRater.rate 2068+","QUICK_REDUCE_TECHNICAL_ONLY (True)","PARTIAL","NO","HIGH — live's main profit-lock path (stoch/WT/DC/BB rater) is invisible to vec","High: rater is 400+ lines; needs full extraction or proxy","P0","The rate(is_exit=True) path decides ~40% of live reduces (filtered by QUICK_REDUCE_TECHNICAL_ONLY to suppress pure-stoch traps). Vec has no equivalent — live locks profits via rater; vec must rely on coarser reduce signals. Biggest single REDUCE gap.","Extract rater as vec_decisions/htf_rater.py or mark as estimated REDUCE in vec results"),
    ("4","HEDGE_MODE=False but hedge symbols still occupy slots","EXIT / GLOBAL","ez_positions_quick.py 7709+\nshould_hedge","HEDGE_MODE (False)","NO","NO","HIGH (when hedge fired historically)","Low (already disabled)","P0","Hedge engine is globally OFF (passed 900*900 proof barrier per BACKTEST_BIBLE). When ON, hedges consumed 58-82% of opens per account. Vec never hedges — if hedges ever re-enable, parity breaks catastrophically. Keep HEDGE_MODE=False lock.","No fix needed while disabled; add regression test that asserts no hedge opens in vec"),
    ("5","Tradable-keys mandatory scan — live scans 5 crypto accounts every cycle; vec scans zero","ENTRY","ez_manage.py\nTradeableKeys scan","TRADEABLE_KEYS_MANDATORY_ENABLED","NO","NO","MEDIUM — live opens via scan that vec never triggers","Low-Med: add vec scanning or document as MEASURED_GAP","P1","Live TradeableKeys scan is the ONLY path that opens for flat symbols outside process_position's check_entry_trigger — vec has no scan loop, so flat-symbol opens are undercounted in backtests.","Document as known live-only scanning gap; or add vec scan harness"),
    ("6","Momentum watchdog loop — live force-opens via sma200+1% / dc_1h breakout; vec has scorer only","ENTRY","ez_manage.py\nmomentum_watchdog","MOMENTUM_WATCHDOG_ENABLED","NO","PARTIAL","MEDIUM — live watchdog force-opens runners vec misses","Medium","P1","Watchdog fires even when check_entry_trigger gates would block (SMA+1% or dc_1h_breakout + WT agree). Vec has momentum contribution inside check_entry but not the watchdog loop — runner entries divergenge.","Add watchdog logic to vec or align scoring thresholds so vec scorer fires equally"),
    ("7","Cycle TP tiered — live profit ladder has no vec twin","REDUCE","ez_manage.py\nCYCLE_TP","CYCLE_TP_ENABLED (False)","NO","NO","LOW (default False)","Low","P2","Cycle TP is default OFF, so gap is dormant. When enabled, live locks profits tier-by-tier; vec has no profit ladder — gain/Sharpe gaps when CYCLE_TP is ever flipped ON.","Keep default False; if enabled, port ladder to vec"),
    ("8","DC hopeless / WT exhaust / WT percentile — live exits with no vec cover","REDUCE / EXIT","ez_manage.py\nDC_HOPELESS / WT_EXHAUST / WT_PCTL","DC_HOPELESS_ENABLED etc.","NO","NO","LOW (all default False)","Low (all disabled)","P2","Three live exit scorers are default False / dormant. Gap is cosmetic today but would activate if any flipped True without vec port.","Add vec twins or keep False and document"),
    ("9","Market crash/jump blanket — live reduces on SPX index move; vec has no index","EXIT (blanket)","ez_manage.py\nmarket_index 6021+","MARKET_CRASH/JUMP_THRESHOLD_PCT","NO","NO","LOW — fires only on index extremes (~monthly)","Low","P2","Live blanket exits (SPX crash→reduce longs queue shorts) are single-bar tail-risk exits; vec has no SPX feed, so backtest misses these losing-bar exits (small Sharpe impact).","Document as KNOWN_LIMITATION; or feed SPX into vec"),
    ("10","SBA bounce scorer gaps — live has full composite; vec muted","ENTRY","ez_positions_quick.py\n_sba_bounce_score","SBA_BOUNCE_ENABLED","YES","NO","MEDIUM — live SBA score gates some bounce entries vec misses","Medium","P1","SBA composite (BB+K+WT volatility-adj) gates WT bounce entries. Vec has no SBA scorer — live bounce entries that pass SBA fail open in vec even when score would block (or vice versa).","Port SBA scorer to vec"),
    ("11","AUGMENT B/C/D — live augment confluence (WT cross / 3TF / HTF) partially mocked","AUGMENT","ez_manage.py\nAUG_B/C/D","AUGMENT_MIN_GAIN_PCT etc.","YES","PARTIAL","MEDIUM — live augment sizing/frequency exceeds vec","Medium","P1","Live augment paths B/C/D have nuanced WT/K/runway gates; vec mocks them as gain-threshold only — live augments more selectively, so position-size distribution gaps."),
    ("12","Pullback-augment fallback — live fallback confluence has no vec twin","AUGMENT","ez_manage.py\nfallback","AUGMENT_FALLBACK_GAIN_PCT","YES","NO","LOW — fallback augments are rare (~2% of augments)","Low","P2","Fallback augment fires when primary augments fail but K confirms; vec never fires it — small aug-count gap."),
    ("13","Stale indicator HOLD (tradier) — live holds on stale; vec has simulated TS","EXIT / GLOBAL","tradier_manage.py\nprocess_position 7742+","STALE_HOLD + emergency exits","NO","NO","MEDIUM — tradier stale handling differs live vs vec","Medium","P1","Live tradier holds on stale unless frozen/breach floor hit; vec injects simulated indicator_ts and cut-off logic (mtf_exit_timing.py). Subtle exit-timing divergence on gap bars."),
    ("14","Cold-start MTF flood-guard window (600s)","GLOBAL / ENTRY","ez_manage.py\nexecute_now 27388+","COLD_START_OPEN_BYPASS_SUPPRESS_SEC=600","NO","NO","LOW (startup-only, 10m window)","Low","P2","After simultaneous cold restart, MTF opener bypasses are suppressed 600s to prevent ~96 junk opens. Vec has no cold-start concept — startup backtest vs live diverge for first 10m."),
    ("15","Partial Profit Lock (PPL) vec hook partial","REDUCE","ez_manage.py\nPPL 43200+","PARTIAL_PROFIT_LOCK_ENABLED (False)","YES","PARTIAL","LOW (default False)","Low","P1","PPL is default False (dormant). When enabled, live auto-locks 50% + upgrades stop to BE (even during AGENT_HOLD). Vec has partial PPL but stop-upgrade logic differs — profit-lock gaps if PPL enabled."),
    ("16","All-TF-against / GR HTF direct — partly vec-hooked","EXIT","ez_manage.py\nALL_TF_AGAINST / GR_HTF","ALL_TF_AGAINST_CLOSE_ENABLED etc.","YES","PARTIAL","LOW","Low","P1","Two HTF reversal exits are PARTIAL in vec — gain gate / cooldown nuances missing; live exits slightly more selective."),
    ("17","SatOshit exit — family partly vec-hooked","REDUCE","ez_manage.py\nsatoshit","SATOSHIT_EXIT_ENABLED","YES","PARTIAL","LOW-MEDIUM","Low-Med","P1","SatOshit (GR/vol/stoch/K) is core reduce family; PARTIAL means some knob variants (EXIT_SCORER_* etc.) not ported — reduce count gap ~10-15%."),
    ("18","NOLOSS / BE exits — NOLOSS vec partial","REDUCE / EXIT","ez_manage.py NOLOSS","NOLOSS_MIN_PROFIT_PCT","YES","PARTIAL","LOW","Low","P1","NOLOSS family vec partial; HTF_QUICK_TP twin exists but MIN_PROFIT threshold differs slightly."),
    ("19","Blacklist / tradeable-keys pre-filter — live O(1) first-line filter","GLOBAL","ez_manage.py\nprocess_position 43046+","BLACKLIST_SYMBOLS","NO","NO","MEDIUM — negative-Sharpe symbols blocked live but not in vec population","Low","P1","process_position returns instantly for blacklisted / non-tradeable keys (saves 12s compute / symbol / cycle). Vec population may include these symbols — backtest universe overcount."),
    ("20","Frozen/breach floor emergency exits (tradier)","EXIT (tradier)","tradier_manage.py\nprocess_position","FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER (-8%)","NO","NO","MEDIUM (tradier only)","Medium","P1","Tradier frozen/breach floor exits at -8% are hard loss-cuts even through NOLOSS (tradier only). Vec tradition has no price-based emergency exits — tradier backtest overestimates tail loss."),
    ("21","V3 emergency close (scalps) — live SCALP_V3 has no vec twin","EXIT","ez_positions_quick.py\n_v3_emergency_close 18357+","SCALP_V3_ENABLED","YES","NO","LOW (scalp only)","Low","P2","SCALP_V3 is scalping-specific override; vec has no scalp path — scalar scalp backtests diverge."),
    ("22","Live entry-engine boost — additive reentry size_mult not in vec","REENTRY (sizing)","ez_manage.py / ez_positions_quick.py\n_ee_reentry_boost","LIVE_ENTRY_ENGINE_ENABLED","NO","NO","LOW — additive only, never blocks reentries","Low","P2","Entry-engine reentry boost is pure-additive (never blocks a reentry; only scales size up to LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT). Vec doesn't scale — size distribution gap only."),
    ("23","DC low_4h reclaim K/WT alignment nuance","REENTRY","ez_manage.py\nR1 reclaim","R1_DC_LOW4_*","YES","YES","NONE","P0","R1 reclaim vec hook is NEW (2026-09); verify prev-bar level (dc_low_4h_prev) usage matches live — Donchian auto-extends bug previously broke it."),
    ("24","vec_deltas vs live: delta accel contract (z-speed lookback = DELTA_ACCEL_LOOKBACK bars)","REDUCE","wt_dc_delta.py\n tradier_manage.py","DELTA_EXIT_ACCEL_THRESHOLD etc.","YES","YES","NONE (repaired 2026-07)","P0","Repaired 2026-07 — verify DELTA_ACCEL_LOOKBACK direction-specific z-speed delta contract remains in sync after TEMPLATE edits."),
    ("25","STRICT_VEC_PARITY gate — when enabled, live trades shrink to vec-achievable routes","GLOBAL","ez_manage.py\nvec_paths/vec_parity_gate.py","STRICT_VEC_PARITY_MODE (False)","NO","NO (gate itself)","LOW (default OFF)","Low","P2","Gate is default OFF (dormant). When enabled it BLOCKS live trades whose reason is not in vec allowlist — diagnostic/audit mode only, not live-prod."),
]
for idx, row in enumerate(gaps, 2):
    vals=list(row)
    prio=vals[9]
    fills={}; fonts={}
    if "CRITICAL" in prio: fills[10]=fill_red; fonts[10]=font_red
    elif prio=="P0": fills[10]=fill_red; fonts[10]=font_red
    elif prio=="P1": fills[10]=fill_yellow; fonts[10]=font_orange_c
    elif prio=="P2": fills[10]=fill_lblue; fonts[10]=Font(size=7, color="1F4E78")
    # impact col
    impact=vals[7]
    if "CRITICAL" in impact or "HIGH" in impact: fills[8]=fill_red; fonts[8]=font_red
    elif "MEDIUM" in impact: fills[8]=fill_yellow; fonts[8]=font_orange_c
    elif "LOW" in impact: fills[8]=fill_lblue; fonts[8]=Font(size=7, color="1F4E78")
    for c in range(1, len(vals)+1):
        cl=ws4.cell(row=idx, column=c, value=vals[c-1])
        cl.alignment=wrap if c in (11,12) else center_wrap if c in (1,3,7,8,9,10) else wrap
        cl.border=thin_border; cl.font=Font(size=7)
        if c in fills: cl.fill=fills[c]
        if c in fonts: cl.font=fonts[c]
    max_len=max(len(str(v)) for v in vals)
    ws4.row_dimensions[idx].height=56 if max_len>160 else 42 if max_len>110 else 28
print(f"  04_LIVE_VS_VEC_GAPS: {len(gaps)} rows")

# ━━━━━━━━━ 05_SERVICE_AUDIT ━━━━━━━━━
headers5 = ["Service","File","Lines","Status","Imported By","Called From Live?","What It Does","What Happens If It Runs","Evidence","Action Required"]
ws5 = new_sheet(wb, "05_SERVICE_AUDIT", headers5, [18,24,8,10,22,13,34,34,26,22])
audit = [
    ("ez_positions_quick","ez_positions_quick.py","19,418","LIVE — active","tradier_manage.py:544 (import as _tradier_matrix_gates)\nbacktest_v12_engine.py:517 (import ez_positions_quick)\nez_manage.py:32116 (from ez_positions_quick import ...)","YES — called from tradier_manage._shared_direct_route, backtest_v12_engine stub, ez_manage execute_now ladder","Position sizing, AdvancedSignalRater (ENTRY/EXIT scoring), hedge engine, trade execution, reentry quick path; core crypto+stocks live logic","Live trading — no change","grep: ez_positions_quick is importable and write-allowed in _POSITION_WRITE_ALLOWED_FILES (line 1602); tradier_manage line 544 imports it","Keep — this is THE live service"),
    ("ez_positions_service","ez_positions_service.py","13,945","HYBRID — storage alive,\ntrading loops dead-gated","ez_positions_quick.py:118 (from ez_positions_service import bootstrap_position_service) — but never invoked from ez_manage or tradier_manage main loops","NO — ez_manage.py has ZERO imports of ez_positions_service in its process_position / execute_now hot path; only dead-path references","Historical positions-service RPC (PositionsServiceClient, WS event handlers, hedge manager, Position class, DummyLock) — superseded by ez_positions_quick's embedded logic","Would duplicate position tracking and re-introduce stale WS/hedge loops if re-enabled; 2026-08 BACKTEST_BIBLE says hedge engine is stripped/moved to MTF compound exit","grep: ez_positions_service is NOT in ez_manage.py's process_position; import at line 118 is vestigial; LOCKED_FILES.md never lists it as locked (only ez_manage, tradier_manage, config, backtest_v8)","Do NOT re-enable. Keep file on disk for provenance but remove vestigial import at ez_positions_quick.py:118 or guard it behind LIVE_POSITIONS_SERVICE_ENABLED=False"),
    ("ez_manage (MultiAccountTradeManager)","ez_manage.py","55,539","LIVE — canonical crypto trader","backtest_v12_engine.py:196 (Config wrapping)","YES — process_position (43046), check_entry_trigger (320), execute_now (27039), queue_trade_action (49814)","Crypto cycle: process_position per symbol → check_entry/exit → queue_trade_action → OrderQueue → execute_now (single gate). 124 unique BLOCKED_/SKIPPED_ gates.","Live crypto trading","All live orders MUST go through execute_now per CLAUDE.md EXECUTE_NOW IS THE ONLY GATE","Keep — canonical crypto engine"),
    ("tradier_manage","tradier_manage.py","28,435","LIVE — canonical stocks trader","backtest_v12_engine.py (tradier path)","YES — process_position (7742), execute_now (21144), queue_trade_action (10856)","Stocks cycle: process_position per symbol → HTF/STDEV/GR gates → queue_trade_action → OrderQueue → execute_now (single gate). Frozen/breach floor, R1 stop, stale-HOLD, Tradier advisory.","Live stocks trading","—","Keep — canonical stocks engine"),
    ("backtest_v12_engine (live replica)","backtest_v12_engine.py","~15,917","LIVE-FAITHFUL scalar verification","tools/*","YES — called per backtest cell to replay live call path bar-by-bar","Calls real ez_manage + ez_positions_quick + tradier_manage decision functions on frozen NPZ bars; honored as BACKTEST_BIBLE parity counterpart to v12_quick_engine","Backtest verification — never trades live","BACKTEST_BIBLE: parity = v12_quick_engine (vector) vs backtest_v12_engine (live call path)","Keep — parity counterpart"),
    ("v12_quick_engine (vector sweep)","v12_quick_engine.py","13,988","SWEEP ENGINE — 38× faster","vec_decisions/*, mtf_live_evaluator.py","NO (backtest-only) — never called from live hot path","Vectorised sweep of 1,465 QuickConfig switches (0.07s/eval); reads 852 unique getattr(cfg,..); 81 vec_decisions + 120+ shims","Fast parameter sweeps (TEMPLATE matrix, fleet, MATRIX_FOCUS)","BACKTEST_BIBLE ENGINE MIGRATION: v12_quick is sweep engine; must match backtest_v12_engine live path","Keep — sweep engine; keep gaps in 04_GAPS under active repair"),
    ("v12_wide_engine (ex v8_vec_sweep)","v12_wide_engine.py","858,758 bytes","WIDE ENGINE — 490 switches QC can't express","v8_vec_sweep deprecation alias","NO (backtest-only)","875 real switch reads (vs 851 in quick); 279 fields declared in quick but only read in wide; shared 233 switches; 2.67s/eval","Full-grid wide sweeps where quick can't express switch","BACKTEST_BIBLE: wide and quick are NOT interchangeable; shared switch surface only 233/~1488","Keep but don't mix tiers (vec vs wide) — delta_crosstier bug fixed 2026-07"),
]
for idx, row in enumerate(audit, 2):
    vals=list(row)
    fills={}; fonts={}
    status=vals[3]
    if "LIVE" in status: fills[4]=fill_green; fonts[4]=font_green
    elif "DEAD" in status: fills[4]=fill_red; fonts[4]=font_red
    elif "SWEEP" in status or "WIDE" in status: fills[4]=fill_yellow; fonts[4]=font_orange_c
    elif "canonical" in status.lower(): fills[4]=fill_green; fonts[4]=font_green
    for c in range(1, len(vals)+1):
        cl=ws5.cell(row=idx, column=c, value=vals[c-1])
        cl.alignment=center_wrap if c in (1,4,6) else wrap
        cl.border=thin_border; cl.font=Font(size=7)
        if c in fills: cl.fill=fills[c]
        if c in fonts: cl.font=fonts[c]
    max_len=max(len(str(v)) for v in vals)
    ws5.row_dimensions[idx].height=54 if max_len>150 else 38 if max_len>100 else 26
print(f"  05_SERVICE_AUDIT: {len(audit)} rows")
# Add verification checklist below audit table
check_start = len(audit)+3
checks = [
    ("VERIFY BEFORE SHIP — run these 4 checks, not assumptions", None),
    ("1. grep -rn 'ez_positions_service' ez_manage.py tradier_manage.py  → expect 0 hits in hot path (line 118 vestigial import only)", None),
    ("   If >0: queue_trade_action or process_position still calls ez_positions_service → re-disabled check fails", None),
    ("2. grep -c 'getattr(cfg' v12_quick_engine.py  → expect 1,046 raw / 852 unique (BACKTEST_BIBLE: v12_quick declares 1,465 QuickConfig fields but reads 852)", None),
    ("3. grep -n 'HEDGE_MODE.*False' config.py  → expect HEDGE_MODE: bool = False (line 129) + never flipped to True without Tier-2 900×900 proof", None),
    ("4. python -c 'import openpyxl; wb=openpyxl.load_workbook(\"SPREADSHEETS/TRADE_PATH_INVENTORY.xlsx\"); print(wb.sheetnames[:7])' → expect 00_README,01_TRADE_PATHS,...,06_TRADE_LIFECYCLE + originals", None),
    ("", None),
    ("If any check fails: fix source first, then re-run /tmp/build_final.py to regenerate this workbook.", Font(size=7, color=RED, bold=True)),
]
for i,(tx,fo) in enumerate(checks, check_start):
    c=ws5.cell(row=i, column=1, value=tx)
    c.alignment=Alignment(wrap_text=True, vertical="top")
    c.font=fo or Font(size=7, color="404040")
    ws5.merge_cells(start_row=i, start_column=1, end_row=i, end_column=10)
    ws5.row_dimensions[i].height=16
    if i==check_start:
        c.font=Font(bold=True, size=8, color=NAVY)
        c.fill=PatternFill("solid", fgColor=MID_BLUE)

# ━━━━━━━━━ 06_TRADE_LIFECYCLE ━━━━━━━━━
headers6 = ["Stage","State","Trigger","Actions Available","Key Filters (that can block)","TEMPLATE Sheet(s)","Vec Representation","Notes"]
ws6 = new_sheet(wb, "06_TRADE_LIFECYCLE", headers6, [10,14,30,22,36,20,20,30])
lifecycle = [
    ("1. FLAT","No position\n(positionAmt=0)","— (idle)","OPEN (E01–E10)\nREENTRY (R01–R11) if prior close\nscans: TradeableKeys","BLACKLIST → NON_TRADEABLE → MANAGED_ACCOUNTS → NO_INDICATOR_DATA → STALE_HOLD (tradier) → AGENT block_entry","ENTRY_* (all)\nREENTRY_*","v12_quick evaluate_sanitized (vector)\nbacktest_v12: process_position(posAmt=0)","FLAT is the ONLY state where ENTRY/REENTRY can fire; reentry paths require flat + prior tracked close metadata"),
    ("2. EVALUATING","Indicators loading\nis_data_fresh() check","process_position enter\n(43k+ / 7742)","ENTRY scoring\ncheck_entry_trigger","PER_SYM_SIDE_DISABLED → COUNTER_TREND → GR_FILTER → MTF_NO_ARMED → TOP_OF_RANGE → VEC_GATE/PARITY → BALANCE_FLOOR","FILTER_DICTIONARY_V8","vec_decisions (bb_pullback, dc_break, wt_cross, gr_filter)","Scoring happens inside check_entry_trigger; failed scores don't queue — biggest silent filter (score<ENTRY_SCORE_THRESHOLD)"),
    ("3. QUEUED","Order in OrderQueue\nawaiting execute_now","queue_trade_action()","— (waiting)","NO_DOUBLE_OPEN → REENTRY_POS_AMT_NONZERO guard → queue capacity","—","—","Queue is unbounded in practice; execute_now serializes per position_key (+ Redis lock tradier)"),
    ("4. EXECUTING","execute_now lock held","execute_now() single gate","OPEN/REENTRY execution","ABSOLUTE_OPEN_LOCK → 74 execute_now gates (see 02_FILTER_GATES)\n+ entry-price gate + UAGAIN + NOLOSS_MIN","—","vec quantity sizing (QuickConfig)","Single chokepoint per CLAUDE.md — every order (open/augment/reduce/close/hedge) must pass here; NO not-is_hedge bypass"),
    ("5. OPEN","Position live\n(positionAmt>0, gain tracking)","fill confirmed\nWS positionAmt>0","AUGMENT (A01–A10)\nREDUCE (RD01–RD16)\nEXIT (X01–X10)\nHOLD","LOSER_KILL (gain < 0.5×MIN_GAIN → AUGMENT blocked)\nALREADY_AUGMENTED\nHEDGE_PROTECT_OPPOSITE_LOSER\nUNIVERSAL_NOLOSS_GATE blocks loss CLOSE","— (live guards)\nAUGMENT_* / REDUCE_* / EXIT_* for sweeps","Vec simulates position state: qty/avg_price/deployed-capital; open→close fixed-notional model was fixed to avg-trade-deployed-2000-v1","OPEN state is where AUGMENT vs REDUCE vs HOLD diverge; 80% of live CPU is process_position in OPEN loop"),
    ("6. AUGMENT CHECK","Add to winner?\nGain ≥ MIN_GAIN?","A_BOUNCE/B/C/D gates\nfallback / DD bounce","AUGMENT (size × mult)","AUGMENT_MIN_GAIN_PCT\nAUGMENTATION_COOLDOWN (900s)\nHARD_AUGMENT_LOCK\nLOSER_KILL\nAUGMENT_HEDGE_MOTHER\nCOUNTER_TREND_ADD_BLOCK\nBREAKOUT_SIZE_LADDER caps mult","AUGMENT_* (all)","vec augment gates partial (01_TRADE_PATHS: PARTIAL/VEC_MISSING)","Augment only adds to WINNERS (gain-filtered); martingale via counter-trend is killed by COUNTER_TREND_ADD_BLOCK"),
    ("7. REDUCE CHECK","Take profit?\nGain ≥ tier threshold?","RD01–RD16 triggers\nPPL / Peak giveback / Delta\nQUICK_REDUCE rate(is_exit)","REDUCE frac → PPL BE shift\n(PPL even during AGENT_HOLD)","PARTIAL_PROFIT_LOCK_GAIN_PCT\nPEAK_GIVEBACK_DROP_TRIGGER\nRECENT_REDUCTION_GUARD (anti-churn)\nQUICK_REDUCE_TECHNICAL_ONLY (filters pure-stoch traps)","REDUCE_* / EXIT_*","vec: peak_giveback yes; QUICK_REDUCE no (rater missing); CYCLE_TP no","REDUCE takes partial profit while position stays OPEN; REDUCE resets recent_reduces cooldown (900s) + enables reentry path"),
    ("8. EXIT DECISION","Close entirely?\nGain + reason vs gates","X01–X10 triggers\nprocess_position exit branch","CLOSE (full)\n→ returns to FLAT","UNIVERSAL_NOLOSS_GATE blocks loss closes (except TECHNICAL: dc_low_4h / WT cross / HEDGE_FAILED)\nNOLOSS_MIN_PROFIT_PCT\nHARD_REDUCE_LOCK\nAGENT_HOLD defers to PPL+emergency only","EXIT_* (all)","vec: dc_breach WT_cross K_extreme yes; stale/breach/r1_stop no; hedge-failed no","EXIT is full close → positionAmt=0 → enables REENTRY after cooldown; TECHNICAL exits bypass NOLOSS"),
    ("9. REDUCED","Was OPEN\nwas_reduced flag set\nlast_reduction_* stamped","REDUCE fill confirmed","Wait → REENTRY\n(R01–R11 after cooldown)","RECENT_REDUCTION_GUARD (600s)\nRECENT_REDUCES map\nreentry snapshot tracking","REENTRY_*","vec: daemon/B00/recovery reentry partial","Intermediate state — position still OPEN but reduced; next event can be another REDUCE or EXIT or (after FLAT) REENTRY"),
    ("10. HOLD / WATCHDOG","Holding (no trigger this cycle)","no entry/exit signal this bar\n→ HOLD","position_watchdog reschedules\nprocess_position re-enters next cycle","COOLDOWN_ACTIVE (2s per-position queue)\nALREADY_PROCESSING\nGhost cooldown\nHeartbeat checks","—","—","HOLD is default — most cycles are no-ops; watchdog loops (position_watchdog_loop, monitor_entries) re-evaluate every 15–60s"),
]
for idx, row in enumerate(lifecycle, 2):
    vals=list(row)
    fills={}; fonts={}
    stage=vals[0]
    if "FLAT" in stage: fills[1]=fill_lblue; fills[2]=fill_lblue
    elif "OPEN" in stage or "AUGMENT" in stage: fills[1]=fill_lgreen; fills[2]=fill_lgreen
    elif "REDUCE" in stage or "EXIT" in stage: fills[1]=fill_orange; fills[2]=fill_orange
    elif "QUEUED" in stage or "EXECUTING" in stage: fills[1]=fill_lyellow; fills[2]=fill_lyellow
    for c in range(1, len(vals)+1):
        cl=ws6.cell(row=idx, column=c, value=vals[c-1])
        cl.alignment=wrap
        cl.border=thin_border; cl.font=Font(size=7)
        if c in fills: cl.fill=fills[c]
        if c in fonts: cl.font=fonts[c]
        if c==1: cl.font=Font(size=7, bold=True)
    ws6.row_dimensions[idx].height=52 if idx in (2,6,8,9) else 36

# Worked examples below lifecycle table
ex_start = len(lifecycle)+4
examples = [
    ("WORKED EXAMPLES — follow a position through filters", None),
    ("Ex 1 — Breakout open → PPL → DC breach exit:  FLAT --E01(dc_high_3m prev breakout, passes TRADEABLE+GR_FILTER+MTF armed)--> OPEN $2k --RD15(PPL @ +1.8% → 50% take, BE shift to entry+0.10%)--> STILL OPEN (was_reduced, effective_entry adjusted) --X01(price < dc_low_4h @ -2.1% → TECHNICAL bypasses NOLOSS)--> FLAT --R01(reclaim dc_low_4h_prev → immediate REENTRY)--> OPEN", None),
    ("Ex 2 — Pullback bounce open blocked:  FLAT --E05(BB%b checks)--> BLOCKED_MTF_NO_ARMED_STATE (cold-start, no armed MTF state yet, 30-60m re-arm window; vec also blocks if no armed state but live additionally has COLD_START flood-guard 600s bypass suppress)", None),
    ("Ex 3 — Augment vs LOSER_KILL:  OPEN gain +0.8% (MIN_GAIN=3.0%, 0.5×=1.5%) --A01(A_BOUNCE)--> BLOCKED_ALREADY_AUGMENTED + BLOCKED_LOSER_KILL_NEGATIVE (gain 0.8% < 1.5% loser-kill floor); live holds, vec would also block if mock includes loser-kill gate (PARTIAL gap). Same augment at +3.2% gain would pass gain gate but still face BOUNCE_TF_ALIGNED_MIN (need 2/3 WT TFs aligned).", None),
    ("Ex 4 — Reentry vs anti-churn:  FLAT after RD14(peak giveback @ -1.2% giveback) --R03(daemon price-cross: cur=exit_px+0.3%)--> BLOCKED_RECENT_REDUCTION_GUARD (only 40s since reduce, need 600s unless dc_high_3m_prev breakout) → HOLD; 10m later same signal passes guard → REENTRY fires.", None),
    ("Ex 5 — Reduce via QUICK_REDUCE rater (live-only path):  OPEN gain +2.1% → AdvancedSignalRater.rate(is_exit=True) returns REDUCE( reason=WT_PERCENTILE_pctD=94_4h=91); QUICK_REDUCE_TECHNICAL_ONLY checks if reason carries TECHNICAL token (R1/R2/WT-vel/MTF/ATR/DC/FAST_CUT/PPL/HEDGE_FAILED) — WT_PERCENTILE is NOT technical → QUICK_REDUCE_TRAP_SUPPRESSED (live holds; vec never calls rater, so vec gap is silent hold vs live hold aligned by accident).", None),
    ("Ex 6 — Universal no-loss gate in action:  OPEN gain -1.4% → RD01-SatOshit wants REDUCE at loss → execute_now: BLOCKED_BY_UNIVERSAL_NOLOSS_GATE (gain -1.4% < NOLOSS_MIN_PROFIT_PCT + no TECHNICAL token) → HOLD; same position at -1.4% but price breaches dc_low_4h → X01 TECHNICAL DC breach token → bypasses NOLOSS → CLOSE even at loss.", None),
    ("", None),
    ("KEY INSIGHT: filters explain MOST live↔vec count gaps. Before changing strategy thresholds (01_TRADE_PATHS columns H–J), check whether the trade was actually BLOCKED by a GATE (02_FILTER_GATES) or EARLY_RETURN (stage 1–2). Threshold tuning on a trade that never reaches scoring is wasted.", Font(size=7, color=NAVY, bold=True)),
]
for i,(tx,fo) in enumerate(examples, ex_start):
    c=ws6.cell(row=i, column=1, value=tx)
    c.alignment=Alignment(wrap_text=True, vertical="top")
    c.font=fo or Font(size=7, color="404040")
    ws6.merge_cells(start_row=i, start_column=1, end_row=i, end_column=8)
    if i==ex_start:
        c.font=Font(bold=True, size=8, color=NAVY)
        c.fill=PatternFill("solid", fgColor=MID_BLUE)
    ws6.row_dimensions[i].height=22 if tx and len(tx)<160 else 36
    if "KEY INSIGHT" in (tx or ""):
        c.fill=PatternFill("solid", fgColor=LIGHT_YELLOW)
        c.font=Font(size=7, color=NAVY, bold=True)

print(f"  05_SERVICE_AUDIT + 06_TRADE_LIFECYCLE done")

# ━━━━━━━━━ Save ━━━━━━━━━
# Add print settings and save
for ws in wb.worksheets:
    ws.sheet_properties.pageSetUpPr.fitToPage = True

# Set active sheet to 00_README
wb.active = wb["00_README"]

# Add original TEMPLATE columns enhancement — add Live/Vec columns to FILTER_DICTIONARY_V8
# Instead of modifying original, add note in 00_README already covers it
# Enhance FILTER_DICTIONARY_V8: if not already has our audit columns, add comment column
# We leave original sheets untouched (requirement: add columns and tabs to show what's correctly represented and what's missing)
# The 03_CONFIG_COVERAGE tab already serves as the column-addition - but also add a helper column to FILTER_DICTIONARY_V8

# Quick enhancement: add column R "Inventory Cross-Ref" to FILTER_DICTIONARY_V8 pointing to 01_TRADE_PATHS / 02_FILTER_GATES
try:
    ws_filt = wb["FILTER_DICTIONARY_V8"]
    # Header is row 1, columns A-Q exist (17 cols). Add R = Inventory Cross-Ref
    c = ws_filt.cell(row=1, column=18, value="Inventory Cross-Ref\n(01_TRADE_PATHS / 02_FILTER_GATES)")
    c.fill = PatternFill("solid", fgColor="FFC000")
    c.font = Font(bold=True, color="1F4E78", size=7)
    c.alignment = header_align
    c.border = thin_border
    ws_filt.column_dimensions[get_column_letter(18)].width = 28
    # Fill formula / note for first 5 rows
    for r in range(2, min(8, ws_filt.max_row+1)):
        filt_name = ws_filt.cell(row=r, column=2).value
        if filt_name and str(filt_name).strip():
            ws_filt.cell(row=r, column=18, value=f"→ 03_CONFIG_COVERAGE: {str(filt_name).strip()[:20]}\n→ 02_FILTER_GATES: gate row")
            ws_filt.cell(row=r, column=18).alignment = wrap
            ws_filt.cell(row=r, column=18).font = Font(size=6, color="1F4E78")
            ws_filt.cell(row=r, column=18).border = thin_border
except Exception as e:
    print(f"FILTER_DICTIONARY_V8 enhancement skipped: {e}")

wb.save(str(OUTPUT))
print(f"\n✓ Saved {OUTPUT}")
print(f"  Sheets ({len(wb.sheetnames)}): {wb.sheetnames}")
import os
print(f"  Size: {os.path.getsize(OUTPUT):,} bytes")
# Verify it re-opens
wb2 = openpyxl.load_workbook(str(OUTPUT))
print(f"  Verify re-open: {len(wb2.sheetnames)} sheets OK")
for s in wb2.sheetnames[:8]:
    print(f"    - {s}: {wb2[s].max_row} rows × {wb2[s].max_column} cols")
