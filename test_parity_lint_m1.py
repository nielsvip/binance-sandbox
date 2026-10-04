"""Focused test: parity lint M1 mask loader handles frozenset(...) Call nodes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.parity_lint_promotion import _load_ast_set


def test_m1_loads_frozenset_call():
    mask, err = _load_ast_set(Path("ez_manage.py"), "_NON_VEC_KNOBS_EZ")
    assert err == "", err
    assert isinstance(mask, set) and len(mask) > 10
    assert "BREAKOUT_LEASH_ENABLED" in mask


def test_m1_missing_name():
    mask, err = _load_ast_set(Path("ez_manage.py"), "_NO_SUCH_MASK_XYZ")
    assert mask is None and "not found" in err
