"""Pin: ang long/short universe stays ~25 symbols (USER 2026-10-15).

Static pin (ez_rankings is a live loop; executing it here is infeasible):
- In-memory ang lists are capped/top-upped at 25, not 20.
- save_market_data tops the tradeable-filtered ang lists back up to 25
  from the ranked pools (tradeable first, best-ranked fill after).
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ez_rankings.py"


def test_ang_caps_are_25():
    text = SRC.read_text()
    assert "list(dict.fromkeys(symbols_ang_long_list))[:25]" in text
    assert "list(dict.fromkeys(symbols_ang_short_list))[:25]" in text
    assert "if len(symbols_ang_long_list) < 25:" in text
    assert "if len(symbols_ang_short_list) < 25:" in text


def test_post_filter_topup_to_25():
    text = SRC.read_text()
    assert "if len(symbols_ang_long_list) >= 25: break" in text
    assert "if len(symbols_ang_short_list) >= 25: break" in text
    assert "_pool_long = " in text and "_pool_short = " in text
