"""sync-defaults paren guard (USER 2026-10-09): a multi-line parenthesized config default must go to needs_manual, never to a single-line rewrite (20261009 chain: GAP_MOC_FORCE_MOC_AT_CLOSE broke config_tradier.py with IndentationError, rc=1, step5b skipped)."""

import switch_parity as sp


def test_multiline_paren_detected():
    assert sp._multiline_paren_value("(") is True
    assert sp._multiline_paren_value("  (\n") is True
    assert sp._multiline_paren_value("(True,") is True


def test_single_line_values_pass():
    assert sp._multiline_paren_value("True") is False
    assert sp._multiline_paren_value("False") is False
    assert sp._multiline_paren_value("10") is False
    assert sp._multiline_paren_value("(True)") is False
    assert sp._multiline_paren_value("'15m'") is False
    assert sp._multiline_paren_value("") is False
    assert sp._multiline_paren_value(None) is False
