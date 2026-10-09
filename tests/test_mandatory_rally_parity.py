"""Mandatory(rally) parity — live imitates vec HARDCODED_RALLY_REENTRY (USER 2026-10-09).

Vec truth (v12 simulate_one): flat + has-closed + strict `px > last_exit`
(+optional WT when REQUIRE_WT, + _hrf_ok filters), passing RTH/DG/CTB/OBUF/
W2-GR/W2-DC4/BT + second-chain killers; bypassing entry_sig ANDs + cooldown.
Augment/reduce paths must be untouched by the alignment (user directive).
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import tradier_manage as tm


def test_favorable_px_strict_cross():
    f = tm._mandatory_favorable_px
    assert f(True, 101.0, 100.0) is True
    assert f(True, 100.0, 100.0) is False
    assert f(True, 99.0, 100.0) is False
    assert f(False, 99.0, 100.0) is True
    assert f(False, 100.0, 100.0) is False
    assert f(False, 101.0, 100.0) is False
    assert f(True, None, 100.0) is False
    assert f(True, "bad", 100.0) is False


def test_favorable_px_matches_vec_grid():
    f = tm._mandatory_favorable_px
    for px in (99.99, 100.0, 100.01):
        assert f(True, px, 100.0) == (px > 100.0)
        assert f(False, px, 100.0) == (px < 100.0)


def test_flat_trigger_uses_helper_exit_path_untouched():
    t = Path("tradier_manage.py").read_text()
    assert t.count("_mandatory_favorable_px(is_long, current_price, _xb_last_px)") == 1
    assert t.count("current_price >= _xb_last_px") == 1


def test_opposition_hold_removed_obuf_bound():
    t = Path("tradier_manage.py").read_text()
    assert "MANDATORY_REENTRY_PENDING_OPPOSITION" not in t
    assert "STOCKS_OPENING_BUFFER_ENTRY_ENABLED" in t


def test_dg_binds_mandatory_flat_only():
    t = Path("tradier_manage.py").read_text()
    assert "Augments on existing open positions are NOT gated" in t
    assert "_dg_is_flat_open" in t
    assert "not _is_exit_or_reduce" in t
