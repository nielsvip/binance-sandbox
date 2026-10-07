"""Blocked-side positions must close (never hedge) as soon as wt1_15m is against.

Non-tradeable keys (e.g. inf:COTIUSDT_SHORT) correctly block OPEN, but an open
position on such a side must exit the moment wt1_15m disagrees. The hedge-first
path can never fire there (hedge = open-action = NON_TRADEABLE_HARD_BLOCK),
so without a direct-close branch the position sits adverse forever.
"""
import ez_manage as em


def test_blocked_side_detects_missing_key_only_when_set_loaded():
    tk = {"ang:MANAUSDT_LONG", "men:SANDUSDT_LONG"}
    assert em._wt15m_blocked_side(tk, "inf:COTIUSDT_SHORT") is True
    assert em._wt15m_blocked_side(tk, "ang:MANAUSDT_LONG") is False
    assert em._wt15m_blocked_side(set(), "inf:COTIUSDT_SHORT") is False
    assert em._wt15m_blocked_side(None, "inf:COTIUSDT_SHORT") is False
    assert em._wt15m_blocked_side(tk, None) is False
    assert em._wt15m_blocked_side(tk, "") is False


def test_wt15m_block_wires_direct_close_for_blocked_side():
    import pathlib

    src = pathlib.Path("ez_manage.py").read_text()
    assert "_w15_blocked_side = _wt15m_blocked_side(" in src
    assert "if not _w15_active and not _w15_blocked_side:" in src
    assert "if _w15_active or _w15_blocked_side:" in src
    assert "_BLOCKED_SIDE" in src
    assert "WT15M_AGAINST_FORCE_CLOSE" in src
