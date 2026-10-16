"""test_v12_overlay_twins — no value-identical tradier-overlay twins; MAX_AUGMENTS no-cap mandate.

Source-level (no engine import): parses QuickConfig field defaults vs
apply_tradier_defaults() assigns in v12_quick_engine.py, and the
the deleted MAX_AUGMENTS_PER_POSITION global (removed 2026-10-15; see tests/test_maxaug_deleted.py).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V12 = ROOT / "v12_quick_engine.py"
CFG = ROOT / "config.py"


def _overlay_region(lines):
    a0 = next(i for i, ln in enumerate(lines) if re.match(r"\s*def apply_tradier_defaults\(self\):", ln))
    a1 = len(lines)
    for i in range(a0 + 1, len(lines)):
        if lines[i].strip() == "":
            continue
        if not lines[i].startswith("        "):
            a1 = i
            break
    return (a0, a1)


def _base_and_overlay():
    lines = V12.read_text().splitlines()
    a0, a1 = _overlay_region(lines)
    base = {}
    for ln in lines[:a0] + lines[a1:]:
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*:[^=]+=\s*(.+?)(\s*#.*)?$", ln.strip())
        if m:
            base[m.group(1)] = m.group(2).strip().strip("'\"")
    ov = {}
    for ln in lines[a0:a1]:
        m = re.match(r"^\s*self\.([A-Z][A-Z0-9_]*)\s*=\s*(.+?)(\s*#.*)?$", ln.strip())
        if m:
            ov[m.group(1)] = m.group(2).strip().strip("'\"")
    return (base, ov)


def test_no_value_identical_overlay_twins():
    base, ov = _base_and_overlay()
    same = sorted(k for k, v in ov.items() if k in base and base[k] == v)
    assert same == [], f"value-identical overlay twins (delete the overlay line): {same}"


