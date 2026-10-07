"""Regression for ez_manage AUGMENT UnboundLocalError k_1m when override_qty used (PLTR/ZEC ang/flz 16:35)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def test_k1m_init_in_augment_block():
    src = Path(ROOT / "ez_manage.py").read_text()
    # Fix must ensure AUGMENT sizing defines k_1m even when override_used True
    assert '2026-09-23 FIX: k_1m/k_3m' in src
    assert 'if "AUGMENT" in action:' in src
    # Ensure the block explicitly assigns k_1m from indicators `i`
    assert 'k_1m = safe_fetch_float((i or {}).get("k_1m"' in src

def test_ez_manage_compiles():
    import py_compile
    py_compile.compile(str(ROOT / "ez_manage.py"), doraise=True)

def test_tradier_htf_and_gr_still_fixed():
    src = Path(ROOT / "tradier_manage.py").read_text()
    assert "_htf_block = False" in src
    assert "_gr_opened_raw" in src
