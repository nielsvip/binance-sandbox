"""2026-10-17 loss-prone entry functions: DB flags -> vector entry filter, PEAK_GIVEBACK twin (pilot-flipped), newborn exemption both venues."""
import re
from pathlib import Path

import loss_prone
import vec_decisions.breakeven_lock as bel
import vec_decisions.check_exit_candidates_crypto__peak_giveback as pgb

ROOT = Path(__file__).resolve().parents[1]


class _C:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_fn_family_normalises_live_and_board_reasons():
    assert loss_prone.fn_family("REENTRY_HIGHER_LOW |VEC_EXACT") == "REENTRY_HIGHER_LOW"
    assert loss_prone.fn_family("VEC_EXACT_OPEN_B12|RATIO_CUT_S(R=0.30)") == "B12"
    assert loss_prone.fn_family("VEC_EXACT_OPEN_B_SRS_ENTRY") == "B_SRS_ENTRY"
    assert loss_prone.fn_family("FIXED_STOP_close1.2") == "FIXED_STOP"
    assert loss_prone.fn_family("") == "UNKNOWN"


def test_scope_covers():
    flags = frozenset({"B12"})
    assert bel.scope_covers(_C(PEAK_GIVEBACK_SCOPE="all"), "X", flags)
    assert bel.scope_covers(_C(), "X", flags)
    assert bel.scope_covers(_C(PEAK_GIVEBACK_SCOPE="loss_prone"), "B12", flags)
    assert not bel.scope_covers(_C(PEAK_GIVEBACK_SCOPE="loss_prone"), "X", flags)


def test_entry_filters_pass_counts_side_aligned_filters():
    cfg = _C(LOSS_PRONE_ENTRY_MIN_FILTERS=2)
    assert bel.entry_filters_pass(cfg, True, 101.0, 5, 1, 5, 1, 100.0)
    assert not bel.entry_filters_pass(cfg, True, 99.0, 5, 1, -5, 1, 100.0)
    assert bel.entry_filters_pass(cfg, False, 99.0, -5, 1, -5, 1, 100.0)
    assert not bel.entry_filters_pass(cfg, True, 101.0, float("nan"), 1, 0, 0, 0)  # missing data = not passed


def test_peak_giveback_core_matches_live_semantics():
    assert pgb._peak_giveback_fires(0.6, 0.05, 0.5, True, 0.08, False, 1.0)
    assert not pgb._peak_giveback_fires(0.4, -1.0, 0.5, True, 0.08, False, 1.0)
    assert not pgb._peak_giveback_fires(0.6, 0.05, 0.5, False, 0.08, False, 1.0)


def test_engine_runs_the_shared_peak_giveback_core_and_has_no_duplicate_lock():
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "check_exit_candidates_crypto__peak_giveback._peak_giveback_fires(" in src
    assert "PEAK_GIVEBACK_GAIN_EROSION_STOP_peak" in src
    assert "BREAKEVEN_LOCK_SCOPE" not in src and "BREAKEVEN_LOCK_ARM_PCT" not in src
    assert "_belm.entry_filters_pass(" in src
    import v12_quick_engine as E

    q = E.QuickConfig()
    assert q.PEAK_GIVEBACK_SCOPE == "all" and q.LOSS_PRONE_ENTRY_FILTER_ENABLED is True and q.LOSS_PRONE_ENTRY_MIN_FILTERS == 2
    assert not E._QC_DUPS


def test_newborn_protection_both_venues_with_loss_prone_exemption():
    for f, venue in (("ez_manage.py", '"crypto"'), ("tradier_manage.py", '"stocks"')):
        src = (ROOT / f).read_text()
        assert "BLOCKED_NEWBORN_PROTECT_" in src, f
        assert re.search(r"position_is_loss_prone\(account_key, position_key, %s" % venue, src), f
        assert 'getattr(config, "NEWBORN_PROTECT_LOSS_PRONE_EXEMPT", True)' in src, f
        for tag in ("NEWBORN_LOSS_KILL", "FIXED_STOP", "HTF_AGAINST_FORCE_CLOSE", "RIDICULOUS_LOSS"):
            assert tag in src, (f, tag)


def test_newborn_exempt_knob_is_live_authority_only():
    import per_sym_store

    assert "NEWBORN_PROTECT_LOSS_PRONE_EXEMPT" in per_sym_store.LIVE_AUTHORITY_KNOBS
