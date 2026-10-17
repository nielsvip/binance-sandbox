"""2026-10-16 USER: "none of the stop market orders we place when we buy are executed this is a MAJOR RISK ... ALL
rejected because the quantity is more than the existing quantity on Tradier."

Source-level contracts for the three defects that produced every rejected stop of 2026-10-16 (proved from
~/logs/tradier_manage_trb.log and the Tradier order list), plus behavioural tests for the snapshot module that now
supplies broker truth. No network, no live config.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MANAGE = (ROOT / "tradier_manage.py").read_text()
WATCHDOG = (ROOT / "run_with_watchdog.sh").read_text()


def _code_only(text: str) -> str:
    """Drop whole-line comments: these contracts are about what the code does, and the comments quote the very
    patterns the code must no longer contain."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _ensure_body() -> str:
    start = MANAGE.index("async def _ensure_fixed_stop_backstop_tradier_locked")
    end = MANAGE.index("async def _fixed_stop_order_action_tradier", start)
    return MANAGE[start:end]


def _wire_body() -> str:
    start = MANAGE.index("async def _fixed_stop_order_action_tradier")
    end = MANAGE.index("async def sweep_orphan_stops_tradier", start)
    return MANAGE[start:end]


def test_unverified_listing_never_places_a_stop():
    """A resting stop reserves the shares, so a stop placed while the listing is UNVERIFIED is either a duplicate
    (rejected) or a second stop on the same shares. XOM trb 2026-10-16: 151865001 resting from 14:13:22, listing
    timed out 14:50:45, placement 14:51:10 rejected."""
    body = _code_only(_ensure_body())
    assert "_live0 = await self._fixed_stop_broker_stops(" in body
    assert "if _live0 is None:" in body, "the unverified branch must be explicit"
    assert "SKIPPED_STOP_LIST_UNVERIFIED" in body
    adopt_at = body.index("_live0 = await self._fixed_stop_broker_stops(")
    place_at = body.index('action="PLACE_STOP"')
    guard_at = body.index("SKIPPED_STOP_LIST_UNVERIFIED")
    assert adopt_at < guard_at < place_at, "the unverified guard must sit between the listing and the placement"
    assert "for _o in (_live0 or [])" not in body, "`or []` turns an unverified listing into 'no stop resting'"


def test_place_waits_for_a_confirmed_cancel_before_replacing():
    """RRC trb 2026-10-16: cancel 14:51:06, replace 14:51:44, still rejected — Tradier had not released the shares."""
    body = _code_only(_ensure_body())
    assert "SKIPPED_STOP_CANCEL_PENDING" in body
    assert "_cancelled_ids" in body
    cancel_wait_at = body.index("SKIPPED_STOP_CANCEL_PENDING")
    place_at = body.index('action="PLACE_STOP"')
    assert cancel_wait_at < place_at


def test_an_order_id_is_not_an_acceptance():
    """Tradier answers a REJECTED order with a normal {"order": {"id": ...}} body. FIXED_STOP_PLACED_TR logged
    151890442 (XOM) and 151890614 (VLO) on 2026-10-16; the broker lists both as `rejected`."""
    body = _code_only(_wire_body())
    assert "FAILED_STOP_BROKER_" in body, "a rejected broker status must not return SUCCESS"
    assert "_bsnap.order_status(" in body, "the placed id must be confirmed resting at the broker"
    status_at = body.index("_bsnap.order_status(")
    success_at = body.index('return f"SUCCESS_STOP_PLACED_{_oid}"')
    assert status_at < success_at, "confirmation must happen before SUCCESS is returned"


def test_reconcile_is_serialised_per_position():
    """process_position fires ensure with asyncio.create_task; two overlapping tasks both placed on 2026-10-16
    (APO 14:47:06 + 14:47:48, VLO 14:51:10 + 14:51:42)."""
    assert "SKIPPED_STOP_RECONCILE_IN_FLIGHT" in MANAGE
    assert "_fx_stop_locks" in MANAGE
    assert "async def _ensure_fixed_stop_backstop_tradier_locked" in MANAGE


def test_order_listing_fallback_pages_at_one_hundred():
    """The 2026-10-14 "all pages" fix tested `len(_got) < 1500` while the endpoint pages at 100 and sent no `limit`,
    so it always stopped after page 1."""
    start = MANAGE.index("async def _fixed_stop_broker_stops")
    end = MANAGE.index("async def _fixed_stop_clear_for_exit", start)
    body = _code_only(MANAGE[start:end])
    assert "< 1500" not in body
    assert '"limit": 100' in body
    assert "if len(_got) < 100:" in body


def test_fenced_watchdog_does_not_kill_what_it_refuses_to_replace():
    """A LEASE_FENCED wrapper ran kill_duplicate_python before the gate, SIGTERMing the live child every 30 s while
    refusing to spawn one: tradier_positions.py was dead 14:52->19:51 on 2026-10-16."""
    loop = WATCHDOG[WATCHDOG.index("    while true; do"):]
    gate_at = loop.index("lf_lease_fenced")
    kill_at = loop.index("        kill_duplicate_python")
    assert gate_at < kill_at, "the lease gate must run before any duplicate reaping"


def test_snapshot_never_publishes_an_empty_book_on_a_failed_read(monkeypatch):
    """An empty order list reads as 'no resting stop' and duplicates every backstop; an empty position list reads as
    'flat' and removes protection. A failed pull must leave the previous snapshot in place."""
    import asyncio

    import tradier_broker_snapshot as S

    S._SNAP.clear()
    S._SNAP["zz"] = {"ts": time.time(), "qty": {"XOM": 20.0}, "orders": [{"id": 1}], "last_fail_ts": 0.0, "consecutive_fails": 0}

    class _FailClient:
        async def get_account_positions(self, _a):
            return None

        async def _request(self, *a, **k):
            return None

    asyncio.run(S._refresh_one(_FailClient(), "zz"))
    assert S.positions("zz") == {"XOM": 20.0}, "a failed read must not erase the last good snapshot"
    assert S._SNAP["zz"]["consecutive_fails"] == 1
    S._SNAP.clear()


def test_snapshot_distinguishes_unverified_from_flat():
    import tradier_broker_snapshot as S

    S._SNAP.clear()
    assert S.quantity("zz", "XOM") is None, "no snapshot at all is UNVERIFIED, never flat"
    assert S.protective_stops("zz", "XOM", "LONG") is None
    S._SNAP["zz"] = {"ts": time.time(), "qty": {}, "orders": [], "last_fail_ts": 0.0, "consecutive_fails": 0}
    assert S.quantity("zz", "XOM") == 0.0, "a fresh empty snapshot IS a confirmed flat"
    assert S.protective_stops("zz", "XOM", "LONG") == []
    S._SNAP["zz"]["ts"] = time.time() - 10_000
    assert S.quantity("zz", "XOM") is None, "a stale snapshot is UNVERIFIED again"
    S._SNAP.clear()


def test_snapshot_filters_protective_stops_by_side_type_and_status():
    import tradier_broker_snapshot as S

    S._SNAP.clear()
    S._SNAP["zz"] = {
        "ts": time.time(),
        "qty": {"XOM": 20.0},
        "orders": [
            {"id": 1, "symbol": "XOM", "side": "sell", "type": "stop", "status": "open", "quantity": 20},
            {"id": 2, "symbol": "XOM", "side": "sell", "type": "stop", "status": "rejected", "quantity": 20},
            {"id": 3, "symbol": "XOM", "side": "sell", "type": "limit", "status": "open", "quantity": 20},
            {"id": 4, "symbol": "XOM", "side": "buy", "type": "stop", "status": "open", "quantity": 20},
            {"id": 5, "symbol": "CRK", "side": "sell", "type": "stop", "status": "open", "quantity": 80},
        ],
        "last_fail_ts": 0.0,
        "consecutive_fails": 0,
    }
    got = S.protective_stops("zz", "XOM", "LONG")
    assert [o["id"] for o in got] == [1], "only the open closing-side stop on that symbol counts"
    assert S.order_status("zz", 2) == "rejected"
    assert S.order_status("zz", 999) == "", "a fresh snapshot that lacks the id says so; it does not say UNVERIFIED"
    S._SNAP.clear()


def test_orphan_sweep_requires_a_fresh_snapshot():
    start = MANAGE.index("async def sweep_orphan_stops_tradier")
    end = MANAGE.index("async def sweep_fixed_stops_tradier", start)
    body = _code_only(MANAGE[start:end])
    assert "SKIPPED_ORPHAN_UNVERIFIED" in body
    assert "_qty_map is None or _orders is None" in body, "unverified truth must never cancel a stop"


def test_audit_tool_is_read_only():
    src = (ROOT / "tools" / "tradier_position_stop_audit.py").read_text()
    for forbidden in ("place_order", "cancel_order", "execute_now", ".write_text(", "os.replace("):
        assert forbidden not in src, f"the audit must never mutate anything ({forbidden})"


def test_auto_stop_adjustment_rule_is_wired():
    """USER 2026-10-16: "stop orders on the exchange are constantly updated with the correct quantity (without
    changing the price unless an augment took place in which case you take the stop for the entire position to the
    new dc_low_15-0.3% (vv short))"."""
    body = _code_only(_ensure_body())
    assert "_prev_qty" in body and "_augmented" in body, "augment and reduce must be distinguishable"
    assert "FIXED_STOP_AUGMENT_REFREEZE_TR" in body, "an augment re-freezes from the CURRENT dc_15m channel"
    assert "FIXED_STOP_REDUCE_KEEP_PRICE_TR" in body, "a reduce must hold the resting price"
    assert "_lv_pin" in body, "the reduce price is pinned, not recomputed"
    assert "freeze_software_level(_is_long, float(dc_low_15m or 0.0), float(dc_high_15m or 0.0)" in body
    for sig in ("async def ensure_fixed_stop_backstop_tradier", "async def _ensure_fixed_stop_backstop_tradier_locked"):
        line = MANAGE[MANAGE.index(sig):MANAGE.index("\n", MANAGE.index(sig))]
        assert "dc_low_15m" in line and "dc_high_15m" in line, f"{sig} must receive the live channel"


def test_combined_exchange_level_is_dc_minus_30_bps():
    """The level the user describes (-0.3%) is freeze_software_level (-0.25%) then exchange_level (-0.05%)."""
    import vec_decisions.fixed_stop as FS

    soft_l = FS.freeze_software_level(True, 100.0, 0.0, 50.0)
    assert abs(FS.exchange_level(True, soft_l) / 100.0 - 0.99700125) < 1e-9
    soft_s = FS.freeze_software_level(False, 0.0, 100.0, 150.0)
    # SHORT mirrors multiplicatively: 1.0025 * 1.0005 = 1.00300125 (+0.3001%), not 2 - 0.99700125.
    assert abs(FS.exchange_level(False, soft_s) / 100.0 - 1.00300125) < 1e-9


def test_continuous_reconciler_exists_and_refuses_unverified():
    start = MANAGE.index("async def stop_truth_reconcile_loop")
    end = MANAGE.index("async def sweep_orphan_stops_tradier", start)
    body = _code_only(MANAGE[start:end])
    assert "_bsnap.positions(" in body, "the reconciler is driven by the off-loop snapshot"
    assert "if _qmap is None:" in body, "no fresh snapshot -> skip the pass, never guess"
    assert "ensure_fixed_stop_backstop_tradier(" in body, "one implementation of the stop rules, not two"
    assert "self.stop_truth_reconcile_loop()" in MANAGE, "the loop must be registered as a background task"


def test_protective_stop_is_exempt_from_the_execute_now_gates():
    """USER 2026-10-16: "this auto stop order adjustment function could get an exemption from the execute_now pass as
    it is totally safe". On 2026-10-16 trb:XOM_LONG's backstop was refused with BROKER_UNREACHABLE_ORDERS and
    BROKER_POSITION_UNKNOWN — the position was left unprotected because the broker was hard to read."""
    guard = (ROOT / "order_dedupe_guard.py").read_text()
    assert "async def guarded_protective_stop_place" in guard
    gp = guard[guard.index("async def guarded_place"):guard.index("async def guarded_exit_place")]
    gp_code = _code_only(gp)
    assert "tradier_protective_stop(order_type, side)" in gp_code
    assert gp_code.index("guarded_protective_stop_place") < gp_code.index("dec = await self.preflight("), "the bypass must come before preflight"
    body = _code_only(guard[guard.index("async def guarded_protective_stop_place"):guard.index("async def guarded_generic")])
    for gate in ("self.preflight(", "self.phase3_gate(", "self.exposure_gate(", "self.intent_check("):
        assert gate not in body, f"a protective stop must not be subject to {gate}"
    assert "BAD_QTY" in body, "a zero/NaN stop is still refused — that is correctness, not policy"
    assert "STOP_QTY_CLAMP_BROKER" in body, "the stop is clamped to the broker holding when it is readable"


def test_stocks_no_board_entry_gate_never_touches_exits():
    """Carried for binance-4b, 2026-10-16: LATEST-BOARD ENTRY GATE for stocks, additive and DEFAULT OFF.
    CLAUDE.md: "NO exit can EVER be skipped AT ALL for ANY REASON" — the gate must be unreachable for anything
    that reduces or closes."""
    assert MANAGE.count("BLOCKED_NO_LATEST_BOARD") == 1, "one refusal site only"
    start = MANAGE.index("_lb_entry = (")
    body = MANAGE[start:MANAGE.index("BLOCKED_NO_LATEST_BOARD", start) + 60]
    assert "not _xg_exit" in body and "not _xg_full" in body, "exits and full closes can never reach the gate"
    assert 'startswith("NO_LATEST_BOARD")' in body, "NO_LATEST_BOARD is the ONLY refusal reason (not LIVE_SET_NE_BOARD)"
    assert 'getattr(config, "TRADIER_NO_BOARD_ENTRY_BLOCK", False)' in body, "default OFF, read off config not _cfg"
    assert "_lb_ok, _lb_why = True," in body, "a gate error must fail OPEN, never stop trading"


def test_stocks_no_board_gate_is_on_and_stays_narrow():
    """ENABLED 2026-10-17, reviewed with the operator after it was flipped unannounced at 00:48Z.

    What decides this is the VALUE, not the presence of the field (added as False at config_tradier.py:2967 on
    2026-10-16, flipped to True on 10-17). It was left ON because the blast radius was MEASURED rather than
    argued: of 131 tradeable stock sym_sides, 108 pass, and only **16** are refused on the unambiguous
    NO_LATEST_BOARD branch (ADP_LONG, APA_LONG, AU_SHORT, COHR_SHORT, CRK_LONG, EOG_LONG, INTC_SHORT, KGC_SHORT,
    MA_LONG, MP_SHORT, MRVL_LONG, NVDA_SHORT, RBLX_SHORT, SLV_SHORT, XLE_LONG, XOP_LONG) — all 16 pinned into the
    scheduler's operator queue the same day so they get a board before Monday's open. The 7 LIVE_SET_NE_BOARD
    sym_sides are deliberately NOT refused.

    This stays a tripwire in BOTH directions: the value is pinned, so flipping it again — on or off — has to be a
    decision someone makes here, with the measurement redone. The guards below are what keep it narrow."""
    from config_tradier import TradierConfig

    assert bool(getattr(TradierConfig(), "TRADIER_NO_BOARD_ENTRY_BLOCK", False)), \
        "the no-board entry gate was reviewed ON 2026-10-17; turning it off is a decision, not a drive-by edit"
    # the two properties that bound it: only the unambiguous branch refuses, and a gate bug can never halt trading
    body = MANAGE[MANAGE.index("TRADIER_NO_BOARD_ENTRY_BLOCK"):][:1400]
    assert 'startswith("NO_LATEST_BOARD")' in body, "only NO_LATEST_BOARD may refuse — LIVE_SET_NE_BOARD must not"
    assert "_lb_ok, _lb_why = True," in body, "the gate must fail OPEN on error, never stop trading"
