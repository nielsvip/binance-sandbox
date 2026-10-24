"""ENABLE_TR_SWING_ENTRIES knob mirror (USER 2026-10-24 template-governed entries).

The tradier live lane blocks every stocks OPEN on the swing-budget knob unless
a board carries True (tradier_manage.is_swing_entry_allowed ->
SWING_ENTRIES_OFF_PARITY). The vector must read the same knob through its own
path (vec_decisions.check_entry_swing_budget, ONE call site at VETO-FINAL) so
the TEMPLATE row earns real per-sym_side deltas in the daily chain instead of
agents flipping the code default.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Cfg:
    def __init__(self, mode="tradier", knob=None):
        self.MODE = mode
        if knob is not None:
            self.ENABLE_TR_SWING_ENTRIES = knob


def test_knob_allows_scalar():
    from vec_decisions import check_entry_swing_budget as S

    assert S.knob_allows_scalar(_Cfg("tradier", True)) is True
    assert S.knob_allows_scalar(_Cfg("tradier", False)) is False
    assert S.knob_allows_scalar(_Cfg("tradier", None)) is False
    assert S.knob_allows_scalar(_Cfg("crypto", False)) is True
    assert S.knob_allows_scalar(_Cfg("crypto", None)) is True


def test_mask_none_when_allows_zeros_when_blocked():
    import numpy as np
    from vec_decisions import check_entry_swing_budget as S

    assert S.entry_mask_vec(_Cfg("tradier", True), 7) is None
    assert S.entry_mask_vec(_Cfg("crypto", False), 7) is None
    m = S.entry_mask_vec(_Cfg("tradier", False), 7)
    assert isinstance(m, np.ndarray) and m.dtype == bool and not m.any()
    m2 = S.entry_mask_vec(_Cfg("tradier", None), 5)
    assert m2 is not None and not m2.any()


def test_ledger_flips_with_knob_ag_short():
    import v12_quick_engine as V
    from tools.dc_simple_8_sweep import _get_template_baseline

    stores = V.load_npz("tradier", ["AG"], "2024-01-01")
    npz = stores.get("AG")
    assert npz is not None and len(npz.get("close", [])) >= 100
    base = _get_template_baseline(False, False)
    out = {}
    for knob in (True, False):
        cfg = V.QuickConfig()
        cfg.apply_tradier_defaults()
        cfg.MODE = "tradier"
        for k, v in base.items():
            try:
                setattr(cfg, k, v)
            except Exception:
                pass
        cfg.ENABLE_TR_SWING_ENTRIES = knob
        r = V.simulate_one(npz, "AG", False, cfg)
        out[knob] = (r["trades"], r.get("gain_pct_2000norm"))
    assert out[True][0] > 0, out
    assert out[False][0] == 0, out
    assert out[True] != out[False], out


def test_single_call_site_and_matching_defaults():
    src = Path("v12_quick_engine.py").read_text()
    assert src.count("check_entry_swing_budget.entry_mask_vec") == 1
    assert "ENABLE_TR_SWING_ENTRIES: bool = False" in src
    tcfg = Path("config_tradier.py").read_text()
    assert "ENABLE_TR_SWING_ENTRIES: bool = (" in tcfg
    live = Path("tradier_manage.py").read_text()
    assert '"ENABLE_TR_SWING_ENTRIES"' in live
    assert "SWING_ENTRIES_OFF_PARITY" in live
