"""CASH-ACCOUNT SETTLEMENT (Good-Faith-Violation) rule — ONE implementation shared by live and both backtest engines.

USER 2026-10-14: "no violations accepted; make sure it works identically in the vectorized backtests of the v15_pilot so
parity can never be put in danger."

Who uses it
  * live  tradier_manage.GFVTracker (settlement dates, strict buy-leg restriction on cash accounts, owed settlement exits)
  * vector v12_quick_engine.simulate_one (SettlementBook + bar audit; inert unless the board's SIM_ACCOUNT is a cash account)
  * scalar backtest_v12_engine (real tradier execute_now / GFVTracker on the sim clock + board-local settled cash)

The rule (cash account, CASH_ACCOUNT_SETTLEMENT_ENABLED=True)
  1. Entry-banned account (order_dedupe_guard.HARD_ENTRY_BANNED_ACCOUNTS = {tra} + WIRE_ENTRY_BANNED_ACCOUNTS): no buy leg
     ever (OPEN / REENTRY / AUGMENT). This holds for ANY account type (live refuses BLOCKED_ACCOUNT_ENTRY_BANNED).
  2. Strict settled-cash gate (board-local): a buy leg on ET date D is refused while proceeds of this board's own sale are
     unsettled (their settlement date > D). Live refuses with GFV_SETTLED_CASH_BLOCK when cost > broker settled cash; the
     per-board backtest cannot see the account's other cash, so it uses the strict board-local reading (never buys with
     its own unsettled proceeds).
  3. Strict sell hold: every buy leg on ET date D restricts selling that position until next_settlement_day(D) (T+1
     business day). This is live's GFVTracker restriction under the worst-case account state (unsettled sales outstanding
     at buy time -> restricted until their latest settlement, which is <= T+1 of the buy).
  4. A sell (full close or partial reduce) requested while restricted is OWED: it is held (no other action on the position)
     and executed at the first regular-session bar whose ET date >= the settlement date, at that bar's price.
Margin / paper accounts: rules 2-4 never apply (live _gfv_guard_applies() -> False); rule 1 still applies.
Unknown account type -> "cash" (fail closed, identical to live _gfv_guard_applies).

Settlement calendar: weekdays that are neither NYSE holidays nor Federal-Reserve settlement holidays (Columbus Day,
Veterans Day: the exchange is open but DTC does not settle). Rule-based (no yearly list to forget) + known one-off closures.
Stdlib only, no side effects at import.
"""
from __future__ import annotations

import copy
import datetime as dt
import functools
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SWITCH = "CASH_ACCOUNT_SETTLEMENT_ENABLED"
DEFERRED_TAG = "CASH_SETTLEMENT_DEFERRED"
BUY_TYPES = ("OPEN", "AUGMENT", "REENTRY", "QUICK_OPEN", "QUICK_AUGMENT")
SELL_TYPES = ("CLOSE", "REDUCE")
NON_SETTLING_TYPES = ("margin", "paper")
# one-off NYSE full-day closures (national days of mourning etc.) that no rule produces
SPECIAL_CLOSURES = frozenset({dt.date(2018, 12, 5), dt.date(2025, 1, 9)})
FALLBACK_BANNED = frozenset({"tra"})


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _observed(d: dt.date, saturday_to_friday: bool = True) -> Optional[dt.date]:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


@functools.lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset:
    out = set()
    ny = _observed(dt.date(year, 1, 1), saturday_to_friday=False)  # NYSE never closes Dec 31 for a Saturday New Year
    if ny:
        out.add(ny)
    out.add(_nth_weekday(year, 1, 0, 3))  # MLK
    out.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    out.add(_easter(year) - dt.timedelta(days=2))  # Good Friday
    out.add(_last_weekday(year, 5, 0))  # Memorial Day
    if year >= 2022:
        out.add(_observed(dt.date(year, 6, 19)))  # Juneteenth
    out.add(_observed(dt.date(year, 7, 4)))
    out.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    out.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    out.add(_observed(dt.date(year, 12, 25)))
    out |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(x for x in out if x is not None and x.year == year)


@functools.lru_cache(maxsize=64)
def settlement_holidays(year: int) -> frozenset:
    out = set(nyse_holidays(year))
    out.add(_nth_weekday(year, 10, 0, 2))  # Columbus Day (Fed closed -> no DTC settlement, exchange open)
    vd = _observed(dt.date(year, 11, 11), saturday_to_friday=False)  # Veterans Day (Fed: Sat not observed, Sun -> Mon)
    if vd:
        out.add(vd)
    return frozenset(out)


def is_exchange_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def is_settlement_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in settlement_holidays(d.year)


def next_settlement_day(d: Any) -> dt.date:
    """T+1: the first settlement business day strictly after trade date d (date / datetime / epoch seconds)."""
    day = to_et_date(d)
    day += dt.timedelta(days=1)
    for _ in range(30):
        if is_settlement_day(day):
            return day
        day += dt.timedelta(days=1)
    return day


def to_et_date(x: Any) -> dt.date:
    if isinstance(x, dt.datetime):
        d = x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(ET).date()
    if isinstance(x, dt.date):
        return x
    t = float(x)
    if t > 1e11:
        t /= 1000.0
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(ET).date()


# ───────────────────────────── account resolution (same answers live + vector) ─────────────────────────────
def _get(cfg: Any, name: str, default=None):
    try:
        v = getattr(cfg, name)
        return default if v is None else v
    except Exception:
        return default


def account_type(cfg: Any, account: Any) -> str:
    """ACCOUNT_TYPE_<ACCT> (cash | margin | paper); unknown -> cash (fail closed, = tradier_manage._gfv_guard_applies)."""
    return str(_get(cfg, "ACCOUNT_TYPE_%s" % str(account or "").upper(), "cash") or "cash").lower()


def settlement_applies(cfg: Any, account: Any) -> bool:
    return bool(_get(cfg, SWITCH, True)) and account_type(cfg, account) not in NON_SETTLING_TYPES


def banned_accounts(cfg: Any = None) -> frozenset:
    try:
        import order_dedupe_guard as _odg  # stdlib-only module

        hard = frozenset(_odg.HARD_ENTRY_BANNED_ACCOUNTS)
    except Exception:
        hard = FALLBACK_BANNED
    extra = set()
    try:
        extra = {str(x).lower() for x in (_get(cfg, "WIRE_ENTRY_BANNED_ACCOUNTS", ()) or ())}
    except Exception:
        extra = set()
    return frozenset({str(a).lower() for a in hard} | extra)


def sim_account(cfg: Any, default: str = "trb") -> str:
    return str(_get(cfg, "SIM_ACCOUNT", "") or _get(cfg, "SIM_TRADING_ACCOUNT", "") or default).lower()


def board_account(symbol: str, side: str, cfg: Any = None, live_accounts: Optional[Iterable[str]] = None) -> str:
    """The account a stock board is evaluated as (pilot hook). A sym_side can be live on several accounts while
    tradier_vec_exact emits ONE decision stream per sym_side, so the board is evaluated under the STRICTEST account that
    can actually open it: entry-banned accounts are skipped (they never open a board), side-restricted accounts
    (ACCOUNT_SIDE_MAPPING / TRA_LONG_ONLY) are skipped for the other side, and if any remaining account is a settling
    (cash) account the board is evaluated as that account. Default trb."""
    accts = [str(a).lower() for a in (live_accounts if live_accounts is not None else (_get(cfg, "ACCOUNT_KEYS", None) or ["tra", "trb", "trc"]))]
    banned = banned_accounts(cfg)
    side_u = str(side or "").upper()
    mapping = _get(cfg, "ACCOUNT_SIDE_MAPPING", {}) or {}
    ok = []
    for a in accts:
        if a in banned:
            continue
        allowed = mapping.get(a) if isinstance(mapping, dict) else None
        if allowed and side_u and side_u not in [str(x).upper() for x in allowed]:
            continue
        ok.append(a)
    if not ok:
        return "trb"
    for a in ok:
        if settlement_applies(cfg, a):
            return a
    return "trb" if "trb" in ok else ok[0]


# ───────────────────────────── per-board book (vector + tests) ─────────────────────────────
class SettlementBook:
    """Board-local settlement state for one sym_side on one account."""

    def __init__(self, cfg: Any = None, account: Optional[str] = None):
        self.account = (account or sim_account(cfg)).lower()
        self.banned = self.account in banned_accounts(cfg)
        self.settling = settlement_applies(cfg, self.account)
        self.restricted_until: Optional[dt.date] = None
        self.unsettled: List[dt.date] = []
        self.owed: Optional[Dict[str, Any]] = None
        self.stats = {"buy_refused": 0, "sell_deferred": 0, "owed_executed": 0}

    @property
    def active(self) -> bool:
        return self.banned or self.settling

    def buy_allowed(self, ts: Any) -> bool:
        if self.banned:
            return False
        if not self.settling:
            return True
        d = to_et_date(ts)
        return all(s <= d for s in self.unsettled)

    def can_sell(self, ts: Any) -> bool:
        if not self.settling or self.restricted_until is None:
            return True
        return to_et_date(ts) >= self.restricted_until

    def on_buy(self, ts: Any) -> None:
        if not self.settling:
            return
        r = next_settlement_day(ts)
        self.restricted_until = r if self.restricted_until is None else max(self.restricted_until, r)

    def on_sell(self, ts: Any, flat: bool) -> None:
        if not self.settling:
            return
        d = to_et_date(ts)
        self.unsettled = [s for s in self.unsettled if s > d] + [next_settlement_day(d)]
        if flat:
            self.restricted_until = None

    def needs_snapshot(self, ts: Any, holding: bool) -> bool:
        """True when an action at this bar could be forbidden (then the bar's state is snapshotted for rollback)."""
        if not self.active:
            return False
        if not self.buy_allowed(ts):
            return True
        return bool(holding) and not self.can_sell(ts)


def snapshot(book: SettlementBook, ts: Any, pos: Any, trades: list, events: list, extra: Dict[str, Any]) -> Dict[str, Any]:
    snap = {"nt": len(trades), "ne": len(events), "ts": ts, "pos": None, "full": False}
    if book.needs_snapshot(ts, pos is not None):
        snap.update({"full": True, "pos": copy.deepcopy(pos), "extra": copy.deepcopy(extra)})
    return snap


def _kind(rec: Dict[str, Any]) -> str:
    return str(rec.get("type") or "").upper()


def audit_bar(book: SettlementBook, snap: Dict[str, Any], trades: list, events: list, pos_after: Any) -> Dict[str, Any]:
    """Judge the actions one bar produced. Returns {"rollback": bool, "owed": dict|None}. On no rollback the book
    absorbs the bar's accepted buys / sells. Pure w.r.t. the engine (the caller restores its locals on rollback)."""
    new = list(trades[snap["nt"]:]) + list(events[snap["ne"]:])
    if not new:
        return {"rollback": False, "owed": None}
    ts = snap["ts"]
    buys = [r for r in new if _kind(r) in BUY_TYPES]
    sells = [r for r in new if _kind(r) in SELL_TYPES]
    held_before = snap["pos"] is not None if snap.get("full") else None
    forbid_buy = bool(buys) and not book.buy_allowed(ts)
    forbid_sell = bool(sells) and snap.get("full") and held_before and not book.can_sell(ts)
    if (forbid_buy or forbid_sell) and snap.get("full"):
        owed = None
        if forbid_sell:
            full = any(_kind(r) == "CLOSE" for r in sells)
            q = sum(float(r.get("qty") or 0.0) for r in sells if _kind(r) == "REDUCE")
            why = next((str(r.get("exit_reason") or r.get("reason") or "") for r in sells), "")
            owed = {"full": bool(full), "qty": float(q), "reason": why[:120], "bar_ts": ts}
            book.stats["sell_deferred"] += 1
        if forbid_buy:
            book.stats["buy_refused"] += 1
        return {"rollback": True, "owed": owed}
    for r in sorted(new, key=lambda x: 0 if _kind(x) in BUY_TYPES else 1):
        if _kind(r) in BUY_TYPES:
            book.on_buy(r.get("ts") or ts)
    if sells:
        book.on_sell(ts, flat=pos_after is None)
    return {"rollback": False, "owed": None}


def merge_owed(prev: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if new is None:
        return prev
    if prev is None:
        return new
    return {"full": prev["full"] or new["full"], "qty": prev["qty"] + new["qty"], "reason": prev["reason"] or new["reason"], "bar_ts": prev["bar_ts"]}


def deferred_reason(owed: Dict[str, Any]) -> str:
    return f"{DEFERRED_TAG}[{str(owed.get('reason') or '')[:80]}]"
