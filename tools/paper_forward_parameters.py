"""Resolve paper-scenario values only from the four active FINAL_NORM workbooks."""
from __future__ import annotations

import hashlib
from pathlib import Path


TEMPLATE_NAMES = {
    ("crypto", "LONG"): "TEMPLATE_CRYPTO_LONG.xlsx",
    ("crypto", "SHORT"): "TEMPLATE_CRYPTO_SHORT.xlsx",
    ("stocks", "LONG"): "TEMPLATE_STOCKS_LONG.xlsx",
    ("stocks", "SHORT"): "TEMPLATE_STOCKS_SHORT.xlsx",
}


def _cell_value(value):
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    text = str(value).strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    return text


def workbook_candidates(path):
    """Return exact values present in switch rows, preserving workbook cell types."""
    import openpyxl

    path = Path(path)
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    values = {}
    try:
        skip = {"PROTOCOL", "LEGEND_FILTERS", "INSTRUCTIONS", "INSTRUCTIONS_V2", "FILTERS_EXPLAINED", "FILTER_DICTIONARY_V2", "Results_Deltas", "Results_30d_Deltas"}
        for sheet in workbook.worksheets:
            if sheet.title in skip or "BASELINE" in sheet.title.upper():
                continue
            for row in sheet.iter_rows(min_row=3, max_col=2):
                if not row or not isinstance(row[0].value, str):
                    continue
                font = row[0].font
                if font is not None and font.color is not None and str(getattr(font.color, "rgb", "") or "").upper() == "FFBFBFBF":
                    continue
                switch = row[0].value.strip().upper()
                if not switch or row[1].value is None:
                    continue
                candidate = _cell_value(row[1].value)
                bucket = values.setdefault(switch, [])
                if not any(type(old) is type(candidate) and old == candidate for old in bucket):
                    bucket.append(candidate)
    finally:
        workbook.close()
    return values, file_hash


def resolve_catalog(catalog, template_dir):
    """Annotate nonboolean/unresolved catalog rows; absent workbook rows stay unresolved."""
    template_dir = Path(template_dir)
    cache = {}
    resolved = []
    for item in catalog.get("scenarios", []):
        copy = dict(item)
        if copy.get("kind") != "baseline" and copy.get("default_kind") != "boolean" and copy.get("switch"):
            key = (copy["venue"], copy["side"])
            if key not in cache:
                filename = TEMPLATE_NAMES.get(key)
                cache[key] = workbook_candidates(template_dir / filename) if filename else ({}, None)
            values, source_hash = cache[key]
            candidates = values.get(str(copy["switch"]).upper(), [])
            copy["candidate_values"] = list(candidates)
            copy["candidate_value_types"] = sorted({type(value).__name__ for value in candidates})
            copy["candidate_source"] = "TEMPLATE_FINAL_NORM" if candidates else None
            copy["candidate_source_xlsx_sha256"] = source_hash
        resolved.append(copy)
    output = dict(catalog)
    output["scenarios"] = resolved
    return output
