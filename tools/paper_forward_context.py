"""Resolve narrow paired contexts and report verified route limitations."""

import hashlib
import json


def _context_id(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive_shared_context(venue, sym_side, switch, default_account=None):
    """Return deterministic prerequisites and evidence status for a paired context."""
    venue = str(venue).lower()
    sym_side = str(sym_side).upper()
    switch = str(switch)
    account = str(default_account or ("ang" if venue == "crypto" else "trb")).lower()
    result = {
        "schema_version": 1,
        "venue": venue,
        "sym_side": sym_side,
        "target_switch": switch,
        "status": "NO_SPECIAL_CONTEXT",
        "account": account,
        "shared_settings": {},
        "experiment_context": {"release_nonvectorizable": True, "release_sub15m": True},
        "prerequisites": [],
        "coverage_note": "No additional account-specific prerequisite was identified.",
    }

    if switch == "TRC_LIVE_5M_TRADING_ENABLED":
        if venue != "stocks":
            result.update(status="INVALID_CONTEXT", coverage_note="TRC_LIVE_5M_TRADING_ENABLED is a stocks/TRC route.")
        else:
            result.update(
                status="READY",
                account="trc",
                prerequisites=[
                    "run account must be trc-only so TradierTradeManager applies TRC overrides",
                    "release the sub-15m worker mask for this candidate in both paired specs",
                    "release the nonvectorizable parity master in both paired specs",
                ],
                coverage_note="TRC account overlay maps this switch to LIVE_5m_trading_ENABLED; the default trb account does not apply that overlay.",
            )
    elif switch == "SCALP_ACCOUNTS":
        result.update(
            status="UNRESOLVED_SHARED_PREREQUISITE",
            shared_settings={"SCALP_MODE": True},
            prerequisites=[
                "SCALP_MODE must be true identically in baseline and candidate",
                "candidate SCALP_ACCOUNTS value must contain the selected account",
                "registered candidate value must be concrete; do not synthesize account membership",
            ],
            coverage_note="V2 entry/exit consumers require both SCALP_MODE and account membership; catalog parameter resolution must supply the candidate account list.",
        )
    elif switch == "SCALP_V3_BOOST_ENABLED":
        result.update(
            status="UNRESOLVED_ENGINE_CALLSITE",
            prerequisites=["prove the forward engine invokes ez_rankings ranking and consumes its adjusted ranking output"],
            coverage_note="A ranking consumer exists, but invocation and propagation through the forward engine are unverified.",
        )
    elif switch == "SCALP_V3_ENABLED" or switch.startswith("SCALP_V3_"):
        result.update(
            status="UNSUPPORTED_CONTEXT",
            coverage_status="UNVERIFIED_SCANNER",
            shared_settings={"SCALP_V3_ACCOUNTS": [account]},
            prerequisites=[
                "SCALP_V3_ACCOUNTS must contain the selected account identically in both arms",
                "forward-engine invocation of the dedicated scanner is not established",
            ],
            coverage_note="Account membership is a shared prerequisite; dedicated scanner invocation in the forward engine remains unverified.",
        )
    elif switch in {"HEDGE_MODE", "HEDGE_ACCOUNTS", "OBLIGATORY_HEDGE_ENABLED"} or switch.startswith("HEDGE_"):
        result.update(
            status="UNVERIFIED_HEDGE_ROUTE",
            coverage_status="SAME_SYMBOL_OPENER_UNAVAILABLE",
            prerequisites=["same-symbol hedge opener returns false before execution; other hedge management/exit effects are not classified here"],
            coverage_note="The known same-symbol opener is unavailable; this does not establish that every HEDGE_* setting is inert.",
        )

    context_payload = {key: value for key, value in result.items() if key != "context_id"}
    result["context_id"] = _context_id(context_payload)
    return result
