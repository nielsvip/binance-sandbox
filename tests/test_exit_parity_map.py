"""Exit-family parity registry test (USER 2026-10-24).

Every native live exit reason must map to BOTH a TEMPLATE_* switch path and a
vectorized v12_quick_engine equivalent. This test pins that contract using
tools/live_desk/exit_parity_map.json: if a family ever loses either side
(switch renamed, v12 leg removed), this goes red BEFORE live and forward can
silently diverge. Read-only: never touches engine, sheets, or v12.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = json.loads((ROOT / "tools" / "live_desk" / "exit_parity_map.json").read_text())


def test_every_family_has_template_switch():
    bible = (ROOT / "data" / "SWITCH_BIBLE.json").read_text()
    missing = [
        fam
        for fam, rec in MAP.items()
        if fam != "_note" and rec["template_switch"] not in bible
    ]
    assert not missing, f"families without TEMPLATE switch: {missing}"


def test_every_family_has_v12_equivalent():
    src = (ROOT / "v12_quick_engine.py").read_text()
    missing = [
        fam
        for fam, rec in MAP.items()
        if fam != "_note" and rec["v12_symbol"] not in src
    ]
    assert not missing, f"families without v12 equivalent: {missing}"


def test_map_covers_observed_friday_reasons():
    observed = [
        "DAYTRADE_TARGET_dc_",
        "TECHNICAL_TARGET_dc_",
        "UNCOND_DC_",
        "STRUCT_BREAK_DC_",
        "GREY_REWIRE_EXIT",
        "GAP_MOC_EXIT",
    ]
    prefixes = [rec["live_reason_prefix"] for fam, rec in MAP.items() if fam != "_note"]
    uncovered = [o for o in observed if not any(o.startswith(p) for p in prefixes)]
    assert not uncovered, f"Friday reasons without registry family: {uncovered}"
