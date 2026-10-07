"""MU_LONG 19-vs-61 gap: _unw_wtdc_tf frame-context fix (2026-10-06).

_cfg_auto resolves (account,symbol,side) by inspecting its CALLER's frame.
Passed bare through the _unw_wtdc_tf wrapper, it sees the wrapper's frame
(no context) and silently falls back to the global default: live ran
WT_DC_TF_ENTRY=1h/thr45 while per_sym+cat_side intend 15m/thr35 (proven by
the S1 MU_LONG trace: tf_entry=1h vs store 15m). Callers must pass a
context-closing getter. Repaired per user order 2026-10-05.
"""
import inspect
from pathlib import Path

import tradier_manage as TM

ROOT = Path(__file__).resolve().parents[1]


def _frame_getter_like_cfg_auto(param, default=None):
    """Mimics _cfg_auto's frame-inspection mechanism (no store I/O)."""
    loc = inspect.currentframe().f_back.f_locals
    if loc.get("symbol") == "MU" and loc.get("position_side") == "LONG":
        return {"WT_DC_TF_ENTRY": "15m"}.get(param, default)
    return "GLOBAL-%s" % default


def test_bare_frame_getter_loses_context_through_wrapper():
    # Documents the trap: called from a context-rich frame, the bare getter
    # still falls back — _unw's frame is what it sees.
    account_key, symbol, position_side = "trb", "MU", "LONG"
    assert (account_key, symbol, position_side) == ("trb", "MU", "LONG")
    got = TM._unw_wtdc_tf(_frame_getter_like_cfg_auto, "WT_DC_TF_ENTRY", "1h")
    assert got == "GLOBAL-1h"


def test_context_closing_getter_keeps_per_sym_value():
    # The repaired call form: closure carries context, wrapper cannot lose it.
    account_key, symbol, position_side = "trb", "MU", "LONG"

    def _closing_getter(k, d=None):
        assert (account_key, symbol, position_side) == ("trb", "MU", "LONG")
        return {"WT_DC_TF_ENTRY": "15m"}.get(k, d)

    got = TM._unw_wtdc_tf(_closing_getter, "WT_DC_TF_ENTRY", "1h")
    assert got == "15m"


def test_no_bare_cfg_auto_unw_calls_remain():
    src = (ROOT / "tradier_manage.py").read_text()
    assert "_unw_wtdc_tf(_cfg_auto" not in src
    assert src.count("_unw_wtdc_tf(lambda k, d=None: _cfg(k, d, account_key, symbol, position_side),") == 4
