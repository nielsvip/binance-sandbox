import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = openpyxl.Workbook()

# ── helpers ────────────────────────────────────────────────────────────────
def make_fill(hex_color):
    return PatternFill(start_color=hex_color, end_color=hex_color, fill_type="solid")

def thin_border():
    s = Side(style="thin")
    return Border(left=s, right=s, top=s, bottom=s)

def header_style(ws, row, cols, bg="1F3864", fg="FFFFFF"):
    fill = make_fill(bg)
    font = Font(bold=True, color=fg, size=11)
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border()

def data_style(ws, row, cols, bg=None, bold=False, wrap=True):
    fill = make_fill(bg) if bg else None
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        if fill:
            cell.fill = fill
        cell.font = Font(bold=bold, size=10)
        cell.alignment = Alignment(vertical="center", wrap_text=wrap)
        cell.border = thin_border()

def set_col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

def freeze(ws, cell="A2"):
    ws.freeze_panes = cell

# ══════════════════════════════════════════════════════════════════════════
# SHEET 1 — Surge Overview
# ══════════════════════════════════════════════════════════════════════════
ws1 = wb.active
ws1.title = "Surge Overview"
ws1.row_dimensions[1].height = 36

headers1 = ["Symbol", "7-Day Gain %", "In Tradeable Keys",
            "Accounts Missing It", "Root Cause (short)", "Fix Priority"]
for col, h in enumerate(headers1, 1):
    ws1.cell(row=1, column=col, value=h)
header_style(ws1, 1, len(headers1))
freeze(ws1)

data1 = [
    ("BRUSDT",        "+119.3%", "None",              "ang, inf, fin, flz, men",
     "Not in tradeable_keys / symbols.json",                    "HIGH: Add to symbols"),
    ("TONUSDT",       "+82.3%",  "ang, inf, fin, men","flz",
     "In TK but check decision logs",                           "MEDIUM: Investigate"),
    ("ARIAUSDT",      "+78.9%",  "None",              "All",
     "Not in tradeable_keys",                                   "HIGH: Add to symbols"),
    ("ONUSDT",        "+74.2%",  "None",              "All",
     "Not in tradeable_keys",                                   "MEDIUM"),
    ("ZECUSDC",       "+61.5%",  "ang, fin, flz, inf","men",
     "7 root causes — evaluate_reentry_2 bug, stale price, GR bypass, DC_BB churn, parabolic threshold, BB state loss, NOCROSS wrong-dir",
     "CRITICAL: Code bugs (see Sheet 2)"),
    ("NOTUSDT",       "+58.2%",  "None",              "All",
     "Not in tradeable_keys",                                   "LOW"),
    ("STOUSDT",       "+57.4%",  "None",              "All",
     "Not in tradeable_keys",                                   "LOW"),
    ("ONTUSDT",       "+48.0%",  "None",              "All",
     "Not in tradeable_keys",                                   "LOW"),
    ("TRADOORUSDT",   "+45.6%",  "None",              "All",
     "Not in tradeable_keys",                                   "LOW"),
    ("DASHUSDT",      "+44.5%",  "ang, inf, fin, men","flz",
     "In TK — check if rode the surge",                        "MEDIUM: Investigate"),
    ("CHILLGUYUSDT",  "+40.2%",  "ang, inf",          "fin, flz, men",
     "In TK — check if rode it",                               "MEDIUM: Investigate"),
]

priority_colors = {
    "CRITICAL": "FF0000",
    "HIGH":     "FF6600",
    "MEDIUM":   "FFD700",
    "LOW":      "90EE90",
}

for r, row_data in enumerate(data1, 2):
    for col, val in enumerate(row_data, 1):
        ws1.cell(row=r, column=col, value=val)
    priority = row_data[5]
    bg = None
    for key, color in priority_colors.items():
        if key in priority.upper():
            bg = color
            break
    # lighter tints
    tint_map = {"FF0000": "FFCCCC", "FF6600": "FFE5CC", "FFD700": "FFFACC", "90EE90": "D9F7D9"}
    bg_tint = tint_map.get(bg, None)
    data_style(ws1, r, len(headers1), bg=bg_tint)
    ws1.row_dimensions[r].height = 40

set_col_widths(ws1, [18, 14, 22, 22, 58, 26])
ws1.sheet_view.showGridLines = False

# ══════════════════════════════════════════════════════════════════════════
# SHEET 2 — ZECUSDC Root Cause
# ══════════════════════════════════════════════════════════════════════════
ws2 = wb.create_sheet("ZECUSDC Root Cause")

# ── title banner ──────────────────────────────────────────────────────────
ws2.merge_cells("A1:F1")
title_cell = ws2["A1"]
title_cell.value = "ZECUSDC Deep-Dive — Per-Account Root Cause Analysis (May 1–7 2026)"
title_cell.font = Font(bold=True, size=13, color="FFFFFF")
title_cell.fill = make_fill("1F3864")
title_cell.alignment = Alignment(horizontal="center", vertical="center")
ws2.row_dimensions[1].height = 28

# ── per-account table ─────────────────────────────────────────────────────
headers2 = ["Account", "LONG in TK", "SHORT in TK", "ZEC Activity May 1-7", "Root Cause", "Fix"]
for col, h in enumerate(headers2, 1):
    ws2.cell(row=2, column=col, value=h)
header_style(ws2, 2, len(headers2))
ws2.row_dimensions[2].height = 32
freeze(ws2, "A3")

account_data = [
    ("ang", "YES", "NO",
     "0 entries May 6-7 despite +61% surge",
     "(1) evaluate_reentry_2 `return` bug at L19298 — kills ALL LONG reentry eval "
     "when any reduced_position is not tradeable.\n"
     "(2) NOCROSS_FULLSTACK opened WRONG-DIRECTION SHORT at 359 (actual ZEC price was 500+) "
     "based on stale reentry record from old hedge.",
     "Fix L19298: `return` → `continue`.\nAdd direction guard to NOCROSS_FULLSTACK."),

    ("inf", "YES", "NO",
     "Opened LONG May 3 — then ZERO activity for 4 days",
     "Same evaluate_reentry_2 `return` bug: any SHORT present in reduced_positions "
     "short-circuits ALL LONG reentry evaluation for every subsequent symbol in the loop.",
     "Fix L19298: `return` → `continue`."),

    ("fin", "YES", "NO (via GR bypass)",
     "SHORTs opened via GOLDEN_RULE at stale price 359 while ZEC was trading 520–593",
     "GOLDEN_RULE fired on stale mark_price (359 repeated 38× while actual was 520–593).\n"
     "DC_BB_D_BREAK_REVERSE churn loop: GR opens SHORT → DC_BB detects BREAK_UP → closes SHORT + opens tiny LONG "
     "→ CROSSBACK triggers → closes LONG + opens SHORT → repeat.",
     "Add mark_price staleness check to GOLDEN_RULE (skip if age > 30s).\nPersist BB_BREAKOUT_CONT state to JSON."),

    ("flz", "YES", "YES (via GR INTERVENTION bypass)",
     "117 decisions (96 closes) — net loss on ZEC",
     "GOLDEN_RULE INTERVENTION auto-added ZECUSDC_SHORT key (not in hand-picked TK).\n"
     "DC_BB churn loop identical to fin.\n"
     "PARABOLIC_PROTECTION never fired — threshold bb_pct_b >= 0.90 was never reached "
     "(actual peak ~0.85). Threshold lowered to 0.70 today.",
     "Remove INTERVENTION auto-add for SHORT keys outside hand-picked TK.\n"
     "Persist BB_BREAKOUT_CONT state.\nPARABOLIC threshold already lowered to 0.70."),

    ("men", "NO", "NO",
     "ZERO — ZEC not in symbols_men.json",
     "Symbol simply not included in men account.",
     "Add ZECUSDC to men tradeable_keys / symbols_men.json if desired."),
]

row_bg = ["FFF0F0", "FFF0F0", "FFF5EE", "FFF5EE", "F5F5F5"]
for r_offset, (acct_data, bg) in enumerate(zip(account_data, row_bg)):
    r = r_offset + 3
    for col, val in enumerate(acct_data, 1):
        ws2.cell(row=r, column=col, value=val)
    data_style(ws2, r, len(headers2), bg=bg)
    ws2.row_dimensions[r].height = 80

# ── ZEC price timeline ────────────────────────────────────────────────────
timeline_row = len(account_data) + 3 + 1

ws2.merge_cells(f"A{timeline_row}:F{timeline_row}")
hdr = ws2.cell(row=timeline_row, column=1, value="ZEC Price Timeline — Key Levels")
hdr.font = Font(bold=True, size=11, color="FFFFFF")
hdr.fill = make_fill("2E4057")
hdr.alignment = Alignment(horizontal="center", vertical="center")
ws2.row_dimensions[timeline_row].height = 24

tl_headers = ["Date / Time (UTC)", "ZEC Price", "Event", "", "", ""]
tl_row = timeline_row + 1
for col, val in enumerate(tl_headers[:3], 1):
    ws2.cell(row=tl_row, column=col, value=val)
header_style(ws2, tl_row, 3, bg="2E4057")
ws2.row_dimensions[tl_row].height = 24

timeline_data = [
    ("May 1 2026",       "~$350",  "ZEC base price — surge not yet started"),
    ("May 5 09:00 UTC",  "359",    "NOCROSS_FULLSTACK opens SHORT on ang (stale price from old hedge record)"),
    ("May 5 20:00 UTC",  "447",    "Surge begins — +28% from base"),
    ("May 6 04:00 UTC",  "593",    "PEAK — +61.5% from base. GOLDEN_RULE still quoting 359 (stale). flz churn loop active."),
    ("May 7 (current)",  "~$540",  "ZEC settling. All bugs still in production code."),
]

for r_offset, tl in enumerate(timeline_data):
    r = tl_row + 1 + r_offset
    for col, val in enumerate(tl, 1):
        ws2.cell(row=r, column=col, value=val)
    bg = "FFEEEE" if "PEAK" in tl[2] else "F9F9F9"
    data_style(ws2, r, 3, bg=bg)
    ws2.row_dimensions[r].height = 22

set_col_widths(ws2, [22, 16, 24, 38, 60, 52])
ws2.sheet_view.showGridLines = False

# ══════════════════════════════════════════════════════════════════════════
# SHEET 3 — Action Items
# ══════════════════════════════════════════════════════════════════════════
ws3 = wb.create_sheet("Action Items")

ws3.merge_cells("A1:E1")
t = ws3["A1"]
t.value = "Surge Miss Action Items — Prioritized Fix List"
t.font = Font(bold=True, size=13, color="FFFFFF")
t.fill = make_fill("1F3864")
t.alignment = Alignment(horizontal="center", vertical="center")
ws3.row_dimensions[1].height = 28

headers3 = ["Priority", "Action", "File", "Line / Config", "Status"]
for col, h in enumerate(headers3, 1):
    ws3.cell(row=2, column=col, value=h)
header_style(ws3, 2, len(headers3))
ws3.row_dimensions[2].height = 32
freeze(ws3, "A3")

P0_BG  = "FFCCCC"
P1_BG  = "FFE5CC"
P2_BG  = "FFFACC"
P3_BG  = "D9F7D9"

action_items = [
    ("P0 CRITICAL", P0_BG,
     "Fix `return` → `continue` bug at line 19298 in evaluate_reentry_2. "
     "Current `return` aborts ALL LONG reentry evaluation for every remaining symbol "
     "the moment a single reduced_position is not tradeable.",
     "ez_manage.py", "Line 19298", "TO DO"),

    ("P0 CRITICAL", P0_BG,
     "Mandatory BB-breakout reentry: after ANY position close during a 4h BB breakout state, "
     "immediately queue MANDATORY_REENTRY valid for 72h. "
     "Prevents system from locking out of a surge it detected but then closed out of.",
     "ez_manage.py", "DC_BB_D_BREAK_REVERSE ~L20804", "TO DO"),

    ("P0 CRITICAL", P0_BG,
     "Persist BB_BREAKOUT_CONT state to disk (JSON). Current in-memory dict is wiped on every "
     "worker restart → 72h breakout window lost. ZEC surge window was active across at least 2 restarts.",
     "ez_manage.py", "_dc_bb_d_break_state dict", "TO DO"),

    ("P1 HIGH", P1_BG,
     "Add mark_price staleness check to GOLDEN_RULE: skip intervention / short-open "
     "if mark_price age > 30 seconds. ZEC was quoted at 359 while actual exchange price was 520-593.",
     "ez_manage.py", "~L6940 GOLDEN_RULE loop", "TO DO"),

    ("P1 HIGH", P1_BG,
     "Block GOLDEN_RULE INTERVENTION from auto-adding SHORT keys that are NOT in the hand-picked "
     "tradeable_keys list. Intervention bypass currently creates ZECUSDC_SHORT on flz "
     "even though flz never opted in.",
     "ez_manage.py", "~L13667 _intervention_bypass", "TO DO"),

    ("P1 HIGH", P1_BG,
     "Add NOCROSS_FULLSTACK direction guard: do not open a SHORT if the symbol is currently in a "
     "surge / BB-breakout state. The stale reentry record from the old hedge caused a wrong-direction "
     "SHORT on ang at 359 while ZEC was already at 500+.",
     "ez_manage.py", "~L16300 NOCROSS_FULLSTACK", "TO DO"),

    ("P2 MEDIUM", P2_BG,
     "Add BRUSDT and ARIAUSDT to symbols.json after investigating liquidity, funding history, "
     "and sweep fit. Both were +78–119% and not in any account.",
     "symbols.json", "tradeable_keys per account", "INVESTIGATE"),

    ("P2 MEDIUM", P2_BG,
     "Check decision JSONL for TONUSDT and DASHUSDT (both in TK for ang/inf/fin/men) "
     "to confirm whether the system entered and rode the surge or missed it entirely.",
     "ez_manage.py", "data/decisions/ JSONL May 1-7", "INVESTIGATE"),

    ("P2 MEDIUM", P2_BG,
     "Evaluate adding ZECUSDC to men account tradeable_keys. ZEC had +61.5% move — "
     "men currently has zero exposure.",
     "symbols.json / symbols_men.json", "men tradeable_keys", "INVESTIGATE"),

    ("P3 LOW", P3_BG,
     "Backport evaluate_reentry_2 fix to ez_positions_quick.py if that file has a parallel "
     "implementation of the same loop pattern.",
     "ez_positions_quick.py", "evaluate_reentry_2 equivalent", "TO DO"),

    ("P3 LOW", P3_BG,
     "Review and add high-momentum symbols (ONUSDT, NOTUSDT, STOUSDT, ONTUSDT, TRADOORUSDT) "
     "after full sweep backtest confirms fit. Do NOT add without sweep proof.",
     "symbols.json", "tradeable_keys", "BACKTEST FIRST"),
]

for r_offset, item in enumerate(action_items):
    r = r_offset + 3
    priority, bg, action, file_, line, status = item
    row_vals = [priority, action, file_, line, status]
    for col, val in enumerate(row_vals, 1):
        ws3.cell(row=r, column=col, value=val)
    data_style(ws3, r, len(headers3), bg=bg)
    ws3.row_dimensions[r].height = 56

set_col_widths(ws3, [16, 72, 24, 28, 18])
ws3.sheet_view.showGridLines = False

# ══════════════════════════════════════════════════════════════════════════
# Save
# ══════════════════════════════════════════════════════════════════════════
out_path = "/Users/niels/Documents/binance/data/surge_analysis_2026-05-07.xlsx"
wb.save(out_path)
print(f"Saved: {out_path}")
print(f"Sheets: {wb.sheetnames}")
print(f"Sheet1 rows: {ws1.max_row}  Sheet2 rows: {ws2.max_row}  Sheet3 rows: {ws3.max_row}")
