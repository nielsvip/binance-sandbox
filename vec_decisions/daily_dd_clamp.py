"""DAILY DRAWDOWN CLAMP — after an account loses a day's worth of value, every trade goes to MIN size until it heals.

OPERATOR 2026-10-28, verbatim:
  "all account were depleted by stupid trading decisions and no backstops the only thing I could recommend is a system
   that reduces any trade to min pos size until it recovers at least 30% of losses after an account loses <3% of its
   value in a day"

STATUS: PURE CORE ONLY. Nothing imports this yet, no config field exists, no manager calls it, no template row. It
cannot change a single order until it is wired, which is a live-money decision for the operator (CLAUDE.md NEW STRATEGY
PROHIBITION: present the logic first; never wire without a kill switch). The numbers below are what it WOULD have done.

WHAT IT IS NOT: not a halt. A clamped account keeps trading the vector's decisions, at minimum size. The operator's
standing law is that live follows the vector and that not-trading is its own risk; this throttles exposure instead of
refusing trades, so the board's signal is still expressed and the account can still recover.

MEASURED on data/live_desk/account_values.json (real per-account UTC-day equity, 2026-10-25..28), trigger 3%,
recovery 30%:
    fin  -11.87% on 10-26 -> CLAMP, still clamped through 10-28 (equity 70.12 -> 55.02)
    ang  -10.44% on 10-26 -> CLAMP, still clamped through 10-28 (160.39 -> 128.41)
    men   -3.83% on 10-27 -> CLAMP, still clamped (214.62 -> 198.89)
    flz / inf / trb        -> never trigger (worst days -1.82% / -2.98% / -0.16%)
Those three clamped accounts are EXACTLY the three now refusing opens with -2019/-2027 (fin 613, ang 211, men 78), and
the clamp would have fired two days before the first refusal. inf's -2.98% missing the threshold by 0.02pp is worth
knowing: 3% is a real edge on this data, not a round number with slack.

SEMANTICS, and the four judgement calls in them (each one the operator can overrule):

1. "value" = EQUITY (cash + open P&L), the `equity` field already written per UTC day by the live desk. NOT available
   margin: fin's equity is $55.02 while its available margin is $0.69, because the rest is tied up in open positions.
   A clamp keyed on available margin would fire on a fully-invested healthy account.

2. "in a day" = against the previous UTC day's CLOSING equity, the natural day-start anchor. The alternative (highest
   equity so far today) would make the trigger depend on intraday noise.

3. "recovers at least 30% of losses" = 30% of the drawdown measured to its TROUGH, not to the trigger bar:
       release when equity >= trough + 0.30 * (anchor - trough)
   so an account that keeps falling has to climb 30% of the WHOLE hole, not 30% of the first day's slice. Measured
   consequence on fin: the release bar rises from $60.80 to $59.55 as the trough deepens, i.e. it tracks reality.

4. The clamp PERSISTS ACROSS UTC DAY BOUNDARIES until release. The operator said "until it recovers", not "until
   tomorrow", and all three real cases are still clamped days later. A daily reset would have released fin on 10-27
   straight back into the losses that followed.

The core is pure: no I/O, no clock, no config import, no manager import. The caller owns persistence (a per-account
state file that must survive a restart) and supplies the venue's minimum size.
"""
from typing import NamedTuple, Optional, Tuple

DEFAULT_TRIGGER_PCT = 3.0
DEFAULT_RECOVERY_FRAC = 0.30


class ClampState(NamedTuple):
    """Per-account, persisted by the caller. `anchor`/`trough` are only meaningful while clamped."""
    clamped: bool = False
    anchor: Optional[float] = None   # equity the drawdown is measured from (previous UTC day's close)
    trough: Optional[float] = None   # lowest equity seen since the clamp engaged


def day_loss_pct(anchor: float, equity: float) -> float:
    """Loss as a POSITIVE percentage of the anchor. Negative result = the account is up."""
    a = float(anchor or 0.0)
    if a <= 0.0:
        return 0.0
    return (a - float(equity)) / a * 100.0


def release_level(anchor: float, trough: float, recovery_frac: float = DEFAULT_RECOVERY_FRAC) -> float:
    """Equity at which the clamp lifts: the trough plus `recovery_frac` of the drawdown to that trough."""
    a, t = float(anchor), float(trough)
    if a <= t:
        return t
    return t + float(recovery_frac) * (a - t)


def step(state: ClampState, prev_day_equity: Optional[float], equity: Optional[float],
         trigger_pct: float = DEFAULT_TRIGGER_PCT,
         recovery_frac: float = DEFAULT_RECOVERY_FRAC) -> Tuple[ClampState, str]:
    """Advance one observation. -> (new_state, reason).

    `prev_day_equity` is the anchor candidate (previous UTC day's closing equity); `equity` is now.
    Unknown inputs never change the state: a missing feed must not silently release a clamped account.
    """
    if equity is None or prev_day_equity is None:
        return state, "NO_DATA"
    eq = float(equity)
    if not state.clamped:
        loss = day_loss_pct(prev_day_equity, eq)
        if loss >= float(trigger_pct):
            return ClampState(True, float(prev_day_equity), eq), \
                "CLAMP day_loss=%.2f%% >= %.2f%% anchor=%.2f equity=%.2f" % (loss, trigger_pct, prev_day_equity, eq)
        return state, "OK day_loss=%.2f%%" % loss
    # clamped: deepen the trough, then test the release bar against it
    anchor = float(state.anchor if state.anchor is not None else prev_day_equity)
    trough = min(float(state.trough if state.trough is not None else eq), eq)
    need = release_level(anchor, trough, recovery_frac)
    if eq >= need:
        return ClampState(False, None, None), \
            "RELEASE equity=%.2f >= %.2f (recovered %.0f%% of %.2f drawdown)" % (eq, need, recovery_frac * 100.0, anchor - trough)
    return ClampState(True, anchor, trough), \
        "CLAMPED equity=%.2f < release=%.2f (trough=%.2f anchor=%.2f)" % (eq, need, trough, anchor)


def clamp_size(requested: float, min_size: float, clamped: bool) -> float:
    """The only sizing effect: a clamped account opens at the venue minimum, never larger, never zero.

    Returning the MINIMUM rather than 0.0 is deliberate -- this is a throttle, not a halt, so the vector's decision is
    still executed and the account can still recover. A caller with no usable minimum gets `requested` back unchanged
    (fail-open) rather than an order of 0, which would be an undocumented halt.
    """
    r, m = float(requested or 0.0), float(min_size or 0.0)
    if not clamped or m <= 0.0:
        return r
    return m if r > m else r


def _selftest():
    # the three real accounts, from data/live_desk/account_values.json
    series = {
        "fin": [70.12175776, 61.79705146, 56.80769253, 55.01502734],
        "ang": [160.38963132, 143.6506098, 128.18818128, 128.41325495],
        "men": [214.61989983, 208.31005171, 200.32287841, 198.89333472],
        "flz": [101.57317976, 99.71972387, 97.40670604, 97.04958915],
        "inf": [34.88976481, 33.84879553, 33.74998785, 33.98864069],
    }
    fired = {}
    for acct, eqs in series.items():
        st = ClampState()
        for i in range(1, len(eqs)):
            st, why = step(st, eqs[i - 1], eqs[i])
            if why.startswith("CLAMP ") and acct not in fired:
                fired[acct] = i
        series[acct] = st.clamped
    assert set(fired) == {"fin", "ang", "men"}, fired
    assert fired["fin"] == 1 and fired["ang"] == 1 and fired["men"] == 2, fired
    assert series["fin"] and series["ang"] and series["men"], "all three stay clamped on this data"
    assert not series["flz"] and not series["inf"], "healthy accounts must never clamp"

    # release arithmetic: anchor 100, trough 80 -> needs 80 + 0.3*20 = 86
    st = ClampState(True, 100.0, 80.0)
    st2, why = step(st, 100.0, 85.9)
    assert st2.clamped and "release=86.00" in why, why
    st3, why3 = step(st, 100.0, 86.0)
    assert not st3.clamped and "RELEASE" in why3, why3

    # a deepening trough RAISES the hole but LOWERS the bar, and never releases on the way down
    st4, _ = step(ClampState(True, 100.0, 80.0), 100.0, 70.0)
    assert st4.clamped and st4.trough == 70.0
    assert abs(release_level(100.0, 70.0) - 79.0) < 1e-9

    # a missing feed must not release a clamped account
    st5, why5 = step(ClampState(True, 100.0, 80.0), None, None)
    assert st5.clamped and why5 == "NO_DATA"

    # sizing: throttle, not halt; fail-open without a minimum; never upsizes a request already at/below min
    assert clamp_size(60.0, 5.0, True) == 5.0
    assert clamp_size(60.0, 5.0, False) == 60.0
    assert clamp_size(3.0, 5.0, True) == 3.0
    assert clamp_size(60.0, 0.0, True) == 60.0
    print("selftest OK: clamps exactly {fin, ang, men}, release bar tracks the trough, no-data never releases")


if __name__ == "__main__":
    _selftest()
