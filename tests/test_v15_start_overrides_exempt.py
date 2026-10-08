"""V15_START_OVERRIDES (BIBLE §58 step 5) must survive V15_TEMPLATE_DEFAULTS=1 (2026-10-08 system audit)."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "v15_pilot.py").read_text()


def test_template_defaults_block_exempts_start_overrides():
    m = re.search(r'^\s*if os\.environ\.get\("V15_TEMPLATE_DEFAULTS", "0"\) == "1"([^\n]*):\s*$', SRC, re.M)
    assert m, "TEMPLATE_DEFAULTS block not found"
    assert 'V15_START_OVERRIDES' in m.group(1), "V15_TEMPLATE_DEFAULTS=1 would drop the START_OVERRIDES baseline"


def test_start_overrides_ingest_builds_on_template_defaults():
    assert 'overrides = {**_tpl_defaults, **_so}' in SRC
