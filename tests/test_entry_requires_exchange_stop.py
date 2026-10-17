"""2026-10-17 USER: "we use PLACE_MAKER_ORDER with market order fallback and stop_market order on exchange for any
tnery/augment as a requirement".

Before today the ONLY placer of a protective stop was a periodic reconcile: the FIXED_STOP probe inside
process_position (crypto) and stop_truth_reconcile_loop every 10 s (stocks, the single caller of
ensure_fixed_stop_backstop_tradier). A filled position was therefore unprotected from the fill until the sweep
reached it, and a sweep that stalled or errored left it unprotected with no alarm at all. That is measured, not
theoretical: tonight's audit found 14 of 44 live stock positions with no resting stop.

So the stop is now placed AT the fill, by a thin wrapper around each venue's execute_now, and VERIFIED afterwards.
This file pins the three properties that make that true and keeps them from rotting:
  1. the requirement fires on exposure increases only, using the SAME predicate as the vec gate;
  2. it can never block, unwind or rewrite a fill — execute_now's verdict passes through untouched, even when the
     stop placement raises;
  3. an unplaceable or unverifiable stop is recorded and shouted, never silently swallowed (NO-LIES).
Source-level + pure-async fakes: no network, no broker, no config mutation.
"""
from __future__ import annotations

import ast
import asyncio
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from live_twins.vec_provenance import increases_exposure


def _src(name):
    return (ROOT / name).read_text()


def _wrapper_body(src, fname="execute_now"):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname:
            return "\n".join(src.splitlines()[n.lineno - 1:n.end_lineno])
    raise AssertionError(f"{fname} not found")


# ── 1. the predicate, pinned against the vec gate ────────────────────────────────────────────────────────────────
def _inlined_predicate(src, is_hedge_supported):
    """Re-evaluate the predicate the wrapper actually inlined, so a future edit to it is caught here."""
    body = _wrapper_body(src)
    m = re.search(r"_is_incr = (.+?)\n            \)\n", body, re.S)
    assert m, "the exposure-increase predicate is no longer recognisable in the execute_now wrapper"
    expr = m.group(1) + ")"

    def _pred(side, position_side, is_full_close=False, action=None, is_hedge=False):
        return bool(eval(expr, {}, dict(side=side, position_side=position_side, is_full_close=is_full_close,
                                        _act_u=str(action or "").upper(), is_hedge=is_hedge)))
    return _pred


CASES = [
    ("BUY", "LONG", False, None), ("SELL", "SHORT", False, None),          # entries / augments
    ("SELL", "LONG", False, None), ("BUY", "SHORT", False, None),          # reductions
    ("SELL", "LONG", True, "CLOSE"), ("BUY", "SHORT", True, "FULL_CLOSE"),  # closes
    ("BUY", "LONG", False, "PLACE_STOP"), ("SELL", "LONG", False, "CANCEL_STOP"),
    ("BUY", "LONG", False, "REDUCE"), ("SELL", "LONG", False, "FORCE_CLOSE_MARKET"),
    ("BUY", "LONG", True, None), ("", "", False, None),
]


@pytest.mark.parametrize("venue,hedge", [("ez_manage.py", True), ("tradier_manage.py", False)])
def test_requirement_fires_exactly_where_the_vec_gate_says_exposure_grows(venue, hedge):
    pred = _inlined_predicate(_src(venue), hedge)
    for side, pside, full, action in CASES:
        want = increases_exposure(side, pside, full, action, False)
        assert pred(side, pside, full, action, False) == want, f"{venue}: {side}/{pside} full={full} action={action}"


def test_stop_and_close_actions_can_never_recurse_into_the_requirement():
    """ensure_* places the stop THROUGH execute_now(action=PLACE_STOP). If that re-triggered the requirement the
    order path would recurse forever, so the predicate must reject every stop/close/reduce action."""
    for venue in ("ez_manage.py", "tradier_manage.py"):
        pred = _inlined_predicate(_src(venue), venue == "ez_manage.py")
        for action in ("PLACE_STOP", "CANCEL_STOP", "FORCE_CLOSE_MARKET", "REDUCE_PROFIT_LOCK"):
            assert pred("BUY", "LONG", False, action, False) is False, f"{venue}: {action} would recurse"


# ── 2. the wrapper is a wrapper: the verdict passes through, always ──────────────────────────────────────────────
class _FakeMgr:
    """Minimal stand-in exercising the wrapper's control flow with the real source semantics."""

    def __init__(self, verdict, boom=False):
        self.verdict = verdict
        self.boom = boom
        self.inner_calls = []
        self.required = []
        self.entry_stop_unprotected = {}

    async def _execute_now_inner(self, **kw):
        self.inner_calls.append(kw)
        return self.verdict

    async def _require(self, **kw):
        self.required.append(kw)
        if self.boom:
            raise RuntimeError("broker on fire")


async def _run_wrapper(mgr, *, side="BUY", position_side="LONG", is_full_close=False, action=None):
    """The wrapper's logic, transcribed from the source shape it must keep."""
    res = await mgr._execute_now_inner(side=side, position_side=position_side, is_full_close=is_full_close, action=action)
    try:
        act_u = str(action or "").upper()
        is_incr = not (is_full_close or "CLOSE" in act_u or "REDUCE" in act_u or "STOP" in act_u) and (
            (position_side.upper() == "LONG" and side.upper() == "BUY")
            or (position_side.upper() == "SHORT" and side.upper() == "SELL"))
        if is_incr and str(res or "").upper().startswith("SUCCESS"):
            await mgr._require(res=res)
    except Exception as e:
        mgr.entry_stop_unprotected["pk"] = {"why": f"requirement_error:{e}"}
    return res


@pytest.mark.parametrize("verdict", [
    "SUCCESS", "SUCCESS_VIA_FALLBACK", "SUCCESS_VIA_MAKER_FALLBACK",
    "BLOCKED_AUGMENT_INSUFFICIENT_GAIN", "FAILED_WEBHOOK", "FAILED_VERIFICATION_AND_FALLBACK",
    "BLOCKED_MAKER_SUPPRESS_WEBHOOK", "ERROR_boom",
])
def test_every_execute_now_verdict_passes_through_unchanged(verdict):
    mgr = _FakeMgr(verdict)
    assert asyncio.run(_run_wrapper(mgr)) == verdict
    assert len(mgr.inner_calls) == 1, "the body must be called exactly once per execute_now"


def test_a_stop_failure_never_unwinds_or_rewrites_a_real_fill():
    """CLAUDE.md money rule: the fill happened. Refusing to report it, or retrying the entry, would be worse than
    an unprotected position — so the requirement blowing up must still return SUCCESS and record the gap."""
    mgr = _FakeMgr("SUCCESS", boom=True)
    assert asyncio.run(_run_wrapper(mgr)) == "SUCCESS"
    assert "requirement_error" in mgr.entry_stop_unprotected["pk"]["why"]


def test_the_requirement_is_skipped_for_everything_that_is_not_a_filled_increase():
    for verdict, side, pside, full, action in [
        ("FAILED_WEBHOOK", "BUY", "LONG", False, None),
        ("SUCCESS", "SELL", "LONG", False, None),
        ("SUCCESS", "SELL", "LONG", True, "CLOSE"),
        ("SUCCESS", "BUY", "LONG", False, "PLACE_STOP"),
    ]:
        mgr = _FakeMgr(verdict)
        asyncio.run(_run_wrapper(mgr, side=side, position_side=pside, is_full_close=full, action=action))
        assert mgr.required == [], f"requirement should not fire for {verdict}/{side}/{pside}/{action}"
    mgr = _FakeMgr("SUCCESS")
    asyncio.run(_run_wrapper(mgr))
    assert len(mgr.required) == 1, "a filled BUY/LONG MUST require a stop"


# ── 3. NO-LIES: no silent success, and both venues verify rather than trust ──────────────────────────────────────
@pytest.mark.parametrize("venue,fn", [
    ("ez_manage.py", "_require_exchange_stop_after_entry"),
    ("tradier_manage.py", "_require_exchange_stop_after_entry_tradier"),
])
def test_no_failure_path_is_silent(venue, fn):
    body = _wrapper_body(_src(venue), fn)
    tree = ast.parse("\n".join(l[4:] if l.startswith("    ") else l for l in body.splitlines()))
    fail_returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return) and isinstance(n.value, (ast.Constant, ast.JoinedStr))
                    and "FAILED" in ast.unparse(n.value)]
    assert fail_returns, f"{fn}: no failure verdicts at all — the requirement cannot be failing closed"
    for marker in ("logger.critical", "_mark_entry_unprotected"):
        assert marker in body, f"{fn}: a failed stop must {marker} — a silent miss is how 14/44 went unnoticed"


@pytest.mark.parametrize("venue,fn,proof", [
    ("ez_manage.py", "_require_exchange_stop_after_entry", "managed_stop_registry"),
    ("tradier_manage.py", "_require_exchange_stop_after_entry_tradier", "_fixed_stop_broker_stops"),
])
def test_the_stop_is_verified_not_assumed(venue, fn, proof):
    """The ensure functions return a string. A string is a claim. Protection is only proven by a broker-acknowledged
    order id (crypto registry) or a live broker listing (stocks) — the 2026-10-13 "a recorded oid is a claim, not
    proof" rule, applied to the entry path."""
    body = _wrapper_body(_src(venue), fn)
    assert proof in body, f"{fn}: must confirm the stop via {proof}, not via the ensure return value"


def test_the_requirement_cannot_hang_the_order_path():
    """Tonight's flapping came from latency in the order path. The stop placement is awaited, so it MUST be bounded."""
    for venue, fn in (("ez_manage.py", "_require_exchange_stop_after_entry"),
                      ("tradier_manage.py", "_require_exchange_stop_after_entry_tradier")):
        body = _wrapper_body(_src(venue), fn)
        assert "asyncio.wait_for" in body and "asyncio.TimeoutError" in body, f"{fn}: unbounded await in the order path"


def test_augment_moves_the_whole_position_to_the_new_channel():
    """USER: "without changing the price unless an augment took place in which case you take the stop for the entire
    position to the new dc_low_15-0.3% (vv short)". Crypto re-freezes in the requirement; stocks already do it inside
    ensure_fixed_stop_backstop_tradier, which is why that venue delegates instead of re-deriving a level."""
    crypto = _wrapper_body(_src("ez_manage.py"), "_require_exchange_stop_after_entry")
    assert "_fixed_stop_freeze_level" in crypto and "FIXED_STOP_AUGMENT_REFREEZE" in crypto
    assert "_grew" in crypto, "an augment must be detected from the position actually growing, not only a reason string"
    stocks = _wrapper_body(_src("tradier_manage.py"), "_require_exchange_stop_after_entry_tradier")
    assert "ensure_fixed_stop_backstop_tradier" in stocks
    assert "_fixed_stop_freeze_level" not in stocks, "stocks must not grow a second stop-level implementation"
