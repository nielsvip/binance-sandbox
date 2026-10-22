"""Augment/reduce gain adjustment: a gain delta that is an artefact of its own denominator never promotes.

gain% = net $ / peak cost basis. A row that augments raises the basis and a row that reduces lowers it, so
the greedy rule can promote a change that LOST money (and reject one that made money). Measured 2026-10-22 on
frozen 30d Mac NPZs: UNIUSDC_LONG AUGMENT_AT_LOSS_ENABLED=True read gain +1.272pp while net $ fell $8.88
(basis $360 -> $453); ALGOUSDT_LONG AUGMENT_INTO_REENTRY_ENABLED=True read +2.062pp while net $ fell $5.02
(basis $360 -> $780); XLMUSDT_LONG AUGMENT_AT_LOSS_ENABLED=True read +1.338pp while net $ fell $3.30.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v15_pilot as P  # noqa: E402


def _res(gain, net, peak, trades=431, valid=True):
    return {"gain_pct": gain, "net_gain_dollars": net, "max_invested_dollars": peak, "trades": trades, "valid": valid}


def setup_function(_fn):
    P._CHAIN_BASES.clear()


def test_uniusdc_long_augment_at_loss_is_refused():
    base = _res(-15.697012157443362, -56.51, 360.0)
    P.remember_chain_base(base)
    cand = _res(-14.425, -65.39, 453.34)
    delta, promotable, why = P.delta_vs_result(cand, base["gain_pct"])
    assert delta > 0, "the real gain delta is still reported, positive"
    assert not promotable, "a +1.27pp gain that lost $8.88 must not promote"
    assert why.startswith("DENOM_ARTEFACT")
    assert "$360->$453" in why.replace(",", "") or "360" in why


def test_algousdt_long_augment_into_reentry_is_refused():
    base = _res(-5.022523694548281, -18.08, 360.0, trades=454)
    P.remember_chain_base(base)
    cand = _res(-2.961, -23.10, 780.0, trades=454)
    delta, promotable, why = P.delta_vs_result(cand, base["gain_pct"])
    assert delta > 2.0 and not promotable and why.startswith("DENOM_ARTEFACT")


def test_xlmusdt_long_augment_at_loss_is_refused():
    base = _res(-10.097, -36.35, 360.0, trades=412)
    P.remember_chain_base(base)
    cand = _res(-8.759, -39.65, 452.72, trades=412)
    _d, promotable, why = P.delta_vs_result(cand, base["gain_pct"])
    assert not promotable and why.startswith("DENOM_ARTEFACT")


def test_same_basis_row_is_untouched():
    """A row that neither augments nor reduces keeps the identical basis — the guard must never fire."""
    base = _res(-15.697012157443362, -56.51, 360.0)
    P.remember_chain_base(base)
    cand = _res(-12.564, -45.23, 360.0, trades=436)  # measured WT_REDUCE_FRAC_LOW=2 on UNIUSDC_LONG
    delta, promotable, why = P.delta_vs_result(cand, base["gain_pct"])
    assert promotable and why == "" and abs(delta - 3.133) < 0.01


def test_augment_that_also_earns_dollars_still_promotes():
    """The guard refuses the ARTEFACT, never the genuine win: basis up AND dollars up promotes."""
    base = _res(-5.022523694548281, -18.08, 360.0, trades=454)
    P.remember_chain_base(base)
    cand = _res(-2.640, -11.99, 454.14, trades=454)  # measured ALGOUSDT_LONG AUGMENT_AT_LOSS_ENABLED=True
    _d, promotable, why = P.delta_vs_result(cand, base["gain_pct"])
    assert promotable and why == ""


def test_guard_fails_open_without_a_matching_base():
    """No remembered base, or one that does not reproduce cum_before from its own money: say nothing."""
    cand = _res(-14.425, -65.39, 453.34)
    _d, promotable, why = P.delta_vs_result(cand, -15.697012157443362)
    assert promotable and why == ""
    P.remember_chain_base(_res(-15.697012157443362, -56.51, 999.0))  # money does not reproduce the gain
    _d, promotable, why = P.delta_vs_result(cand, -15.697012157443362)
    assert promotable and why == ""


def test_gain_collision_cannot_arm_the_guard():
    """Two different sets with the same gain to 9dp: the last one wins the cache, and it still has to
    reproduce cum_before from its own net/peak before the guard may fire."""
    P.remember_chain_base(_res(-5.0, -18.0, 360.0))
    P.remember_chain_base(_res(-5.0, -36.0, 720.0))  # same gain%, different money — both self-consistent
    _d, promotable, why = P.delta_vs_result(_res(-4.0, -40.0, 1000.0), -5.0)
    assert not promotable and why.startswith("DENOM_ARTEFACT")


def test_invalid_result_keeps_its_delta_and_never_promotes():
    base = _res(-15.697012157443362, -56.51, 360.0)
    P.remember_chain_base(base)
    cand = _res(-14.425, -65.39, 453.34, valid=False)
    cand["invalid_reason"] = "TIM 91.0% >80% (vomit)"
    delta, promotable, why = P.delta_vs_result(cand, base["gain_pct"])
    assert delta > 0 and not promotable and why.startswith("TIM")


def test_zero_trades_and_missing_result_unchanged():
    assert P.delta_vs_result(None, 1.0) == (None, False, "no result")
    assert P.delta_vs_result(_res(1.0, 1.0, 1.0, trades=0), 0.0) == (None, False, "ZERO_TRADES")
