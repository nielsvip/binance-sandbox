from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import csv
import gzip

ROOT = Path('/Users/niels/Documents/binance')
SOURCE = ROOT / 'data/reports/SWITCH_MATRIX_TRB_S1_CURRENT.xlsx'
OUTPUT = ROOT / 'outputs/019fcd2f-b9de-78d3-a50a-398223e8822c/detailed_logic_sheets.xlsx'

EXCLUDE = {'config.py', 'config_tradier.py'}


def fn_ranges(path):
    try:
        tree = ast.parse(path.read_text(errors='ignore'))
    except Exception:
        return []
    return [(n.lineno, getattr(n, 'end_lineno', n.lineno), n.name)
            for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def candidates(key):
    vals = [key]
    vals.append(re.sub(r'\[cfg:[^\]]+\]', '', key))
    vals.append(vals[-1].replace('WT1_5M', 'WT_3M'))
    return list(dict.fromkeys(vals))


def build_hits():
    hits = defaultdict(list)
    for path in ROOT.glob('*.py'):
        if path.name in EXCLUDE or path.name.startswith('test_') or 'sweep' in path.name.lower():
            continue
        try:
            lines = path.read_text(errors='ignore').splitlines()
        except Exception:
            continue
        ranges = fn_ranges(path)
        for lineno, line in enumerate(lines, 1):
            if re.search(r'\bassert\s', line):
                continue
            for token in re.findall(r'\b[A-Z][A-Z0-9_]{4,}\b', line):
                fn = [name for start, end, name in ranges if start <= lineno <= end]
                hits[token].append((path.name, lineno, fn[-1] if fn else '<module>', line.strip()))
    return hits


def collect(ws, area):
    rows = []
    current = None
    active_sub = None
    for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        main, sub, value, desc = row[:4]
        if main is not None and str(main).strip():
            current = {'main': str(main).strip(), 'desc': desc or '', 'values': [], 'subs': {}}
            rows.append(current)
            active_sub = None
        if current is None:
            continue
        if sub is not None and str(sub).strip():
            active_sub = str(sub).strip()
            current['subs'].setdefault(active_sub, {'values': [], 'desc': desc or ''})
        if value is not None:
            if active_sub:
                current['subs'][active_sub]['values'].append(value)
            else:
                current['values'].append(value)
        if desc and not current['desc']:
            current['desc'] = desc
    result = []
    for block in rows:
        if block['values'] or not block['subs']:
            result.append((area, block['main'], '', list(dict.fromkeys(block['values'])), block['desc']))
        for sub, data in block['subs'].items():
            result.append((area, block['main'], sub, list(dict.fromkeys(data['values'])), data['desc'] or block['desc']))
    return result


def kind(values):
    vals = [str(v).lower() for v in values if v is not None]
    if vals and set(vals) <= {'true', 'false'}:
        return 'boolean'
    try:
        for v in vals:
            float(v.replace(',', ''))
        return 'numeric' if vals else 'unspecified'
    except Exception:
        return 'selector/string'


def find_hits(key, index):
    out = []
    for c in candidates(key):
        for hit in index.get(c, []):
            if hit not in out:
                out.append(hit)
    return out[:5]


def explain(area, key, sub, values, desc, found):
    label = sub or key
    role = 'entry' if area == 'Entry' else 'exit'
    if 'FILTER' in (desc or '').upper() or 'CONDITION' in (desc or '').upper() or 'TF' in (desc or '').upper():
        role = f'{role} filter'
    if found:
        clauses = []
        for fname, line, fn, code in found[:3]:
            clauses.append(f'{fname}:{line} in {fn}: {code}')
        exact = ' || '.join(clauses)
        return (f'This is the {role} controlled by {label}. The live engine reads it at the locations below. '
                f'Use the exact expression to determine the firing rule: {exact}. '
                f'Operationally, the path only qualifies when the surrounding condition in that function passes; '
                f'a false/disabled switch prevents this branch, while a true/enabled switch allows it to be evaluated. '
                f'Configured values observed: {", ".join(map(str, values[:12])) or "none"}.')
    return (f'This is a {role} setting named {label}, but no direct read was found in the live Python engine. '
            f'It may be stale, config-only, or implemented under a differently named adapter. '
            f'Configured values observed: {", ".join(map(str, values[:12])) or "none"}.')


def classify(area, desc, key, sub):
    text = ' '.join([desc or '', key, sub]).upper()
    if area == 'Entry' and not any(x in text for x in ('FILTER', 'CONDITION', 'VETO', 'GATE', 'THRESHOLD', 'TF_', '_TF', 'BYPASS')):
        return 'Entry Paths'
    if area == 'Exit' and not any(x in text for x in ('FILTER', 'CONDITION', 'VETO', 'GATE', 'THRESHOLD', 'TF_', '_TF', 'BYPASS')):
        return 'Exit Paths'
    return 'Filters'


def style(ws):
    fill = PatternFill('solid', fgColor='1F4E78')
    white = Font(color='FFFFFF', bold=True)
    thin = Side(style='thin', color='D9E2F3')
    for c in ws[1]:
        c.fill = fill; c.font = white; c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 34
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical='top', wrap_text=True)
            c.border = Border(bottom=thin)
    for i, width in enumerate([10, 44, 44, 18, 32, 15, 56, 150, 36, 28], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions; ws.sheet_view.showGridLines = False


def tabular_files():
    for p in ROOT.rglob('*'):
        if not p.is_file() or 'outputs' in p.parts or '.git' in p.parts:
            continue
        if any(tag in p.name.lower() for tag in ('bloated', 'corrupt', 'vector_filled')):
            continue
        # Result workbooks over this size are preserved in the base file; reopening
        # them here would duplicate their raw trade/evidence payload into the audit.
        if p.stat().st_size > 5 * 1024 * 1024:
            continue
        if p.suffix.lower() in {'.csv', '.tsv'} or (p.suffix.lower() in {'.xlsx', '.xlsm'} and p.stat().st_size < 5 * 1024 * 1024):
            yield p


def result_rows():
    rows = {}
    metric_words = ('status', 'n_tested', 'n_inert', 'delta', 'sharpe', 'pnl', 'trades', 'win_rate', 'vector', 'scalar', 'real', 'exact', 'pass', 'fail', 'coverage', 'mean_')
    selected = [
        ROOT / 'data/reports/SWITCH_MATRIX_TRB_S1_CURRENT.xlsx',
        ROOT / 'data/reports/SWITCH_MATRIX_TRB_COHORT3_20260803.xlsx',
        ROOT / 'data/reports/SWITCH_MATRIX_TRB_VEC_DIAGNOSTIC.xlsx',
    ]
    for path in selected:
        if not path.exists():
            continue
        try:
            if path.suffix.lower() in {'.csv', '.tsv'} or path.name.lower().endswith(('.csv.gz', '.tsv.gz')):
                opener = gzip.open if path.name.lower().endswith('.gz') else open
                with opener(path, 'rt', encoding='utf-8-sig', errors='replace', newline='') as f:
                    reader = csv.reader(f, delimiter='\t' if path.name.lower().endswith('.tsv') else ',')
                    sheet = 'CSV'
                    for rn, vals in enumerate(reader, 1):
                        text = ' | '.join(str(v).strip() for v in vals if v is not None and str(v).strip())
                        if not text or not (re.search(r'\b[A-Z][A-Z0-9_]{4,}\b', text) or any(w in text.lower() for w in metric_words)):
                            continue
                        keys = re.findall(r'\b[A-Z][A-Z0-9_]{4,}\b', text)[:10]
                        if not keys and rn > 1:
                            continue
                        ident = tuple(x.strip().lower() for x in vals)
                        record = [str(path.relative_to(ROOT)), sheet, rn, ', '.join(keys), text[:2200]]
                        if ident in rows:
                            rows[ident][0] += f'; {record[0]}:{record[1]}:{record[2]}'
                        else:
                            rows[ident] = record
            else:
                wb = load_workbook(path, read_only=True, data_only=True)
                for ws in wb.worksheets:
                    for rn, vals in enumerate(ws.iter_rows(values_only=True), 1):
                        vals = list(vals)
                        text = ' | '.join(str(v).strip() for v in vals if v is not None and str(v).strip())
                        if not text or not (re.search(r'\b[A-Z][A-Z0-9_]{4,}\b', text) or any(w in text.lower() for w in metric_words)):
                            continue
                        keys = re.findall(r'\b[A-Z][A-Z0-9_]{4,}\b', text)[:10]
                        ident = tuple(str(x).strip().lower() for x in vals)
                        record = [str(path.relative_to(ROOT)), ws.title, rn, ', '.join(keys), text[:2200]]
                        if ident in rows:
                            rows[ident][0] += f'; {record[0]}:{record[1]}:{record[2]}'
                        else:
                            rows[ident] = record
        except Exception:
            continue
    return list(rows.values())


def main():
    src = load_workbook(SOURCE, read_only=True, data_only=True)
    index = build_hits()
    records = collect(src['Entry'], 'Entry') + collect(src['Exit'], 'Exit')
    wb = Workbook(); wb.remove(wb.active)
    headers = ['Area', 'Path / main switch', 'Sub-setting', 'Parameter type', 'Values', 'Scope', 'Exact code trace', 'Detailed logic explanation', 'Non-bool/non-numeric meaning', 'Original matrix note']
    sheets = {name: wb.create_sheet(name) for name in ('Entry Paths', 'Exit Paths', 'Filters')}
    for ws in sheets.values(): ws.append(headers)
    for area, main, sub, values, desc in records:
        key = sub or main
        k = kind(values)
        found = find_hits(key, index)
        trace = ' || '.join(f'{f}:{n} ({fn})' for f, n, fn, _ in found) or 'No direct engine hit found'
        scope = 'per-symbol' if 'per-symbol' in (desc or '').lower() else ('global-only' if 'global-only' in (desc or '').lower() else 'not stated')
        note = '' if k in ('boolean', 'numeric', 'unspecified') else f'Categorical value(s): {", ".join(map(str, values[:12]))}. Exact label selects the branch; it is not a numeric threshold.'
        target = sheets[classify(area, desc, main, sub)]
        target.append([area, main, sub, k, ', '.join(map(str, values[:20])), scope, trace, explain(area, key, sub, values, desc, found), note, desc])
    for ws in sheets.values(): style(ws)
    test_ws = wb.create_sheet('Test Results Consolidated')
    test_ws.append(['Source file', 'Sheet / type', 'Source row', 'Extracted keys', 'Unique result/evidence row'])
    test_rows = result_rows()
    for row in test_rows:
        test_ws.append(row)
    style(test_ws)
    for i, width in enumerate([58, 32, 12, 48, 180], 1):
        test_ws.column_dimensions[get_column_letter(i)].width = width
    OUTPUT.parent.mkdir(parents=True, exist_ok=True); wb.save(OUTPUT)
    print(f'Wrote {OUTPUT}; records={len(records)}; unique test/result rows={len(test_rows)}')


if __name__ == '__main__': main()
