from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ROOT = Path('/Users/niels/Documents/binance')
SOURCE = ROOT / 'data/reports/SWITCH_MATRIX_TRB_S1_CURRENT.xlsx'
OUTPUT = ROOT / 'outputs/019fcd2f-b9de-78d3-a50a-398223e8822c/SWITCH_MATRIX_TRB_S1_PATH_EXPLANATIONS.xlsx'


CORE_FILES = [
    'tradier_manage.py', 'ez_manage.py', 'ez_positions_quick.py', 'ez_positions_service.py',
    'entry_engine_dc.py', 'entry_engine_stdev_macro.py', 'mtf_live_evaluator.py',
    'stdev_macro.py', 'scalp_v3.py', 'v8_quick_engine.py', 'v8_vec_sweep.py',
    'backtest_v8_engine.py',
]


def function_ranges(path: Path):
    try:
        tree = ast.parse(path.read_text(errors='ignore'))
    except Exception:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, 'end_lineno', node.lineno)
            out.append((node.lineno, end, node.name))
    return out


def build_code_index():
    index = {}
    for fname in CORE_FILES:
        path = ROOT / fname
        if not path.exists():
            continue
        ranges = function_ranges(path)
        lines = path.read_text(errors='ignore').splitlines()
        for lineno, line in enumerate(lines, 1):
            if re.search(r'config(?:_tradier)?\.py', fname, re.I) or fname.startswith('test_') or 'sweep' in fname.lower() or re.search(r'\bassert\s', line):
                continue
            containing = [name for start, end, name in ranges if start <= lineno <= end]
            fn = containing[-1] if containing else '<module/config gate>'
            for token in re.findall(r'[A-Z][A-Z0-9_]{3,}', line):
                index.setdefault(token, (f'{fname}:{lineno} ({fn})', line.strip()[:240]))
    return index


def trace_for(setting, code_index):
    candidates = [setting]
    stripped = re.sub(r'\[cfg:[^\]]+\]', '', setting)
    candidates.append(stripped)
    candidates.append(stripped.replace('WT1_5M', 'WT_3M'))
    candidates.append(stripped.replace('WT1_5M_FORCE_OPEN', 'WT_3M_FORCE_OPEN'))
    for candidate in candidates:
        if candidate in code_index:
            return code_index[candidate]
    return 'No direct engine hit found', ''


def type_of(values):
    vals = [v for v in values if v is not None and v != '']
    if not vals:
        return 'unspecified'
    normalized = {str(v).strip().lower() for v in vals}
    if normalized <= {'true', 'false'}:
        return 'boolean'
    numeric = True
    for v in vals:
        try:
            float(str(v).replace(',', ''))
        except Exception:
            numeric = False
            break
    if numeric:
        return 'numeric'
    return 'selector/string'


def type_note(kind, values, name):
    if kind in {'boolean', 'numeric', 'unspecified'}:
        return ''
    sample = ', '.join(str(v) for v in values[:6])
    lname = name.lower()
    if 'tf' in lname or any(x in lname for x in ('timeframe', 'basis')):
        return f'Non-boolean/non-numeric selector: {sample}. This chooses the timeframe/basis branch; it is not a magnitude threshold and should be interpreted as a discrete mode.'
    if any(x in lname for x in ('mode', 'type', 'style', 'method', 'regime', 'source')):
        return f'Non-boolean/non-numeric selector: {sample}. This selects a discrete implementation mode; changing the label routes execution to a different branch.'
    return f'Non-boolean/non-numeric selector: {sample}. Treat values as exact categorical labels; changing spelling/case can select no branch or a different branch.'


def explanation(area, name, desc, trace_line, code_line, kind, values):
    clean = (desc or '').strip()
    if clean.startswith('ENTRY') or clean.startswith('EXIT') or clean.startswith('OTHER'):
        clean = clean.replace('ENTRY MAIN SWITCH — ', '').replace('ENTRY FILTER — ', '')
        clean = clean.replace('ENTRY SUB SETTING — ', '').replace('ENTRY TF — ', '')
        clean = clean.replace('ENTRY CONDITION — ', '').replace('ENTRY BYPASS — ', '')
        clean = clean.replace('EXIT MAIN SWITCH — ', '').replace('EXIT FILTER — ', '')
        clean = clean.replace('EXIT SUB SETTING — ', '').replace('EXIT TF — ', '')
        clean = clean.replace('EXIT CONDITION — ', '').replace('OTHER MAIN SWITCH — ', '')
        clean = clean.replace('OTHER FILTER — ', '').replace('OTHER SUB SETTING — ', '')
        clean = clean.replace('OTHER CONDITION — ', '').replace('OTHER TF — ', '')
    if not clean:
        clean = f'{area} path/setting {name}.'
    if area == 'Entry' and 'entry' not in clean.lower():
        clean = 'Entry gate/path: ' + clean
    if area == 'Exit' and 'exit' not in clean.lower():
        clean = 'Exit gate/path: ' + clean
    if code_line:
        return f'{clean} Code trace: {trace_line}. The referenced line contains this setting in the live engine gate. Example: {code_line}'
    return f'{clean} Code trace: {trace_line}.'


def collect_records(ws, area):
    records = []
    current = None
    active_sub = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        main, sub, value, desc = row[:4]
        if main is not None and str(main).strip() != '':
            current = {'main': str(main).strip(), 'desc': desc or '', 'main_values': [], 'subs': {}}
            records.append(current)
            active_sub = None
        if current is None:
            continue
        if sub is not None and str(sub).strip() != '':
            active_sub = str(sub).strip()
            current['subs'].setdefault(active_sub, {'values': [], 'desc': desc or ''})
        if value is not None:
            if active_sub:
                current['subs'][active_sub]['values'].append(value)
            else:
                current['main_values'].append(value)
        if desc and not current['desc']:
            current['desc'] = desc
    out = []
    for block in records:
        main = block['main']
        vals = block['main_values']
        if vals or not block['subs']:
            out.append((area, main, '', vals, block['desc']))
        for sub, data in block['subs'].items():
            out.append((area, main, sub, data['values'], data['desc'] or block['desc']))
    return out


def main():
    wb = load_workbook(SOURCE)
    if 'Path Explanations' in wb.sheetnames:
        del wb['Path Explanations']
    ws = wb.create_sheet('Path Explanations')
    headers = [
        'Area', 'Path / main switch', 'Sub-setting (original col 2)', 'Parameter type',
        'Observed values', 'Scope', 'Engine function / file:line', 'What the path does',
        'Non-bool/non-numeric interpretation', 'Status / source note',
    ]
    ws.append(headers)

    records = collect_records(wb['Entry'], 'Entry') + collect_records(wb['Exit'], 'Exit')
    code_index = build_code_index()
    for area, main, sub, values, desc in records:
        setting = sub or main
        kind = type_of(values)
        trace, code_line = trace_for(setting, code_index)
        # Pull scope from the current workbook description where present.
        scope = 'per-symbol' if 'per-symbol' in (desc or '').lower() else ('global-only' if 'global-only' in (desc or '').lower() else 'not stated')
        status = ''
        m = re.search(r'\boff=([^;\.]+)', desc or '')
        if m:
            status = f'Default/off convention: {m.group(1).strip()}'
        note = type_note(kind, values, setting)
        ws.append([
            area, main, sub or '', kind, ', '.join(str(v) for v in values[:20]), scope,
            trace, explanation(area, setting, desc, trace, code_line, kind, values), note, status,
        ])

    # Visual match: restrained header band, readable wrapped descriptions, frozen headers.
    header_fill = PatternFill('solid', fgColor='1F4E78')
    section_fill = PatternFill('solid', fgColor='D9EAF7')
    white_font = Font(color='FFFFFF', bold=True)
    thin = Side(style='thin', color='D9E2F3')
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 32
    for row in ws.iter_rows(min_row=2):
        row[0].fill = section_fill
        row[0].font = Font(bold=True)
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            cell.border = Border(bottom=thin)
    widths = [10, 42, 42, 18, 30, 14, 42, 90, 70, 28]
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT)
    print(f'Wrote {OUTPUT} with {len(records)} explanation rows')


if __name__ == '__main__':
    main()
