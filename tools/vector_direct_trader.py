#!/usr/bin/env python3
"""vector_direct_trader — S1 (crypto) / S5 (stocks) trade the forward vector state directly. No ez_/tradier_ scripts.

USER 2026-10-27 (verbatim): "S1 waits, gives up, and nobody trades. <- that is EXACTLY the behavior that can NEVER happen.
s1 starts trading immediately if it sees macbook is not trading or is missing any piece of information ... JUST TRADE THE
STATE IT CALCULATES EVERY 15min FOR EVERY CRYPTO SYM_SIDE ... Also s5 has to do the same for stocks as soon as macbook
misses a beat." and "a tiny trading script that immediately executes both entry/exit/augment/reduce bar AND quantity with
START_POSITION_SIZE 15 until proven reliable." and "unreliable ssh is another reason s1 needs to fall back to its own direct
vector trading script immediately when it misses a trade".

OPERATOR EXCEPTION (recorded in LOCKED_FILES.md change log 2026-10-27): orders leave from THIS script over the venue REST API
(Binance USD-M futures / Tradier), not through ez_manage.execute_now — the user ordered that no ez_/tradier_ script runs or is
imported on S1/S5.

DECISION SOURCE  data/forward_state/<SYM_SIDE>.json (tools/v15_forward_state.py, recomputed per closed 15m bar from the
                 published per-sym set through tools/opt/v12_pilot + v12_quick_engine). `open` is the board state;
                 `acts_last_bar` (+ engine_qty / engine_open_qty) are the engine's OPEN/AUGMENT/REDUCE/CLOSE at that bar.
CONVERGENCE      per (account, sym_side), against the REAL broker position read right before sending:
                   board flat, broker holds       -> CLOSE the whole broker qty (reduce-only semantics)
                   board holds, broker flat       -> OPEN START_POSITION_SIZE x (engine_qty / engine_open_qty), entry books only
                   board holds, broker holds      -> the engine's AUGMENT/REDUCE at that bar, scaled to the broker qty
                 Idempotent by key (account|sym_side|bar|act); ledger data/vector_direct/ledger_<venue>_<day>.jsonl.
ACTIVATION       per account, immediate: Mac heartbeat absent/stale (ssh push failed) -> ALL accounts; the Mac's own health
                 names an account dead/unreachable/order-path-dead -> that account; the board acted (OPEN/CLOSE) at a bar and
                 the broker does not reflect it within the grace -> that account. Hand back only after a minimum hold AND a
                 continuous healthy stretch; repeated takeovers double the hold (no ping-pong).
SAFETY           data/vector_direct/KILL stops every order instantly; --dry-run logs the orders it would send; a 418/429 from
                 Binance blocks all REST until Retry-After (the IP was banned before — positionRisk at most once per pass).

USER 2026-10-27 (verbatim): "keep going for 4 hours make sure it trades correctly then switch back to macbook complete scripts
so we can keep discovering VEC_ logs that mean template rows and vec functions are still missing in the real scripts, and
switch back immediately as soon as macbook has any data or trading issue until it comes back online. Have it paper tade the
rest of the time and alert and execute any order that was not executed by macbook indicating a backstop failure."
PIN              data/vector_direct/pin_<venue>.json {"until_epoch", "accounts"} keeps those accounts owned until it expires.
PAPER            accounts the Mac trades are planned every bar against the real broker position and written (never sent) to
                 paper_<venue>_<day>.jsonl — what S1 would do.
BACKSTOP         a board OPEN/CLOSE the broker still does not show after the grace is NOT an account takeover (that would stand
                 the Mac engine down and hide its VEC_ logs): S1 alerts (ALERTS.jsonl) and sends exactly that one order.
                 Mac data/trading faults (heartbeat, health kinds, engine dead) still take the account over until healthy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import socket
import sys
import time
import urllib.parse
from pathlib import Path

BAR_S = 900
CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
STOCK_ACCOUNTS = ("tra", "trb", "trc")
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
# ═══ OPERATOR SIZING MANDATE 2026-10-28, VERBATIM: "stock and crypto are not the same pls DONT FUCK THIS UP
# stocks 900 crypto 15 NO EXCEPTION make sure this is ABSOLUTELY HONORED in the new vector trading scripts on
# s1 and s5". These were ONE venue-blind pair of module constants defaulting to the CRYPTO value (15), so the
# stocks trader on S5 ran on a $15 slice for days and kept falling through to the one-whole-share path. The
# default is now chosen BY VENUE and the mandated START size is PINNED: a wrong value is corrected and logged,
# never silently traded, and never a refusal to trade (a sizing mistake must not stop the book).
VENUE_SIZING = {
    "crypto": {"START_POSITION_SIZE": 15.0, "MAX_OPEN_NOTIONAL": 80.0, "STOCK_ONE_SHARE_MAX": 0.0},
    "stocks": {"START_POSITION_SIZE": 900.0, "MAX_OPEN_NOTIONAL": 4000.0, "STOCK_ONE_SHARE_MAX": 4000.0},
}
MANDATED_START = {"crypto": 15.0, "stocks": 900.0}  # NO EXCEPTION without VD_ALLOW_SIZE_OVERRIDE=1
START_POSITION_SIZE = float(os.environ.get("VD_START_POSITION_SIZE", "15"))
MAX_OPEN_NOTIONAL = float(os.environ.get("VD_MAX_OPEN_NOTIONAL", "110"))
STOCK_ONE_SHARE_MAX = float(os.environ.get("VD_STOCK_ONE_SHARE_MAX", "500"))


def apply_venue_sizing(venue: str) -> dict:
    """Resolve the three sizing knobs for THIS venue and pin the operator's mandated START size.

    Precedence: VD_<KNOB>_<VENUE> (venue-specific, cannot cross-contaminate) > VD_<KNOB> (shared) >
    the per-venue default above. Then the mandate is enforced on START_POSITION_SIZE. Returns a report
    dict for the startup log so the live value is verifiable at a glance.
    """
    global START_POSITION_SIZE, MAX_OPEN_NOTIONAL, STOCK_ONE_SHARE_MAX
    v = "stocks" if str(venue).lower().startswith("stock") else "crypto"
    rep = {"venue": v, "pinned": [], "source": {}}
    for knob, dflt in VENUE_SIZING[v].items():
        spec = os.environ.get(f"VD_{knob}_{v.upper()}")
        shared = os.environ.get(f"VD_{knob}")
        if spec not in (None, ""):
            val, src = float(spec), f"VD_{knob}_{v.upper()}"
        elif shared not in (None, ""):
            val, src = float(shared), f"VD_{knob}"
        else:
            val, src = float(dflt), "venue_default"
        if knob == "START_POSITION_SIZE":
            want = MANDATED_START[v]
            if abs(val - want) > 1e-9 and os.environ.get("VD_ALLOW_SIZE_OVERRIDE") != "1":
                rep["pinned"].append(f"START_POSITION_SIZE {val} -> {want} ({v} mandate; {src} ignored)")
                val, src = want, f"{v}_mandate_pin"
        rep["source"][knob] = f"{val} via {src}"
        globals()[knob] = val
    START_POSITION_SIZE = globals()["START_POSITION_SIZE"]
    MAX_OPEN_NOTIONAL = globals()["MAX_OPEN_NOTIONAL"]
    STOCK_ONE_SHARE_MAX = globals()["STOCK_ONE_SHARE_MAX"]
    return rep
HB_STALE_S = float(os.environ.get("VD_HB_STALE_S", "90"))
ACCT_DEAD_S = float(os.environ.get("VD_ACCT_DEAD_S", "180"))
ENGINE_DOWN_S = float(os.environ.get("VD_ENGINE_DOWN_S", "90"))  # trader_alive=0 this long (a routine restart is shorter)
MISS_GRACE_S = float(os.environ.get("VD_MISS_GRACE_S", "90"))
MISS_MIN_AFTER_CLOSE_S = float(os.environ.get("VD_MISS_MIN_AFTER_CLOSE_S", "180"))
HOLD_MIN_S = float(os.environ.get("VD_HOLD_MIN_S", "1800"))
HOLD_MAX_S = float(os.environ.get("VD_HOLD_MAX_S", "14400"))
RELEASE_HEALTHY_S = float(os.environ.get("VD_RELEASE_HEALTHY_S", "600"))
REPEAT_WINDOW_S = float(os.environ.get("VD_REPEAT_WINDOW_S", "7200"))
STATE_MAX_LAG_S = float(os.environ.get("VD_STATE_MAX_LAG_S", "1800"))
REPLAN_S = float(os.environ.get("VD_REPLAN_S", "60"))
# 2026-10-28 USER "s1 never misses a beat ... executes it immediately": the loop wakes as soon as a state lands in the state
# dir (dir mtime) or tools/s1_bar_guarantee.py writes data/vector_direct/PLAN_NOW_<venue>; MIN_PASS_GAP_S bounds REST weight.
MIN_PASS_GAP_S = float(os.environ.get("VD_MIN_PASS_GAP_S", "2.0"))
MAX_ATTEMPTS = 3
VENUE_WIDE_KINDS_IGNORED = ("account_dead", "broker_unreachable_account")


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def last_closed_bar(now: float) -> int:
    return int(now // BAR_S) * BAR_S - BAR_S


def is_crypto_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(CRYPTO_SUFFIXES)


def split_ss(ss: str):
    symbol, side = ss.rsplit("_", 1)
    return symbol.upper(), side.upper()


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    os.replace(tmp, path)


def load_env_files(paths) -> dict:
    """KEY = VALUE lines (spaces and quotes tolerated). Values never printed."""
    env = {}
    for p in paths:
        try:
            for line in Path(p).expanduser().read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().replace("export ", "").strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in env:
                    env[k] = v
        except OSError:
            continue
    return env


# ───────────────────────────── books ─────────────────────────────
def entry_allowed(root: Path, venue: str, acct: str, ss: str) -> bool:
    """Same books live uses: tools/account_comparison_books (crypto: side book + tradeable key; stocks: side book)."""
    symbol, side = split_ss(ss)
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.account_comparison_books import crypto_entry_allowed, side_allowed
        if venue == "crypto":
            return bool(crypto_entry_allowed(root, acct, symbol, side))
        return bool(side_allowed(root, acct, symbol, side))
    except Exception:
        src = {"trc": "trb"}.get(acct, acct)
        book = read_json(root / f"symbols_{src}_{side.lower()}.json", []) or []
        if symbol not in {str(x).upper() for x in book}:
            return False
        if venue == "crypto":
            keys = read_json(root / "tradeable_keys.json", []) or []
            return f"{acct}:{symbol}_{side}" in keys
        return True


def universe(root: Path, venue: str, accounts) -> dict:
    out = {a: set() for a in accounts}
    if venue == "crypto":
        for k in read_json(root / "tradeable_keys.json", []) or []:
            if ":" not in str(k):
                continue
            acct, ss = str(k).split(":", 1)
            if acct in out and is_crypto_symbol(ss.rsplit("_", 1)[0]):
                out[acct].add(ss.upper())
    else:
        for acct in accounts:
            src = {"trc": "trb"}.get(acct, acct)
            for side in ("long", "short"):
                for s in read_json(root / f"symbols_{src}_{side}.json", []) or []:
                    out[acct].add(f"{str(s).upper()}_{side.upper()}")
    return out


# ───────────────────────────── decisions (pure) ─────────────────────────────
def state_fresh(st: dict, bar: int) -> bool:
    try:
        return bool(st and st.get("ok")) and int(st.get("npz_tail") or 0) >= bar - STATE_MAX_LAG_S
    except Exception:
        return False


def wt15_against(ss, wt):
    """(against, detail) for a held side from the latest closed 15m WaveTrend {bar_ts, wt1_15m, wt2_15m}.
    LONG: wt1_15m < wt2_15m (bearish); SHORT: wt1_15m > wt2_15m (bullish). None when the reading is missing."""
    try:
        w1, w2 = float(wt["wt1_15m"]), float(wt["wt2_15m"])
    except Exception:
        return None, "no wt15 reading"
    if not (math.isfinite(w1) and math.isfinite(w2)):
        return None, "wt15 not finite"
    _sym, side = split_ss(ss)
    against = w1 < w2 if side == "LONG" else w1 > w2
    return against, f"wt1_15m={w1:.3f} wt2_15m={w2:.3f} bar={wt.get('bar_ts')}"


NOINFO_GRACE_S = float(os.environ.get("VD_NOINFO_GRACE_S", str(BAR_S + 180)))
NOINFO_CLOSE = os.environ.get("VD_NOINFO_CLOSE", "1") != "0"


def no_information(st) -> bool:
    """USER 2026-10-27 "any sym_side with no information is considered non tradeable and closed". Coordinator scope:
    ONLY a side the per-sym store truly has no settings for (no state at all, or `missing live settings` from
    missing_published_per_sym_row after the best-existing-settings fallback also found nothing). Pipeline failures
    (override/validation/prepare/stale-NPZ/env) are NOT "no information" and never close anything."""
    if not isinstance(st, dict):
        return True
    return (not st.get("ok")) and st.get("reason") == "missing live settings" and st.get("src") == "missing_published_per_sym_row"


def plan_account(acct, venue, keys, states, positions, bar, done, allowed, prev_qty=None, wt15=None, noinfo_age=None):
    """Orders that converge one account to the board. positions: {ss: {"qty": abs qty, "price": px}} read just now.
    prev_qty: {ss: broker qty observed BEFORE this bar closed} — an AUGMENT/REDUCE the broker already shows was done by
    someone else (the Mac) and is not repeated. wt15: callable ss -> {bar_ts, wt1_15m, wt2_15m} of the latest closed 15m
    bar. USER 2026-10-27 "non tradeable keys get closes as soon as wt_15m goes against it": a held side that is NOT one of
    this account's tradeable keys is CLOSED (NON_TRADEABLE_WT15_AGAINST) on a fresh wt15 reading against it, whatever its
    board state says. Returns (orders, notes)."""
    orders, notes = [], []
    prev_qty = prev_qty or {}
    keyset = set(keys)
    held_sides = [s for s, p in positions.items() if float(p.get("qty") or 0) > 0]
    # no-information sides past the grace (one full bar past the state deadline) are treated as non-tradeable; a breaker
    # stops it when many sides lose information at once (that is a pipeline outage, not missing settings)
    noinfo = set()
    if NOINFO_CLOSE and noinfo_age is not None:
        aged = {s for s in held_sides if (noinfo_age(s, states.get(s)) or 0) >= NOINFO_GRACE_S}
        if len(aged) > max(3, 0.25 * len(held_sides)):
            notes.extend((s, f"NOINFO_BREAKER {len(aged)}/{len(held_sides)} held sides without information — no closes") for s in sorted(aged))
        else:
            noinfo = aged
    for ss in sorted(keyset | set(held_sides)):
        st = states.get(ss)
        held = float((positions.get(ss) or {}).get("qty") or 0.0)
        if held > 0 and ss in noinfo:
            why_state = "no state file" if not isinstance(st, dict) else f"state ok={st.get('ok')} reason={st.get('reason')} src={st.get('src')}"
            wt = (wt15(ss) or {}) if wt15 is not None else {}
            fresh_wt = int(wt.get("bar_ts") or 0) >= bar - STATE_MAX_LAG_S
            against, detail = wt15_against(ss, wt) if fresh_wt else (None, "wt15 stale/unavailable")
            if against:
                orders.append({"acct": acct, "ss": ss, "act": "CLOSE", "qty": held, "key": f"{acct}|{ss}|{int(wt['bar_ts'])}|NON_TRADEABLE_WT15_AGAINST",
                               "why": f"NON_TRADEABLE_WT15_AGAINST (no information: {why_state}) {detail}", "reason": "NON_TRADEABLE_WT15_AGAINST"})
            elif against is None:
                orders.append({"acct": acct, "ss": ss, "act": "CLOSE", "qty": held, "key": f"{acct}|{ss}|{bar}|NO_INFORMATION_CLOSE",
                               "why": f"NO_INFORMATION_CLOSE ({why_state}; {detail})", "reason": "NO_INFORMATION_CLOSE"})
            else:
                notes.append((ss, f"NO_INFORMATION_WT15_WITH {why_state} {detail}"))
            continue
        if held > 0 and ss not in keyset and wt15 is not None:
            wt = wt15(ss) or {}
            if int(wt.get("bar_ts") or 0) >= bar - STATE_MAX_LAG_S:
                against, detail = wt15_against(ss, wt)
                if against:
                    orders.append({"acct": acct, "ss": ss, "act": "CLOSE", "qty": held, "key": f"{acct}|{ss}|{int(wt['bar_ts'])}|NON_TRADEABLE_WT15_AGAINST",
                                   "why": f"NON_TRADEABLE_WT15_AGAINST {detail}", "reason": "NON_TRADEABLE_WT15_AGAINST"})
                    continue
                notes.append((ss, f"NON_TRADEABLE_WT15_WITH {detail}" if against is False else f"NON_TRADEABLE_WT15_UNKNOWN {detail}"))
            else:
                notes.append((ss, "NON_TRADEABLE_WT15_STALE"))
        if not state_fresh(st, bar):
            if held > 0:
                notes.append((ss, "HOLD_NO_FRESH_STATE"))
            continue
        tail = int(st.get("npz_tail"))
        board_open = bool(st.get("open"))
        if not board_open and held > 0:
            orders.append({"acct": acct, "ss": ss, "act": "CLOSE", "qty": held, "key": f"{acct}|{ss}|{tail}|CLOSE",
                           "why": f"board flat, broker holds {held}"})
        elif board_open and held <= 0:
            if not allowed(acct, ss):
                notes.append((ss, "SKIP_OPEN_NOT_IN_BOOKS"))
                continue
            eq, oq = float(st.get("engine_qty") or 0.0), float(st.get("engine_open_qty") or 0.0)
            ratio = min(4.0, max(0.25, eq / oq)) if eq > 0 and oq > 0 else 1.0
            anchor = st.get("engine_open_bar_ts") or st.get("entry_bar") or tail
            orders.append({"acct": acct, "ss": ss, "act": "OPEN", "notional": START_POSITION_SIZE * ratio,
                           "key": f"{acct}|{ss}|{anchor}|OPEN", "why": f"board holds since {anchor}, broker flat (engine qty ratio {ratio:.3f})"})
        elif board_open and held > 0:
            if int(st.get("acts_bar_ts") or -1) != tail:
                continue
            base = float(prev_qty.get(ss) or held)
            for i, a in enumerate(st.get("acts_last_bar") or []):
                typ = str(a.get("type") or "").upper()
                q, before = float(a.get("qty") or 0.0), float(a.get("qty_before") or 0.0)
                if typ == "AUGMENT" and q > 0 and before > 0:
                    want = base * q / before
                    if prev_qty.get(ss) and held >= base + 0.5 * want:
                        notes.append((ss, f"AUGMENT_ALREADY_ON_BROKER {base}->{held}"))
                        continue
                    orders.append({"acct": acct, "ss": ss, "act": "AUGMENT", "qty": want, "key": f"{acct}|{ss}|{tail}|AUGMENT|{i}",
                                   "why": f"engine AUGMENT {q:.6g} on {before:.6g} ({a.get('reason')})"})
                elif typ == "REDUCE" and a.get("qty_frac"):
                    frac = float(a["qty_frac"])
                    want = base * frac
                    if prev_qty.get(ss) and held <= base - 0.5 * want:
                        notes.append((ss, f"REDUCE_ALREADY_ON_BROKER {base}->{held}"))
                        continue
                    orders.append({"acct": acct, "ss": ss, "act": "REDUCE", "qty": min(held, want), "key": f"{acct}|{ss}|{tail}|REDUCE|{i}",
                                   "why": f"engine REDUCE frac {frac:.4f} ({a.get('reason')})"})
    return [o for o in orders if o["key"] not in done], notes


def expectations_from_states(accounts, keys_by_acct, states, bar, allowed):
    """Board transitions at `bar` that a trading Mac must show on the broker: (acct, ss, 'HOLD'|'FLAT')."""
    out = []
    for acct in accounts:
        for ss in keys_by_acct.get(acct, ()):
            st = states.get(ss)
            if not st or not st.get("ok") or int(st.get("npz_tail") or 0) != bar or int(st.get("acts_bar_ts") or -1) != bar:
                continue
            types = [str(a.get("type") or "").upper() for a in st.get("acts_last_bar") or []]
            if st.get("open") and "OPEN" in types and allowed(acct, ss):
                out.append((acct, ss, "HOLD"))
            elif not st.get("open") and "CLOSE" in types:
                out.append((acct, ss, "FLAT"))
    return out


class Activation:
    """Per-account takeover state with hysteresis. Persisted so a restart keeps ownership."""

    def __init__(self, path: Path, accounts):
        self.path = path
        self.accounts = tuple(accounts)
        self.st = read_json(path, {}) or {}
        self.down_since = {}
        for a in self.accounts:
            self.st.setdefault(a, {"active": False, "since": None, "reason": "", "healthy_since": None, "takeovers": 0, "hold_s": HOLD_MIN_S, "released_at": None})

    def triggers(self, now, hb, venue, missed):
        trig = {}
        self._named_kinds = set()
        hb_epoch = float((hb or {}).get("epoch") or 0.0)
        age = now - hb_epoch if hb_epoch else None
        if age is None or age > HB_STALE_S:
            for a in self.accounts:
                trig[a] = f"mac_heartbeat_{'absent' if age is None else f'stale_{int(age)}s'}"
        else:
            sec = ((hb or {}).get("trading_health") or {}).get(venue) or {}
            if sec.get("applicable") is not False:
                # a kind that names its accounts (<kind>_accounts) takes over only those; a kind without a list is venue-wide
                for kind in sec.get("kinds") or []:
                    named = sec.get(f"{kind}_accounts")
                    if kind in VENUE_WIDE_KINDS_IGNORED and named is None:
                        continue
                    if isinstance(named, list):
                        self._named_kinds.add(kind)
                    for a in (named if isinstance(named, list) else self.accounts):
                        if a in self.accounts:
                            trig.setdefault(a, f"mac_kind:{kind}")
                for lk in ("account_dead_accounts", "broker_dead_accounts", "broker_unreachable_accounts"):
                    for a in sec.get(lk) or []:
                        if a in self.accounts:
                            trig.setdefault(a, f"mac_{lk[:-9]}")
                for a, v in (sec.get("accounts") or {}).items():
                    if a not in self.accounts or not isinstance(v, dict):
                        continue
                    if not v.get("trader_alive"):
                        self.down_since.setdefault(a, now)
                    else:
                        self.down_since.pop(a, None)
                    down_s = now - self.down_since[a] if a in self.down_since else 0.0
                    if venue == "crypto":
                        if down_s >= ENGINE_DOWN_S or float(v.get("engine_dead_s") or 0) >= ACCT_DEAD_S:
                            trig.setdefault(a, f"mac_engine_dead_{int(float(v.get('engine_dead_s') or 0))}s_down={int(down_s)}s")
                    else:
                        if down_s >= ENGINE_DOWN_S or v.get("order_path_dead") or v.get("broker_unreachable"):
                            trig.setdefault(a, f"mac_stock_engine down={int(down_s)}s order_path_dead={v.get('order_path_dead')} broker_unreachable={v.get('broker_unreachable')}")
        for a, why in (missed or {}).items():
            if a in self.accounts:
                trig.setdefault(a, why)
        # An account S1 owns has its Mac engine stood down (mac_failover_agent VD_STANDDOWN), so the Mac's per-account
        # signals for it (engine dead, not trading, positions stale) are caused by the stand-down itself and cannot
        # extend ownership; only heartbeat loss and venue-wide kinds keep it. Handback = hold elapsed + healthy stretch;
        # a relapse after handback doubles the next hold.
        venue_wide = {a: w for a, w in trig.items() if w.startswith("mac_heartbeat_") or w.startswith("mac_kind:") and w[9:] not in self._named_kinds}
        for a in list(trig):
            if self.st.get(a, {}).get("active") and a not in venue_wide:
                trig.pop(a)
        return trig, age

    def update(self, now, trig):
        changes = []
        for a in self.accounts:
            s = self.st[a]
            if a in trig:
                s["healthy_since"] = None
                s["reason"] = trig[a]
                if not s["active"]:
                    recent = s.get("released_at") and now - float(s["released_at"]) < REPEAT_WINDOW_S
                    s["takeovers"] = int(s.get("takeovers") or 0) + 1 if recent else 1
                    s["hold_s"] = min(HOLD_MAX_S, HOLD_MIN_S * (2 ** (s["takeovers"] - 1)))
                    s["active"], s["since"] = True, now
                    changes.append((a, "TAKEOVER", trig[a]))
            elif s["active"]:
                s["healthy_since"] = s["healthy_since"] or now
                if now - float(s["since"]) >= float(s["hold_s"]) and now - float(s["healthy_since"]) >= RELEASE_HEALTHY_S:
                    s["active"], s["released_at"] = False, now
                    changes.append((a, "HANDBACK", f"Mac healthy {int(now - s['healthy_since'])}s, held {int(now - s['since'])}s"))
        return changes

    def active(self):
        return [a for a in self.accounts if self.st[a]["active"]]

    def save(self):
        atomic_write_json(self.path, self.st)


# ───────────────────────────── brokers ─────────────────────────────
class RestBlocked(Exception):
    pass


def _session(local_ip=None):
    import requests
    from requests.adapters import HTTPAdapter

    class _Bound(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            k["source_address"] = (local_ip, 0)
            return super().init_poolmanager(*a, **k)

    s = requests.Session()
    if local_ip:
        s.mount("https://", _Bound())
    return s


class Binance:
    BASE = "https://fapi.binance.com"

    def __init__(self, env, out: Path, local_ip=None):
        self.env, self.out, self.s = env, out, _session(local_ip)
        self.block_path = out / "rest_block_crypto.json"
        self.dual = {}
        self._xi = None

    def _blocked(self):
        until = float((read_json(self.block_path, {}) or {}).get("until") or 0)
        if time.time() < until:
            raise RestBlocked(f"REST blocked until {dt.datetime.fromtimestamp(until, dt.timezone.utc):%H:%M:%SZ}")

    def _req(self, method, path, acct=None, params=None, weight_note=""):
        self._blocked()
        params = dict(params or {})
        headers = {}
        if acct:
            key, sec = self.env.get(f"{acct}_API_KEY"), self.env.get(f"{acct}_API_SECRET")
            if not key or not sec:
                raise RuntimeError(f"missing {acct}_API_KEY/_API_SECRET")
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000
            q = urllib.parse.urlencode(params)
            params_q = q + "&signature=" + hmac.new(sec.encode(), q.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = key
        else:
            params_q = urllib.parse.urlencode(params)
        url = f"{self.BASE}{path}?{params_q}" if params_q else f"{self.BASE}{path}"
        r = self.s.request(method, url, headers=headers, timeout=15)
        if r.status_code in (418, 429):
            ra = float(r.headers.get("Retry-After") or (900 if r.status_code == 418 else 120))
            atomic_write_json(self.block_path, {"until": time.time() + ra, "status": r.status_code, "at": time.time(), "path": path})
            raise RestBlocked(f"HTTP {r.status_code} on {path}; blocking REST {ra:.0f}s")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} {path}: {r.text[:200]}")
        return r.json()

    def exchange_info(self):
        cache = self.out / "exchange_info_crypto.json"
        doc = read_json(cache, {}) or {}
        if self._xi is None or time.time() - float(doc.get("_fetched") or 0) > 6 * 3600:
            if time.time() - float(doc.get("_fetched") or 0) > 6 * 3600:
                raw = self._req("GET", "/fapi/v1/exchangeInfo")
                doc = {"_fetched": time.time(), "symbols": {}}
                for s in raw.get("symbols") or []:
                    f = {x["filterType"]: x for x in s.get("filters") or []}
                    doc["symbols"][s["symbol"]] = {
                        "step": float((f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}).get("stepSize") or 0),
                        "min_qty": float((f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}).get("minQty") or 0),
                        "max_qty": float((f.get("MARKET_LOT_SIZE") or {}).get("maxQty") or 0),
                        "min_notional": float((f.get("MIN_NOTIONAL") or {}).get("notional") or 5.0),
                        "status": s.get("status")}
                atomic_write_json(cache, doc)
            self._xi = doc.get("symbols") or {}
        return self._xi

    def is_dual(self, acct):
        if acct not in self.dual:
            self.dual[acct] = bool(self._req("GET", "/fapi/v1/positionSide/dual", acct).get("dualSidePosition"))
        return self.dual[acct]

    def positions(self, acct):
        rows = self._req("GET", "/fapi/v2/positionRisk", acct)
        out = {}
        for r in rows or []:
            amt = float(r.get("positionAmt") or 0.0)
            if amt == 0:
                continue
            ps = str(r.get("positionSide") or "BOTH").upper()
            side = ps if ps in ("LONG", "SHORT") else ("LONG" if amt > 0 else "SHORT")
            out[f"{r['symbol']}_{side}"] = {"qty": abs(amt), "price": float(r.get("markPrice") or 0.0), "entry": float(r.get("entryPrice") or 0.0)}
        return out

    def price(self, symbol):
        return float(self._req("GET", "/fapi/v1/ticker/price", params={"symbol": symbol})["price"])

    @staticmethod
    def _floor(q, step):
        if step <= 0:
            return q
        n = math.floor(q / step + 1e-9)
        dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        return round(n * step, dec)

    def size(self, o, held, price):
        """Exchange-legal quantity for an order, or (0, why)."""
        symbol, _side = split_ss(o["ss"])
        xi = self.exchange_info().get(symbol)
        if not xi or xi.get("status") not in (None, "TRADING"):
            return 0.0, f"symbol not TRADING ({(xi or {}).get('status')})"
        step, mq, mn = xi["step"], xi["min_qty"], xi["min_notional"]
        if o["act"] == "CLOSE":
            return held, ""
        if o["act"] == "OPEN":
            notional = max(float(o["notional"]), mn * 1.02)
            if notional > MAX_OPEN_NOTIONAL:
                return 0.0, f"min notional {mn} needs ${notional:.0f} > cap ${MAX_OPEN_NOTIONAL:.0f}"
            q = self._floor(notional / price, step)
            if q * price < mn:
                q = self._floor(notional / price + step, step)
            return (q, "") if q >= mq else (0.0, f"qty {q} < minQty {mq}")
        if o["act"] == "AUGMENT":
            q = self._floor(max(float(o["qty"]), mn * 1.02 / price), step)
            return (q, "") if q >= mq else (0.0, f"qty {q} < minQty {mq}")
        q = self._floor(min(float(o["qty"]), held), step)
        if q < mq or q <= 0:
            return 0.0, f"reduce qty {o['qty']:.6g} rounds below minQty {mq}"
        if held - q < mq:
            q = held
        return q, ""

    def send(self, acct, o, qty):
        symbol, side = split_ss(o["ss"])
        opening = o["act"] in ("OPEN", "AUGMENT")
        params = {"symbol": symbol, "type": "MARKET", "quantity": f"{qty:.10f}".rstrip("0").rstrip("."),
                  "side": ("BUY" if side == "LONG" else "SELL") if opening else ("SELL" if side == "LONG" else "BUY"),
                  "newClientOrderId": "vd" + hashlib.sha1(o["key"].encode()).hexdigest()[:24], "newOrderRespType": "RESULT"}
        if self.is_dual(acct):
            params["positionSide"] = side
        elif not opening:
            params["reduceOnly"] = "true"
        r = self._req("POST", "/fapi/v1/order", acct, params)
        return {"orderId": r.get("orderId"), "status": r.get("status"), "executedQty": r.get("executedQty"), "avgPrice": r.get("avgPrice")}


class Tradier:
    def __init__(self, env, out: Path):
        self.env, self.out, self.s = env, out, _session(None)
        self._clock = (0.0, False)

    def account(self, acct):
        a = acct.upper()
        if acct == "trc":
            aid = self.env.get("TRADIER_ACCOUNT_ID_TRC") or self.env.get("TRADIER_SANDBOX_ID")
            key = self.env.get("TRADIER_API_KEY_TRC") or self.env.get("TRADIER_SANDBOX_ACCESS_TOKEN") or self.env.get("TRADIER_SANDBOX_KEY")
            return aid, key, "https://sandbox.tradier.com/v1"
        aid = self.env.get(f"TRADIER_ACCOUNT_ID_{a}") or (self.env.get("TRADIER_ACCOUNT_ID") if acct == "tra" else None)
        key = self.env.get(f"TRADIER_API_KEY_{a}") or self.env.get("TRADIER_API_KEY_TRA") or self.env.get("TRADIER_API_KEY")
        return aid, key, "https://api.tradier.com/v1"

    def _req(self, method, acct, path, params=None, data=None):
        _aid, key, base = self.account(acct)
        if not key:
            raise RuntimeError(f"missing Tradier key for {acct}")
        r = self.s.request(method, base + path, params=params, data=data, timeout=15,
                           headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} {path}: {r.text[:200]}")
        return r.json()

    def market_open(self):
        t, val = self._clock
        if time.time() - t > 60:
            try:
                st = (self._req("GET", "tra", "/markets/clock").get("clock") or {}).get("state")
                val = st == "open"
            except Exception as exc:
                log(f"tradier clock failed ({exc}); using weekday 13:30-20:00Z fallback")
                now = dt.datetime.now(dt.timezone.utc)
                try:
                    from zoneinfo import ZoneInfo
                    et = now.astimezone(ZoneInfo("America/New_York"))
                    val = et.weekday() < 5 and dt.time(9, 30) <= et.time() < dt.time(16, 0)
                except Exception:
                    val = False
            self._clock = (time.time(), val)
        return val

    def positions(self, acct):
        aid, _k, _b = self.account(acct)
        doc = self._req("GET", acct, f"/accounts/{aid}/positions")
        rows = (doc.get("positions") or {}) if isinstance(doc.get("positions"), dict) else {}
        rows = rows.get("position") or []
        rows = [rows] if isinstance(rows, dict) else rows
        out = {}
        for r in rows:
            q = float(r.get("quantity") or 0.0)
            if q:
                out[f"{str(r['symbol']).upper()}_{'LONG' if q > 0 else 'SHORT'}"] = {"qty": abs(q), "price": 0.0}
        return out

    def price(self, symbol):
        q = (self._req("GET", "tra", "/markets/quotes", params={"symbols": symbol}).get("quotes") or {}).get("quote") or {}
        q = q[0] if isinstance(q, list) else q
        return float(q.get("last") or q.get("ask") or 0.0)

    def size(self, o, held, price):
        if o["act"] == "CLOSE":
            return float(int(held)), ""
        if o["act"] == "OPEN":
            # USER 2026-10-28: "s5 needs START_POS 900 and MAX_POS 4000 for stocks". MAX_OPEN_NOTIONAL was
            # applied on the CRYPTO path only, so stocks had NO max-position ceiling at all. It CLAMPS here
            # and never refuses, because a ceiling must not obstruct a trade: the requested notional is
            # START_POSITION_SIZE x ratio (ratio 0.25-4.0), so 900 x 4 = 3600 stays under 4000 in normal
            # operation and this binds only if START_POS is raised. MAX_POSITION_SIZE itself stays
            # exclusively MARKET MODE's business (operator, same message) — this is the trader's own ceiling.
            _want = float(o["notional"])
            if _want > MAX_OPEN_NOTIONAL:
                log(f"[VD-CAP] {o.get('ss')} open notional ${_want:.0f} clamped to MAX_OPEN_NOTIONAL ${MAX_OPEN_NOTIONAL:.0f}")
                _want = MAX_OPEN_NOTIONAL
            n = math.floor(_want / price) if price > 0 else 0
            if n >= 1:
                return float(n), ""
            # stock convention (tradier_manage max(1, int(qty)); config_tradier START_POSITION_SIZE $500 "$150 can't buy 1
            # share of $300+ stocks"): one whole share when the $15 slice is less than a share, up to STOCK_ONE_SHARE_MAX
            if 0 < price <= STOCK_ONE_SHARE_MAX:
                return 1.0, ""
            return 0.0, f"one share ${price:.2f} > ${STOCK_ONE_SHARE_MAX:.0f} one-share cap (skip)"
        if o["act"] == "AUGMENT":
            n = math.floor(float(o["qty"]))
            return (float(n), "") if n >= 1 else (0.0, f"augment {o['qty']:.3f} shares < 1")
        n = min(int(held), int(round(float(o["qty"]))))
        return (float(n), "") if n >= 1 else (0.0, f"reduce {o['qty']:.3f} shares < 1")

    def send(self, acct, o, qty):
        aid, _k, _b = self.account(acct)
        symbol, side = split_ss(o["ss"])
        opening = o["act"] in ("OPEN", "AUGMENT")
        tside = ("buy" if side == "LONG" else "sell_short") if opening else ("sell" if side == "LONG" else "buy_to_cover")
        data = {"class": "equity", "symbol": symbol, "side": tside, "quantity": str(int(qty)), "type": "market", "duration": "day",
                "tag": "vd-" + hashlib.sha1(o["key"].encode()).hexdigest()[:20]}
        r = self._req("POST", acct, f"/accounts/{aid}/orders", data=data)
        return r.get("order") or r


# ───────────────────────────── the loop ─────────────────────────────
class Trader:
    def __init__(self, a):
        self.a = a
        self.venue = a.venue
        self.root = Path(a.root).expanduser()
        self.state_dir = Path(a.state_dir).expanduser()
        self.out = self.root / "data" / "vector_direct"
        self.out.mkdir(parents=True, exist_ok=True)
        self.accounts = [x for x in (a.accounts.split(",") if a.accounts else (CRYPTO_ACCOUNTS if self.venue == "crypto" else STOCK_ACCOUNTS)) if x]
        env = load_env_files([self.root / ".env", Path.home() / ".env", Path.home() / "binance-sandbox" / ".env", Path.home() / "binance" / ".env"])
        local_ip = a.local_ip or os.environ.get("VD_LOCAL_IP") or None
        if local_ip:
            try:
                probe = socket.socket()
                probe.bind((local_ip, 0))
                probe.close()
            except OSError:
                log(f"local ip {local_ip} not bindable here; default routing")
                local_ip = None
        self.broker = Binance(env, self.out, local_ip) if self.venue == "crypto" else Tradier(env, self.out)
        # dry runs / forced tests keep their own ownership file: they must never hand accounts to the armed trader
        _sfx = "_dryrun" if (a.dry_run or a.force_active) else ""
        self.act = Activation(self.out / f"activation_{self.venue}{_sfx}.json", self.accounts)
        self.done_ts = {}
        self.done = self._load_done()
        self.quiet = set()
        self.attempts = {}
        self.expect = []
        self.obs = {}
        self.last_plan = {}
        self.state_sig = {}
        self.hb_remote = (0.0, None)
        self.missed_rows = {}
        self.paper_plan = {}
        self.paper_done = set()
        self.held = {}
        self.held_written = (0.0, None)

    def _ledger_path(self, ts=None):
        return self.out / f"ledger_{self.venue}_{dt.datetime.fromtimestamp(ts or time.time(), dt.timezone.utc):%Y-%m-%d}.jsonl"

    def _load_done(self):
        done = set()
        for back in (0, 1):
            p = self._ledger_path(time.time() - back * 86400)
            try:
                for line in p.read_text().splitlines():
                    r = json.loads(line)
                    if r.get("final") and r.get("key") and not r.get("dry_run") and r.get("status") not in ("DRY_RUN", "KILLED"):
                        done.add(r["key"])
                        if r.get("status") in ("SENT", "ERROR") and "|OPEN" in r["key"] and hasattr(self, "done_ts"):
                            self.done_ts[r["key"]] = float(r.get("ts") or 0)
            except Exception:
                continue
        return done

    def _ledger(self, row):
        row = {"ts": time.time(), "iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "venue": self.venue,
               "dry_run": bool(self.a.dry_run or self.a.force_active), **row}
        with open(self._ledger_path(), "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def heartbeat(self):
        hb = read_json(self.root / "data" / "live_failover" / "mac_data_heartbeat.json")
        for extra in (self.a.hb_extra or []):
            d = read_json(Path(extra).expanduser())
            if d and float(d.get("epoch") or 0) > float((hb or {}).get("epoch") or 0):
                hb = d
        if self.a.hb_ssh:
            t, d = self.hb_remote
            if time.time() - t > 30:
                try:
                    import subprocess
                    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", self.a.hb_ssh,
                                        "cat binance/data/live_failover/mac_data_heartbeat.json"], capture_output=True, text=True, timeout=12)
                    d = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
                except Exception:
                    d = None
                self.hb_remote = (time.time(), d)
            if d and float(d.get("epoch") or 0) > float((hb or {}).get("epoch") or 0):
                hb = d
        return hb

    def load_states(self, sides):
        out = {}
        for ss in sides:
            d = read_json(self.state_dir / f"{ss}.json")
            if isinstance(d, dict):
                out[ss] = d
        return out

    def positions(self, acct):
        """Broker positions + bookkeeping of what is held. 2026-10-27: held sides are published to held_<venue>.json so the
        state producer (tools/fwdstate_npz_watch.py on S1) computes them FIRST after every bar close and covers a held side
        that is outside the tradeable universe."""
        pos = self.broker.positions(acct)
        held = self.__dict__.setdefault("held", {})
        held[acct] = sorted(ss for ss, p in pos.items() if float(p.get("qty") or 0) > 0)
        sides = sorted(set().union(*held.values()))
        t, last = self.__dict__.get("held_written", (0.0, None))
        if sides != last or time.time() - t > 60:
            try:
                atomic_write_json(self.out / f"held_{self.venue}.json", {"epoch": time.time(), "sides": sides, "by_account": held})
                self.held_written = (time.time(), sides)
            except OSError as exc:
                log(f"held file write failed: {exc}")
        return pos

    def wt15(self, ss):
        """Latest closed 15m wt1/wt2 for ss's symbol from S1's own store: the .tail sidecar row (npz_tail_service appends
        the closed bar there within seconds) when newer than the zip, else the zip's last row. Cached per file stamps."""
        symbol, _side = split_ss(ss)
        d = Path(getattr(self.a, "npz_dir", "") or "").expanduser() if getattr(self, "a", None) is not None and getattr(self.a, "npz_dir", "") else Path(self.state_dir).expanduser().parents[1] / "backtest_v8" / "indicators"
        zp, tp = d / f"{symbol}.npz", d / ".tail" / f"{symbol}.jsonl"
        stamp = tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in (zp, tp))
        cache = self.__dict__.setdefault("wt15_cache", {})
        hit = cache.get(symbol)
        if hit and hit[0] == stamp:
            return hit[1]
        out = {}
        try:
            if stamp[1]:
                for line in reversed(tp.read_text().splitlines()):
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if "wt1_15m" in row and "wt2_15m" in row:
                        out = {"bar_ts": int(row["timestamps"]), "wt1_15m": row["wt1_15m"], "wt2_15m": row["wt2_15m"], "src": "tail"}
                    break
            if stamp[0]:
                import numpy as np
                with np.load(str(zp), allow_pickle=False) as z:
                    ts = int(z["timestamps"][-1])
                    if ts > int(out.get("bar_ts") or 0):
                        out = {"bar_ts": ts, "wt1_15m": float(z["wt1_15m"][-1]), "wt2_15m": float(z["wt2_15m"][-1]), "src": "zip"}
        except Exception as exc:
            log(f"wt15 {symbol}: {str(exc)[:120]}")
        cache[symbol] = (stamp, out)
        return out

    def noinfo_age(self, ss, st, now=None):
        """Seconds this side has continuously had no information (see no_information), else None."""
        now = time.time() if now is None else now
        since = self.__dict__.setdefault("noinfo_since", {})
        if no_information(st):
            return now - since.setdefault(ss, now)
        since.pop(ss, None)
        return None

    def with_held_states(self, states, pos):
        """A held side outside the universe still needs its state (board flat -> close; board open -> manage)."""
        for ss, p in pos.items():
            if ss not in states and float(p.get("qty") or 0) > 0 and getattr(self, "state_dir", None):
                d = read_json(self.state_dir / f"{ss}.json")
                if isinstance(d, dict):
                    states[ss] = d
        return states

    def alert(self, kind, **fields):
        row = {"iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "epoch": time.time(), "host": socket.gethostname(), "venue": self.venue, "kind": kind, **fields}
        with open(self.out / "ALERTS.jsonl", "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        atomic_write_json(self.out / f"ALERT_LATEST_{self.venue}.json", row)
        log(f"ALERT {kind} {json.dumps(fields, default=str)}")

    def pinned(self, now):
        pin = read_json(self.out / f"pin_{self.venue}.json", {}) or {}
        if float(pin.get("until_epoch") or 0) <= now:
            return {}
        return {a: f"operator_pin_until_{pin.get('until_iso') or int(pin['until_epoch'])}" for a in (pin.get("accounts") or self.accounts) if a in self.accounts}

    def paper(self, now, inactive, keys, states, bar, allowed):
        """What S1 would send for the accounts the Mac trades. Logged once per key, never sent."""
        for acct in inactive:
            if now - self.paper_plan.get(acct, 0) < REPLAN_S:
                continue
            self.paper_plan[acct] = now
            try:
                pos = self.positions(acct)
            except Exception as exc:
                log(f"paper {acct}: positions failed {str(exc)[:120]}")
                continue
            self.with_held_states(states, pos)
            orders, _ = plan_account(acct, self.venue, keys.get(acct, ()), states, pos, bar, self.paper_done, allowed, wt15=self.wt15, noinfo_age=self.noinfo_age)
            path = self.out / f"paper_{self.venue}_{dt.datetime.fromtimestamp(now, dt.timezone.utc):%Y-%m-%d}.jsonl"
            with open(path, "a") as fh:
                for o in orders:
                    self.paper_done.add(o["key"])
                    held = float((pos.get(o["ss"]) or {}).get("qty") or 0.0)
                    fh.write(json.dumps({"iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "bar": bar, "held": held, **o}, default=str) + "\n")

    def backstop(self, now, killed, keys, states, allowed):
        """Mac did not execute a board OPEN/CLOSE within the grace -> alert + send exactly that order (no takeover)."""
        n = 0
        for acct, rows in self.missed_rows.items():
            try:
                pos = self.positions(acct)
            except Exception as exc:
                log(f"backstop {acct}: positions failed {str(exc)[:120]}")
                continue
            orders = []
            for e in rows:
                held = float((pos.get(e["ss"]) or {}).get("qty") or 0.0)
                st = states.get(e["ss"]) or {}
                if e["want"] == "FLAT" and held > 0 and not st.get("open"):
                    orders.append({"acct": acct, "ss": e["ss"], "act": "CLOSE", "qty": held, "key": f"{acct}|{e['ss']}|{e['bar']}|BACKSTOP_CLOSE", "why": f"BACKSTOP Mac missed board CLOSE at bar {e['bar']}"})
                elif e["want"] == "HOLD" and held <= 0 and st.get("open"):
                    eq, oq = float(st.get("engine_qty") or 0.0), float(st.get("engine_open_qty") or 0.0)
                    ratio = min(4.0, max(0.25, eq / oq)) if eq > 0 and oq > 0 else 1.0
                    orders.append({"acct": acct, "ss": e["ss"], "act": "OPEN", "notional": START_POSITION_SIZE * ratio, "key": f"{acct}|{e['ss']}|{e['bar']}|BACKSTOP_OPEN", "why": f"BACKSTOP Mac missed board OPEN at bar {e['bar']}"})
                else:
                    continue
                self.alert("BACKSTOP_FAILURE_MAC_MISSED_ORDER", account=acct, sym_side=e["ss"], board=e["want"], bar=e["bar"], broker_qty=held, action=orders[-1]["act"])
            orders = [o for o in orders if o["key"] not in self.done]
            if orders:
                n += self.send_orders(acct, orders, pos, killed)
        self.missed_rows = {}
        return n

    def check_expectations(self, now, active):
        """Board OPEN/CLOSE at a bar the Mac had to execute: broker must show it after the grace."""
        missed, due, keep = {}, {}, []
        for e in self.expect:
            if e["acct"] in active:
                continue
            if now >= e["deadline"]:
                due.setdefault(e["acct"], []).append(e)
            else:
                keep.append(e)
        self.expect = keep
        for acct, rows in due.items():
            try:
                pos = self.positions(acct)
            except RestBlocked as exc:
                log(f"expectation check {acct}: {exc}")
                continue
            except Exception as exc:
                missed[acct] = f"broker_read_failed_from_S1 ({str(exc)[:80]})"
                continue
            self.obs[acct] = (now, {ss: p["qty"] for ss, p in pos.items()})
            for e in rows:
                held = float((pos.get(e["ss"]) or {}).get("qty") or 0.0)
                if (e["want"] == "HOLD" and held <= 0) or (e["want"] == "FLAT" and held > 0):
                    # USER 2026-10-27: a missed order is a BACKSTOP (alert + send that order), not an account takeover
                    self.missed_rows.setdefault(acct, []).append(e)
                    self._ledger({"event": "MISSED_TRADE", "acct": acct, "ss": e["ss"], "want": e["want"], "bar": e["bar"], "broker_qty": held})
        return missed

    def run_once(self):
        now = time.time()
        bar = last_closed_bar(now)
        keys = universe(self.root, self.venue, self.accounts)
        all_sides = set().union(*keys.values()) if keys else set()
        states = self.load_states(all_sides | set().union(*self.held.values()) if self.held else all_sides)
        market_ok = True
        if self.venue == "stocks":
            market_ok = self.broker.market_open() or bool(self.a.assume_open and self.a.dry_run)
        allowed = lambda acct, ss: entry_allowed(self.root, self.venue, acct, ss)
        hb = self.heartbeat()
        missed = self.check_expectations(now, set(self.act.active())) if market_ok else {}
        trig, hb_age = self.act.triggers(now, hb, self.venue, missed)
        if self.a.force_active:
            for x in self.a.force_active.split(","):
                trig.setdefault(x, "forced_by_operator")
        for x, why in self.pinned(now).items():
            trig.setdefault(x, why)
        if not market_ok:
            trig = {}
        # tools/live_mode.sh real: release_<venue>.json hands every account taken before its epoch back NOW (no hold, no
        # healthy stretch) so the Mac's real execution and this one never overlap; a heartbeat loss still keeps it.
        rel_epoch = float((read_json(self.out / f"release_{self.venue}.json", {}) or {}).get("epoch") or 0)
        for x in [a for a in self.accounts if rel_epoch and self.act.st[a]["active"] and float(self.act.st[a].get("since") or 0) < rel_epoch]:
            if str(trig.get(x, "")).startswith("mac_heartbeat_"):
                continue
            self.act.st[x].update(active=False, released_at=now, healthy_since=None, takeovers=0)
            trig.pop(x, None)
            log(f"HANDBACK {self.venue}:{x} — operator release (tools/live_mode.sh real)")
            self._ledger({"event": "HANDBACK", "acct": x, "why": "operator_release", "dry_run": self.a.dry_run})
        for acct, kind, why in self.act.update(now, trig):
            log(f"{kind} {self.venue}:{acct} — {why}")
            self._ledger({"event": kind, "acct": acct, "why": why, "hb_age": hb_age, "dry_run": self.a.dry_run})
        self.act.save()
        active = self.act.active()
        # new board transitions -> expectations for accounts the Mac still trades
        sig = {ss: (st.get("npz_tail"), st.get("ts")) for ss, st in states.items()}
        changed = {ss for ss, s in sig.items() if self.state_sig.get(ss) != s}
        self.state_sig = sig
        if changed and market_ok:
            fresh = {ss: states[ss] for ss in changed}
            for acct, ss, want in expectations_from_states([a for a in self.accounts if a not in active], keys, fresh, bar, allowed):
                st = states[ss]
                try:
                    st_epoch = dt.datetime.strptime(str(st.get("ts")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
                except Exception:
                    st_epoch = now
                self.expect.append({"acct": acct, "ss": ss, "want": want, "bar": bar, "deadline": max(st_epoch + MISS_GRACE_S, bar + BAR_S + MISS_MIN_AFTER_CLOSE_S)})
        killed = (self.out / "KILL").exists()
        # 2026-10-27: an OPEN that failed (margin / max position) or that someone else closed afterwards (the Mac closed S1's
        # flz ETHUSDC_SHORT 96 s after it filled) was done forever; the board still holds it, so retry once per bar.
        for k, t_done in list(self.done_ts.items()):
            if now - t_done > BAR_S:
                self.done.discard(k)
                self.done_ts.pop(k, None)
        orders_n = 0
        if self.__dict__.pop("plan_now", False):
            self.last_plan = {}
        consumed = self.__dict__.setdefault("consumed", {})
        consumed_dirty = False
        for acct in active if market_ok else []:
            acct_sides = set(keys.get(acct, ())) | set(self.held.get(acct, ()))
            acct_changed = any(ss in changed for ss in acct_sides)
            if not acct_changed and now - self.last_plan.get(acct, 0) < REPLAN_S:
                continue
            self.last_plan[acct] = now
            try:
                pos = self.positions(acct)
            except RestBlocked as exc:
                log(f"{acct}: {exc}")
                continue
            except Exception as exc:
                log(f"{acct}: broker positions FAILED {str(exc)[:160]}")
                continue
            self.with_held_states(states, pos)
            t_obs, prev = self.obs.get(acct, (0, {}))
            prev_qty = prev if t_obs and bar - BAR_S <= t_obs < bar + BAR_S else {}
            orders, notes = plan_account(acct, self.venue, keys.get(acct, ()), states, pos, bar, self.done, allowed, prev_qty, wt15=self.wt15, noinfo_age=self.noinfo_age)
            planned_at = time.time()
            # consumed_<venue>.json: which state (npz_tail, ts) this account was planned against, first time per state —
            # read by tools/s1_bar_guarantee.py as the per-bar "executed" stage
            for ss in set(keys.get(acct, ())) | set(pos):
                st = states.get(ss) or {}
                sig = [st.get("npz_tail"), st.get("ts")]
                row = consumed.setdefault(ss, {}).get(acct)
                if not row or row.get("sig") != sig:
                    consumed[ss][acct] = {"sig": sig, "npz_tail": st.get("npz_tail"), "ok": bool(st.get("ok")), "at": planned_at,
                                          "orders": [o["act"] for o in orders if o["ss"] == ss]}
                    consumed_dirty = True
            self.obs[acct] = (now, {ss: p["qty"] for ss, p in pos.items()})
            for ss, n in notes:
                if n != "HOLD_NO_FRESH_STATE":
                    k = f"{acct}|{ss}|{bar}|NOTE|{n[:24]}"
                    if k not in self.done:
                        self.done.add(k)
                        self._ledger({"event": "NOTE", "acct": acct, "ss": ss, "note": n, "key": k, "final": True})
            orders_n += self.send_orders(acct, orders, pos, killed)
        if consumed_dirty:
            try:
                atomic_write_json(self.out / f"consumed_{self.venue}.json", {"epoch": time.time(), "pid": os.getpid(), "sides": consumed})
            except OSError as exc:
                log(f"consumed file write failed: {exc}")
        if market_ok:
            self.paper(now, [a for a in self.accounts if a not in active], keys, states, bar, allowed)
            orders_n += self.backstop(now, killed, keys, states, allowed)
        fresh_n = sum(1 for st in states.values() if state_fresh(st, bar))
        unmanaged = sorted(f"{a}:{ss}" for a, sides in self.held.items() for ss in sides if not state_fresh(states.get(ss), bar))
        status = {"epoch": now, "iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "host": socket.gethostname(),
                  "venue": self.venue, "armed": not self.a.dry_run and not killed, "dry_run": self.a.dry_run, "killed": killed,
                  "market_open": market_ok, "active_accounts": active, "triggers": trig, "hb_age_s": None if hb_age is None else round(hb_age, 1),
                  "bar": bar, "states_fresh": fresh_n, "states_total": len(all_sides), "held_without_fresh_state": unmanaged, "pending_expectations": len(self.expect),
                  "orders_this_pass": orders_n, "start_position_size": START_POSITION_SIZE, "pin": self.pinned(now),
                  "pass_s": round(time.time() - now, 3), "wake": self.__dict__.get("last_wake")}
        atomic_write_json(self.out / f"status_{self.venue}.json", status)
        return status

    def wait_next(self, t0, loop_s):
        """Sleep until the next pass: loop_s at most, but wake (no earlier than MIN_PASS_GAP_S after t0) when a state file
        lands in the state dir or data/vector_direct/PLAN_NOW_<venue> appears (tools/s1_bar_guarantee.py poke)."""
        trigger = self.out / f"PLAN_NOW_{self.venue}"
        def dir_stamp():
            try:
                return self.state_dir.stat().st_mtime_ns
            except OSError:
                return None
        start = self.__dict__.get("dir_seen")
        if start is None:
            start = dir_stamp()
        while True:
            now = time.time()
            if now - t0 >= loop_s:
                self.last_wake = "timer"
                break
            if now - t0 >= MIN_PASS_GAP_S:
                if trigger.exists():
                    try:
                        why = trigger.read_text()[:200]
                        trigger.unlink()
                    except OSError:
                        why = ""
                    self.plan_now = True
                    self.last_wake = f"plan_now {why}".strip()
                    log(f"WAKE PLAN_NOW {why}")
                    break
                if dir_stamp() != start:
                    self.last_wake = "state_dir"
                    break
            time.sleep(0.2)
        self.dir_seen = dir_stamp()

    def send_orders(self, acct, orders, pos, killed):
        orders_n = 0
        if self.venue == "stocks":
            held_syms = {}
            for ss in pos:
                held_syms.setdefault(split_ss(ss)[0], set()).add(split_ss(ss)[1])
        for o in orders:
            orders_n += 1
            symbol, side = split_ss(o["ss"])
            held = float((pos.get(o["ss"]) or {}).get("qty") or 0.0)
            row = {"event": "ORDER", "acct": acct, "ss": o["ss"], "act": o["act"], "key": o["key"], "why": o["why"], "held": held}
            if self.venue == "stocks" and o["act"] == "OPEN" and held_syms.get(symbol, set()) - {side}:
                row.update({"status": "SKIP_OPPOSITE_SIDE_HELD", "final": True})
                self.done.add(o["key"])
                self._ledger(row)
                log(f"{acct} {o['ss']} OPEN skipped: opposite side held on Tradier")
                continue
            try:
                price = float((pos.get(o["ss"]) or {}).get("price") or 0.0) or self.broker.price(symbol)
                qty, why = self.broker.size(o, held, price)
            except RestBlocked as exc:
                log(f"{acct} {o['ss']}: {exc}")
                break
            except Exception as exc:
                qty, why, price = 0.0, f"sizing failed: {str(exc)[:120]}", 0.0
            row.update({"price": price, "qty": qty, "notional": round(qty * price, 4) if price else None})
            if qty <= 0:
                row.update({"status": "SKIP_SIZE", "detail": why, "final": True})
                self.done.add(o["key"])
                self._ledger(row)
                log(f"{acct} {o['ss']} {o['act']} SKIP: {why}")
                continue
            if self.a.dry_run or killed:
                # never final on disk: arming (or removing KILL) must still send what the board dictates
                if o["key"] not in self.quiet:
                    self.quiet.add(o["key"])
                    row.update({"status": "DRY_RUN" if self.a.dry_run else "KILLED"})
                    self._ledger(row)
                    log(f"{'DRY' if self.a.dry_run else 'KILL'} {acct} {o['ss']} {o['act']} qty={qty} px={price} (${qty * price:.2f}) — {o['why']}")
                continue
            try:
                resp = self.broker.send(acct, o, qty)
                row.update({"status": "SENT", "resp": resp, "final": True})
                self.done.add(o["key"])
                self.done_ts[o["key"]] = time.time()
                log(f"SENT {acct} {o['ss']} {o['act']} qty={qty} px~{price} -> {resp}")
                # USER 2026-10-27: a STOP MARKET at dc_low_15m -0.4% (dc_high_15m +0.4% shorts) for every non-exit order
                if os.environ.get("VD_STOPS", "1") == "1":
                    try:
                        if not hasattr(self, "stops"):
                            sys.path.insert(0, str(Path(__file__).resolve().parent))
                            from vector_direct_stops import Stops
                            self.stops = Stops(self)
                        log(f"STOP {acct} {o['ss']} {o['act']}: {self.stops.after_fill(acct, o, qty, held, price)}")
                    except Exception as exc:
                        log(f"STOP {acct} {o['ss']} {o['act']}: hook failed {str(exc)[:160]}")
            except RestBlocked as exc:
                row.update({"status": "BLOCKED", "detail": str(exc)})
                log(f"{acct} {o['ss']} {o['act']}: {exc}")
                self._ledger(row)
                break
            except Exception as exc:
                n = self.attempts.get(o["key"], 0) + 1
                self.attempts[o["key"]] = n
                row.update({"status": "ERROR", "detail": str(exc)[:300], "attempt": n, "final": n >= MAX_ATTEMPTS})
                if n >= MAX_ATTEMPTS:
                    self.done.add(o["key"])
                    self.done_ts[o["key"]] = time.time()
                    self.attempts.pop(o["key"], None)
                log(f"ERROR {acct} {o['ss']} {o['act']} attempt {n}: {str(exc)[:200]}")
            self._ledger(row)
        return orders_n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--venue", choices=("crypto", "stocks"), required=True)
    ap.add_argument("--root", default="~/binance", help="tree with books, tradeable_keys.json, .env, data/live_failover")
    ap.add_argument("--state-dir", default="~/binance-sandbox/data/forward_state")
    ap.add_argument("--npz-dir", default="", help="15m NPZ store for the non-tradeable wt15 exit (default: <state-dir>/../../backtest_v8/indicators)")
    ap.add_argument("--accounts", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop-s", type=float, default=10.0)
    ap.add_argument("--local-ip", default="")
    ap.add_argument("--force-active", default="", help="testing: treat these accounts as taken over")
    ap.add_argument("--hb-extra", action="append", help="additional heartbeat file(s); the freshest wins")
    ap.add_argument("--hb-ssh", default="", help="also read the heartbeat from this ssh host (e.g. S1 for S5)")
    ap.add_argument("--probe", action="store_true", help="read-only: broker positions per account, then exit")
    ap.add_argument("--assume-open", action="store_true", help="stocks + --dry-run only: plan as if the market were open")
    a = ap.parse_args(argv)
    # Venue sizing is resolved BEFORE the Trader exists, so no order can ever be planned on the
    # other venue's slice (operator 2026-10-28: "stocks 900 crypto 15 NO EXCEPTION").
    _size_rep = apply_venue_sizing(a.venue)
    for _pin in _size_rep["pinned"]:
        log(f"[VD-SIZE-PIN] {_pin}")
    t = Trader(a)
    if a.probe:
        for acct in t.accounts:
            try:
                p = t.broker.positions(acct)
                extra = f" dual={t.broker.is_dual(acct)}" if a.venue == "crypto" else ""
                log(f"PROBE {acct}: {len(p)} open positions{extra}: " + ", ".join(f"{k}={v['qty']}" for k, v in sorted(p.items())[:12]))
            except Exception as exc:
                log(f"PROBE {acct}: FAILED {str(exc)[:200]}")
        return 0
    log(f"vector_direct_trader {a.venue} start: accounts={t.accounts} dry_run={a.dry_run} START_POSITION_SIZE=${START_POSITION_SIZE} MAX_OPEN_NOTIONAL=${MAX_OPEN_NOTIONAL} ONE_SHARE_MAX=${STOCK_ONE_SHARE_MAX} sizing={_size_rep['source']} root={t.root} states={t.state_dir}")
    while True:
        t0 = time.time()
        try:
            st = t.run_once()
            if a.once or int(t0) % 300 < a.loop_s:
                log(f"pass: active={st['active_accounts']} hb_age={st['hb_age_s']} market_open={st['market_open']} fresh={st['states_fresh']}/{st['states_total']} orders={st['orders_this_pass']} armed={st['armed']}")
        except Exception as exc:
            log(f"PASS FAILED {type(exc).__name__}: {str(exc)[:300]}")
        if a.once:
            return 0
        t.wait_next(t0, a.loop_s)


if __name__ == "__main__":
    sys.exit(main())
