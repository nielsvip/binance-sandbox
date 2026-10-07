"""template_row_guard — ONE rule for every script that re-orders TEMPLATE_*.xlsx rows (USER 2026-10-01): a cell may NEVER move without its
ENTIRE row. fingerprints() hashes every row over ALL columns (value + fill colour); assert_rows_intact(before, after, skip_cols) compares
the multiset of row fingerprints (columns the script intentionally rewrites are skipped) and raises on any row whose cells were split,
partially moved, or altered. Use it in every writer after re-ordering and before saving."""
import collections
import hashlib

HDR = 2


def _cell_sig(cell):
    f = cell.fill
    rgb = str(getattr(f.fgColor, "rgb", "") or "") if f is not None and f.fill_type == "solid" else ""
    return f"{cell.value!r}|{rgb}"


def fingerprints(ws, skip_cols=frozenset(), hdr=HDR):
    c = collections.Counter()
    for r in range(hdr + 1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value in (None, ""):
            continue
        h = hashlib.md5("\x1f".join(_cell_sig(ws.cell(row=r, column=col)) for col in range(1, ws.max_column + 1) if col not in skip_cols).encode()).hexdigest()
        c[h] += 1
    return c


def assert_rows_intact(before_ws, after_ws, skip_cols=frozenset()):
    a, b = fingerprints(before_ws, skip_cols), fingerprints(after_ws, skip_cols)
    if a != b:
        lost, gained = sum((a - b).values()), sum((b - a).values())
        raise AssertionError(f"{after_ws.title}: row integrity broken — {lost} original rows changed/split, {gained} new row images (a cell moved without its row)")
    return True
