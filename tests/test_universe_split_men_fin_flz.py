"""Universe split: men/fin directional books, flz unconditional (USER 2026-10-21).

men/fin must trade outlier-vs-BTC directional books (SYMBOLS_*_LONG -> LONG,
SYMBOLS_*_SHORT -> SHORT), never the whole universe both sides. flz trades its
curated universe both sides unconditionally (no w20/l20 narrowing).
"""

import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import v15_outlier_universe as OU  # noqa: E402

SRC = pathlib.Path("ez_positions_service.py").read_text()


def test_cleanup_loads_split_books():
    assert "load_set('SYMBOLS_MEN_LONG')" in SRC
    assert "load_set('SYMBOLS_MEN_SHORT')" in SRC
    assert "load_set('SYMBOLS_FIN_LONG')" in SRC
    assert "load_set('SYMBOLS_FIN_SHORT')" in SRC


def test_cleanup_no_whole_universe_gather():
    assert "load_set('SYMBOLS_MEN'), load_set('SYMBOLS_FIN')" not in SRC


def test_men_fin_directional_loops():
    assert "for s in men_l:" in SRC
    assert "for s in men_s:" in SRC
    assert "for s in fin_l:" in SRC
    assert "for s in fin_s:" in SRC
    assert "for s in men:" not in SRC
    assert "for s in fin:" not in SRC


def test_flz_unconditional_no_narrowing():
    assert "_flz_winner" not in SRC
    assert "_flz_loser" not in SRC


def test_account_attr_map_split():
    assert "'men': ['symbols_men_long', 'symbols_men_short']" in SRC
    assert "'fin': ['symbols_fin_long', 'symbols_fin_short']" in SRC


def test_pick_side_exclusive_and_sized():
    rows = [{"symbol": f"S{i:02d}", "z": float(10 - i)} for i in range(40)]
    longs, shorts = OU.pick(rows, 25)
    assert longs == [f"S{i:02d}" for i in range(15)]
    assert shorts == [f"S{i:02d}" for i in range(39, 14, -1)]
    assert not (set(longs) & set(shorts))
    longs, shorts = OU.pick(rows, 10)
    assert longs == [f"S{i:02d}" for i in range(10)]
    assert shorts == [f"S{i:02d}" for i in range(39, 29, -1)]
    assert not (set(longs) & set(shorts))


def test_pick_clash_resolves_to_stronger_side():
    rows = [
        {"symbol": "A", "z": 3.0},
        {"symbol": "B", "z": -3.0},
        {"symbol": "C", "z": 0.1},
    ]
    longs, shorts = OU.pick(rows, 5)
    assert "A" in longs and "A" not in shorts
    assert "B" in shorts and "B" not in longs


def test_sma_math():
    assert OU.sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5
