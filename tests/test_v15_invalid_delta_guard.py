"""BIBLE §19: an INVALID engine result must never become a positive row delta (2026-10-08, MOM3_FILTER_TF phantom promotion)."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "v15_pilot.py").read_text()


def test_every_row_delta_site_is_guarded():
    sites = [m.start() for m in re.finditer(r"delta = vg - cumulative_before\n", SRC)]
    assert len(sites) >= 2, "row-delta sites not found"
    for pos in sites:
        window = SRC[pos: pos + 1200]
        assert "if delta > 0 and not vec.get('valid'):" in window and "delta = 0.0" in window, f"unguarded delta site at offset {pos}"
