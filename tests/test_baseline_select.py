"""Baseline winner selection (USER 2026-10-11: EVERY test runs latest-best per_sym + TEMPLATE_CAT_SIDE
in parallel and starts from the best). Pure rule shared by v15 Layer-1 and the challenger-choose hook:
credible first, then gain, win-rate, -dd, sharpe; ties go to the incumbent (Layer-1: tie -> cat).
"""

from baseline_select import is_credible, pick_winner, score

FLOOR = 30
ULTRA = -50.0


def _v(gain, trades=100, valid=True, dd=10.0, sharpe=1.0, wr=None):
    v = {
        "gain_pct": gain,
        "trades": trades,
        "valid": valid,
        "max_dd_pct": dd,
        "pool_sharpe": sharpe,
    }
    if wr is not None:
        v["wr"] = wr
    return v


def test_credible_outranks_noncredible_despite_lower_gain():
    assert is_credible(_v(5.0), FLOOR, ULTRA) is True
    assert is_credible(_v(99.0, valid=False), FLOOR, ULTRA) is False
    assert is_credible(_v(99.0, trades=3), FLOOR, ULTRA) is False
    assert is_credible(_v(-60.0), FLOOR, ULTRA) is False
    assert pick_winner({"a": _v(99.0, valid=False), "b": _v(5.0)}, FLOOR, ULTRA) == "b"


def test_gain_then_wr_then_dd_then_sharpe_order():
    assert pick_winner({"a": _v(1.0), "b": _v(2.0)}, FLOOR, ULTRA) == "b"
    assert (
        pick_winner({"a": _v(2.0, wr=0.4), "b": _v(2.0, wr=0.6)}, FLOOR, ULTRA) == "b"
    )
    assert (
        pick_winner(
            {"a": _v(2.0, wr=0.5, dd=20.0), "b": _v(2.0, wr=0.5, dd=5.0)}, FLOOR, ULTRA
        )
        == "b"
    )
    assert (
        pick_winner(
            {
                "a": _v(2.0, wr=0.5, dd=5.0, sharpe=0.1),
                "b": _v(2.0, wr=0.5, dd=5.0, sharpe=0.9),
            },
            FLOOR,
            ULTRA,
        )
        == "b"
    )


def test_tie_goes_to_incumbent_else_first():
    assert (
        pick_winner({"cat": _v(2.0), "per": _v(2.0)}, FLOOR, ULTRA, incumbent="cat")
        == "cat"
    )
    assert (
        pick_winner({"per": _v(2.0), "cat": _v(2.0)}, FLOOR, ULTRA, incumbent="cat")
        == "cat"
    )
    assert pick_winner({"per": _v(2.0), "cat": _v(2.0)}, FLOOR, ULTRA) == "per"


def test_layer1_parity_two_candidates():
    # Layer-1: per_sym wins iff strictly greater, else cat (tie -> cat).
    assert (
        pick_winner({"cat": _v(1.0), "per_sym": _v(2.0)}, FLOOR, ULTRA, incumbent="cat")
        == "per_sym"
    )
    assert (
        pick_winner({"cat": _v(2.0), "per_sym": _v(1.0)}, FLOOR, ULTRA, incumbent="cat")
        == "cat"
    )
    assert (
        pick_winner({"cat": _v(2.0), "per_sym": _v(2.0)}, FLOOR, ULTRA, incumbent="cat")
        == "cat"
    )


def test_none_entries_skipped_empty_returns_none():
    assert pick_winner({"a": None, "b": _v(1.0)}, FLOOR, ULTRA) == "b"
    assert pick_winner({"a": None}, FLOOR, ULTRA) is None
    assert pick_winner({}, FLOOR, ULTRA) is None


def test_score_tuple_shape():
    s = score(_v(3.0, wr=0.6, dd=7.0, sharpe=1.2), FLOOR, ULTRA)
    assert (
        s[0] == 1
        and s[1] == 3.0
        and abs(s[2] - 0.6) < 1e-9
        and s[3] == -7.0
        and s[4] == 1.2
    )
