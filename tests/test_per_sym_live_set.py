#!/usr/bin/env python3
"""per_sym_live_set — the single per-sym reader layer. Pins the three things that can silently rot:

  1. the sizing-cap table must stay identical to ez_manage.CRYPTO_MIN_SIZE_CAPS (it is duplicated as a
     literal so this module never imports ez_manage — parsed here with ast, never executed);
  2. the caps must actually clamp a live crypto board, and must never touch a stock sym_side;
  3. PER_SYM_FULL_SET_READER=0 must switch the module off, so every caller falls back to its legacy
     JSON block without a code change.
"""
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import per_sym_live_set as pls


def _ez_manage_caps() -> dict:
    """ez_manage.CRYPTO_MIN_SIZE_CAPS read from source with ast — no import, no side effects."""
    tree = ast.parse((ROOT / "ez_manage.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CRYPTO_MIN_SIZE_CAPS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("CRYPTO_MIN_SIZE_CAPS not found in ez_manage.py")


def test_cap_table_matches_ez_manage():
    assert pls.CRYPTO_MIN_SIZE_CAPS == _ez_manage_caps()


def test_reader_kill_switch():
    os.environ["PER_SYM_FULL_SET_READER"] = "0"
    try:
        assert pls.enabled() is False
        assert pls.full_set("BTCUSDT_LONG") is None
    finally:
        os.environ.pop("PER_SYM_FULL_SET_READER", None)
    assert pls.enabled() is True


def test_min_mode_fail_safe():
    # a broken config read must APPLY the caps, never skip them
    assert pls._min_mode_on() in (True, False)
    saved = sys.modules.pop("config", None)
    sys.modules["config"] = object()  # getattr on a bare object -> no attribute -> False, not a crash
    try:
        broken = {"START_POSITION_SIZE": 999.0}
        sys.modules["config"] = None  # import config -> raises -> fail-safe branch
        assert pls._apply_caps("BTCUSDT_LONG", broken)["START_POSITION_SIZE"] == 6.0
    finally:
        sys.modules.pop("config", None)
        if saved is not None:
            sys.modules["config"] = saved
    pls._cap_memo.clear()


def test_caps_clamp_crypto_and_spare_stocks():
    raw = {k: 10_000.0 for k in pls.CRYPTO_MIN_SIZE_CAPS}
    raw["ENTRY_SCORE_THRESHOLD"] = 7
    capped = pls._apply_caps("BTCUSDT_LONG", raw)
    for knob, cap in pls.CRYPTO_MIN_SIZE_CAPS.items():
        assert capped[knob] == cap, knob
    assert capped["ENTRY_SCORE_THRESHOLD"] == 7, "non-sizing keys must pass through untouched"
    pls._cap_memo.clear()
    assert pls._apply_caps("AAPL_LONG", raw) is raw, "stock sym_sides are not crypto-capped"
    pls._cap_memo.clear()


def test_caps_never_raise_a_lower_value():
    raw = {"START_POSITION_SIZE": 1.5, "MAX_POSITION_SIZE": 2.0}
    assert pls._apply_caps("BTCUSDT_LONG", raw) is raw
    pls._cap_memo.clear()


def test_live_board_is_served_and_capped():
    """End-to-end on the real store: non-sizing keys come from the board, sizing keys come back capped."""
    import per_sym_store as pss

    board = pss.get_overrides("BTCUSDT_LONG")
    if not board:
        return  # no live row on this host (workers have no per_sym_active) — nothing to assert
    got = pls.full_set("BTCUSDT_LONG")
    assert got is not None
    for knob, cap in pls.CRYPTO_MIN_SIZE_CAPS.items():
        if knob in got:
            assert float(got[knob]) <= cap, knob
    off = [
        k
        for k, v in board.items()
        if k not in pls.CRYPTO_MIN_SIZE_CAPS and got.get(k) != v
    ]
    assert not off, f"full_set must equal the board outside the capped knobs: {off[:10]}"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
