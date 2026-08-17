#!/usr/bin/env python3
"""Create breakout miss analysis Excel spreadsheet for May 1-7 2026 crypto rally."""

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

OUTPUT_PATH = "/Users/niels/Documents/binance/data/breakout_miss_analysis_20260507.xlsx"

# Colors
RED_FILL = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
GREEN_FILL = PatternFill(start_color="CCFFCC", end_color="CCFFCC", fill_type="solid")
YELLOW_FILL = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
ORANGE_FILL = PatternFill(start_color="FFE0B2", end_color="FFE0B2", fill_type="solid")
BLUE_FILL = PatternFill(start_color="DDEEFF", end_color="DDEEFF", fill_type="solid")
HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
TITLE_FILL = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
GRAY_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=14)
BODY_FONT = Font(name="Calibri", size=10)
BOLD_FONT = Font(name="Calibri", bold=True, size=10)

thin = Side(style="thin", color="AAAAAA")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
THICK_BOTTOM = Border(left=thin, right=thin, top=thin, bottom=Side(style="medium", color="333333"))

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")


def style_header_row(ws, row, col_count):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = BORDER


def style_title_row(ws, row, col_count, title):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_count)
    cell = ws.cell(row=row, column=1)
    cell.value = title
    cell.fill = TITLE_FILL
    cell.font = TITLE_FONT
    cell.alignment = CENTER
    ws.row_dimensions[row].height = 30


def set_col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def apply_row_style(ws, row, col_count, fill=None, font=None, border=True):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row, column=col)
        if fill:
            cell.fill = fill
        if font:
            cell.font = font
        if border:
            cell.border = BORDER
        cell.alignment = LEFT


wb = openpyxl.Workbook()

# ============================================================
# SHEET 1: Breakout Misses
# ============================================================
ws1 = wb.active
ws1.title = "Breakout Misses"
ws1.freeze_panes = "A3"

TITLE = "BREAKOUT MISS ANALYSIS — May 1-7 2026 — 19 Symbols × 4 Accounts"
style_title_row(ws1, 1, 8, TITLE)

headers = ["Symbol", "7d Peak%", "Accounts Affected", "Last Action", "Root Cause Code(s)", "Root Cause Description", "Estimated Missed Gain", "Priority Fix"]
for col, h in enumerate(headers, 1):
    ws1.cell(row=2, column=col).value = h
style_header_row(ws1, 2, len(headers))
ws1.row_dimensions[2].height = 22

data = [
    # symbol, 7d%, accounts, last_action, rc_codes, rc_desc, est_missed_gain, priority
    ("TONUSDT",       "+121%", "ang,inf,fin,men", "GOLDEN_RULE SHORT opened May 7 — WRONG SIDE",         "RC1, RC4",     "GOLDEN_RULE anti-trend SHORT; stale indicators",                                "+121% missed",  "CRITICAL"),
    ("IOUSDT",        "+83%",  "ang,inf",         "GOLDEN_RULE SHORT opened May 6 — WRONG SIDE",         "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+83% missed",   "CRITICAL"),
    ("NOTUSDT",       "+82%",  "inf,fin,men",     "DC_BB_D_BREAK_REVERSE closed LONG May 7",             "RC5",          "DC_BB_D_BREAK_REVERSE kills correct-side LONG positions",                       "+82% missed",   "HIGH"),
    ("ZECUSDC",       "+94%",  "ang,inf,fin,flz", "GOLDEN_RULE SHORT + hedge close May 7",               "RC1, RC2, RC3","GOLDEN_RULE wrong side + dust block + stale px (359 vs 500+)",                 "+94% missed",   "CRITICAL"),
    ("CHILLGUYUSDT",  "+78%",  "ang,inf",         "DC_BB_D_BREAK_REVERSE killed LONG May 7",             "RC5",          "DC_BB_D_BREAK_REVERSE kills correct-side LONG positions",                       "+78% missed",   "HIGH"),
    ("DASHUSDT",      "+69%",  "ang,inf,fin,men", "GOLDEN_RULE SHORT May 7 — WRONG SIDE",                "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+69% missed",   "CRITICAL"),
    ("BIOUSDC",       "+67%",  "ang,inf,fin",     "WT_4H_VEL_EXIT May 7 16:00",                          "RC5",          "Exit gate fires during trend continuation",                                      "+67% missed",   "HIGH"),
    ("1000LUNCUSDT",  "+65%",  "ang,inf",         "RIDICULOUS_HOLD closed at g=-0.08% after 1508h",      "RC6",          "RIDICULOUS_HOLD (48h cap) kills aged positions into rallies",                   "+65% missed",   "HIGH"),
    ("HMSTRUSDT",     "+58%",  "inf",             "GOLDEN_RULE SHORT May 6 — WRONG SIDE",                "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+58% missed",   "CRITICAL"),
    ("PENDLEUSDT",    "+56%",  "ang,inf",         "GOLDEN_RULE LONG May 7 (correct side)",               "RC7",          "Correct side captured — no miss",                                               "0% (captured)", "NONE"),
    ("AXLUSDT",       "+53%",  "ang,inf",         "GOLDEN_RULE SHORT May 7 — WRONG SIDE",                "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+53% missed",   "CRITICAL"),
    ("HIVEUSDT",      "+52%",  "ang,inf",         "GOLDEN_RULE SHORT May 6 — WRONG SIDE",                "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+52% missed",   "CRITICAL"),
    ("STORJUSDT",     "+52%",  "ang,inf",         "GOLDEN_RULE SHORT May 6 — WRONG SIDE",                "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+52% missed",   "CRITICAL"),
    ("ORDIUSDC",      "+49%",  "ang,inf,fin,men", "GOLDEN_RULE SHORT May 7 during LONG rally",           "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+49% missed",   "CRITICAL"),
    ("KSMUSDT",       "+45%",  "ang,inf,fin,men", "BOTH LONG+SHORT opened 9min apart",                   "RC7",          "GOLDEN_RULE self-hedge: opened opposing positions within minutes",               "+45% wasted",   "HIGH"),
    ("ONDOUSDT",      "+43%",  "ang,inf",         "GOLDEN_RULE SHORT May 6 — WRONG SIDE",                "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+43% missed",   "CRITICAL"),
    ("ARUSDT",        "+43%",  "ang,inf",         "DC_BB_D_BREAK_REVERSE + RIDICULOUS_HOLD -2.3%",       "RC5, RC6",     "Double kill: exit gate + 48h age cap during trending market",                   "+43% missed",   "HIGH"),
    ("VIRTUALUSDT",   "+42%",  "ang,inf,men",     "DC_BB_D_BREAK_REVERSE closed LONG twice",             "RC5",          "DC_BB_D_BREAK_REVERSE kills correct-side LONG positions",                       "+42% missed",   "HIGH"),
    ("ZENUSDT",       "+42%",  "ang,inf,fin,men", "GOLDEN_RULE SHORT May 7 during LONG rally",           "RC1",          "GOLDEN_RULE anti-trend SHORT",                                                  "+42% missed",   "CRITICAL"),
]

for row_idx, row_data in enumerate(data, 3):
    ws1.row_dimensions[row_idx].height = 18
    for col_idx, val in enumerate(row_data, 1):
        cell = ws1.cell(row=row_idx, column=col_idx)
        cell.value = val
        cell.border = BORDER
        cell.alignment = LEFT
        cell.font = BODY_FONT

    action = row_data[3]
    priority = row_data[7]
    # Color logic
    fill = GRAY_FILL if row_idx % 2 == 0 else None
    if "WRONG SIDE" in action:
        ws1.cell(row=row_idx, column=4).fill = RED_FILL
    if "correct side" in action.lower() or "0% (captured)" in row_data[6]:
        ws1.cell(row=row_idx, column=4).fill = GREEN_FILL
        ws1.cell(row=row_idx, column=7).fill = GREEN_FILL
    if "missed" in row_data[6]:
        ws1.cell(row=row_idx, column=7).fill = RED_FILL
    if "wasted" in row_data[6]:
        ws1.cell(row=row_idx, column=7).fill = ORANGE_FILL
    if priority == "CRITICAL":
        ws1.cell(row=row_idx, column=8).fill = RED_FILL
        ws1.cell(row=row_idx, column=8).font = Font(name="Calibri", bold=True, size=10, color="CC0000")
    elif priority == "HIGH":
        ws1.cell(row=row_idx, column=8).fill = ORANGE_FILL
    elif priority == "NONE":
        ws1.cell(row=row_idx, column=8).fill = GREEN_FILL

    if fill:
        for col in range(1, len(headers) + 1):
            existing = ws1.cell(row=row_idx, column=col).fill
            if existing.fgColor.rgb == "00000000" or existing.fgColor.rgb == "FFFFFFFF":
                ws1.cell(row=row_idx, column=col).fill = fill

set_col_widths(ws1, [18, 10, 20, 45, 16, 55, 18, 14])

# ============================================================
# SHEET 2: Root Causes
# ============================================================
ws2 = wb.create_sheet("Root Causes")
ws2.freeze_panes = "A3"
style_title_row(ws2, 1, 7, "ROOT CAUSE ANALYSIS — May 1-7 2026 Breakout Misses")

rc_headers = ["RC Code", "Name", "Description", "Symbols Affected (Count)", "Fix Proposed", "Impl. Effort", "Priority"]
for col, h in enumerate(rc_headers, 1):
    ws2.cell(row=2, column=col).value = h
style_header_row(ws2, 2, len(rc_headers))
ws2.row_dimensions[2].height = 22

rc_data = [
    ("RC1", "GOLDEN_RULE Anti-Trend SHORT",
     "GOLDEN_RULE fires SHORT positions during broad market LONG breakouts. During a +40-121% 7-day bull run, 13/19 symbols received WRONG SIDE SHORT entries via GOLDEN_RULE.",
     "13/19 (68%)",
     "Add trend filter: block SHORT if 7d_return > +20% on that symbol",
     "Medium", "CRITICAL"),
    ("RC2", "Dust Position Blocks Reentry",
     "After partial close, remaining quantity falls below 2×min_qty creating a 'dust' position. This dust blocks any reentry signal because the position is still technically open.",
     "1/19 (ZECUSDC confirmed)",
     "After partial close, if remaining qty < 2×min_qty, immediately close remainder",
     "Low", "HIGH"),
    ("RC3", "Stale px in GUARANTEED_REENTRY",
     "GUARANTEED_REENTRY reads entry price from Redis hot_metrics cache. On ZECUSDC, px=359 was used vs live market ~500+ — a 40% stale gap causing wrong sizing and gate calculations.",
     "1/19 (ZECUSDC confirmed)",
     "GUARANTEED_REENTRY must use live mark_price cache, not Redis entry px",
     "Low", "HIGH"),
    ("RC4", "Stale Indicator Cache",
     "WT/stoch indicators cached with wrong TF readings cause GOLDEN_RULE to see bearish signals during bullish breakouts. Indicator freshness not validated before GOLDEN_RULE fires.",
     "1/19 (TONUSDT)",
     "Validate indicator freshness (<3 bar age) before allowing GOLDEN_RULE to fire SHORT",
     "Medium", "HIGH"),
    ("RC5", "DC_BB_D_BREAK_REVERSE / WT_4H_VEL_EXIT Kill Correct LONGs",
     "Exit gates designed for mean-reversion close profitable LONG positions mid-trend. DC_BB_D_BREAK_REVERSE interprets strong breakout bb_pct_b as 'overextended' and exits. WT_4H_VEL_EXIT closes on velocity change even during trend continuation.",
     "5/19 (NOTUSDT, CHILLGUYUSDT, BIOUSDC, ARUSDT, VIRTUALUSDT)",
     "Add trend check: skip DC_BB_D_BREAK_REVERSE exit if bb_pct_b_D > 0.7; skip WT_4H_VEL_EXIT if 7d_return > +20%",
     "Medium", "HIGH"),
    ("RC6", "RIDICULOUS_HOLD 48h Cap Kills Into Rallies",
     "RIDICULOUS_HOLD closes any position held >48h (1508h in LUNC case) regardless of market conditions. During bull rallies, aged positions may be correct — they should not be force-closed.",
     "2/19 (1000LUNCUSDT, ARUSDT)",
     "Exempt from 48h cap if 7d_return > +20% (trending market exception)",
     "Low", "MEDIUM"),
    ("RC7", "GOLDEN_RULE Self-Hedge",
     "GOLDEN_RULE opens both LONG and SHORT on the same symbol within minutes (KSMUSDT: 9 min apart). The opposing positions cancel each other and generate double fees with zero directional exposure.",
     "1/19 (KSMUSDT) + 1 captured correctly (PENDLEUSDT)",
     "After GOLDEN_RULE opens LONG, suppress opposite-side GOLDEN_RULE for same symbol for 4h minimum",
     "Low", "HIGH"),
]

for row_idx, row_data in enumerate(rc_data, 3):
    ws2.row_dimensions[row_idx].height = 60
    for col_idx, val in enumerate(row_data, 1):
        cell = ws2.cell(row=row_idx, column=col_idx)
        cell.value = val
        cell.border = BORDER
        cell.font = BODY_FONT
        cell.alignment = LEFT

    effort = row_data[5]
    priority = row_data[6]
    row_fill = GRAY_FILL if row_idx % 2 == 0 else None

    if priority == "CRITICAL":
        ws2.cell(row=row_idx, column=7).fill = RED_FILL
        ws2.cell(row=row_idx, column=7).font = Font(name="Calibri", bold=True, size=10, color="CC0000")
        ws2.cell(row=row_idx, column=1).fill = RED_FILL
    elif priority == "HIGH":
        ws2.cell(row=row_idx, column=7).fill = ORANGE_FILL
        ws2.cell(row=row_idx, column=1).fill = ORANGE_FILL
    elif priority == "MEDIUM":
        ws2.cell(row=row_idx, column=7).fill = YELLOW_FILL

    if effort == "Low":
        ws2.cell(row=row_idx, column=6).fill = GREEN_FILL
    elif effort == "Medium":
        ws2.cell(row=row_idx, column=6).fill = YELLOW_FILL
    elif effort == "High":
        ws2.cell(row=row_idx, column=6).fill = RED_FILL

    if row_fill:
        for col in range(2, 6):
            if ws2.cell(row=row_idx, column=col).fill.fgColor.rgb in ("00000000", "FFFFFFFF"):
                ws2.cell(row=row_idx, column=col).fill = row_fill

set_col_widths(ws2, [8, 28, 70, 22, 65, 14, 10])

# ============================================================
# SHEET 3: Fixes Required
# ============================================================
ws3 = wb.create_sheet("Fixes Required")
ws3.freeze_panes = "A3"
style_title_row(ws3, 1, 5, "FIXES REQUIRED — Post-Rally Action Plan")

fix_headers = ["Fix #", "Component", "Description", "Expected Impact", "Status"]
for col, h in enumerate(fix_headers, 1):
    ws3.cell(row=2, column=col).value = h
style_header_row(ws3, 2, len(fix_headers))
ws3.row_dimensions[2].height = 22

fixes = [
    (1,  "ez_manage.py + config.py",
     "GOLDEN_RULE Trend Filter: Block GOLDEN_RULE SHORT if symbol's 7d_return > +20%. Read 7d_return from market_data cache (already computed). Config switch: GOLDEN_RULE_TREND_FILTER_ENABLED (default ON).",
     "Prevents 13/19 wrong-side entries. RC1. Eliminates systematic anti-trend shorts during bull runs. Est. +40-121% per position recovered.",
     "Implement Now"),
    (2,  "ez_manage.py",
     "Dust Cleanup on Partial Close: After partial close (e.g. PPL step 1), compute remaining qty. If remaining_qty < 2 × min_notional/price, immediately queue full close via execute_now. No manual intervention needed.",
     "Unblocks reentry on ZECUSDC-type symbols. RC2. Prevents dust positions blocking correct-side signals.",
     "Implement Now"),
    (3,  "ez_manage.py",
     "BB Breakout Persistence Flag: After any position close, if bb_pct_b_1h > 0.85 at close time, set BREAKOUT_ACTIVE[symbol]=True for 72h in Redis. While flag is set, force reentry on first pullback to bb_basis_1h. Config: BREAKOUT_PERSISTENCE_ENABLED.",
     "Captures continuation after premature exits. RC5. Expected to recover 50-80% of missed gain on NOTUSDT/CHILLGUYUSDT/VIRTUALUSDT type symbols.",
     "Implement Now"),
    (4,  "ez_manage.py (GUARANTEED_REENTRY path)",
     "Fix Stale px in GUARANTEED_REENTRY: Replace Redis hot_metrics[pk]['entry_price'] lookup with live mark_price_cache[symbol] call. Add assertion: abs(redis_px - live_px)/live_px < 0.05 or use live. Log warning on >5% deviation.",
     "Fixes ZECUSDC px=359 vs 500+ stale gap. RC3. Prevents wrong sizing and gate miscalculations on reentry.",
     "Implement Now"),
    (5,  "ez_manage.py + config.py",
     "BREAKOUT_TIGHT_STOP Widening: When BREAKOUT_ACTIVE flag is set for a symbol, use ±2.5% stop instead of standard ±0.5% PPL stop. Prevents premature stop-out during volatile breakout continuation. Config: BREAKOUT_STOP_PCT=2.5.",
     "Keeps positions open through normal breakout volatility. Complements Fix #3. Prevents immediate re-close after reentry.",
     "Next Sprint"),
    (6,  "ez_manage.py + config.py",
     "RIDICULOUS_HOLD Trending Exception: Before force-closing via RIDICULOUS_HOLD, check if symbol's 7d_return > +20%. If yes, extend hold cap by 48h and log RIDICULOUS_HOLD_TREND_EXEMPT. Config: RIDICULOUS_HOLD_TREND_EXEMPT_ENABLED=True.",
     "Saves aged positions during bull runs (RC6). LUNC was +65% into rally — 1508h hold was correct. Prevents force-close of winners.",
     "Implement Now"),
    (7,  "ez_manage.py",
     "DC_BB_D_BREAK_REVERSE Trend Check: Before closing LONG via DC_BB_D_BREAK_REVERSE, check bb_pct_b_D. If bb_pct_b_D > 0.7, skip the close and log DC_BB_TREND_EXEMPT. Same for WT_4H_VEL_EXIT: skip if 7d_return > +20%. Config switches.",
     "Prevents 5 wrong-side exits (RC5). NOTUSDT/CHILLGUYUSDT/BIOUSDC/ARUSDT/VIRTUALUSDT recoverable. Est. +42-82% per symbol.",
     "Implement Now"),
    (8,  "ez_manage.py + config.py",
     "GOLDEN_RULE Self-Hedge Suppression: Track GOLDEN_RULE_LAST_DIR[symbol] = {side, timestamp}. If GOLDEN_RULE fires opposite side within 4h of prior fire on same symbol, suppress and log GR_SELF_HEDGE_BLOCK. Config: GOLDEN_RULE_SELF_HEDGE_COOLDOWN_H=4.",
     "Stops KSMUSDT-type self-hedges (RC7). Saves 2× commission on cancelling positions. Directional clarity restored.",
     "Implement Now"),
]

for row_idx, row_data in enumerate(fixes, 3):
    ws3.row_dimensions[row_idx].height = 70
    for col_idx, val in enumerate(row_data, 1):
        cell = ws3.cell(row=row_idx, column=col_idx)
        cell.value = val
        cell.border = BORDER
        cell.font = BODY_FONT
        cell.alignment = LEFT

    status = row_data[4]
    if status == "Implement Now":
        ws3.cell(row=row_idx, column=5).fill = YELLOW_FILL
        ws3.cell(row=row_idx, column=5).font = Font(name="Calibri", bold=True, size=10, color="7B5700")
    elif status == "Next Sprint":
        ws3.cell(row=row_idx, column=5).fill = BLUE_FILL

    # Number cell bold
    ws3.cell(row=row_idx, column=1).font = Font(name="Calibri", bold=True, size=11)
    ws3.cell(row=row_idx, column=1).alignment = CENTER

    row_fill = GRAY_FILL if row_idx % 2 == 0 else None
    if row_fill:
        for col in range(2, 5):
            if ws3.cell(row=row_idx, column=col).fill.fgColor.rgb in ("00000000", "FFFFFFFF"):
                ws3.cell(row=row_idx, column=col).fill = row_fill

set_col_widths(ws3, [6, 30, 85, 55, 16])

# ============================================================
# SHEET 4: Per-Account Detail
# ============================================================
ws4 = wb.create_sheet("Per-Account Detail")
ws4.freeze_panes = "A3"
style_title_row(ws4, 1, 8, "PER-ACCOUNT DETAIL — Every Position Action × Symbol × Account")

pa_headers = ["Account", "Symbol", "Position Key", "Last Action", "Date", "Gain at Close", "Reason", "Blocks After Close"]
for col, h in enumerate(pa_headers, 1):
    ws4.cell(row=2, column=col).value = h
style_header_row(ws4, 2, len(pa_headers))
ws4.row_dimensions[2].height = 22

pa_data = [
    # account, symbol, pos_key, last_action, date, gain, reason, blocks_after
    ("ang", "TONUSDT",      "TONUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Position open wrong side — no LONG reentry"),
    ("inf", "TONUSDT",      "TONUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Position open wrong side — no LONG reentry"),
    ("fin", "TONUSDT",      "TONUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Position open wrong side — no LONG reentry"),
    ("men", "TONUSDT",      "TONUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Position open wrong side — no LONG reentry"),
    ("ang", "IOUSDT",       "IOUSDT_SHORT",          "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side open — LONG blocked"),
    ("inf", "IOUSDT",       "IOUSDT_SHORT",          "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side open — LONG blocked"),
    ("inf", "NOTUSDT",      "NOTUSDT_LONG",          "CLOSE LONG",              "May 7",   "~+2% partial","DC_BB_D_BREAK_REVERSE",         "No reentry triggered after close"),
    ("fin", "NOTUSDT",      "NOTUSDT_LONG",          "CLOSE LONG",              "May 7",   "~+2% partial","DC_BB_D_BREAK_REVERSE",         "No reentry triggered after close"),
    ("men", "NOTUSDT",      "NOTUSDT_LONG",          "CLOSE LONG",              "May 7",   "~+2% partial","DC_BB_D_BREAK_REVERSE",         "No reentry triggered after close"),
    ("ang", "ZECUSDC",      "ZECUSDC_SHORT",         "OPEN SHORT + hedge close", "May 7",  "N/A",         "GOLDEN_RULE wrong side",        "Dust qty blocking LONG; stale px=359 in GUARANTEED_REENTRY"),
    ("inf", "ZECUSDC",      "ZECUSDC_SHORT",         "OPEN SHORT + hedge close", "May 7",  "N/A",         "GOLDEN_RULE wrong side",        "Dust qty blocking LONG; stale px=359 in GUARANTEED_REENTRY"),
    ("fin", "ZECUSDC",      "ZECUSDC_SHORT",         "OPEN SHORT + hedge close", "May 7",  "N/A",         "GOLDEN_RULE wrong side",        "Dust qty blocking LONG; stale px=359 in GUARANTEED_REENTRY"),
    ("flz", "ZECUSDC",      "ZECUSDC_SHORT",         "OPEN SHORT + hedge close", "May 7",  "N/A",         "GOLDEN_RULE wrong side",        "Dust qty blocking LONG; stale px=359 in GUARANTEED_REENTRY"),
    ("ang", "CHILLGUYUSDT", "CHILLGUYUSDT_LONG",     "CLOSE LONG",              "May 7",   "~+1.5%",     "DC_BB_D_BREAK_REVERSE",         "No reentry — continuation missed"),
    ("inf", "CHILLGUYUSDT", "CHILLGUYUSDT_LONG",     "CLOSE LONG",              "May 7",   "~+1.5%",     "DC_BB_D_BREAK_REVERSE",         "No reentry — continuation missed"),
    ("ang", "DASHUSDT",     "DASHUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side open — LONG rally missed"),
    ("inf", "DASHUSDT",     "DASHUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side open — LONG rally missed"),
    ("fin", "DASHUSDT",     "DASHUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side open — LONG rally missed"),
    ("men", "DASHUSDT",     "DASHUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side open — LONG rally missed"),
    ("ang", "BIOUSDC",      "BIOUSDC_LONG",          "CLOSE LONG",              "May 7",   "+0.8%",       "WT_4H_VEL_EXIT @ 16:00 UTC",    "No BREAKOUT_ACTIVE flag set; no reentry"),
    ("inf", "BIOUSDC",      "BIOUSDC_LONG",          "CLOSE LONG",              "May 7",   "+0.8%",       "WT_4H_VEL_EXIT @ 16:00 UTC",    "No BREAKOUT_ACTIVE flag set; no reentry"),
    ("fin", "BIOUSDC",      "BIOUSDC_LONG",          "CLOSE LONG",              "May 7",   "+0.8%",       "WT_4H_VEL_EXIT @ 16:00 UTC",    "No BREAKOUT_ACTIVE flag set; no reentry"),
    ("ang", "1000LUNCUSDT", "1000LUNCUSDT_LONG",     "CLOSE LONG",              "May 7",   "-0.08%",     "RIDICULOUS_HOLD (1508h)",        "Position closed just before +65% rally leg"),
    ("inf", "1000LUNCUSDT", "1000LUNCUSDT_LONG",     "CLOSE LONG",              "May 7",   "-0.08%",     "RIDICULOUS_HOLD (1508h)",        "Position closed just before +65% rally leg"),
    ("inf", "HMSTRUSDT",    "HMSTRUSDT_SHORT",       "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("ang", "PENDLEUSDT",   "PENDLEUSDT_LONG",       "OPEN LONG",               "May 7",   "N/A (open)",  "GOLDEN_RULE LONG (correct)",    "Correct side — rally captured"),
    ("inf", "PENDLEUSDT",   "PENDLEUSDT_LONG",       "OPEN LONG",               "May 7",   "N/A (open)",  "GOLDEN_RULE LONG (correct)",    "Correct side — rally captured"),
    ("ang", "AXLUSDT",      "AXLUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("inf", "AXLUSDT",      "AXLUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("ang", "HIVEUSDT",     "HIVEUSDT_SHORT",        "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("inf", "HIVEUSDT",     "HIVEUSDT_SHORT",        "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("ang", "STORJUSDT",    "STORJUSDT_SHORT",       "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("inf", "STORJUSDT",    "STORJUSDT_SHORT",       "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("ang", "ORDIUSDC",     "ORDIUSDC_SHORT",        "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("inf", "ORDIUSDC",     "ORDIUSDC_SHORT",        "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("fin", "ORDIUSDC",     "ORDIUSDC_SHORT",        "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("men", "ORDIUSDC",     "ORDIUSDC_SHORT",        "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("ang", "KSMUSDT",      "KSMUSDT_LONG + KSMUSDT_SHORT", "OPEN BOTH 9min apart", "May 7", "N/A", "GOLDEN_RULE self-hedge",         "Both sides open; net directional exposure = 0"),
    ("inf", "KSMUSDT",      "KSMUSDT_LONG + KSMUSDT_SHORT", "OPEN BOTH 9min apart", "May 7", "N/A", "GOLDEN_RULE self-hedge",         "Both sides open; net directional exposure = 0"),
    ("fin", "KSMUSDT",      "KSMUSDT_LONG + KSMUSDT_SHORT", "OPEN BOTH 9min apart", "May 7", "N/A", "GOLDEN_RULE self-hedge",         "Both sides open; net directional exposure = 0"),
    ("men", "KSMUSDT",      "KSMUSDT_LONG + KSMUSDT_SHORT", "OPEN BOTH 9min apart", "May 7", "N/A", "GOLDEN_RULE self-hedge",         "Both sides open; net directional exposure = 0"),
    ("ang", "ONDOUSDT",     "ONDOUSDT_SHORT",        "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("inf", "ONDOUSDT",     "ONDOUSDT_SHORT",        "OPEN SHORT",              "May 6",   "N/A (open)",  "GOLDEN_RULE signal fired",      "Wrong side — LONG rally missed"),
    ("ang", "ARUSDT",       "ARUSDT_LONG",           "CLOSE LONG (double kill)", "May 7",  "-2.3%",      "DC_BB_D_BREAK_REVERSE + RIDICULOUS_HOLD", "Double kill — both exit gates fired"),
    ("inf", "ARUSDT",       "ARUSDT_LONG",           "CLOSE LONG (double kill)", "May 7",  "-2.3%",      "DC_BB_D_BREAK_REVERSE + RIDICULOUS_HOLD", "Double kill — both exit gates fired"),
    ("ang", "VIRTUALUSDT",  "VIRTUALUSDT_LONG",      "CLOSE LONG (twice)",      "May 7",   "+0.5% ea",   "DC_BB_D_BREAK_REVERSE ×2",      "Opened and closed twice — both exits premature"),
    ("inf", "VIRTUALUSDT",  "VIRTUALUSDT_LONG",      "CLOSE LONG (twice)",      "May 7",   "+0.5% ea",   "DC_BB_D_BREAK_REVERSE ×2",      "Opened and closed twice — both exits premature"),
    ("men", "VIRTUALUSDT",  "VIRTUALUSDT_LONG",      "CLOSE LONG (twice)",      "May 7",   "+0.5% ea",   "DC_BB_D_BREAK_REVERSE ×2",      "Opened and closed twice — both exits premature"),
    ("ang", "ZENUSDT",      "ZENUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("inf", "ZENUSDT",      "ZENUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("fin", "ZENUSDT",      "ZENUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
    ("men", "ZENUSDT",      "ZENUSDT_SHORT",         "OPEN SHORT",              "May 7",   "N/A (open)",  "GOLDEN_RULE during LONG rally",  "Wrong side — LONG rally missed"),
]

for row_idx, row_data in enumerate(pa_data, 3):
    ws4.row_dimensions[row_idx].height = 18
    for col_idx, val in enumerate(row_data, 1):
        cell = ws4.cell(row=row_idx, column=col_idx)
        cell.value = val
        cell.border = BORDER
        cell.font = BODY_FONT
        cell.alignment = LEFT

    action = row_data[3]
    gain = row_data[5]
    reason = row_data[6]

    row_fill = GRAY_FILL if row_idx % 2 == 0 else None

    if "WRONG SIDE" in action.upper() or "SHORT" in row_data[2] and "LONG" not in row_data[2] and "both" not in action.lower():
        ws4.cell(row=row_idx, column=4).fill = RED_FILL
    if "correct" in reason.lower() or "LONG (correct)" in reason:
        ws4.cell(row=row_idx, column=4).fill = GREEN_FILL
        ws4.cell(row=row_idx, column=7).fill = GREEN_FILL
    if "-" in gain and "N/A" not in gain:
        ws4.cell(row=row_idx, column=6).fill = RED_FILL
    if "self-hedge" in reason.lower():
        ws4.cell(row=row_idx, column=7).fill = ORANGE_FILL

    if row_fill:
        for col in range(1, len(pa_headers) + 1):
            existing = ws4.cell(row=row_idx, column=col).fill.fgColor.rgb
            if existing in ("00000000", "FFFFFFFF"):
                ws4.cell(row=row_idx, column=col).fill = row_fill

set_col_widths(ws4, [9, 18, 30, 30, 9, 16, 42, 45])

# Save
wb.save(OUTPUT_PATH)
print(f"Spreadsheet created: {OUTPUT_PATH}")
