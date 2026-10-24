"""Tradier parity-breach mirror (USER 2026-10-24 VEC-LAST: no silent vec refusal).

A vector decision that stocks-live REFUSES anywhere in execute_now — including
the pre-backstop structural gates the backstop never sees — must be named in
data/vector_parity_breaches.jsonl like the crypto twin. These tests pin the
predicate and the writer without touching the live ledger.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_vec_order_predicate():
    import tradier_manage as tr

    assert tr._tr_parity_vec_order("VEC_DRIVEN_OPEN_PURE_BOARD_X") is True
    assert tr._tr_parity_vec_order("anything |VEC_EXACT") is True
    assert tr._tr_parity_vec_order("BOARD_CONVERGE_FLAT_X") is True
    assert tr._tr_parity_vec_order("B_KZONE plain native") is False
    assert tr._tr_parity_vec_order("") is False


def test_breach_writer_names_gate(tmp_path, monkeypatch):
    import tradier_manage as tr

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(tr, "_cfg_auto", lambda k, d=None: str(tmp_path) if k == "BASE_PATH" else d)
    tr._parity_breach_record_tradier("trb:AAPL_LONG", "OPEN", "VEC_DRIVEN_OPEN_PURE_BOARD_X", "BLOCKED_SYM_HALT")
    rows = (tmp_path / "data" / "vector_parity_breaches.jsonl").read_text().strip().split("\n")
    assert len(rows) == 1
    d = json.loads(rows[0])
    assert d["venue"] == "stocks" and d["action"] == "OPEN"
    assert d["gate"] == "BLOCKED_SYM_HALT" and "VEC_DRIVEN" in d["reason"]


def test_breach_writer_no_pytest_guard_bypass(tmp_path, monkeypatch):
    import tradier_manage as tr

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    tr._parity_breach_record_tradier("trb:AAPL_LONG", "OPEN", "VEC_DRIVEN_X", "BLOCKED_SYM_HALT")
    assert not (tmp_path / "data" / "vector_parity_breaches.jsonl").exists()
