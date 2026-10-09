"""v15_pilot REDO precedence pins (USER 2026-10-09, AGLDUSDT_SHORT proof).

Static contract guards on the start-overrides precedence block: the fresh repaired set (needs_redo)
must ALWAYS win — never ignored under V15_START_OVERRIDES, never dropped by TEMPLATE_DEFAULTS.
Live proof happens on the fleet (REDO relaunch must log the repaired-set baseline); these pins stop
a future edit from silently re-introducing the drop. Reads the file, never imports the pilot.
"""
import re
from pathlib import Path

PILOT = Path(__file__).resolve().parents[1] / "v15_pilot.py"


def _src():
    return PILOT.read_text()


def test_template_defaults_exempts_redo_base():
    src = _src()
    conds = [
        m.group(1)
        for m in re.finditer(
            r'if os\.environ\.get\("V15_TEMPLATE_DEFAULTS", "0"\) == "1"(.*?):\n', src
        )
    ]
    assert conds, "TEMPLATE_DEFAULTS gate not found"
    assert any(
        "V15_START_OVERRIDES" in c and "_nr_applied" in c for c in conds
    ), conds


def test_needs_redo_not_gated_on_start_overrides_absence():
    src = _src()
    assert 'if not os.environ.get("V15_START_OVERRIDES"):\n        # USER 2026-10-02' not in src
    i = src.find("_nr_applied = False")
    j = src.find("[REDO-START]", i)
    k = src.find("_nr_applied = True", j)
    assert 0 < i < j < k, "needs_redo ingest must set _nr_applied"


def test_redo_merge_order_template_then_repaired():
    src = _src()
    assert "overrides = {**_tpl_defaults, **_so2}" in src
