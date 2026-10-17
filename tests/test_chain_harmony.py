"""USER 2026-10-16: "duplicate contradictory fields in TEMPLATE_ or v12_quick_engine should be impossible"."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_quickconfig_has_no_duplicate_field():
    import v12_quick_engine as E

    assert E._quickconfig_duplicate_fields() == []


def test_templates_have_no_duplicate_or_contradictory_switch():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "chain_harmony_check.py")], capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
