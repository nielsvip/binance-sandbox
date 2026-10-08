"""Pin: ez_rankings INF book writes stay suspended while the v15 chain owns symbols_inf (USER 2026-10-08).

Static pin (ez_rankings is a live loop; executing it here is infeasible): the suspension
constant must be True and the guard must sit before every symbols_inf write in the file.
Flip INF_BEST_SAVE_SUSPENDED to False only on explicit user order to resume rankings writes.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ez_rankings.py"


def test_inf_best_save_suspended():
    text = SRC.read_text()
    assert "\nINF_BEST_SAVE_SUSPENDED = True" in text
    guard = text.index("raise _InfSaveSuspended()")
    for needle in ('BASE_PATH / "symbols_inf_long.json"', 'BASE_PATH / "symbols_inf_short.json"'):
        assert guard < text.index(needle), needle
    assert "except _InfSaveSuspended:" in text
