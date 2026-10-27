"""MIN_HOLD_BARS — one definition, used by the vector AND by live.

USER 2026-10-27: "refuse a discretionary CLOSE/REDUCE when age < MIN_HOLD_BARS
<- this has to be IDENTICAL in vec and live and TESTED with 0-1-2-4-8 on EVERY
crypto; I REPEAT SCALP IS PROHIBITED"

Identity is not achieved by writing the same rule twice. Every function here is
pure and is the ONLY definition both sides call, so the two cannot drift.

WHAT WAS MEASURED 2026-10-27 (why this module exists)
-----------------------------------------------------
1. A 5x VEC/LIVE SPLIT. `BASE_TF` is 15m on both sides, but
   `v12_quick_engine.simulate_one` computed `min_hold` in BASE-TF bars (10 bars
   = 150 min) while `ez_manage` turned the same bar count into seconds at
   `* 180.0` — a legacy 3-minute-bar assumption — giving 30 min. Live could
   exit five times sooner than the board that authorized the position ever
   simulated, which is churn the board never priced.
2. THE SWEEP COULD NOT BIND. vec used
   `min_hold = max(MIN_HOLD_BARS, MIN_HOLD_BARS_BEFORE_EXIT)` = max(3, 10) = 10,
   so every swept MIN_HOLD_BARS value <= 8 was masked by the other switch and
   would have reported a structural 0 for all five options (Bible 14.3). The two
   switches live in different template tabs (GLOBAL_RISK_GATES vs
   REDUCE_PROFIT_LOCK) and were never one quantity; `threshold_bars` therefore
   reads MIN_HOLD_BARS alone and MIN_HOLD_BARS_BEFORE_EXIT keeps its own leg.

SCALP IS PROHIBITED (operator, repeated 2026-10-27)
---------------------------------------------------
This gate is the enforcement, not an advisory. A scalp is exactly a
discretionary exit inside the min hold, so no reason, family, lane or
`SCALP_*` switch may buy an exemption: `is_scalp_prohibited()` asserts the
scalp switches stay off, and the exempt set below is the price-protection
family only. `MIN_HOLD_BARS = 0` disables the gate by configuration — that is
the sanctioned "off", and the sweep tests it as one of the five options.

EXEMPT, ALWAYS (CLAUDE.md "exits never blocked")
------------------------------------------------
Stops, emergencies, liquidation, broker bookkeeping and anything the board
itself dictated. Price protection is not discretionary and is never held back;
a stop inside the min hold still fires.
"""

from __future__ import annotations

# A trade whose reason belongs to one of these families is NOT discretionary and
# is never held. Kept as an explicit tuple rather than an import so the vector
# path has no live-module dependency.
STOP_TOKENS = (
    "FIXED_STOP",
    "HARD_STOP",
    "STOP_LOSS",
    "STOPLOSS",
    "UNCOND_DC",
    "TRAIL_STOP",
    "LIQUIDAT",
    "MARGIN",
    "EMERGENCY",
    "VIGILANCE",
    "BALANCE_FLOOR",
    "NEGBOOK",
)
BOOKKEEPING_TOKENS = (
    "BROKER_SYNC",
    "BROKER_RECONCIL",
    "NON_TRADEABLE",
    "OVERSIZE_TRIM",
    "DUST",
    "SETTLE",
    "DELIST",
)
BOARD_TOKENS = (
    "VEC_DRIVEN_",
    "RECONCILE_",
    "BOARD_CONVERGE_",
    "VEC_FWD_",
    "VEC_EXIT_OWED",
    "|VEC_EXACT",
    "PURE_BOARD",
)
# A scalp reason must never be treated as its own exemption: listed so a future
# edit that adds SCALP_* back to an exempt set fails the unit test instead of
# quietly re-opening sub-min-hold churn.
SCALP_TOKENS = ("SCALP",)

DEFAULT_BAR_MINUTES = 15
# The five options the operator ordered swept on every crypto sym_side.
SWEEP_OPTIONS = (0, 1, 2, 4, 8)


def _int(cfg, key) -> int:
    try:
        return max(0, int(float(getattr(cfg, key, 0) or 0)))
    except (TypeError, ValueError):
        return 0


def min_hold_bars_only(cfg) -> int:
    """MIN_HOLD_BARS as configured — the knob the operator ordered swept."""
    return _int(cfg, "MIN_HOLD_BARS")


def threshold_bars(cfg) -> int:
    """THE effective discretionary min hold, in BASE-TF bars, for BOTH sides.

    `max(MIN_HOLD_BARS, MIN_HOLD_BARS_BEFORE_EXIT)` — the same composition
    `v12_quick_engine.simulate_one` already used, so adopting it live changes
    live to match the board rather than changing what the board does, and
    neither promoted switch is neutralised (MIN_HOLD_BARS_BEFORE_EXIT is swept
    10/16/24/32/40/48 in REDUCE_PROFIT_LOCK on many boards; dropping it from
    here would silently make those promotions inert, which CLAUDE.md forbids).

    CONSEQUENCE THE OPERATOR MUST KNOW: with MIN_HOLD_BARS_BEFORE_EXIT = 10,
    sweeping MIN_HOLD_BARS at 0/1/2/4/8 cannot move this number — the max pins
    it at 10 and every option reports a structural 0 (Bible 14.3). MIN_HOLD_BARS
    is therefore ALREADY a dead switch in the vector today (3 is always masked
    by 10), which is what its registry `options_need_review: true` was pointing
    at. `MIN_HOLD_SOLE_SWITCH_ENABLED=True` makes MIN_HOLD_BARS the sole driver
    so the five options bind; it is reported both ways rather than flipped
    unilaterally, because it would re-base a switch live positions are held on.
    """
    if bool(getattr(cfg, "MIN_HOLD_SOLE_SWITCH_ENABLED", False)):
        return min_hold_bars_only(cfg)
    return max(_int(cfg, "MIN_HOLD_BARS"), _int(cfg, "MIN_HOLD_BARS_BEFORE_EXIT"))


def bar_minutes(cfg) -> int:
    """Minutes per BASE-TF bar. The ONE grid both sides measure age on."""
    tf = str(getattr(cfg, "BASE_TF", "15m") or "15m").strip().lower()
    table = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "d": 1440}
    return int(table.get(tf, DEFAULT_BAR_MINUTES))


def threshold_seconds(cfg) -> float:
    """Min hold in SECONDS. Live must use this, never bars x a hardcoded 180."""
    return float(threshold_bars(cfg)) * float(bar_minutes(cfg)) * 60.0


def is_exit_action(action) -> bool:
    a = str(action or "").upper()
    return "CLOSE" in a or "REDUCE" in a


def is_scalp_reason(reason) -> bool:
    return any(t in str(reason or "").upper() for t in SCALP_TOKENS)


def is_exempt(reason) -> bool:
    """True for price protection, emergencies, bookkeeping and board dictates.

    A SCALP reason is deliberately NOT exempt: scalping is the thing being
    prohibited, so it must fall through to the hold check.
    """
    r = str(reason or "")
    u = r.upper()
    if any(t in u for t in STOP_TOKENS):
        return True
    if any(t in u for t in BOOKKEEPING_TOKENS):
        return True
    if "|VEC_EXACT" in r:
        return True
    if any(t in u for t in BOARD_TOKENS if t != "|VEC_EXACT"):
        return True
    return False


def refuse(age_seconds, cfg, action="CLOSE", reason="") -> tuple:
    """(refuse?, detail) — the ONE decision. Live passes real seconds.

    Refuses only a DISCRETIONARY exit younger than the min hold. Anything that
    is not an exit, is exempt, or has an unusable age passes untouched, because
    guessing an age is how a stop gets stranded.
    """
    bars = threshold_bars(cfg)
    if bars <= 0:
        return False, "MIN_HOLD_OFF"
    if not is_exit_action(action):
        return False, "NOT_AN_EXIT"
    if is_exempt(reason):
        return False, "EXEMPT_PROTECTION_OR_BOARD"
    try:
        age = float(age_seconds)
    except (TypeError, ValueError):
        return False, "AGE_UNKNOWN"
    if age < 0:
        return False, "AGE_UNKNOWN"
    need = threshold_seconds(cfg)
    if age < need:
        return True, f"MIN_HOLD_BARS={bars}({need / 60.0:.0f}m) age={age / 60.0:.1f}m"
    return False, "HOLD_SATISFIED"


def refuse_bars(held_bars, cfg, action="CLOSE", reason="") -> tuple:
    """Bar-indexed twin for the vector, which counts bars, not seconds.

    Converts through the SAME `threshold_bars`, so a vector bar comparison and a
    live seconds comparison cannot disagree.
    """
    bars = threshold_bars(cfg)
    if bars <= 0:
        return False, "MIN_HOLD_OFF"
    if not is_exit_action(action):
        return False, "NOT_AN_EXIT"
    if is_exempt(reason):
        return False, "EXEMPT_PROTECTION_OR_BOARD"
    try:
        held = float(held_bars)
    except (TypeError, ValueError):
        return False, "AGE_UNKNOWN"
    if held < 0:
        return False, "AGE_UNKNOWN"
    if held < bars:
        return True, f"MIN_HOLD_BARS={bars} held={held:.0f}"
    return False, "HOLD_SATISFIED"


def is_scalp_prohibited(cfg) -> tuple:
    """(ok, detail) — the operator's standing prohibition, asserted not assumed.

    Scalp mode must be off and the scalp reduce path must be off. Reported
    rather than raised so a config drift surfaces in a test and a log line
    instead of killing a trading process.
    """
    bad = []
    for key in ("SCALP_MODE", "SCALP_REDUCE_ENABLED"):
        if bool(getattr(cfg, key, False)):
            bad.append(key)
    if bad:
        return False, "SCALP PROHIBITED but enabled: " + ",".join(bad)
    return True, "scalp off"
