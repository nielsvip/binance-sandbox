import ast
import re
import os
import sys
import glob as glob_mod
from collections import defaultdict

BASE = "/Users/niels/Documents/binance"
LOGS_PRIMARY = f"{BASE}/logs"
LOGS_SECONDARY = "/Users/niels/logs"
SCRIPTS = {
    "ez_manage.py": f"{BASE}/ez_manage.py",
    "ez_positions_quick.py": f"{BASE}/ez_positions_quick.py",
    "ez_positions_service.py": f"{BASE}/ez_positions_service.py",
}

# Match last ~2 hours of entries (00:xx and 01:xx and 02:xx on Mar 25)
TIME_PATS = [
    re.compile(r'\[03-25 0[012]:'),
    re.compile(r'\[2026-03-25[ T]0[012]:'),
    re.compile(r'^25 0[012]:'),  # format like "25 02:39:59"
    re.compile(r'^03-25 0[012]:'),
]

def is_recent(line):
    for p in TIME_PATS:
        if p.search(line):
            return True
    return False

# ─── STEP 1: Extract functions ───
print("Step 1: Extracting functions...")

class FuncVisitor(ast.NodeVisitor):
    def __init__(self, source_lines):
        self.functions = []
        self.source_lines = source_lines
        self._class_stack = []
    def visit_ClassDef(self, node):
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()
    def visit_FunctionDef(self, node):
        cls = self._class_stack[-1] if self._class_stack else None
        end_line = getattr(node, 'end_lineno', node.lineno + 50) or node.lineno + 50
        body = "\n".join(self.source_lines[node.lineno-1:min(end_line, len(self.source_lines))])
        tags = set(re.findall(r'\[([A-Z][A-Z_]{2,})\]', body))
        self.functions.append({
            'name': node.name, 'line': node.lineno, 'end_line': end_line,
            'class': cls, 'tags': tags,
            'display': f"{cls}.{node.name}" if cls else node.name,
        })
        self.generic_visit(node)
    visit_AsyncFunctionDef = visit_FunctionDef

all_functions = {}
all_source = {}
for script_name, path in SCRIPTS.items():
    with open(path, 'r', errors='ignore') as f:
        source = f.read()
    lines = source.split('\n')
    all_source[script_name] = lines
    tree = ast.parse(source)
    v = FuncVisitor(lines)
    v.visit(tree)
    all_functions[script_name] = v.functions
    print(f"  {script_name}: {len(v.functions)} functions")

# ─── STEP 2: Parse ALL log files ───
print("\nStep 2: Parsing logs...")

def read_log_lines(path):
    lines = []
    try:
        with open(path, 'r', errors='ignore') as f:
            for line in f:
                if is_recent(line):
                    lines.append(line)
    except: pass
    return lines

log_lines = {"ez_manage.py": [], "ez_positions_quick.py": [], "ez_positions_service.py": []}

# ez_manage: check BOTH directories + rotated files
for d in [LOGS_PRIMARY, LOGS_SECONDARY]:
    for pat in [f"{d}/ez_manage_*.log", f"{d}/ez_manage_*.log.*"]:
        for f in glob_mod.glob(pat):
            if '_app.' not in f and '_watchdog.' not in f and '_restarts' not in f:
                new_lines = read_log_lines(f)
                log_lines["ez_manage.py"].extend(new_lines)
    for f in [f"{d}/actions.log", f"{d}/utils.log"]:
        log_lines["ez_manage.py"].extend(read_log_lines(f))

# ez_positions_quick: check BOTH directories
for d in [LOGS_PRIMARY, LOGS_SECONDARY]:
    for pat in [f"{d}/ez_positions_quick*.log", f"{d}/ez_positions_quick*.log.*"]:
        for f in glob_mod.glob(pat):
            if '_watchdog.' not in f and '_app.' not in f:
                log_lines["ez_positions_quick.py"].extend(read_log_lines(f))

# ez_positions_service + watchdog
for d in [LOGS_PRIMARY, LOGS_SECONDARY]:
    for f in glob_mod.glob(f"{d}/ez_positions_service*.log*") + glob_mod.glob(f"{d}/ez_positions_watchdog*.log*"):
        if '_app.' not in f:
            log_lines["ez_positions_service.py"].extend(read_log_lines(f))

for k, v in log_lines.items():
    print(f"  {k}: {len(v)} lines")

# ─── STEP 3: Count tags per symbol ───
print("\nStep 3: Counting tags per symbol...")
SYMBOL_RE = re.compile(r'([A-Z0-9]{2,}(?:USDT|USDC)\b)')
TAG_RE = re.compile(r'\[([A-Z][A-Z_]{2,})\]')

tag_symbol_counts = {}
tag_total_counts = {}
for script, lines in log_lines.items():
    tag_sym = defaultdict(lambda: defaultdict(int))
    tag_tot = defaultdict(int)
    for line in lines:
        tags = TAG_RE.findall(line)
        symbols = SYMBOL_RE.findall(line)
        if not symbols: symbols = ['_GLOBAL_']
        for tag in tags:
            tag_tot[tag] += 1
            for sym in symbols:
                tag_sym[tag][sym] += 1
    tag_symbol_counts[script] = dict(tag_sym)
    tag_total_counts[script] = dict(tag_tot)

# Map tags to functions
tag_to_func = {}
for script, funcs in all_functions.items():
    m = {}
    for func in funcs:
        for tag in func['tags']:
            if tag not in m:
                m[tag] = func['display']
    tag_to_func[script] = m

# Top symbols
symbol_activity = defaultdict(int)
for script in SCRIPTS:
    for tag, sym_counts in tag_symbol_counts.get(script, {}).items():
        for sym, cnt in sym_counts.items():
            if sym != '_GLOBAL_':
                symbol_activity[sym] += cnt
top_symbols = sorted(symbol_activity.keys(), key=lambda s: symbol_activity[s], reverse=True)[:25]
print(f"  Top symbols: {', '.join(top_symbols[:8])}...")

# ─── STEP 4: Generate XLS ───
print("\nStep 4: Generating XLS...")
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = openpyxl.Workbook()
HDR = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
HDR_F = Font(color="FFFFFF", bold=True, size=9)
GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
RED = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
DEAD = PatternFill(start_color="FF4444", end_color="FF4444", fill_type="solid")
GRAY = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
bdr = Border(left=Side('thin'), right=Side('thin'), top=Side('thin'), bottom=Side('thin'))

sym_cols = top_symbols[:20]

for si, (script_name, funcs) in enumerate(all_functions.items()):
    sn = script_name.replace('.py', '')[:31]
    ws = wb.active if si == 0 else wb.create_sheet(sn)
    if si == 0: ws.title = sn
    
    headers = ['#', 'Function', 'Class', 'Line', 'Total (2h)', 'Status', 'Matched Tags'] + sym_cols + ['WHY NOT CALLED']
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill = HDR; cell.font = HDR_F; cell.border = bdr
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
    
    stc = tag_total_counts.get(script_name, {})
    sts = tag_symbol_counts.get(script_name, {})
    t2f = tag_to_func.get(script_name, {})
    
    # Reverse map
    f2t = defaultdict(set)
    for tag, fd in t2f.items():
        f2t[fd].add(tag)
    
    def get_calls(func):
        total = 0
        matched = set()
        for tag in func['tags']:
            if tag in stc:
                total += stc[tag]; matched.add(tag)
        for tag in f2t.get(func['display'], set()):
            if tag in stc and tag not in matched:
                total += stc[tag]; matched.add(tag)
        return total, matched
    
    sorted_funcs = sorted(funcs, key=lambda f: (-get_calls(f)[0], f['line']))
    src_text = "\n".join(all_source.get(script_name, []))
    
    row = 2
    for idx, func in enumerate(sorted_funcs):
        total, matched = get_calls(func)
        
        if total > 100: status, sfill = "HOT", GREEN
        elif total > 10: status, sfill = "ACTIVE", GREEN
        elif total > 0: status, sfill = "LOW", YELLOW
        else: status, sfill = "ZERO", RED
        
        # WHY
        why = ""
        if total == 0:
            nm = func['name']
            if nm.startswith('__') and nm.endswith('__'):
                why = "Python magic method"
            elif nm in ('main', '_run', 'run', 'start'):
                why = "Entry point (runs once)"
            elif any(k in nm.lower() for k in ['loop', 'monitor', 'periodic', 'watchdog', 'poll']):
                # Check if any log output at all
                body_start = func['line'] - 1
                body_end = min(func['end_line'], len(all_source.get(script_name, [])))
                body = "\n".join(all_source.get(script_name, [])[body_start:body_end])
                if 'logger' in body or 'log.' in body:
                    why = "Background loop — runs but log tags not matched (check tag names)"
                else:
                    why = "Background loop — no logger calls in body"
            elif any(k in nm.lower() for k in ['init', 'setup', 'bootstrap', 'configure', 'prepare']):
                why = "Startup init (called once)"
            elif any(k in nm.lower() for k in ['shutdown', 'cleanup', 'close', 'teardown', 'stop']):
                why = "Shutdown/cleanup handler"
            elif any(k in nm.lower() for k in ['callback', 'handler', 'on_', 'handle_']):
                why = "Event/WS callback (triggered by external events)"
            elif any(k in nm.lower() for k in ['get_', 'is_', 'has_', 'can_']):
                call_cnt = len(re.findall(rf'\.{re.escape(nm)}\s*\(', src_text))
                if call_cnt > 0:
                    why = f"Getter/predicate — called {call_cnt}x internally, no log tag"
                else:
                    why = "POSSIBLY DEAD — getter with no callers"
            elif any(k in nm.lower() for k in ['format', 'parse', 'convert', 'serialize', 'deserialize', 'encode', 'decode']):
                why = "Data transform helper"
            elif any(k in nm.lower() for k in ['calculate', 'compute', 'score', 'evaluate']):
                call_cnt = len(re.findall(rf'\.?{re.escape(nm)}\s*\(', src_text)) - 1
                why = f"Calculation helper — called {max(0,call_cnt)}x internally"
            elif any(k in nm.lower() for k in ['update', 'set_', 'mark_', 'reset', 'clear']):
                call_cnt = len(re.findall(rf'\.{re.escape(nm)}\s*\(', src_text))
                why = f"State mutator — called {call_cnt}x internally"
            elif any(k in nm.lower() for k in ['save', 'persist', 'write', 'dump', 'export']):
                why = "Persistence helper"
            elif any(k in nm.lower() for k in ['load', 'read', 'fetch', 'import', 'restore']):
                why = "Data loader (startup/periodic)"
            elif nm.startswith('_'):
                call_cnt = len(re.findall(rf'\.{re.escape(nm)}\s*\(', src_text))
                self_cnt = len(re.findall(rf'self\.{re.escape(nm)}\s*\(', src_text))
                direct_cnt = len(re.findall(rf'(?<!\.)(?<!self\.){re.escape(nm)}\s*\(', src_text)) - 1
                total_ref = call_cnt + max(0, direct_cnt)
                if total_ref > 0:
                    why = f"Private helper — called {total_ref}x internally, no log tag"
                else:
                    why = "⚠️ POSSIBLY DEAD — private with no callers found"
            else:
                call_cnt = len(re.findall(rf'\.{re.escape(nm)}\s*\(', src_text))
                direct_cnt = len(re.findall(rf'(?<!\.)(?<!self\.)\b{re.escape(nm)}\s*\(', src_text)) - 1
                total_ref = call_cnt + max(0, direct_cnt)
                if total_ref > 0:
                    why = f"Called {total_ref}x internally — no matching log tag"
                else:
                    # Check cross-script
                    cross_found = False
                    for other_script, other_path in SCRIPTS.items():
                        if other_script == script_name: continue
                        try:
                            with open(other_path, 'r', errors='ignore') as xf:
                                if re.search(rf'\b{re.escape(nm)}\s*\(', xf.read()):
                                    cross_found = True; break
                        except: pass
                    if cross_found:
                        why = f"Called from other script ({other_script})"
                    else:
                        why = "⚠️ POSSIBLY DEAD — no callers found anywhere"
        
        ws.cell(row=row, column=1, value=idx+1).border = bdr
        ws.cell(row=row, column=2, value=func['display']).border = bdr
        ws.cell(row=row, column=3, value=func.get('class', '') or '').border = bdr
        ws.cell(row=row, column=4, value=func['line']).border = bdr
        c5 = ws.cell(row=row, column=5, value=total); c5.border = bdr; c5.fill = sfill
        c6 = ws.cell(row=row, column=6, value=status); c6.border = bdr; c6.fill = sfill
        c7 = ws.cell(row=row, column=7, value=", ".join(sorted(matched)) if matched else ""); c7.border = bdr
        
        for si2, sym in enumerate(sym_cols):
            sc = sum(sts.get(tag, {}).get(sym, 0) for tag in matched)
            cell = ws.cell(row=row, column=8+si2, value=sc if sc > 0 else "")
            cell.border = bdr
            if sc > 0: cell.fill = GREEN
            elif total > 0: cell.fill = GRAY
        
        wc = ws.cell(row=row, column=8+len(sym_cols), value=why); wc.border = bdr
        if "DEAD" in why:
            wc.fill = DEAD; wc.font = Font(color="FFFFFF", bold=True)
        elif total == 0 and why:
            wc.fill = RED
        row += 1
    
    ws.column_dimensions['A'].width = 5
    ws.column_dimensions['B'].width = 48
    ws.column_dimensions['C'].width = 30
    ws.column_dimensions['D'].width = 7
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 8
    ws.column_dimensions['G'].width = 35
    for i in range(len(sym_cols)):
        ws.column_dimensions[get_column_letter(8+i)].width = 14
    ws.column_dimensions[get_column_letter(8+len(sym_cols))].width = 60
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row-1}"
    print(f"  {sn}: {row-2} rows")

# ─── SUMMARY SHEET ───
ws_sum = wb.create_sheet("SUMMARY", 0)
ws_sum.cell(row=1, column=1, value="Function Call Grid — Last 2 Hours (UTC)").font = Font(bold=True, size=14)
ws_sum.cell(row=2, column=1, value="Generated: 2026-03-25 ~02:50 UTC | Log paths: binance/logs/ + ~/logs/")

row = 4
for script_name, funcs in all_functions.items():
    ws_sum.cell(row=row, column=1, value=script_name).font = Font(bold=True, size=12)
    row += 1
    stc = tag_total_counts.get(script_name, {})
    active = sum(1 for f in funcs if any(stc.get(t, 0) > 0 for t in f['tags']))
    total_log = sum(stc.values())
    ws_sum.cell(row=row, column=1, value=f"  Functions: {len(funcs)} total | {active} with log hits | {len(funcs)-active} silent")
    row += 1
    ws_sum.cell(row=row, column=1, value=f"  Log lines parsed: {len(log_lines.get(script_name, []))} | Unique tags: {len(stc)}")
    row += 1
    
    dead = []
    for f in funcs:
        calls = sum(stc.get(t, 0) for t in f['tags'])
        if calls == 0:
            nm = f['name']
            src = "\n".join(all_source.get(script_name, []))
            dot_calls = len(re.findall(rf'\.{re.escape(nm)}\s*\(', src))
            direct_calls = max(0, len(re.findall(rf'(?<!\.)(?<!self\.)\b{re.escape(nm)}\s*\(', src)) - 1)
            if dot_calls + direct_calls == 0 and not nm.startswith('__'):
                dead.append(f['display'])
    
    if dead:
        ws_sum.cell(row=row, column=1, value=f"  ⚠️ POSSIBLY DEAD ({len(dead)}):").font = Font(color="FF0000", bold=True)
        row += 1
        for d in dead[:15]:
            ws_sum.cell(row=row, column=1, value=f"    • {d}")
            row += 1
        if len(dead) > 15:
            ws_sum.cell(row=row, column=1, value=f"    ... and {len(dead)-15} more (see script sheet)")
            row += 1
    
    sorted_tags = sorted(stc.items(), key=lambda x: -x[1])[:15]
    if sorted_tags:
        ws_sum.cell(row=row, column=1, value="  Top 15 active tags:").font = Font(bold=True)
        row += 1
        for tag, cnt in sorted_tags:
            fn = tag_to_func.get(script_name, {}).get(tag, "?")
            ws_sum.cell(row=row, column=1, value=f"    [{tag}] = {cnt:,} → {fn}")
            row += 1
    row += 1

ws_sum.cell(row=row, column=1, value="Top 25 Active Symbols").font = Font(bold=True, size=12)
row += 1
for i, sym in enumerate(top_symbols):
    ws_sum.cell(row=row, column=1, value=f"  {i+1}. {sym}: {symbol_activity[sym]:,} log entries")
    row += 1

ws_sum.column_dimensions['A'].width = 80

out = f"{BASE}/function_call_grid.xlsx"
wb.save(out)
print(f"\n✅ Saved: {out}")
print(f"   {sum(len(f) for f in all_functions.values())} functions, {sum(len(v) for v in log_lines.values())} log lines")
