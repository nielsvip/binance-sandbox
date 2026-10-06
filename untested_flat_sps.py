"""Untested-symbol flat-SPS guard (USER 2026-10-06).

A sym_side with NO completed 30d pilot matrix has no proven edge, so it must
not touch the sizing multiplier stack (sentiment/LS/ladder/DC/slope adds).
It trades FLAT START_POSITION_SIZE only. Tested = a file named
{SYM}_{SIDE}[_...]_30d_matrix.xlsx in SPREADSHEEDS/V15_V16_CELL_BY_CELL/.
New matrices graduate automatically (cache is per-process; restarts reload).

Zero dependencies: safe to import from live managers and unit tests.
"""
import os

_MATRIX_SUFFIX = "_30d_matrix.xlsx"
_MATRIX_SUBDIR = os.path.join("SPREADSHEETS", "V15_V16_CELL_BY_CELL")
_CACHE = {}


def parse_matrix_sym_side(filename):
    """'EDUUSDT_LONG_bh1p0_gain2p0_30d_matrix.xlsx' -> ('EDUUSDT', 'LONG'). None when unparseable."""
    base = os.path.basename(filename)
    if not base.endswith(_MATRIX_SUFFIX):
        return None
    rest = base[: -len(_MATRIX_SUFFIX)]
    parts = rest.split("_")
    if len(parts) < 2 or parts[1] not in ("LONG", "SHORT"):
        return None
    return (parts[0], parts[1])


def _scan(root):
    found = set()
    d = os.path.join(root, _MATRIX_SUBDIR)
    try:
        names = os.listdir(d)
    except OSError:
        return found
    for n in names:
        p = parse_matrix_sym_side(n)
        if p:
            found.add(p)
    return found


def is_backtested(symbol, side, root=None):
    """True when sym_side has a completed 30d matrix (result cached per root)."""
    if root is None:
        root = os.path.dirname(os.path.abspath(__file__))
    if root not in _CACHE:
        _CACHE[root] = _scan(root)
    return (str(symbol).upper(), str(side).upper()) in _CACHE[root]


def flat_sps_quantity(sps_usd, price):
    """Flat-SPS order quantity (0 when inputs invalid)."""
    try:
        sps_usd = float(sps_usd)
        price = float(price)
    except (TypeError, ValueError):
        return 0.0
    if sps_usd <= 0 or price <= 0:
        return 0.0
    return sps_usd / price
