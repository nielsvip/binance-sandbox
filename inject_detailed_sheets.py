from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import re

from openpyxl import load_workbook

ROOT = Path('/Users/niels/Documents/binance')
BASE = ROOT / 'data/reports/SWITCH_MATRIX_TRB.bloated_20260804.xlsx'
SHEETS = ROOT / 'outputs/019fcd2f-b9de-78d3-a50a-398223e8822c/detailed_logic_sheets.xlsx'
OUT = ROOT / 'outputs/019fcd2f-b9de-78d3-a50a-398223e8822c/SWITCH_MATRIX_TRB_COMPLETE.xlsx'

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
ET.register_namespace('', NS)
ET.register_namespace('r', RNS)


def cell_ref(col, row):
    out = ''
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return f'{out}{row}'


def sheet_xml(ws):
    root = ET.Element(f'{{{NS}}}worksheet')
    sd = ET.SubElement(root, f'{{{NS}}}sheetData')
    for rnum, row in enumerate(ws.iter_rows(values_only=True), 1):
        rr = ET.SubElement(sd, f'{{{NS}}}row', {'r': str(rnum)})
        for cnum, value in enumerate(row, 1):
            if value is None:
                continue
            c = ET.SubElement(rr, f'{{{NS}}}c', {'r': cell_ref(cnum, rnum), 't': 'inlineStr'})
            isel = ET.SubElement(c, f'{{{NS}}}is')
            t = ET.SubElement(isel, f'{{{NS}}}t')
            text = str(value)
            if text[:1].isspace() or text[-1:].isspace() or '\n' in text:
                t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            t.text = text
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def main():
    temp = Path(tempfile.mkdtemp(prefix='switch_inject_'))
    try:
        with zipfile.ZipFile(SHEETS) as z:
            swb = z.read('xl/workbook.xml')
            relb = z.read('xl/_rels/workbook.xml.rels')
            ctb = z.read('[Content_Types].xml')
        root = ET.fromstring(swb); relroot = ET.fromstring(relb); ctroot = ET.fromstring(ctb)
        sheets_el = root.find(f'{{{NS}}}sheets')
        existing_names = {e.get('name') for e in sheets_el}
        with zipfile.ZipFile(BASE) as zin:
            names = zin.namelist()
            sheet_nums = [int(m.group(1)) for n in names if (m := re.match(r'xl/worksheets/sheet(\d+)\.xml$', n))]
            next_sheet = max(sheet_nums) + 1
            rel_ids = [int(e.get('Id')[3:]) for e in relroot if e.get('Id', '').startswith('rId') and e.get('Id')[3:].isdigit()]
            next_rel = max(rel_ids) + 1
            for sheet_name in ('Entry Paths', 'Exit Paths', 'Filters', 'Test Results Consolidated'):
                if sheet_name in existing_names:
                    continue
                sheet_num = next_sheet; next_sheet += 1
                rid = f'rId{next_rel}'; next_rel += 1
                ET.SubElement(sheets_el, f'{{{NS}}}sheet', {'name': sheet_name, 'sheetId': str(sheet_num), f'{{{RNS}}}id': rid})
                ET.SubElement(relroot, f'{{{PKG}}}Relationship', {'Type': f'{RNS}/worksheet', 'Target': f'/xl/worksheets/sheet{sheet_num}.xml', 'Id': rid})
                ET.SubElement(ctroot, f'{{{CT}}}Override', {'PartName': f'/xl/worksheets/sheet{sheet_num}.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})

            # Write the modified small metadata and new sheets to a staging folder.
            (temp / 'xl/worksheets').mkdir(parents=True)
            (temp / 'xl/_rels').mkdir(parents=True)
            (temp / 'xl/workbook.xml').write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=True))
            (temp / 'xl/_rels/workbook.xml.rels').write_bytes(ET.tostring(relroot, encoding='utf-8', xml_declaration=True))
            (temp / '[Content_Types].xml').write_bytes(ET.tostring(ctroot, encoding='utf-8', xml_declaration=True))
            wb = load_workbook(SHEETS, read_only=True, data_only=True)
            for idx, sheet_name in enumerate(('Entry Paths', 'Exit Paths', 'Filters', 'Test Results Consolidated'), start=max(sheet_nums) + 1):
                (temp / f'xl/worksheets/sheet{idx}.xml').write_bytes(sheet_xml(wb[sheet_name]))
        shutil.copyfile(BASE, OUT)
        # Replace only the three small package metadata entries; all original worksheet XML stays byte-for-byte intact.
        subprocess.run(['zip', '-d', str(OUT), '[Content_Types].xml', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['zip', '-q', str(OUT), '[Content_Types].xml', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', 'xl/worksheets/sheet24.xml', 'xl/worksheets/sheet25.xml', 'xl/worksheets/sheet26.xml', 'xl/worksheets/sheet27.xml'], check=True, cwd=temp)
        print(f'Wrote {OUT}')
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == '__main__': main()
