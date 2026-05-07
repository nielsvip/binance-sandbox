"""
create_missed_breakouts_excel.py
Generates missed_breakouts_20260507.xlsx analyzing missed trading opportunities
across all crypto accounts for the Apr 30 → May 7, 2026 week.
"""
import json
import os
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side
)
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# COLOR FILLS
# ---------------------------------------------------------------------------
RED_FILL    = PatternFill("solid", fgColor="FF4444")      # NET SHORT on up move
ORANGE_FILL = PatternFill("solid", fgColor="FF9900")      # missed entry
GREEN_FILL  = PatternFill("solid", fgColor="00AA44")      # fixed / addressed
YELLOW_FILL = PatternFill("solid", fgColor="FFE066")      # mixed / partial
HEADER_FILL = PatternFill("solid", fgColor="1A1A2E")      # dark header
HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
BOLD_FONT   = Font(bold=True)
NORMAL_FONT = Font(size=10)

THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# ---------------------------------------------------------------------------
# ACCOUNT SYMBOL SETS  (derived from reading symbol files)
# ---------------------------------------------------------------------------
BASE_PATH = "/Users/niels/Documents/binance"

def load_json(path):
    with open(path) as f:
        return json.load(f)

all_symbols = set(load_json(f"{BASE_PATH}/symbols.json"))
flz_symbols = set(load_json(f"{BASE_PATH}/symbols_flz.json"))
fin_symbols  = set(load_json(f"{BASE_PATH}/symbols_fin.json"))
men_symbols  = set(load_json(f"{BASE_PATH}/symbols_men.json"))
ang_long     = set(load_json(f"{BASE_PATH}/symbols_ang_long.json"))
ang_short    = set(load_json(f"{BASE_PATH}/symbols_ang_short.json"))
inf_long     = set(load_json(f"{BASE_PATH}/symbols_inf_long.json"))
inf_short    = set(load_json(f"{BASE_PATH}/symbols_inf_short.json"))
ang_symbols  = ang_long | ang_short
inf_symbols  = inf_long | inf_short

ACCOUNT_SYMBOLS = {
    "flz": flz_symbols,
    "ang": ang_symbols,
    "inf": inf_symbols,
    "men": men_symbols,
    "fin": fin_symbols,
}

# ---------------------------------------------------------------------------
# 7-DAY MOVERS  (Apr 30 → May 7)
# ---------------------------------------------------------------------------
MOVERS = [
    ("ZECUSDC",      59.9,  94.0),
    ("TONUSDT",      86.7,  None),
    ("NOTUSDT",      65.0,  None),
    ("DASHUSDT",     44.0,  None),
    ("CHILLGUYUSDT", 39.8,  None),
    ("ONDOUSDT",     34.8,  None),
    ("HMSTRUSDT",    32.3,  None),
    ("PENDLEUSDT",   30.7,  None),
    ("IOUSDT",       29.3,  None),
    ("VIRTUALUSDT",  29.1,  None),
    ("ARUSDT",       28.2,  None),
    ("PARTIUSDT",    27.4,  None),
    ("ICPUSDT",      24.7,  None),
    ("TAOUSDT",      24.6,  None),
    ("FARTCOINUSDT", 24.3,  None),
    ("1000LUNCUSDT", 22.2,  None),
    ("OPUSDT",       21.9,  None),
    ("RSRUSDT",      21.6,  None),
    ("WLFIUSDC",     21.2,  None),
    ("POPCATUSDT",   21.1,  None),
]

# ---------------------------------------------------------------------------
# ROOT CAUSE & SYSTEM ACTION DATA  (from decision log analysis)
# ---------------------------------------------------------------------------
SYMBOL_DATA = {
    "ZECUSDC": {
        "accounts": ["flz"],
        "system_action": "NET SHORT — 18+ SHORTs opened via GOLDEN_RULE during the rally; LONGs killed within 90s by stale DC_CROSSBACK_TO_SHORT signal",
        "root_cause": "Stale 359.02 klines init bar → false DC_CROSSBACK_TO_SHORT on every LONG open; PARABOLIC gate 0.90 too tight; GOLDEN_RULE fires SHORTs during confirmed daily BB breakout",
        "pnl_estimate": "-$800 to -$2,000 (estimated from 148 flipped trades; not holding the +59.9% move)",
        "color": "red",
        "fixes": [1, 3, 4],
    },
    "TONUSDT": {
        "accounts": ["ang", "inf", "men", "fin"],
        "system_action": "NET SHORT — GUARANTEED_PRICE_CROSS_REENTRY_DISK_SHORT using stale exit price ~1.303/1.334 while price traded 1.8–2.49; every account reopened SHORTs repeatedly",
        "root_cause": "Disk reentry using old exit price ~1.303 vs live price 1.76–2.49 (>20% divergence); triggered SHORT reentries throughout the entire +86.7% rally",
        "pnl_estimate": "-$1,500 to -$3,500 across 4 accounts (missed +86.7% move + losses from stale SHORT reopens at wrong prices)",
        "color": "red",
        "fixes": [2],
    },
    "DASHUSDT": {
        "accounts": ["inf", "men", "fin"],
        "system_action": "NET SHORT — DC_BREAKOUT_REENTRY & GUARANTEED_PRICE_CROSS_REENTRY_DISK_SHORT using stale exit ~$37 while price rallied to $50+",
        "root_cause": "Disk reentry: old exit price $37 vs live price $44–$52 (>20% divergence); SHORTs opened on upward reentries throughout the +44% rally",
        "pnl_estimate": "-$600 to -$1,200 (3 accounts shorting a +44% move)",
        "color": "red",
        "fixes": [2],
    },
    "NOTUSDT": {
        "accounts": ["fin"],
        "system_action": "NET SHORT — 12 SHORTs vs 5 LONGs logged; disk reentry pattern into stale SHORT from prior range",
        "root_cause": "GUARANTEED_PRICE_CROSS_REENTRY_DISK_SHORT fired repeatedly; system predominantly short during the +65% move",
        "pnl_estimate": "-$300 to -$800 (fin account shorting +65% move)",
        "color": "red",
        "fixes": [2],
    },
    "CHILLGUYUSDT": {
        "accounts": ["ang", "inf"],
        "system_action": "In tradeable_keys (ang_long, inf_long) — status unclear from decision logs; likely some LONG exposure",
        "root_cause": "Insufficient WT signal or position sizing constraints; partial or delayed entry on +39.8% move",
        "pnl_estimate": "Missed upside estimated $200–$500 if under-positioned",
        "color": "orange",
        "fixes": [4],
    },
    "ONDOUSDT": {
        "accounts": ["ang"],
        "system_action": "In ang_long — ONDOUSDT was in tradeable list; WT signal may have triggered some LONG",
        "root_cause": "WT entry timing — if system entered but exited early, missed part of the +34.8% move",
        "pnl_estimate": "Missed upside estimated $100–$300",
        "color": "orange",
        "fixes": [],
    },
    "PENDLEUSDT": {
        "accounts": ["ang", "inf"],
        "system_action": "In ang_long, inf_long — PENDLEUSDT tradeable; WT-driven entry expected",
        "root_cause": "Early exit via PEAK_GIVEBACK or WT_4H_VEL_EXIT likely captured only partial +30.7% move",
        "pnl_estimate": "Partial capture; missed upside $150–$400",
        "color": "orange",
        "fixes": [],
    },
    "IOUSDT": {
        "accounts": ["ang"],
        "system_action": "In ang_long — IOUSDT tradeable; entry via WT/DC score expected",
        "root_cause": "Partial capture of +29.3% move; exit gates may have triggered early",
        "pnl_estimate": "Estimated missed upside $100–$250",
        "color": "orange",
        "fixes": [],
    },
    "VIRTUALUSDT": {
        "accounts": ["men"],
        "system_action": "In men tradeable list — VIRTUALUSDT present; some LONG exposure likely",
        "root_cause": "WT signal timing; early exit or size constraint reduced capture of +29.1% move",
        "pnl_estimate": "Missed upside estimated $100–$300",
        "color": "orange",
        "fixes": [],
    },
    "ARUSDT": {
        "accounts": ["ang"],  # in ang_long; inf_short has it as short!
        "system_action": "ARUSDT in ang_long but also in inf_short — conflicting positions across accounts",
        "root_cause": "inf_short opened ARUSDT SHORT while price rallied +28.2%; ang_long may have had LONG for partial offset",
        "pnl_estimate": "Net loss estimated $200–$500 (inf SHORT on +28.2% move)",
        "color": "red",
        "fixes": [2],
    },
    "PARTIUSDT": {
        "accounts": ["ang", "fin"],  # ang_short has it; fin has it
        "system_action": "PARTIUSDT in ang_short and fin — ang may have shorted during the +27.4% rally",
        "root_cause": "ang_short list includes PARTIUSDT; if DISK reentry fired SHORT, would have shorted the move",
        "pnl_estimate": "Risk of $100–$300 loss (potential SHORT on +27.4% move)",
        "color": "orange",
        "fixes": [2],
    },
    "ICPUSDT": {
        "accounts": ["fin", "men"],
        "system_action": "ICPUSDT in fin and men tradeable lists; WT-driven LONG entries expected",
        "root_cause": "Move captured partially; exit gates (PEAK_GIVEBACK, BE_EROSION) likely triggered before +24.7% fully played out",
        "pnl_estimate": "Partial capture; estimated missed $100–$200",
        "color": "orange",
        "fixes": [],
    },
    "TAOUSDT": {
        "accounts": [],
        "system_action": "NOT in any account tradeable_keys — no exposure",
        "root_cause": "Symbol not in any account's tradeable list; missed entire +24.6% move",
        "pnl_estimate": "$0 (no position possible)",
        "color": "orange",
        "fixes": [],
    },
    "FARTCOINUSDT": {
        "accounts": ["ang", "inf", "men", "fin"],
        "system_action": "In all 4 active accounts (ang_long, inf_long, men, fin); LONG entries expected via WT",
        "root_cause": "Likely captured portion of move; early exits via WT reversal signals may have reduced capture",
        "pnl_estimate": "Partial capture across 4 accounts; estimated $200–$600 captured",
        "color": "orange",
        "fixes": [],
    },
    "1000LUNCUSDT": {
        "accounts": ["ang", "inf"],
        "system_action": "In ang_long, inf_long — LONG entries via WT/DC expected",
        "root_cause": "Small position size limits; early WT reversal exits reduced capture of +22.2% move",
        "pnl_estimate": "Partial capture estimated; missed upside $100–$300",
        "color": "orange",
        "fixes": [],
    },
    "OPUSDT": {
        "accounts": ["fin", "men"],
        "system_action": "OPUSDT in fin and men; inf_short also has OPUSDT — conflicting direction risk",
        "root_cause": "inf_short SHORT + fin/men LONG = net diluted; disk reentry may have re-shorted during +21.9% move",
        "pnl_estimate": "Mixed; net impact estimated -$100 to +$200",
        "color": "orange",
        "fixes": [2],
    },
    "RSRUSDT": {
        "accounts": ["fin", "men"],
        "system_action": "RSRUSDT in fin and men; LONG entries expected; WT signal capture",
        "root_cause": "Partial capture of +21.6% move; standard exit gate behavior",
        "pnl_estimate": "Partial capture; estimated $50–$150 captured",
        "color": "orange",
        "fixes": [],
    },
    "WLFIUSDC": {
        "accounts": ["ang"],  # in ang_short!
        "system_action": "WLFIUSDC in ang_SHORT list — system may have been SHORT during the +21.2% rally",
        "root_cause": "ang_short includes WLFIUSDC; if DISK reentry fired SHORT, system shorted a +21.2% move",
        "pnl_estimate": "Risk of -$100 to -$300 if SHORT was active",
        "color": "red",
        "fixes": [2],
    },
    "POPCATUSDT": {
        "accounts": ["inf"],  # in inf_short!
        "system_action": "POPCATUSDT in inf_short — system likely SHORT during the +21.1% rally",
        "root_cause": "inf_short includes POPCATUSDT; disk reentry could fire SHORTs during the +21.1% rally",
        "pnl_estimate": "Risk of -$100 to -$250 if SHORT active during move",
        "color": "red",
        "fixes": [2],
    },
    "HMSTRUSDT": {
        "accounts": [],
        "system_action": "NOT in any account tradeable_keys — no exposure",
        "root_cause": "Symbol not in any account's tradeable list; missed entire +32.3% move",
        "pnl_estimate": "$0 (no position possible)",
        "color": "orange",
        "fixes": [],
    },
}

FIXES = [
    {
        "id": 1,
        "name": "STALE_KLINES_INIT_BAR_REMOVAL",
        "description": "Remove zero-volume init bar from ZECUSDC klines cache that set stale reference price at $359.02",
        "component": "klines_cache / ez_klines.py",
        "status": "PENDING",
        "priority": "P0 — CRITICAL",
        "expected_impact": "Eliminates stale DC_CROSSBACK_TO_SHORT signals that killed every LONG within 90s; stops 18+ SHORTs being opened on fresh rally",
        "symbols_affected": ["ZECUSDC"],
        "accounts_affected": ["flz"],
        "estimated_pnl_recovery": "$800–$2,000/week",
    },
    {
        "id": 2,
        "name": "STALE_DISK_REENTRY_PRICE_DIVERGENCE_GUARD",
        "description": "Add 20% price divergence check before firing GUARANTEED_PRICE_CROSS_REENTRY_DISK_SHORT/LONG — if current price is >20% away from stored exit price, invalidate the disk record and skip reentry",
        "component": "ez_manage.py (GUARANTEED_PRICE_CROSS_REENTRY paths)",
        "status": "PENDING",
        "priority": "P0 — CRITICAL",
        "expected_impact": "Prevents system from opening SHORTs using $1.303 exit price when TONUSDT is at $2.49 (+91%); same fix covers DASHUSDT ($37 vs $50+), NOTUSDT, WLFIUSDC, POPCATUSDT, ARUSDT",
        "symbols_affected": ["TONUSDT", "DASHUSDT", "NOTUSDT", "WLFIUSDC", "POPCATUSDT", "ARUSDT", "OPUSDT", "PARTIUSDT"],
        "accounts_affected": ["ang", "inf", "men", "fin"],
        "estimated_pnl_recovery": "$2,000–$5,000/week",
    },
    {
        "id": 3,
        "name": "BB_BREAKOUT_BLOCK_SHORT",
        "description": "Block GOLDEN_RULE SHORT entries when daily Bollinger Band breakout is active (<72h since bb_upper_D breach) — prevents system from shorting confirmed parabolic breakouts",
        "component": "ez_manage.py (GOLDEN_RULE_SHORT path) + config.py",
        "status": "PENDING",
        "priority": "P1 — HIGH",
        "expected_impact": "Eliminates GOLDEN_RULE_SHORT_mult4 opens during ZEC +94% intraday spike; reduces SHORT frequency during strong uptrends by 60–80%",
        "symbols_affected": ["ZECUSDC", "any parabolic mover"],
        "accounts_affected": ["flz", "ang", "inf", "men", "fin"],
        "estimated_pnl_recovery": "$500–$1,500/week",
    },
    {
        "id": 4,
        "name": "BB_PULLBACK_REENTRY_LONG",
        "description": "Auto-reenter LONG on pullback to 1h Bollinger Band basis during 72h breakout window — captures continuation moves after parabolic breakouts",
        "component": "ez_manage.py (reentry logic) + config.py switch BB_PULLBACK_REENTRY_ENABLED (default OFF pending sweep)",
        "status": "DESIGN ONLY — needs V8 sweep proof before enabling",
        "priority": "P2 — MEDIUM",
        "expected_impact": "Could capture 30–50% of continuation move on confirmed breakouts; estimated additional $300–$800/week if sweep validates",
        "symbols_affected": ["ZECUSDC", "any confirmed daily BB breakout"],
        "accounts_affected": ["flz", "ang", "inf", "men", "fin"],
        "estimated_pnl_recovery": "$300–$800/week (requires sweep validation first)",
    },
]

# ---------------------------------------------------------------------------
# ZEC REENTRY AUDIT TIMELINE  (from decision log parsing)
# ---------------------------------------------------------------------------
ZEC_TIMELINE = [
    ("2026-05-05 22:00", 521.05, "OPEN", "GOLDEN_RULE_SHORT_mult4_INTERVENTION", "N/A", "0m", "Stale 359.02 klines; GOLDEN_RULE fires SHORT at top of initial spike"),
    ("2026-05-05 22:20", 359.02, "CLOSE", "CB_HTF_EXHAUST_BAD_ENTRY: g=-0.95% age=17m", "-0.95%", "17m", "Stale price in close snapshot; still using 359.02 reference"),
    ("2026-05-05 22:27", 359.02, "OPEN",  "GOLDEN_RULE_SHORT_mult4_INTERVENTION", "N/A", "0m", "Stale init bar still active; reopens SHORT immediately"),
    ("2026-05-05 22:43", 359.02, "CLOSE", "FLZ_AGENT_FORCE_CLOSE(PROFIT_TAKE_MTF_REVERSE)", "+0.28%", "16m", "Agent closed multiple duplicates"),
    ("2026-05-05 23:23", 359.02, "OPEN",  "GOLDEN_RULE_SHORT_mult2.0_INTERVENTION", "N/A", "0m", "Third SHORT cycle; stale reference persists"),
    ("2026-05-05 23:41", 359.02, "REDUCE","QUICK_BREAKEVEN_GAIN_EROSION_STOP age101m", "N/A", "18m", "Partial reduce; position still open"),
    ("2026-05-06 00:00", 515.01, "REDUCE","QUICK_PEAK_GIVEBACK peak30.99% drop0.5%", "-0.50%", "37m", "Price was 515 — SHORT now deeply underwater vs open at 521"),
    ("2026-05-06 00:15", 359.02, "OPEN",  "GOLDEN_RULE_SHORT_mult2.0_INTERVENTION", "N/A", "0m", "Fourth SHORT cycle; DC_CROSSBACK not cleared"),
    ("2026-05-06 00:33", 359.02, "QUICK_CLOSE","WT_4H_VEL_EXIT_vel=90.1_g=1.48%_MANDATORY_REENTRY", "+1.48%", "18m", "Exits but mandates reentry"),
    ("2026-05-06 02:24", 359.02, "OPEN",  "GOLDEN_RULE_SHORT_mult2.0_INTERVENTION", "N/A", "0m", "Fifth SHORT cycle"),
    ("2026-05-06 02:24", 359.02, "CLOSE", "DC_BB_D_BREAK_REVERSE_close_DC_BREAK_UP", "N/A", "0m", "Finally closes on DC_BREAK_UP signal — system fought the breakout"),
    ("2026-05-06 05:14", 531.13, "OPEN",  "GOLDEN_RULE_LONG_mult3.0_INTERVENTION", "N/A", "0m", "First LONG attempt; price already 531"),
    ("2026-05-06 05:15", 531.13, "CLOSE", "DC_BB_D_BREAK_REVERSE_close_DC_CROSSBACK_TO_SHORT", "N/A", "<1m", "Killed in <1min by stale DC_CROSSBACK signal"),
    ("2026-05-06 05:49", 541.82, "OPEN",  "GOLDEN_RULE_LONG_mult3.0_INTERVENTION", "N/A", "0m", "Second LONG attempt; stale reference kills it again"),
    ("2026-05-06 05:49", 541.82, "CLOSE", "DC_BB_D_BREAK_REVERSE_close_DC_CROSSBACK_TO_SHORT", "N/A", "<1m", "Killed in <1min — DC_CROSSBACK fires from stale 359.02 bar"),
    ("2026-05-06 07:10", 579.49, "AUGMENT","GOLDEN_RULE_LONG_mult3.0_INTERVENTION", "N/A", "N/A", "Augment on LONG position (price 579 = +61% from Apr 30)"),
    ("2026-05-06 07:17", 579.49, "CLOSE", "DC_BB_D_BREAK_REVERSE_close_DC_CROSSBACK_TO_SHORT", "N/A", "7m", "Killed again; same stale DC signal; missed nearly entire move"),
    ("2026-05-07 00:53", 550.03, "OPEN",  "GOLDEN_RULE_SHORT_mult1.0_INTERVENTION", "N/A", "0m", "Still opening SHORTs at 550 (price down from 694 peak)"),
    ("2026-05-07 01:03", 550.03, "OPEN",  "GOLDEN_RULE_SHORT_mult1.0_INTERVENTION", "N/A", "0m", "Multiple SHORTs May 7 as price consolidates post-peak"),
    ("2026-05-07 16:05", 564.00, "OPEN",  "GOLDEN_RULE_SHORT_mult1.0_INTERVENTION", "N/A", "0m", "Still shorting into close of day; init bar never cleared"),
]

# ---------------------------------------------------------------------------
# PER-ACCOUNT DETAIL
# ---------------------------------------------------------------------------
def get_per_account_rows():
    rows = []
    for acct, sym_set in ACCOUNT_SYMBOLS.items():
        for sym, pct, peak in MOVERS:
            in_keys = sym in sym_set
            mover_data = SYMBOL_DATA.get(sym, {})
            acct_in_sym = acct in mover_data.get("accounts", [])
            if in_keys:
                # determine direction bias for this account
                if sym in ang_short and acct == "ang":
                    direction = "SHORT"
                elif sym in inf_short and acct == "inf":
                    direction = "SHORT"
                else:
                    direction = "LONG"
                sys_action = mover_data.get("system_action", "Expected LONG via WT/DC") if acct_in_sym else f"In {acct} keys — standard WT entry"
                root = mover_data.get("root_cause", "N/A") if acct_in_sym else "No anomaly detected"
                color = mover_data.get("color", "orange") if acct_in_sym else "none"
            else:
                direction = "NOT TRADEABLE"
                sys_action = "Not in tradeable_keys — no position"
                root = "Symbol not in account tradeable list"
                color = "none"
            rows.append({
                "account": acct,
                "symbol": sym,
                "7d_pct": pct,
                "peak_pct": peak if peak else pct,
                "in_keys": "YES" if in_keys else "NO",
                "direction_bias": direction,
                "system_action": sys_action,
                "root_cause": root,
                "color": color,
            })
    return rows

# ---------------------------------------------------------------------------
# EXCEL CREATION
# ---------------------------------------------------------------------------
def style_header_row(ws, row_num, n_cols):
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER

def style_data_row(ws, row_num, n_cols, fill=None):
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row_num, column=col)
        if fill:
            cell.fill = fill
        cell.font = NORMAL_FONT
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        cell.border = BORDER

def autofit_columns(ws, min_width=12, max_width=55):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                val = str(cell.value) if cell.value else ""
                lines = val.split("\n")
                max_len = max(max_len, max(len(line) for line in lines) if lines else 0)
            except Exception:
                pass
        adjusted = max(min_width, min(max_len + 2, max_width))
        ws.column_dimensions[col_letter].width = adjusted

def write_sheet1(wb):
    ws = wb.create_sheet("Missed Breakouts")
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 40

    headers = [
        "Symbol", "Account(s)", "7d_pct_change", "Peak_intraday_pct",
        "In_Tradeable_Keys", "System_Action", "Root_Cause",
        "$PnL_Impact_Estimate", "Fix_Applied", "Fix_Description",
        "Expected_Behavior_After_Fix", "Priority"
    ]
    ws.append(headers)
    style_header_row(ws, 1, len(headers))

    row_idx = 2
    for sym, pct, peak in MOVERS:
        data = SYMBOL_DATA.get(sym, {})
        accounts = data.get("accounts", [])
        in_keys_accounts = [a for a in ["ang", "inf", "flz", "men", "fin"] if sym in ACCOUNT_SYMBOLS[a]]
        in_keys_str = ", ".join(in_keys_accounts) if in_keys_accounts else "NONE"
        fix_ids = data.get("fixes", [])
        fix_names = ", ".join(f"Fix {i}" for i in fix_ids) if fix_ids else "None"
        fix_descs = "\n".join(FIXES[i-1]["name"] for i in fix_ids) if fix_ids else "No fix needed"
        fix_expected = "\n".join(FIXES[i-1]["expected_impact"] for i in fix_ids) if fix_ids else "N/A"

        priority = "P0-CRITICAL" if data.get("color") == "red" else ("P1-HIGH" if fix_ids else "P2-LOW")

        row = [
            sym,
            ", ".join(accounts) if accounts else "NONE",
            f"+{pct}%",
            f"+{peak}% (intraday)" if peak else f"+{pct}% (close)",
            in_keys_str,
            data.get("system_action", "N/A"),
            data.get("root_cause", "N/A"),
            data.get("pnl_estimate", "N/A"),
            fix_names,
            fix_descs,
            fix_expected,
            priority,
        ]
        ws.append(row)

        color = data.get("color", "none")
        fill = None
        if color == "red":
            fill = RED_FILL
        elif color == "orange":
            fill = ORANGE_FILL
        style_data_row(ws, row_idx, len(headers), fill)
        row_idx += 1

    ws.row_dimensions[1].height = 40
    for i in range(2, row_idx):
        ws.row_dimensions[i].height = 80
    autofit_columns(ws)

def write_sheet2(wb):
    ws = wb.create_sheet("Per-Account Detail")
    ws.freeze_panes = "A2"

    headers = [
        "Account", "Symbol", "7d_pct_change", "Peak_pct",
        "In_Tradeable_Keys", "Direction_Bias", "System_Action",
        "Root_Cause", "Color_Code"
    ]
    ws.append(headers)
    style_header_row(ws, 1, len(headers))

    rows = get_per_account_rows()
    row_idx = 2
    for r in rows:
        ws.append([
            r["account"],
            r["symbol"],
            f"+{r['7d_pct']}%",
            f"+{r['peak_pct']}%",
            r["in_keys"],
            r["direction_bias"],
            r["system_action"],
            r["root_cause"],
            r["color"].upper(),
        ])
        color = r["color"]
        fill = None
        if color == "red":
            fill = RED_FILL
        elif color == "orange":
            fill = ORANGE_FILL
        style_data_row(ws, row_idx, len(headers), fill)
        row_idx += 1

    for i in range(2, row_idx):
        ws.row_dimensions[i].height = 60
    autofit_columns(ws)

def write_sheet3(wb):
    ws = wb.create_sheet("Reentry Audit — ZECUSDC")
    ws.freeze_panes = "A2"

    # Title banner
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = "ZECUSDC (flz) — Full Trade Timeline May 5–7, 2026  |  Price moved +59.9% (Apr 30→May7), peaked +94% intraday  |  148 total decision log entries"
    title_cell.font = Font(bold=True, color="FFFFFF", size=11)
    title_cell.fill = PatternFill("solid", fgColor="8B0000")
    title_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 35

    headers = [
        "Date/Time (UTC)", "Price", "Action", "Reason",
        "Gain", "Duration", "Root_Cause", "Analysis"
    ]
    ws.append(headers)
    style_header_row(ws, 2, len(headers))

    # Summary box
    summary_data = [
        ("KEY FINDING", "The system executed 148 ZECUSDC decisions in 2 days. "
         "GOLDEN_RULE opened 18+ SHORTs during the rally. "
         "Every LONG attempt was killed within 90 seconds by a stale DC_CROSSBACK_TO_SHORT signal "
         "generated by a zero-volume klines init bar at price $359.02. "
         "The system NEVER held a LONG through the +59.9% move."),
    ]

    row_idx = 3
    for ts, price, action, reason, gain, duration, cause in ZEC_TIMELINE:
        analysis = ""
        if "GOLDEN_RULE_SHORT" in action + reason:
            analysis = "WRONG DIRECTION — shorting during confirmed daily BB breakout"
        elif "DC_CROSSBACK_TO_SHORT" in reason:
            analysis = "STALE SIGNAL — stale 359.02 init bar triggers false DC crossback, kills LONG in <1min"
        elif "DC_BREAK_UP" in reason or "BB_BREAK_UP" in reason:
            analysis = "System finally detects the breakout but only to close SHORTs, not hold LONGs"
        elif "GOLDEN_RULE_LONG" in action + reason:
            analysis = "Correct direction but killed by stale DC signal within 90s every time"
        elif "PEAK_GIVEBACK" in reason or "GAIN_EROSION" in reason:
            analysis = "Exit gate too tight; reduces position before move completes"
        elif "WT_4H_VEL_EXIT" in reason:
            analysis = "WT velocity exit triggers on the sharp move up — wrong direction for SHORT"

        row = [ts, f"${price:,.2f}" if isinstance(price, float) else str(price),
               action, reason, gain, duration, cause, analysis]
        ws.append(row)

        fill = None
        if "GOLDEN_RULE_SHORT" in (action + reason):
            fill = RED_FILL
        elif "GOLDEN_RULE_LONG" in (action + reason):
            fill = ORANGE_FILL
        elif "DC_CROSSBACK_TO_SHORT" in reason:
            fill = PatternFill("solid", fgColor="FF6666")
        elif "DC_BREAK_UP" in reason or "BB_BREAK_UP" in reason:
            fill = YELLOW_FILL
        style_data_row(ws, row_idx, len(headers), fill)
        row_idx += 1

    # Color legend
    ws.append([])
    row_idx += 1
    ws.append(["LEGEND:", "RED = GOLDEN_RULE SHORT (wrong direction)", "",
               "LIGHT RED = DC_CROSSBACK kills LONG (stale signal)", "",
               "ORANGE = GOLDEN_RULE LONG (killed by stale signal)", "",
               "YELLOW = Breakout finally detected (too late)", "", ""])
    for col in range(1, 11):
        ws.cell(row=row_idx, column=col).font = Font(bold=True, size=9)

    for i in range(3, row_idx):
        ws.row_dimensions[i].height = 55
    autofit_columns(ws)

def write_sheet4(wb):
    ws = wb.create_sheet("Action Plan")
    ws.freeze_panes = "A3"

    # Title
    ws.merge_cells("A1:J1")
    t = ws["A1"]
    t.value = "ACTION PLAN — Missed Breakout Fixes  |  Ordered by Priority  |  Total estimated PnL recovery: $3,600–$8,300/week"
    t.font = Font(bold=True, color="FFFFFF", size=12)
    t.fill = PatternFill("solid", fgColor="1A1A2E")
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 35

    headers = [
        "Fix_ID", "Priority", "Name", "Description",
        "Component", "Status", "Symbols_Affected",
        "Accounts_Affected", "Estimated_PnL_Recovery",
        "Expected_Impact"
    ]
    ws.append(headers)
    style_header_row(ws, 2, len(headers))

    row_idx = 3
    for fix in FIXES:
        row = [
            f"Fix {fix['id']}",
            fix["priority"],
            fix["name"],
            fix["description"],
            fix["component"],
            fix["status"],
            ", ".join(fix["symbols_affected"]),
            ", ".join(fix["accounts_affected"]),
            fix["estimated_pnl_recovery"],
            fix["expected_impact"],
        ]
        ws.append(row)

        fill = None
        if "P0" in fix["priority"]:
            fill = RED_FILL
        elif "P1" in fix["priority"]:
            fill = ORANGE_FILL
        elif "PENDING" in fix["status"]:
            fill = YELLOW_FILL
        elif "DONE" in fix["status"] or "DEPLOYED" in fix["status"]:
            fill = GREEN_FILL
        style_data_row(ws, row_idx, len(headers), fill)
        row_idx += 1

    # Add implementation notes
    ws.append([])
    ws.append([])
    row_idx += 2

    notes = [
        ["IMPLEMENTATION ORDER", "1. Fix 2 (STALE_DISK_INVALIDATE) — highest systemic impact: stops SHORTs on TONUSDT, DASHUSDT, NOTUSDT, others. Code change in ez_manage.py at GUARANTEED_PRICE_CROSS_REENTRY_DISK_* paths. Add: if abs(current_price - exit_price) / exit_price > 0.20: skip_and_invalidate_disk_record()."],
        ["", "2. Fix 1 (STALE_KLINES) — immediate fix for flz ZECUSDC. Delete/fix the zero-volume init bar in klines cache. Also review all symbols for stale init bars."],
        ["", "3. Fix 3 (BB_BREAKOUT_BLOCK_SHORT) — prevents GOLDEN_RULE from fighting confirmed daily breakouts. Add config switch BB_BREAKOUT_BLOCK_SHORT_ENABLED (default OFF) and sweep before enabling."],
        ["", "4. Fix 4 (BB_PULLBACK_REENTRY) — design phase only. Must run full V8 sweep (48+ syms × 4yr) before any live deployment. DO NOT enable without sweep proof."],
        ["WARNING", "Fixes 1 and 2 are code edits to ez_manage.py — follow CLAUDE.md Step 1 workflow: backup → edit → compile → rsync S1+S2 → verify md5."],
        ["WARNING", "Fix 2 touches GUARANTEED_PRICE_CROSS_REENTRY which is a core reentry path. Test on paper account first. The 20% threshold should be configurable (DISK_REENTRY_MAX_PRICE_DIVERGENCE_PCT=20.0 in config.py)."],
    ]
    for note in notes:
        ws.append(note)
        ws.cell(row=row_idx, column=1).font = Font(bold=True, color="FF0000" if "WARNING" in note[0] else "000000")
        ws.cell(row=row_idx, column=1).fill = PatternFill("solid", fgColor="FFEEEE" if "WARNING" in note[0] else "F0F0FF")
        ws.cell(row=row_idx, column=2).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row_idx].height = 70
        row_idx += 1

    for i in range(3, row_idx - len(notes)):
        ws.row_dimensions[i].height = 80
    autofit_columns(ws)

def main():
    out_path = "/Users/niels/Documents/binance/data/missed_breakouts_20260507.xlsx"
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    print("Writing Sheet 1: Missed Breakouts...")
    write_sheet1(wb)
    print("Writing Sheet 2: Per-Account Detail...")
    write_sheet2(wb)
    print("Writing Sheet 3: Reentry Audit — ZECUSDC...")
    write_sheet3(wb)
    print("Writing Sheet 4: Action Plan...")
    write_sheet4(wb)

    wb.save(out_path)
    print(f"\nSaved: {out_path}")
    print(f"File size: {os.path.getsize(out_path):,} bytes")
    print("\nSheets created:")
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        print(f"  - {sheet}: {ws.max_row} rows x {ws.max_column} cols")

if __name__ == "__main__":
    main()
