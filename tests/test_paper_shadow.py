"""Ledger collateral for tools/paper_shadow.py (USER 2026-10-20).

The unified ledger must score every paper decision against the 15m vec reference honestly:
timing shadows in bps with the right sign, no-data cases pending rather than zero.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.paper_shadow as ps  # noqa: E402


def test_split_sym_side_venue_and_side():
    assert ps.split_sym_side("BTCUSDC_LONG") == ("crypto", "BTCUSDC", "LONG")
    assert ps.split_sym_side("AAPL_SHORT") == ("stocks", "AAPL", "SHORT")
    assert ps.split_sym_side("garbage") == ("stocks", "garbage", "")


def test_entry_edge_sign_long_and_short():
    e = ps.entry_edge_bps("LONG", 100.0, 99.0, 110.0)  # shadow bought 1% cheaper, same exit
    assert e is not None and e > 0
    e2 = ps.entry_edge_bps("SHORT", 100.0, 101.0, 90.0)  # shadow shorted 1% higher, same exit
    assert e2 is not None and e2 > 0
    e3 = ps.entry_edge_bps("LONG", 100.0, 101.0, 110.0)  # shadow paid more
    assert e3 is not None and e3 < 0


def test_exit_edge_sign():
    assert ps.exit_edge_bps("LONG", 100.0, 102.0) > 0  # shadow sold higher
    assert ps.exit_edge_bps("SHORT", 100.0, 98.0) > 0  # shadow covered lower
    assert ps.exit_edge_bps("LONG", 100.0, 98.0) < 0
    assert ps.exit_edge_bps("LONG", 100.0, None) is None
    assert ps.entry_edge_bps("LONG", 100.0, 99.0, None) is None  # horizon not closed -> pending, never 0


def test_earliest_in_span_uses_monkeypatched_cache(monkeypatch):
    monkeypatch.setitem(ps._TFCL, ("crypto", "TST", "3m"),
                        ([100.0, 180.0, 400.0, 900.0], [1.0, 1.1, 1.2, 1.3]))
    assert ps.earliest_in_span("crypto", "TST", "3m", 0.0, 900.0) == (100.0, 1.0)
    assert ps.earliest_in_span("crypto", "TST", "3m", 200.0, 900.0) == (400.0, 1.2)
    assert ps.earliest_in_span("crypto", "TST", "3m", 950.0, 1800.0) is None
    assert ps.earliest_in_span("crypto", "NOSUCH", "3m", 0.0, 900.0) is None


def test_glitch_guard_filters_decimal_shifts():
    assert ps.is_glitch(30.55, 92.35) is True  # measured ZCSH 5m corruption
    assert ps.is_glitch(100.0, 101.5) is False
    assert ps.is_glitch(100.0, 80.0) is False  # boundary: exactly 20% is real
    assert ps.is_glitch(100.0, 79.9) is True
    assert ps.is_glitch(0.0, 1.0) is True
    assert ps.is_glitch(None, 1.0) is True


def test_verdict_thresholds_mirror_eval():
    def row(leg, sym, edge, ts):
        return {"leg": leg, "sym_side": sym, "edge_bps": edge, "ts": ts, "pending": False}
    rows = [row("shadow_3m", f"S{i}_LONG", 5.0, 1000.0 + i * 86400) for i in range(6)]
    vd = ps.verdict(rows, 7, 5, 14)
    assert vd["shadow_3m"]["verdict"] == "WATCH"  # span only 5d < 7
    rows = [row("shadow_3m", f"S{i}_LONG", 5.0, 1000.0 + i * 2 * 86400) for i in range(6)]
    vd = ps.verdict(rows, 7, 5, 14)
    assert vd["shadow_3m"]["verdict"] == "FASTER_CANDIDATE"
    rows = [row("nonvec:FN_X", f"S{i}_LONG", -5.0, 1000.0 + i * 2 * 86400) for i in range(6)]
    vd = ps.verdict(rows, 7, 5, 14)
    assert vd["nonvec:FN_X"]["verdict"] == "REJECT"
    pend = [dict(r, pending=True) for r in rows]
    assert ps.verdict(pend, 7, 5, 14) == {}  # pending rows never vote
