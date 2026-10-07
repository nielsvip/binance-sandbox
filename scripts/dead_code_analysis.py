import ast
import re
import os
import sys
import glob as glob_mod
from collections import defaultdict

BASE = "/Users/niels/Documents/binance"
SCRIPTS = {
    "ez_manage.py": f"{BASE}/ez_manage.py",
    "ez_positions_quick.py": f"{BASE}/ez_positions_quick.py",
    "ez_positions_service.py": f"{BASE}/ez_positions_service.py",
}

# Load all source files in the project for cross-reference
all_py_files = {}
for f in glob_mod.glob(f"{BASE}/*.py"):
    try:
        with open(f, 'r', errors='ignore') as fh:
            all_py_files[os.path.basename(f)] = fh.read()
    except: pass

# Extract functions
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
        self.functions.append({
            'name': node.name, 'line': node.lineno, 'end_line': end_line,
            'class': cls,
            'display': f"{cls}.{node.name}" if cls else node.name,
            'lines_of_code': end_line - node.lineno,
        })
        self.generic_visit(node)
    visit_AsyncFunctionDef = visit_FunctionDef

script_funcs = {}
for sn, path in SCRIPTS.items():
    with open(path, 'r', errors='ignore') as f:
        src = f.read()
    lines = src.split('\n')
    tree = ast.parse(src)
    v = FuncVisitor(lines)
    v.visit(tree)
    script_funcs[sn] = (v.functions, src)

# For each function, count references across ALL .py files
dead_candidates = []
maybe_dead = []
internal_only = []

for script_name, (funcs, own_src) in script_funcs.items():
    module_name = script_name.replace('.py', '')
    
    for func in funcs:
        nm = func['name']
        cls = func['class']
        display = func['display']
        
        # Skip magic methods
        if nm.startswith('__') and nm.endswith('__'):
            continue
        
        # Count references in own script (excluding definition line)
        own_refs_dot = len(re.findall(rf'\.{re.escape(nm)}\s*\(', own_src))
        own_refs_self = len(re.findall(rf'self\.{re.escape(nm)}\s*\(', own_src))
        own_refs_bare = max(0, len(re.findall(rf'(?<![.\w]){re.escape(nm)}\s*\(', own_src)) - 1)
        own_total = own_refs_dot + own_refs_bare
        
        # Count references in OTHER scripts
        cross_refs = 0
        cross_scripts = []
        for other_file, other_src in all_py_files.items():
            if other_file == script_name:
                continue
            # Check if this script imports our module
            if re.search(rf'(from|import)\s+{re.escape(module_name)}\b', other_src):
                refs = len(re.findall(rf'\.{re.escape(nm)}\s*\(', other_src))
                refs += len(re.findall(rf'(?<![.\w]){re.escape(nm)}\s*\(', other_src))
                if refs > 0:
                    cross_refs += refs
                    cross_scripts.append(other_file)
        
        total_refs = own_total + cross_refs
        
        entry = {
            'script': script_name,
            'display': display,
            'name': nm,
            'class': cls,
            'line': func['line'],
            'loc': func['lines_of_code'],
            'own_refs': own_total,
            'cross_refs': cross_refs,
            'cross_scripts': cross_scripts,
            'total_refs': total_refs,
        }
        
        if total_refs == 0:
            dead_candidates.append(entry)
        elif own_total == 0 and cross_refs > 0:
            pass  # Called from other scripts - fine
        elif own_total <= 1 and cross_refs == 0 and cls:
            # Method only referenced once (maybe just the definition pattern match)
            maybe_dead.append(entry)

# Sort by lines of code (biggest dead code first)
dead_candidates.sort(key=lambda x: -x['loc'])
maybe_dead.sort(key=lambda x: -x['loc'])

# Output
print("=" * 100)
print(f"DEAD CODE ANALYSIS — {len(dead_candidates)} DEFINITELY DEAD | {len(maybe_dead)} MAYBE DEAD")
print("=" * 100)

print(f"\n{'='*100}")
print("DEFINITELY DEAD — zero references anywhere (safe to delete)")
print(f"{'='*100}")
total_dead_loc = 0
for script_name in SCRIPTS:
    entries = [e for e in dead_candidates if e['script'] == script_name]
    if not entries:
        continue
    loc_sum = sum(e['loc'] for e in entries)
    total_dead_loc += loc_sum
    print(f"\n--- {script_name} ({len(entries)} dead functions, {loc_sum} lines) ---")
    for e in entries:
        print(f"  L{e['line']:>5} | {e['loc']:>4} LOC | {e['display']}")

print(f"\n{'='*100}")
print(f"TOTAL DEAD: {len(dead_candidates)} functions, ~{total_dead_loc} lines of code that can be removed")
print(f"{'='*100}")

print(f"\n{'='*100}")
print("MAYBE DEAD — only 0-1 references, likely unused (verify before deleting)")
print(f"{'='*100}")
for script_name in SCRIPTS:
    entries = [e for e in maybe_dead if e['script'] == script_name]
    if not entries:
        continue
    print(f"\n--- {script_name} ({len(entries)} suspicious functions) ---")
    for e in entries[:20]:
        print(f"  L{e['line']:>5} | {e['loc']:>4} LOC | {e['display']} | refs: own={e['own_refs']} cross={e['cross_refs']}")
    if len(entries) > 20:
        print(f"  ... and {len(entries)-20} more")

# Also add to the XLS
print(f"\n{'='*100}")
print("Adding DEAD_CODE sheet to function_call_grid.xlsx...")
print(f"{'='*100}")

import openpyxl
from openpyxl.styles import Font, PatternFill, Border, Side

wb = openpyxl.load_workbook(f"{BASE}/function_call_grid.xlsx")
if "DEAD_CODE" in wb.sheetnames:
    del wb["DEAD_CODE"]
ws = wb.create_sheet("DEAD_CODE", 1)

HDR = PatternFill(start_color="8B0000", end_color="8B0000", fill_type="solid")
HDR_F = Font(color="FFFFFF", bold=True, size=10)
DEAD_F = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
MAYBE_F = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
bdr = Border(left=Side('thin'), right=Side('thin'), top=Side('thin'), bottom=Side('thin'))

headers = ['#', 'Script', 'Function', 'Class', 'Line', 'LOC', 'Verdict', 'Own Refs', 'Cross Refs', 'Cross Scripts', 'Action']
for c, h in enumerate(headers, 1):
    cell = ws.cell(row=1, column=c, value=h)
    cell.fill = HDR; cell.font = HDR_F; cell.border = bdr

row = 2
ws.cell(row=row, column=1, value="").border = bdr
ws.cell(row=row, column=2, value="--- DEFINITELY DEAD (safe to delete) ---").font = Font(bold=True, size=11, color="FF0000")
row += 1

for idx, e in enumerate(dead_candidates):
    ws.cell(row=row, column=1, value=idx+1).border = bdr
    ws.cell(row=row, column=2, value=e['script']).border = bdr
    ws.cell(row=row, column=3, value=e['display']).border = bdr
    ws.cell(row=row, column=4, value=e['class'] or '').border = bdr
    ws.cell(row=row, column=5, value=e['line']).border = bdr
    ws.cell(row=row, column=6, value=e['loc']).border = bdr
    c7 = ws.cell(row=row, column=7, value="DELETE"); c7.border = bdr; c7.fill = DEAD_F; c7.font = Font(bold=True, color="CC0000")
    ws.cell(row=row, column=8, value=e['own_refs']).border = bdr
    ws.cell(row=row, column=9, value=e['cross_refs']).border = bdr
    ws.cell(row=row, column=10, value=", ".join(e['cross_scripts'])).border = bdr
    ws.cell(row=row, column=11, value="Safe to delete — no references found").border = bdr
    row += 1

row += 1
ws.cell(row=row, column=2, value="--- MAYBE DEAD (verify before deleting) ---").font = Font(bold=True, size=11, color="CC8800")
row += 1

for idx, e in enumerate(maybe_dead):
    ws.cell(row=row, column=1, value=idx+1).border = bdr
    ws.cell(row=row, column=2, value=e['script']).border = bdr
    ws.cell(row=row, column=3, value=e['display']).border = bdr
    ws.cell(row=row, column=4, value=e['class'] or '').border = bdr
    ws.cell(row=row, column=5, value=e['line']).border = bdr
    ws.cell(row=row, column=6, value=e['loc']).border = bdr
    c7 = ws.cell(row=row, column=7, value="VERIFY"); c7.border = bdr; c7.fill = MAYBE_F
    ws.cell(row=row, column=8, value=e['own_refs']).border = bdr
    ws.cell(row=row, column=9, value=e['cross_refs']).border = bdr
    ws.cell(row=row, column=10, value=", ".join(e['cross_scripts'])).border = bdr
    ws.cell(row=row, column=11, value="Only 0-1 refs — likely dead but verify").border = bdr
    row += 1

for col in ['A','B','C','D','E','F','G','H','I','J','K']:
    ws.column_dimensions[col].width = [5, 25, 48, 30, 7, 6, 10, 8, 8, 30, 40][ord(col)-65]
ws.freeze_panes = 'D2'

wb.save(f"{BASE}/function_call_grid.xlsx")
print(f"\n✅ DEAD_CODE sheet added to function_call_grid.xlsx")
