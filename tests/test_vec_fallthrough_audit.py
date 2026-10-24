"""Vec fall-through audit (USER 2026-10-24: live never refuses a vec trade).

Two tiers, both pinned:
- TIER A (opinion/strategy vetoes): a vec-dictated order records a bypass and
  falls through to the late VEC_LAST backstop (which executes it). Every name
  below must appear as a bypassed gate in its manager.
- TIER B (integrity/physical gates): duplicate protection, blind-trading,
  churn brakes, user bans, hedge-mode, brokerage physics. These STILL refuse
  (executing blind/duplicated is not parity — the backtest never took that
  risk) but every refusal on a vec order is breach-filed by the execute_now
  wrapper on both venues. Every string below must still be a bare return.

Change either list deliberately or not at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EZ = Path("ez_manage.py").read_text()
TR = Path("tradier_manage.py").read_text()

TIER_A_EZ = [
    "365D_CONFIRM",
    "SCALP_V3_DISABLED",
    "SCALP_DISABLED",
    "EXIT_LH_LL",
    "INF_NOT_WINNER",
    "UNIVERSAL_NOLOSS_GATE",
    "SYM_HALT",
    "VIGILANCE",
    "STALE_SET_HALT",
    "CIRCUIT_BREAKER",
    "FLAGGED_ORIGIN",
    "EMA_BLANKET",
]

TIER_A_TR = [
    "NO_LATEST_BOARD",
    "SYM_HALT",
    "VIGILANCE",
    "NEWBORN_PROTECT",
    "OPEN_RATE_BREAKER",
    "EXIT_LH_LL",
    "DC_HARDSTOP_COOLDOWN",
]

TIER_B_RETURNS = [
    "BLOCKED_EXCHANGE_OPEN_ORDER_EXISTS",
    "BLOCKED_BROKER_SYNC_STALE",
    "BLOCKED_BROKER_SYNC_PENDING_CONFIRMATION",
    "BLOCKED_EMERGENCY_BRAKE_MAX_ENTRIES",
    "BLOCKED_EMERGENCY_BRAKE_MAX_TRADES",
    "BLOCKED_BLACKLIST",
    "BLOCKED_NON_TRADEABLE",
    # USER 2026-10-24 unreachable-real-fix: BLOCKED_POSITIONS_BROKER_UNREACHABLE revoked from Tier B — a missing
    # broker-truth slot is a reading failure, never execution evidence; orders proceed, the broker attests at
    # place-time. Pinned gone by test_open_on_leftover_single_waitfree_read_then_proceeds + the truth suite.
    "BLOCKED_BY_HEDGE_MODE",
    "BLOCKED_HEDGE_MISIDENT",
    "BLOCKED_ACCOUNT_ENTRY_BANNED",
    "BLOCKED_OPPOSITE_SIDE_OPEN",
    "BLOCKED_SERVER_HEARTBEAT_ACTIVE",
    "BLOCKED_HEDGE_DISABLED",
    "BLOCKED_GFV_ACCOUNT_RULE",
]


def test_tier_a_bypassed_ez():
    for gate in TIER_A_EZ:
        assert f'_vec_last_bypassed.append("{gate}")' in EZ, gate


def test_tier_a_bypassed_tradier():
    for gate in TIER_A_TR:
        assert (f'_vec_last_bypassed.append("{gate}")' in TR) or (f'_tr_bypass("{gate}")' in TR), gate


def test_tier_b_still_refuses():
    for ret in TIER_B_RETURNS:
        assert (f'return "{ret}' in EZ) or (f"return f\"{ret}" in EZ) or (f'return "{ret}' in TR) or (f"return f\"{ret}" in TR), ret


def test_wrapper_breach_both_venues():
    assert "_parity_vec_order(reason)" in EZ and "_parity_breach_record(position_key, action, reason, _res)" in EZ
    assert "_tr_parity_vec_order(reason)" in TR and "_parity_breach_record_tradier(position_key, action, reason, _res)" in TR


def test_vec_preflight_failopen_unreachable():
    """USER 2026-10-24 unreachable-real-fix: a vec-dictated order fails OPEN on the unverifiable-broker-state
    preflight class (the lie class, no evidence of a live order); OPEN_ORDER_EXISTS / LAST_ORDER_* still refuse."""
    i = EZ.index("ORDER_DEDUPE_GUARD (USER 2026-10-06 ABSOLUTE)")
    seg = EZ[i:i + 3000]
    assert "[VEC_PREFLIGHT_FAILOPEN]" in seg and "_parity_vec_order(reason)" in seg and "_FX_UNREACHABLE_PREFLIGHT_CODES" in seg
