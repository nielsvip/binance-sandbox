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
import sqlite3
import socket
import sys
import time
import urllib.parse
from pathlib import Path

BAR_S = 900
CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
STOCK_ACCOUNTS = ("tra", "trb", "trc")
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
# ═══ OPERATOR SIZING MANDATE 2026-10-28 ═══
# VERBATIM: "stock and crypto are not the same pls DONT FUCK THIS UP stocks 900 crypto 15 NO EXCEPTION" and
# "a stock can NEVER see config.py and a crypto can never see config_tradier.py JUST MAKE SURE THE NEW TINY
# SCIPTS THAT ACUALLLY TRADE on s1 crypto and s5 stock DO ACUTLALY READ HTESE VALUES FORM THE RIGHT CONFIG FILES".
# These three knobs used to be ONE venue-blind triple of env reads defaulting to the CRYPTO value (15), so the
# stocks trader on S5 ran on a $15 slice and kept falling through to the one-whole-share path.
# They are now resolved ONLY by apply_venue_sizing(venue) from the venue's own config file — see that function.
# The placeholders below are 0.0 on purpose: if any path ever sizes an order before the venue is resolved, it
# sizes to ZERO (no order) instead of silently trading the other venue's slice.
MANDATED_START = {"crypto": 15.0, "stocks": 250.0}  # a mismatch is reported loudly on every start, never traded silently
START_POSITION_SIZE = 0.0
MAX_OPEN_NOTIONAL = 0.0
STOCK_ONE_SHARE_MAX = 0.0
# USER 2026-10-29: "they can ONLY trade what the vectorized script dictates ... >$200 SUICIDE trades must be limited
# right now", then "change it to 800" (same day). Hard ceiling on any single ENTRY (OPEN or AUGMENT) order notional, both venues.
# The vector multiplier (engine_qty / engine_open_qty, carrying STDEV sizing) is applied BEFORE this ceiling, never replaced by it. Over the ceiling the
# entry is SKIPPED, never partially sized. CLOSE/REDUCE are never limited (exits are never blocked).
ENTRY_NOTIONAL_CEILING_USD = 3200.0  # = stocks START 800 x ratio max 4 (USER 2026-10-29)


def apply_venue_sizing(venue: str) -> dict:
    """Read the sizing for THIS venue out of THIS venue's OWN config file. Nothing else.

    OPERATOR 2026-10-28, VERBATIM: "a stock can NEVER see config.py and a crypto can never see
    config_tradier.py JUST MAKE SURE THE NEW TINY SCIPTS THAT ACUALLLY TRADE on s1 crypto and s5 stock
    DO ACUTLALY READ HTESE VALUES FORM THE RIGHT CONFIG FILES."

      crypto (S1) -> config.py          Config.START_POSITION_SIZE / Config.MAX_POSITION_SIZE
      stocks (S5) -> config_tradier.py  TradierConfig.START_POSITION_SIZE / .MAX_POSITION_SIZE

    The config file is the ONE authority: there is deliberately no env fallback for these three knobs,
    because a second source is how the stocks trader ended up running on the crypto $15 slice. Each
    branch imports exactly one module, so the other venue's config is never even loaded; the guard
    below also refuses a value that arrived from the wrong class. A read failure is fatal — the trader
    must not guess a position size.
    """
    global START_POSITION_SIZE, MAX_OPEN_NOTIONAL, STOCK_ONE_SHARE_MAX, ENTRY_NOTIONAL_CEILING_USD
    v = "stocks" if str(venue).lower().startswith("stock") else "crypto"
    if v == "crypto":
        import config as _m
        cls, src, want_mod = _m.Config, "config.py:Config", "config"
    else:
        import config_tradier as _m
        cls, src, want_mod = _m.TradierConfig, "config_tradier.py:TradierConfig", "config_tradier"
    # EXACT module match, never a substring: "config" is a substring of "config_tradier", so a substring
    # test refuses every stocks start. This asserts the numbers came from THIS venue's module and no other.
    got_mod = str(getattr(cls, "__module__", ""))
    if got_mod != want_mod:
        raise SystemExit(f"REFUSED: {v} sizing resolved from module {got_mod!r}, expected {want_mod!r} — a {v} trader may never read the other venue's config")
    # INSTANTIATE, never read the class: operator 2026-10-28 "MAX_POSITION_SIZE that is exclusively
    # managed by MARKET MODE". config.Config.__init__ runs _apply_mode(), which overlays the active
    # market mode onto the class defaults — LIGHT_MODE was holding crypto START at 12 and MAX at 40
    # while the class said 15/80, so reading the CLASS would have bypassed market mode and reading the
    # instance is the only way the trader trades what actually governs.
    _inst = cls()
    start = float(getattr(_inst, "START_POSITION_SIZE"))
    maxpos = float(getattr(_inst, "MAX_POSITION_SIZE"))
    src += f" [{getattr(_inst, 'MARKET_MODE', 'n/a')}]"
    if not (start > 0 and maxpos > 0):
        raise SystemExit(f"REFUSED: {src} gives START_POSITION_SIZE={start} MAX_POSITION_SIZE={maxpos} — refusing to trade on a non-positive size")
    START_POSITION_SIZE = start
    MAX_OPEN_NOTIONAL = maxpos
    if v == "crypto":
        ENTRY_NOTIONAL_CEILING_USD = float(getattr(_inst, "MAX_ORDER_VALUE"))
        if not math.isfinite(ENTRY_NOTIONAL_CEILING_USD) or ENTRY_NOTIONAL_CEILING_USD <= 0:
            raise SystemExit(f"REFUSED: {src} MAX_ORDER_VALUE must be positive and finite")
    # stocks-only: one whole share is allowed up to the venue's own max position, never a third number
    STOCK_ONE_SHARE_MAX = maxpos if v == "stocks" else 0.0
    rep = {"venue": v, "config": src, "pinned": [],
           "source": {"START_POSITION_SIZE": f"{start} via {src}", "MAX_OPEN_NOTIONAL": f"{maxpos} via {src}.MAX_POSITION_SIZE",
                      "STOCK_ONE_SHARE_MAX": f"{STOCK_ONE_SHARE_MAX} via {src}.MAX_POSITION_SIZE" if v == "stocks" else "n/a (crypto)"}}
    want = MANDATED_START.get(v)
    if want is not None and abs(start - want) > 1e-9:
        # Do NOT silently trade a size the operator did not order, and do NOT silently change the
        # config either: say it loudly on every start so the wrong number cannot hide.
        rep["pinned"].append(f"WARNING {src} START_POSITION_SIZE={start} but the {v} mandate is {want} — FIX THE CONFIG FILE")
    for _k in ("VD_START_POSITION_SIZE", "VD_MAX_OPEN_NOTIONAL", "VD_STOCK_ONE_SHARE_MAX"):
        if os.environ.get(_k) not in (None, ""):
            rep["pinned"].append(f"{_k}={os.environ[_k]} IGNORED — {src} is the only sizing authority")
    return rep
HB_STALE_S = float(os.environ.get("VD_HB_STALE_S", "90"))
ACCT_DEAD_S = float(os.environ.get("VD_ACCT_DEAD_S", "180"))
ENGINE_DOWN_S = float(os.environ.get("VD_ENGINE_DOWN_S", "90"))  # trader_alive=0 this long (a routine restart is shorter)
MISS_GRACE_S = float(os.environ.get("VD_MISS_GRACE_S", "90"))
MISS_MIN_AFTER_CLOSE_S = float(os.environ.get("VD_MISS_MIN_AFTER_CLOSE_S", "180"))
HOLD_MIN_S = float(os.environ.get("VD_HOLD_MIN_S", "1800"))
HOLD_MAX_S = float(os.environ.get("VD_HOLD_MAX_S", "14400"))
# USER 2026-10-29: "live trading has to be running 24/7 for crypto on s1 and 9:00-16:00 on s5 ... take out the pin".
# S1/S5 own every account of their venue at all times: no takeover-on-Mac-health, no handback, no pin file.
OWN_ALWAYS = os.environ.get("VD_OWN_ALWAYS", "1") == "1"
# USER 2026-10-30: stocks plan from 08:45 ET (premarket bars feed the 7D); orders only from the 09:30 open; the last 90 min
# (14:30 ET) the REBAL_ACCOUNTS long book is trimmed to REBAL_LONG_CAP at local tops (forced at 15:45 ET if still over).
REBAL_LONG_CAP = float(os.environ.get("VD_REBAL_LONG_CAP", "2000"))
# USER 2026-10-30: stocks mirror crypto's market mode. tools/stock_market_mode.py writes data/market_mode_stocks.json
# (same index formula and thresholds as ez_rankings). START_POSITION_SIZE follows the mode table of config_tradier
# (LIGHT 250 / NORMAL 600 / EXTREME 1200). A missing or stale file falls back to LIGHT_MODE, the same default crypto uses.
STOCK_MODE_START = {"LIGHT_MODE": 250.0, "NORMAL_MODE": 600.0, "EXTREME_MODE": 1200.0}


def stock_mode_sizing(mode):
    """(START_POSITION_SIZE, MAX_ORDER_VALUE, MAX_POSITION_SIZE) for a market mode, from config_tradier's OWN table.

    USER 2026-10-30: "make sure the START_POSITION_SIZE and MAX_ORDER_VALUE and MAX_POSITION_SIZE trc uses is identical
    to trb and according to market movement between light normal and extreme modes calculated multiple times per day."
    Before this, only START followed the mode: MAX_POSITION_SIZE was frozen at process start from the config's class
    default and MAX_ORDER_VALUE was never read at all — a hard-coded $3,200 entry ceiling stood in for it, derived from
    a START of $800 that no longer exists. The numbers are not copied here; `TradierConfig._apply_mode` is the one
    authority (LIGHT 250/1000/4000, NORMAL 600/2400/12000, EXTREME 1200/4800/18000) and the result is cached per mode."""
    cache = stock_mode_sizing.__dict__.setdefault("cache", {})
    if mode not in cache:
        import config_tradier
        cfg = config_tradier.TradierConfig()
        cfg._apply_mode(mode)
        cache[mode] = (float(cfg.START_POSITION_SIZE), float(cfg.MAX_ORDER_VALUE), float(cfg.MAX_POSITION_SIZE))
    return cache[mode]
STOCK_MODE_MAX_AGE_S = float(os.environ.get("VD_STOCK_MODE_MAX_AGE_S", "1800"))
# USER 2026-10-30: "VD_REBAL_ACCOUNTS and down gapper closes need to be identical as well we need to be comparing apples
# with apples." trc (7D) must trim and close exactly like trb (30D), so the only difference between the two books is the
# settings. The rebalance IS the only account-scoped close in this trader — there is no separate gap rule anywhere.
REBAL_ACCOUNTS = tuple(x for x in os.environ.get("VD_REBAL_ACCOUNTS", "trb,trc").split(",") if x)
RELEASE_HEALTHY_S = float(os.environ.get("VD_RELEASE_HEALTHY_S", "600"))
REPEAT_WINDOW_S = float(os.environ.get("VD_REPEAT_WINDOW_S", "7200"))
STATE_MAX_LAG_S = float(os.environ.get("VD_STATE_MAX_LAG_S", "1800"))
REPLAN_S = float(os.environ.get("VD_REPLAN_S", "60"))
# 2026-10-28 USER "s1 never misses a beat ... executes it immediately": the loop wakes as soon as a state lands in the state
# dir (dir mtime) or tools/s1_bar_guarantee.py writes data/vector_direct/PLAN_NOW_<venue>; MIN_PASS_GAP_S bounds REST weight.
MIN_PASS_GAP_S = float(os.environ.get("VD_MIN_PASS_GAP_S", "2.0"))
# USER 2026-10-30: "it should rsyn positions data to s1 crypto and s5 stocks which the vectorized trader needs to use for
# its positions tracking". tools/mac_positions_push_executors.py lands data/vector_direct/mac_positions_<venue>.json here.
# It carries the LIFECYCLE the broker APIs do not return (entry_price, opened_at, max_gain, gain, last_signal) — Tradier
# /positions gives quantity + cost_basis only. The BROKER remains the sole authority on what is held: a row here never
# creates, removes or resizes a position, it only annotates one the broker already shows, and a disagreement rings.
# USER 2026-10-30 (verbatim): "MAKE SURE ALL FIN TRADING IS SSTILLL BLOCKED aexcept the trading of men sym_sides from 7D
# resultss that are < a day old". fin is a LIVE account that sent 47 real orders on 2026-10-30 — it was NOT blocked. It
# may now open or augment ONE kind of position: a sym_side that is in men's book AND carries a 7D result younger than
# SEVENDAY_MAX_AGE_S. Everything else is refused. This FAILS CLOSED: no readable 7D artifact means no entries at all,
# which is today's state because the crypto 7D lane has no driver. EXITS are never gated — closing a position is an
# absolute law of this system, and a gate that strands a live position is worse than the entry it was meant to stop.
# USER 2026-10-31 (correcting an earlier misreading of mine, verbatim): "this is ONLY for fin trading men sym_sides and
# for trc trading trb sym_sides and gets calculated every day and ONLY applies to these 2 accounts NEVER ANYWHERE ELSE
# but with the exact same sizing as the trb and men accounts - we ened to COMPARE wto see what is better if you start
# trading 7D on all accounts the system is fucked".
# fin and trc are the 7D ARMS of a two-arm comparison: fin vs men on men's book, trc vs trb on trb's book, same sizing,
# the only difference being 7D versus 30D settings. Gating any other account would destroy the control arm and the
# comparison with it. "*" is still accepted by the env for an operator override but is NEVER the default.
SEVENDAY_ONLY_ACCOUNTS = tuple(x.strip() for x in os.environ.get("VD_SEVENDAY_ONLY_ACCOUNTS", "fin,trc").split(",") if x.strip())
# Both 7D arms are UNCONDITIONAL: no fresh 7D set means no entry, full stop, even with the whole lane down. fin and trc
# exist only to trade 7D, so an empty lane must leave them flat rather than quietly trading 30D sets — a 7D arm that
# silently trades 30D settings is not a comparison, it is a duplicate of its control.
SEVENDAY_STRICT_ACCOUNTS = tuple(x.strip() for x in os.environ.get("VD_SEVENDAY_STRICT_ACCOUNTS", "fin,trc").split(",") if x.strip())
# LANE-DOWN BRAKE. "Only trade 7D results under 24 h old" and "guarantee trading 24/7" cannot both hold while the 7D
# lane publishes NOTHING — and measured 2026-10-31 it publishes nothing on either venue: a board computes its
# cumulative_overrides but exhausts the 90-min herd cap before live-verify, so `v15_preopen_7d_stage.validate_one`
# never sees result_done_utc/final_path. That is why trc has shown 0 QUALIFIED on every run. A venue with zero fresh
# sides is therefore a PIPELINE FAULT, not a signal to stop trading: it rings CRITICAL and entries continue on the
# published 30D sets. The moment the lane publishes even one side the gate binds strictly, per side, everywhere.
SEVENDAY_LANE_DOWN_BRAKE = os.environ.get("VD_SEVENDAY_LANE_DOWN_BRAKE", "1") == "1"
# USER 2026-10-31: "making sure no sym_side ever exceeds 24hr (whcih would block trading - actually we should make
# that ~30hrs ot give it some more time and consistency)". The run starts 20:15 daily, so 30 h leaves a ~6 h
# cushion: a side recomputed late one night does not fall out of the window before the next run reaches it.
SEVENDAY_MAX_AGE_S = float(os.environ.get("VD_SEVENDAY_MAX_AGE_S", str(30 * 3600)))
# each 7D arm trades its CONTROL account's book and nothing else: fin -> men, trc -> trb (the same mapping universe() uses)
SEVENDAY_BOOK = {"fin": "men", "trc": "trb"}
MAC_POSITIONS = os.environ.get("VD_MAC_POSITIONS", "1") == "1"
MAC_POSITIONS_MAX_AGE_S = float(os.environ.get("VD_MAC_POSITIONS_MAX_AGE_S", "900"))
# Every held position must carry a resting protective stop. Audited per account at most this often (0 = off).
PROTECTION_AUDIT_S = float(os.environ.get("VD_PROTECTION_AUDIT_S", "300"))
# Mass brake on the permanent deterioration latch: a pass that would latch more than this share of the sides it
# actually compared is a fault signature, not that many real deteriorations (2026-10-30: 187 latched vs a 90-side
# universe emptied every crypto account). 0 disables the brake.
DETERIORATION_MASS_PCT = float(os.environ.get("VD_DETERIORATION_MASS_PCT", "0.25"))
DETERIORATION_MASS_MIN = int(os.environ.get("VD_DETERIORATION_MASS_MIN", "3"))
# How often a BACKUP re-proves it can actually reach the broker before it would ever claim the lease.
BROKER_READY_EVERY_S = float(os.environ.get("VD_BROKER_READY_EVERY_S", "60"))
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


def persist_decision_state(directory, state):
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    relative = Path("decision_states") / digest[:2] / (digest + ".json")
    path = Path(directory) / relative
    if not path.exists():
        atomic_write_json(path, state)
    return {"state_sha256": digest, "state_file": str(relative), "npz_tail": state.get("npz_tail"), "state_ts": state.get("ts"), "settings_sha256": state.get("settings_sha256"), "engine_ast_sha256": state.get("engine_ast_sha256"), "settings_updated_at": state.get("settings_updated_at")}


def actual_reduction_is_profitable(symbol_side, position, price):
    held = float(position.get("qty") or 0.0)
    entry = float(position.get("entry") or (float(position.get("cost") or 0.0) / held if held > 0 else 0.0))
    break_even = float(position.get("break_even") or entry)
    if held <= 0 or not all(math.isfinite(value) and value > 0 for value in (entry, break_even, price)):
        return False, {"entry": entry, "break_even": break_even, "price": price, "reason": "missing valid broker cost or quote"}
    is_long = split_ss(symbol_side)[1] == "LONG"
    floor = max(entry, break_even) if is_long else min(entry, break_even)
    gain = (price - floor) if is_long else (floor - price)
    return gain > 0.0, {"entry": entry, "break_even": break_even, "price": price, "profit_floor": floor, "gain_per_unit_before_exit_fee": gain}


def verified_fixed_stop_trigger(symbol_side, position):
    if position.get("position_source") != "mac_snapshot_broker_verified":
        return None
    source = position.get("mac") or {}
    try:
        opened = dt.datetime.fromisoformat(str(source["opened_at"]).replace("Z", "+00:00")).timestamp()
        anchor, level = float(source["fixed_stop_anchor"]), float(source["fixed_stop_price"])
        entry, source_entry, mark = float(position["entry"]), float(source["entry_price"]), float(position["price"])
        if source.get("fixed_stop_armed") is not True or not all(math.isfinite(value) and value > 0 for value in (opened, anchor, level, entry, source_entry, mark)):
            return None
        if abs(opened - anchor) > 0.001 or abs(entry - source_entry) > max(1e-10, entry * 1e-6):
            return None
        crossed = mark <= level if split_ss(symbol_side)[1] == "LONG" else mark >= level
        if not crossed:
            return None
        return {"kind": "LIFECYCLE_BOUND_FIXED_STOP_CROSSED", "anchor": anchor, "level": level, "broker_mark": mark, "broker_entry": entry, "mac_source_file": source.get("source_file"), "source_file_epoch": source.get("source_file_epoch"), "fixed_stop_source": source.get("fixed_stop_source", "mac_position")}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def verified_deterioration_wt15_trigger(order, venue, halted, wt, now):
    if venue != "crypto" or order.get("act") != "CLOSE" or order.get("ss") not in halted:
        return None
    bar = last_closed_bar(now)
    if order.get("deterioration_wt15_bar") != bar or int((wt or {}).get("bar_ts") or 0) != bar:
        return None
    against, detail = wt15_against(order["ss"], wt)
    return {"kind": "CURRENT_BAR_WT15_AGAINST_HALTED_SIDE", "bar": bar, "detail": detail, "wt_source": wt.get("src")} if against else None


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


def deterioration_halts(root: Path) -> dict:
    """Read daily same-settings 30D gain drops of at least 15 pp. Crypto held sides close on a halt."""
    doc = read_json(root / "data" / "deterioration_halt.json", {}) or {}
    return {str(k).upper(): v for k, v in (doc.get("halts") or {}).items()} if isinstance(doc, dict) else {}


def rolling_deterioration_halts(root: Path, state_dir: Path, states: dict, bar: int, alert=None) -> dict:
    """Latch a fresh, same-settings 30D loss of at least 15 percentage points.

    A mass change is treated as a likely data or settings fault and refused. Existing latches are released after a fresh,
    matching-settings replay measures a drop below 15 pp."""
    path = root / "data" / "vector_direct" / "deteriorated_crypto.json"
    latched = read_json(path, {}) or {}
    if not isinstance(latched, dict):
        raise RuntimeError(f"invalid deterioration latch {path}")
    original_latched = dict(latched)
    db = state_dir.parents[1] / "data" / "hourly_reconfig" / "per_sym_store.db"
    if not db.exists():
        log(f"rolling deterioration DB missing: {db}; existing latches kept")
        return latched
    added, evaluated = {}, 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            for ss, state in states.items():
                if not state_fresh(state, bar) or state.get("gain_pct") is None:
                    continue
                row = con.execute("SELECT updated_at, gain_30d, acc_gain_pct FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
                if not row or row[0] != state.get("settings_updated_at"):
                    continue
                old = row[1] if row[1] is not None else row[2]
                if old is None:
                    continue
                published, current = float(old), float(state["gain_pct"])
                if not (math.isfinite(published) and math.isfinite(current)):
                    continue
                evaluated += 1
                if published - current < 15.0:
                    if ss in latched:
                        latched.pop(ss)
                    continue
                if ss in latched:
                    continue
                added[ss] = {"published_gain_pct": published, "rolling_gain_pct": current,
                             "drop_pp": published - current, "published_settings_at": row[0],
                             "state_ts": state.get("ts"), "npz_tail": state.get("npz_tail"),
                             "latched_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "reason": "fresh 30D replay deteriorated by at least 15 pp"}
        finally:
            con.close()
    except Exception as exc:
        log(f"rolling deterioration read failed: {type(exc).__name__}: {exc}; existing latches kept")
        return latched
    if added and len(added) > max(DETERIORATION_MASS_MIN, DETERIORATION_MASS_PCT * evaluated):
        share = (len(added) / evaluated * 100.0) if evaluated else 100.0
        log(f"CRITICAL DETERIORATION_MASS_LATCH_REFUSED: {len(added)} of {evaluated} evaluated sides ({share:.0f}%) would latch in one pass "
            f"— that is a data or settings fault, not {len(added)} deteriorations; NOTHING latched, no position closed: {','.join(sorted(added)[:12])}")
        if alert is not None:
            try:
                alert("DETERIORATION_MASS_LATCH_REFUSED", venue="crypto", would_latch=len(added), evaluated=evaluated,
                      share_pct=round(share, 1), sample=sorted(added)[:12], severity="CRITICAL")
            except Exception:
                pass
        if latched != original_latched:
            atomic_write_json(path, latched)
        return latched
    if added:
        latched.update(added)
    if latched != original_latched:
        atomic_write_json(path, latched)
    if added:
        log(f"ROLLING DETERIORATION LATCH {len(added)} new crypto sides; CLOSE held and forbid reentry: {','.join(sorted(added))}")
    return latched


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


def reconcile_terminal_lifecycle(state):
    if not isinstance(state, dict) or not state.get("ok") or not state.get("open"):
        return state
    ledger = state.get("ledger")
    if not isinstance(ledger, list):
        return state
    openings = [int(row.get("bar", row.get("bar_entry"))) for row in ledger if isinstance(row, dict) and row.get("type") in ("OPEN", "AUGMENT") and row.get("bar", row.get("bar_entry")) is not None]
    endings = [int(row["bar_exit"]) for row in ledger if isinstance(row, dict) and row.get("type") == "REDUCE" and row.get("pnl_is_lifecycle") is True and not row.get("unrealised") and row.get("bar_exit") is not None]
    if not openings or not endings or max(endings) < max(openings):
        return state
    return {**state, "open": False, "engine_qty": 0.0, "engine_open_qty": 0.0, "engine_open_bar_ts": None, "entry_bar": None, "entry_px": None, "consumer_state_repair": "recorded_terminal_reduction_closed_lifecycle"}

def plan_account(acct, venue, keys, states, positions, bar, done, allowed, prev_qty=None, wt15=None, noinfo_age=None, untradeable=None, untradeable_close=True, halted=None):
    """Orders that converge one account to the board. positions: {ss: {"qty": abs qty, "price": px}} read just now.
    prev_qty: {ss: broker qty observed BEFORE this bar closed} — an AUGMENT/REDUCE the broker already shows was done by
    someone else (the Mac) and is not repeated. wt15: callable ss -> {bar_ts, wt1_15m, wt2_15m} of the latest closed 15m
    bar. USER 2026-10-27 "non tradeable keys get closes as soon as wt_15m goes against it": a held side that is NOT one of
    this account's tradeable keys is CLOSED (NON_TRADEABLE_WT15_AGAINST) on a fresh wt15 reading against it, whatever its
    board state says. Returns (orders, notes)."""
    orders, notes = [], []
    prev_qty = prev_qty or {}
    halted = halted or {}
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
    untradeable = untradeable or {}
    for ss in sorted(keyset | set(held_sides)):
        st = states.get(ss)
        held = float((positions.get(ss) or {}).get("qty") or 0.0)
        if venue == "crypto" and ss in halted:
            if held > 0:
                wt = (wt15(ss) or {}) if wt15 is not None else {}
                wt_bar = int(wt.get("bar_ts") or 0)
                if wt_bar == bar:
                    against, detail = wt15_against(ss, wt)
                else:
                    against, detail = None, "wt15 stale/unavailable"
                if against:
                    orders.append({"acct": acct, "ss": ss, "act": "CLOSE", "qty": held,
                                   "key": f"{acct}|{ss}|{wt_bar}|DETERIORATED_CLOSE",
                                   "why": f"DETERIORATED_CLOSE_WT15_AGAINST: {detail}; {str((halted[ss] or {}).get('reason') or '30D gain deterioration')[:120]}",
                                   "reason": "DETERIORATED_CLOSE", "deterioration_wt15_bar": wt_bar})
                    continue
                notes.append((ss, f"DETERIORATION_HOLD_WAIT_WT15 {detail}"))
            else:
                notes.append((ss, "DETERIORATED_NO_REENTRY"))
                continue
        # OPERATOR 2026-10-28 (verbatim): "EVERY tradeable sym_side needs to have a verdict with a quantity from a NEW kline right
        # then and there or a position needs to be closed and declared untradeable and sounding all bells and whistles".
        # tools/s1_bar_guarantee.py declares a side UNTRADEABLE (data/vector_direct/untradeable_<venue>.json) once its repair
        # chain for the new bar is exhausted: close it NOW on every account, no opens until a fresh ok state returns. No grace
        # and no NOINFO_BREAKER here (operator rule); kill switch data/vector_direct/UNTRADEABLE_CLOSE_DISABLED.
        if ss in untradeable:
            u = untradeable[ss] or {}
            if u.get("mode") == "hold":
                # OPERATOR 2026-10-28 "wait one bar above": >VD_MASS_FAIL_PCT of sides failed this bar -> no close this bar, opens blocked;
                # the guarantee closes whatever still has no verdict on the next bar
                notes.append((ss, f"UNTRADEABLE_MASS_HOLD_NO_OPEN bar {u.get('bar')}{' (held, close deferred one bar)' if held > 0 else ''}"))
                continue
            if held > 0 and untradeable_close:
                orders.append({"acct": acct, "ss": ss, "act": "CLOSE", "qty": held, "key": f"{acct}|{ss}|{u.get('bar')}|UNTRADEABLE_NO_VERDICT",
                               "why": f"UNTRADEABLE_NO_VERDICT bar {u.get('bar')}: {str(u.get('reason'))[:200]}", "reason": "UNTRADEABLE_NO_VERDICT"})
            else:
                notes.append((ss, f"UNTRADEABLE_NO_OPEN{'_CLOSE_DISABLED' if held > 0 else ''} bar {u.get('bar')}"))
            continue
        if isinstance(st, dict) and (st.get('settings_fallback') or str(st.get('src') or '').startswith('fallback:')):
            if held > 0:
                orders.append({'acct': acct, 'ss': ss, 'act': 'CLOSE', 'qty': held,
                               'key': f'{acct}|{ss}|{bar}|UNPUBLISHED_SETTINGS_CLOSE',
                               'why': 'no active per_sym_store.db settings for this side'})
            else:
                notes.append((ss, 'UNPUBLISHED_SETTINGS_NO_OPEN'))
            continue
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
            if ss in halted:
                notes.append((ss, f"DETERIORATION_HALT_NO_OPEN since {(halted[ss] or {}).get('halted_utc')}"))
                continue
            if not allowed(acct, ss):
                notes.append((ss, "SKIP_OPEN_NOT_IN_BOOKS"))
                continue
            eq, oq = float(st.get("engine_qty") or 0.0), float(st.get("engine_open_qty") or 0.0)
            ratio = min(4.0, max(0.25, eq / oq)) if eq > 0 and oq > 0 else 1.0
            anchor = st.get("engine_open_bar_ts") or st.get("entry_bar") or tail
            orders.append({"acct": acct, "ss": ss, "act": "OPEN", "notional": START_POSITION_SIZE * ratio,
                           "key": f"{acct}|{ss}|{tail}|OPEN", "anchor": int(anchor), "why": f"board holds since {anchor}, broker flat (engine qty ratio {ratio:.3f})"})
        elif board_open and held > 0:
            if int(st.get("acts_bar_ts") or -1) != tail:
                continue
            base = float(prev_qty.get(ss) or held)
            for i, a in enumerate(st.get("acts_last_bar") or []):
                typ = str(a.get("type") or "").upper()
                q, before = float(a.get("qty") or 0.0), float(a.get("qty_before") or 0.0)
                if typ == "AUGMENT" and q > 0 and before > 0:
                    if ss in halted:
                        notes.append((ss, f"DETERIORATION_HALT_NO_AUGMENT since {(halted[ss] or {}).get('halted_utc')}"))
                        continue
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
        if OWN_ALWAYS:
            for a in self.accounts:
                if not self.st[a]["active"]:
                    self.st[a].update(active=True, since=now, healthy_since=None, reason="own_always")
                    changes.append((a, "OWN_ALWAYS", "owned at start (no handback)"))
            return changes
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
        if OWN_ALWAYS:
            return list(self.accounts)
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
            out[f"{r['symbol']}_{side}"] = {"qty": abs(amt), "price": float(r.get("markPrice") or 0.0), "entry": float(r.get("entryPrice") or 0.0), "break_even": float(r.get("breakEvenPrice") or r.get("entryPrice") or 0.0)}
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
            if notional > min(MAX_OPEN_NOTIONAL, ENTRY_NOTIONAL_CEILING_USD):
                return 0.0, f"min notional {mn} needs ${notional:.0f} > cap ${min(MAX_OPEN_NOTIONAL, ENTRY_NOTIONAL_CEILING_USD):.0f}"
            q = self._floor(notional / price, step)
            if q * price < mn:
                q = self._floor(notional / price + step, step)
            if q * price > ENTRY_NOTIONAL_CEILING_USD:
                return 0.0, f"entry ${q * price:.0f} > ${ENTRY_NOTIONAL_CEILING_USD:.0f} ceiling (skip)"
            if (held + q) * price > MAX_OPEN_NOTIONAL + 1e-8:
                return 0.0, f"position ${(held + q) * price:.2f} > ${MAX_OPEN_NOTIONAL:.2f} market-mode cap (skip)"
            return (q, "") if q >= mq else (0.0, f"qty {q} < minQty {mq}")
        if o["act"] == "AUGMENT":
            q = self._floor(max(float(o["qty"]), mn * 1.02 / price), step)
            # 2026-10-28: flooring to the step pushed RAYSOLUSDT augments under the $5 min notional (-4164 on 3 accounts)
            if q * price < mn * 1.02:
                q = self._floor(mn * 1.02 / price + step, step)
            if q * price > ENTRY_NOTIONAL_CEILING_USD:
                return 0.0, f"augment ${q * price:.0f} > ${ENTRY_NOTIONAL_CEILING_USD:.0f} ceiling (skip)"
            if (held + q) * price > MAX_OPEN_NOTIONAL + 1e-8:
                return 0.0, f"position ${(held + q) * price:.2f} > ${MAX_OPEN_NOTIONAL:.2f} market-mode cap (skip)"
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


def _send_gate_entry_banned(acct):
    """Fail-closed hard entry ban at the Tradier send gate (order_dedupe_guard.entry_banned; tra: exits only)."""
    try:
        for base in (Path.home() / "binance-sandbox", Path(__file__).resolve().parents[1]):
            if (base / "order_dedupe_guard.py").exists() and str(base) not in sys.path:
                sys.path.insert(0, str(base))
        from order_dedupe_guard import entry_banned
        return bool(entry_banned(acct))
    except Exception:
        return True


class Tradier:
    def __init__(self, env, out: Path):
        self.env, self.out, self.s = env, out, _session(None)
        self._clock = (0.0, False)
        self._orders_cache = {}
        self._protected_ids = {}
        for days_back in (1, 0):
            path = self.out / f"ledger_stocks_{dt.datetime.fromtimestamp(time.time() - days_back * 86400, dt.timezone.utc):%Y-%m-%d}.jsonl"
            try:
                for line in path.read_text().splitlines():
                    row = json.loads(line)
                    key = (row.get("acct"), row.get("ss"))
                    if row.get("status") == "PROTECTED_OTO_SUBMITTED" and (row.get("resp") or {}).get("id"):
                        self._protected_ids.setdefault(key, set()).add(str(row["resp"]["id"]))
                    elif row.get("status") == "PROTECTED_OTO_CANCELLED":
                        self._protected_ids.setdefault(key, set()).discard(str(row.get("order_id")))
            except (OSError, ValueError, TypeError):
                continue

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
        if method == "DELETE" and not r.content:
            return {"status": "cancelled"}
        return r.json()

    def market_open(self):
        # USER 2026-10-29: S5 stocks run 09:00-16:00 ET; orders only from the 09:30 open (3 premarket bars feed the first plan).
        # Clock is computed locally from New York time, never from the broker clock API (it dropped connections).
        from zoneinfo import ZoneInfo
        et = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo("America/New_York"))
        return et.weekday() < 5 and dt.time(8, 45) <= et.time() < dt.time(16, 0)

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
                cost = abs(float(r.get("cost_basis") or 0.0))
                out[f"{str(r['symbol']).upper()}_{'LONG' if q > 0 else 'SHORT'}"] = {"qty": abs(q), "price": 0.0, "cost": cost, "entry": cost / abs(q)}
        return out

    def price(self, symbol):
        q = (self._req("GET", "tra", "/markets/quotes", params={"symbols": symbol}).get("quotes") or {}).get("quote") or {}
        q = q[0] if isinstance(q, list) else q
        return float(q.get("last") or q.get("ask") or 0.0)

    def _protected_order(self, acct, order_id):
        cached = self._orders_cache.get((acct, str(order_id)))
        if cached and time.time() - cached[0] < 5:
            return cached[1]
        aid, _key, _base = self.account(acct)
        row = (self._req("GET", acct, f"/accounts/{aid}/orders/{order_id}").get("order") or {})
        self._orders_cache[(acct, str(order_id))] = (time.time(), row)
        return row

    def pending_protected(self, acct, symbol, side):
        key = (acct, f"{symbol}_{side}")
        for order_id in tuple(self._protected_ids.get(key, ())):
            row = self._protected_order(acct, order_id)
            legs = row.get("leg") or []
            legs = [legs] if isinstance(legs, dict) else legs
            opening = next((leg for leg in legs if str(leg.get("side") or "").lower() in ("buy", "sell_short")), {})
            if opening and float(opening.get("exec_quantity") or 0) >= float(opening.get("quantity") or 0) > 0:
                self._protected_ids[key].discard(order_id)
                continue
            if str(row.get("status") or "").lower() in ("open", "pending", "partially_filled"):
                return row
            self._protected_ids[key].discard(order_id)
        return None

    def cancel_stale_protected(self, acct, desired):
        aid, _key, _base = self.account(acct)
        cancelled = []
        for (owner, ss), order_ids in tuple(self._protected_ids.items()):
            if owner != acct or ss in desired:
                continue
            for order_id in tuple(order_ids):
                row = self._protected_order(acct, order_id)
                if str(row.get("status") or "").lower() not in ("open", "pending", "partially_filled"):
                    order_ids.discard(order_id)
                    continue
                legs = row.get("leg") or []
                legs = [legs] if isinstance(legs, dict) else legs
                opening = next((leg for leg in legs if str(leg.get("side") or "").lower() in ("buy", "sell_short")), {})
                if opening and float(opening.get("exec_quantity") or 0) >= float(opening.get("quantity") or 0) > 0:
                    order_ids.discard(order_id)
                    continue
                if float(opening.get("exec_quantity") or 0) > 0:
                    raise RuntimeError(f"OTO_PARTIAL_ENTRY_NEEDS_STOP_RECONCILIATION {acct} {ss} id={order_id}")
                result = self._req("DELETE", acct, f"/accounts/{aid}/orders/{order_id}")
                order_ids.discard(order_id)
                self._orders_cache.pop((acct, order_id), None)
                cancelled.append((ss, order_id, result))
        return cancelled

    def send_protected(self, acct, order, quantity, stop_level):
        aid, _key, _base = self.account(acct)
        symbol, side = split_ss(order["ss"])
        quote = (self._req("GET", acct, "/markets/quotes", params={"symbols": symbol}).get("quotes") or {}).get("quote") or {}
        quote = quote[0] if isinstance(quote, list) else quote
        opening_side = "buy" if side == "LONG" else "sell_short"
        closing_side = "sell" if side == "LONG" else "buy_to_cover"
        touch = float(quote.get("ask") or 0) if side == "LONG" else float(quote.get("bid") or 0)
        if not math.isfinite(touch) or touch <= 0:
            raise RuntimeError(f"OTO_NO_TOUCH_QUOTE {symbol}: no {('ask' if side == 'LONG' else 'bid')}")
        limit_price = math.ceil(touch * 1.003 * 100) / 100 if side == "LONG" else math.floor(touch * 0.997 * 100) / 100
        if limit_price <= 0:
            raise RuntimeError(f"OTO_BAD_LIMIT {symbol}: {limit_price}")
        data = {"class": "oto", "duration[0]": "day", "duration[1]": "gtc", "symbol[0]": symbol, "quantity[0]": str(int(quantity)), "type[0]": "limit", "side[0]": opening_side, "price[0]": f"{limit_price:.2f}", "symbol[1]": symbol, "quantity[1]": str(int(quantity)), "type[1]": "stop", "side[1]": closing_side, "stop[1]": f"{stop_level:.2f}", "tag": "vdo" + hashlib.sha1(order["key"].encode()).hexdigest()[:20]}
        result = self._req("POST", acct, f"/accounts/{aid}/orders", data=data)
        parent = result.get("order") or {}
        if not parent.get("id"):
            raise RuntimeError(f"OTO_SUBMISSION_UNCONFIRMED {symbol}: {str(parent)[:160]}")
        order_id = str(parent["id"])
        self._protected_ids.setdefault((acct, order["ss"]), set()).add(order_id)
        self._orders_cache[(acct, order_id)] = (time.time(), {"id": parent["id"], "status": "pending", "class": "oto"})
        return {"id": parent["id"], "status": parent.get("status"), "class": "oto", "limit": limit_price, "stop": stop_level}

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
            _cap = min(MAX_OPEN_NOTIONAL, ENTRY_NOTIONAL_CEILING_USD)
            if _want > _cap:
                log(f"[VD-CAP] {o.get('ss')} open notional ${_want:.0f} clamped to ${_cap:.0f} (MAX_OPEN_NOTIONAL ${MAX_OPEN_NOTIONAL:.0f}, entry ceiling ${ENTRY_NOTIONAL_CEILING_USD:.0f})")
                _want = _cap
            n = math.floor(_want / price) if price > 0 else 0
            if n >= 1:
                return float(n), ""
            # stock convention (tradier_manage max(1, int(qty)); config_tradier START_POSITION_SIZE $500 "$150 can't buy 1
            # share of $300+ stocks"): one whole share when the $15 slice is less than a share, up to STOCK_ONE_SHARE_MAX
            # and never above the entry ceiling
            if 0 < price <= min(STOCK_ONE_SHARE_MAX, ENTRY_NOTIONAL_CEILING_USD):
                return 1.0, ""
            return 0.0, f"one share ${price:.2f} > ${min(STOCK_ONE_SHARE_MAX, ENTRY_NOTIONAL_CEILING_USD):.0f} entry cap (skip)"
        if o["act"] == "AUGMENT":
            if price <= 0:
                return 0.0, "augment with no price (skip)"
            n = math.floor(float(o["qty"]))
            if n * price > ENTRY_NOTIONAL_CEILING_USD:
                n = math.floor(ENTRY_NOTIONAL_CEILING_USD / price)
                log(f"[VD-CAP] {o.get('ss')} augment clamped to ${ENTRY_NOTIONAL_CEILING_USD:.0f} entry ceiling ({n} sh @ ${price:.2f})")
            return (float(n), "") if n >= 1 else (0.0, f"augment {o['qty']:.3f} shares < 1 or over ${ENTRY_NOTIONAL_CEILING_USD:.0f} ceiling")
        n = min(int(held), int(round(float(o["qty"]))))
        return (float(n), "") if n >= 1 else (0.0, f"reduce {o['qty']:.3f} shares < 1")

    def send(self, acct, o, qty):
        aid, _k, _b = self.account(acct)
        symbol, side = split_ss(o["ss"])
        opening = o["act"] in ("OPEN", "AUGMENT")
        if opening and _send_gate_entry_banned(acct):
            raise RuntimeError(f"REFUSED_TRA_EXITS_ONLY {acct} {o['ss']} {o['act']} (order_dedupe_guard.entry_banned at the send gate)")
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
        self.filled_open_anchors = self._load_filled_open_anchors()
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

    def _load_filled_open_anchors(self):
        filled = set()
        for days_back in range(31):
            path = self._ledger_path(time.time() - days_back * 86400)
            try:
                with path.open() as rows:
                    for line in rows:
                        row = json.loads(line)
                        if row.get("event") == "OBSERVED_OPEN" and row.get("anchor") is not None:
                            filled.add((row.get("acct"), row.get("ss"), int(row["anchor"])))
                            continue
                        if row.get("event") != "ORDER" or row.get("act") != "OPEN" or row.get("status") != "SENT":
                            continue
                        response = row.get("resp") or {}
                        if not isinstance(response, dict):
                            continue
                        if str(response.get("status") or "").upper() != "FILLED" and float(response.get("executedQty") or 0) <= 0:
                            continue
                        anchor = row.get("anchor") or str(row.get("why") or "").split("board holds since ")[-1].split(",", 1)[0]
                        if str(anchor).isdigit():
                            filled.add((row.get("acct"), row.get("ss"), int(anchor)))
            except (OSError, ValueError, TypeError):
                continue
        return filled

    def _remember_held_anchors(self, acct, positions, states):
        for symbol_side, position in positions.items():
            if float(position.get("qty") or 0) <= 0:
                continue
            state = states.get(symbol_side) or {}
            if not state.get("ok") or not state.get("open"):
                continue
            anchor = state.get("engine_open_bar_ts") or state.get("entry_bar")
            if anchor is None:
                continue
            key = (acct, symbol_side, int(anchor))
            if key not in self.filled_open_anchors:
                self.filled_open_anchors.add(key)
                self._ledger({"event": "OBSERVED_OPEN", "acct": acct, "ss": symbol_side, "anchor": int(anchor), "qty": float(position["qty"])})

    def _load_done(self):
        done = set()
        for back in (0, 1):
            p = self._ledger_path(time.time() - back * 86400)
            try:
                for line in p.read_text().splitlines():
                    r = json.loads(line)
                    if r.get("act") == "REDUCE" and r.get("status") == "SKIP_SIZE":
                        continue
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
                out[ss] = reconcile_terminal_lifecycle(d)
        return out

    def positions(self, acct):
        """Broker positions + bookkeeping of what is held. 2026-10-27: held sides are published to held_<venue>.json so the
        state producer (tools/fwdstate_npz_watch.py on S1) computes them FIRST after every bar close and covers a held side
        that is outside the tradeable universe."""
        pos = self.broker.positions(acct)
        self.__dict__.setdefault("position_observed_at", {})[acct] = time.time()
        self.annotate_with_mac(acct, pos)
        held = self.__dict__.setdefault("held", {})
        held[acct] = sorted(ss for ss, p in pos.items() if float(p.get("qty") or 0) > 0)
        sides = sorted(set().union(*held.values()))
        # USER 2026-10-28 "calculate open positions first from biggest to smallest then the rest of tradeable keys": per-side open
        # notional (|qty| x mark; Tradier has no mark here -> |cost_basis|) summed over accounts, read by the NPZ appender, the
        # state producer and the bar guarantee to order their queues
        hn = self.__dict__.setdefault("held_notional", {})
        hn[acct] = {ss: round(float(p.get("qty") or 0) * float(p.get("price") or 0) or float(p.get("cost") or 0), 2) for ss, p in pos.items() if float(p.get("qty") or 0) > 0}
        notional = {}
        for per in hn.values():
            for ss, v in per.items():
                notional[ss] = round(notional.get(ss, 0.0) + v, 2)
        t, last = self.__dict__.get("held_written", (0.0, None))
        if sides != last or time.time() - t > 15:
            try:
                atomic_write_json(self.out / f"held_{self.venue}.json", {"epoch": time.time(), "sides": sides, "by_account": held,
                                                                          "notional": notional, "total_notional": round(sum(notional.values()), 2)})
                self.held_written = (time.time(), sides)
            except OSError as exc:
                log(f"held file write failed: {exc}")
        return pos

    def sevenday_sides(self):
        """{sym_side: staged_epoch} from the 7D lane, re-read when the artifact changes. Empty = nothing is fresh.

        Two sources, first readable wins: data/vector_direct/sevenday_<venue>.json — the contract a 7D runner writes,
        {"epoch": ..., "sides": {ss: staged_epoch}} — then the older data/preopen_7d/active/current.json. An artifact
        that cannot be parsed yields {}, which refuses every entry rather than guessing one is fresh."""
        out, stamps = {}, []
        root = getattr(self, "root", None) or self.out
        paths = (self.out / f"sevenday_{self.venue}.json", Path(root) / "data" / "preopen_7d" / "active" / "current.json")
        for path in paths:
            try:
                stamps.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                stamps.append((str(path), 0))
        hit = self.__dict__.get("sevenday_cache")
        if hit and hit[0] == stamps:
            return hit[1]
        for path in paths:
            doc = read_json(path, None)
            if not isinstance(doc, dict):
                continue
            sides = doc.get("sides") if isinstance(doc.get("sides"), dict) else doc.get("sym_sides")
            if not isinstance(sides, dict):
                continue
            fallback = float(doc.get("epoch") or 0.0)
            for ss, value in sides.items():
                when = value if isinstance(value, (int, float)) else (value or {}).get("staged_epoch") if isinstance(value, dict) else None
                try:
                    out[str(ss).upper()] = float(when) if when else fallback
                except (TypeError, ValueError):
                    out[str(ss).upper()] = fallback
            if out:
                break
        self.sevenday_cache = (stamps, out)
        return out

    def sevenday_ok(self, acct, ss, now):
        """(allowed, why) for an OPEN/AUGMENT on a 7D-only account. Entries only; exits never reach here."""
        if "*" not in SEVENDAY_ONLY_ACCOUNTS and acct not in SEVENDAY_ONLY_ACCOUNTS:
            return True, ""
        book = SEVENDAY_BOOK.get(acct)
        if book:
            keys = universe(self.root, self.venue, (book,)).get(book) or set()
            if not keys:
                return False, f"{acct} is 7D-only on {book}'s book and {book}'s key list is unreadable"
            if ss.upper() not in keys:
                return False, f"{ss} is not in {book}'s book"
        published = self.sevenday_sides()
        strict = acct in SEVENDAY_STRICT_ACCOUNTS
        if not published and SEVENDAY_LANE_DOWN_BRAKE and not strict:
            if not self.__dict__.get("sevenday_lane_rung"):
                self.sevenday_lane_rung = True
                self.alert("SEVENDAY_LANE_DOWN", venue=self.venue, severity="CRITICAL",
                           detail="the 7D lane has published no side at all; entries continue on the 30D sets")
                log(f"CRITICAL SEVENDAY_LANE_DOWN {self.venue}: nothing published, so the 7D-only rule cannot be met by any side — "
                    f"entries continue on the published 30D sets and every side is reported unverified. Fix the 7D lane.")
            return True, "7D lane down (no side published anywhere) — 30D set used, CRITICAL rung"
        self.sevenday_lane_rung = False
        staged = published.get(ss.upper())
        if not staged:
            return False, f"no 7D result for {ss} (the 7D lane has published nothing for it)"
        age = now - float(staged)
        if age > SEVENDAY_MAX_AGE_S:
            return False, f"7D result for {ss} is {age / 3600:.1f} h old (limit {SEVENDAY_MAX_AGE_S / 3600:.0f} h)"
        return True, f"7D result {age / 3600:.1f} h old"

    def audit_protection(self, acct, pos):
        """Ring for every held position with no resting protective stop. Reports only — it places no order.

        An entry is protected by the stop its own order carries (Tradier OTO leg, Binance closePosition algo). Nothing
        re-checked afterwards, so a stop that was rejected, cancelled or expired left the position bare and silent.
        Measured 2026-10-30: trc held LMT_LONG with no stop of any kind at the broker."""
        if not PROTECTION_AUDIT_S or time.time() - self.__dict__.setdefault("protection_audited", {}).get(acct, 0.0) < PROTECTION_AUDIT_S:
            return
        self.protection_audited[acct] = time.time()
        if not hasattr(self, "stops"):
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from vector_direct_stops import Stops
            self.stops = Stops(self)
        bare, rung = [], self.__dict__.setdefault("bare_rung", set())
        for ss, row in pos.items():
            if float(row.get("qty") or 0) <= 0:
                continue
            symbol, side = split_ss(ss)
            try:
                level = self.stops.resting_level(acct, symbol, side)
            except Exception as exc:
                log(f"protection audit {acct} {ss}: resting stop unreadable {str(exc)[:120]}")
                continue
            if level:
                rung.discard((acct, ss))
                continue
            bare.append(ss)
            if (acct, ss) not in rung:
                rung.add((acct, ss))
                self.alert("VD_HELD_POSITION_UNPROTECTED", account=acct, sym_side=ss, qty=float(row.get("qty") or 0), severity="CRITICAL")
        if bare:
            log(f"CRITICAL UNPROTECTED {acct}: {len(bare)} held position(s) with no resting stop: {','.join(sorted(bare))}")
        self._ledger({"event": "PROTECTION_AUDIT", "acct": acct, "held": len([1 for r in pos.values() if float(r.get("qty") or 0) > 0]), "unprotected": sorted(bare)})

    def lease_ok(self, now):
        """True when THIS process holds the venue's trading lease. Writes its own beat either way."""
        if not hasattr(self, "lease"):
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import vector_direct_lease
            self.lease = vector_direct_lease
            self.lease_state = {}
        try:
            may, why, self.lease_state = self.lease.decide(self.venue, self.a.role, self.out, now, self.lease_state)
        except Exception as exc:
            # A primary must never be stopped by a fault in the arbiter — that would leave the venue uncovered.
            # A backup must never trade on a fault, because that is how two traders happen.
            may = self.a.role == "primary"
            why = f"lease decision failed ({type(exc).__name__}: {str(exc)[:120]}); {'primary keeps trading' if may else 'backup stands down'}"
        try:
            self.lease.write_beat(self.out, self.venue, self.a.role, may, why)
        except OSError as exc:
            log(f"lease beat write failed: {exc}")
        if may and self.a.role == "backup":
            # A backup that claims the lease makes the primary stand down. If it then cannot reach the broker the venue
            # has ZERO traders — worse than having no backup at all. Measured 2026-10-31: S2 cannot authenticate to
            # Binance (-2015, its egress 2.29.22.116 is not on the API key allowlist, while S1 binds an allowlisted
            # address), so it must never take the lease. Readiness is a real read-only broker call, cached briefly.
            ready, detail = self.broker_ready(now)
            if not ready:
                self.lease.write_beat(self.out, self.venue, self.a.role, False, f"NOT broker-ready: {detail}"[:200])
                if getattr(self, "lease_why", None) != detail:
                    self.lease_why = detail
                    self.alert("VD_BACKUP_NOT_BROKER_READY", venue=self.venue, severity="CRITICAL", detail=detail[:200])
                    log(f"CRITICAL LEASE {self.venue} backup REFUSING the lease: {detail} — the primary must keep the venue")
                    self._ledger({"event": "LEASE", "venue": self.venue, "role": "backup", "may_trade": False, "why": f"not broker-ready: {detail}"})
                return False
        if why != getattr(self, "lease_why", None):
            self.lease_why = why
            log(f"LEASE {self.venue} {self.a.role}: {'TRADING' if may else 'STOOD DOWN'} — {why}")
            self._ledger({"event": "LEASE", "venue": self.venue, "role": self.a.role, "may_trade": bool(may), "why": why})
            if not may:
                self.alert("VD_LEASE_STOOD_DOWN", venue=self.venue, role=self.a.role, why=why[:200])
        return bool(may)

    def broker_ready(self, now):
        """(ready, detail) from a real read-only broker call on this host. Cached for VD_BROKER_READY_EVERY_S."""
        hit = self.__dict__.get("broker_ready_cache")
        if hit and now - hit[0] < BROKER_READY_EVERY_S:
            return hit[1], hit[2]
        acct = (self.accounts or (None,))[0]
        if not acct:
            return False, "no account configured"
        try:
            self.broker.positions(acct)
            ready, detail = True, f"broker reachable for {acct} from this host"
        except Exception as exc:
            ready, detail = False, f"broker unreachable for {acct} from this host: {str(exc)[:200]}"
        self.broker_ready_cache = (now, ready, detail)
        return ready, detail

    def mac_positions(self):
        """The Mac books as pushed by tools/mac_positions_push_executors.py, re-read only when the file changes."""
        path = self.out / f"mac_positions_{self.venue}.json"
        try:
            stat = path.stat()
        except OSError:
            return {}
        stamp = (stat.st_mtime_ns, stat.st_size)
        hit = self.__dict__.get("mac_positions_cache")
        if hit and hit[0] == stamp:
            return hit[1]
        doc = read_json(path, {}) or {}
        self.mac_positions_cache = (stamp, doc if isinstance(doc, dict) else {})
        return self.mac_positions_cache[1]

    def annotate_with_mac(self, acct, pos):
        """Attach the Mac's lifecycle fields to the broker's positions. The broker's qty is never touched."""
        if not MAC_POSITIONS:
            return pos
        doc = self.mac_positions()
        if not doc:
            return pos
        age = time.time() - float(doc.get("epoch") or 0)
        if age > MAC_POSITIONS_MAX_AGE_S:
            if self.__dict__.setdefault("mac_pos_stale", None) != int(age // MAC_POSITIONS_MAX_AGE_S):
                self.mac_pos_stale = int(age // MAC_POSITIONS_MAX_AGE_S)
                self.alert("VD_MAC_POSITIONS_STALE", venue=self.venue, age_s=round(age, 1), written_at=doc.get("written_at"))
                log(f"mac positions stale: {round(age / 60, 1)} min old (limit {MAC_POSITIONS_MAX_AGE_S / 60:.0f} min) — lifecycle fields not attached")
            return pos
        self.mac_pos_stale = None
        book = (doc.get("accounts") or {}).get(acct) or {}
        rung = self.__dict__.setdefault("mac_pos_rung", set())
        for ss, broker_row in pos.items():
            row = book.get(ss)
            if not isinstance(row, dict):
                signal = (acct, ss, "mac_missing", doc.get("written_at"))
                if signal not in rung:
                    rung.add(signal)
                    self.alert("VD_MAC_POSITION_MISSING", account=acct, sym_side=ss, broker_qty=float(broker_row.get("qty") or 0), written_at=doc.get("written_at"))
                continue
            broker_qty, mac_qty = float(broker_row.get("qty") or 0), float(row.get("qty") or 0)
            source_epoch = float(row.get("source_file_epoch") or doc.get("epoch") or 0)
            if row.get("last_updated"):
                try:
                    source_epoch = min(source_epoch, dt.datetime.fromisoformat(str(row["last_updated"]).replace("Z", "+00:00")).replace(tzinfo=dt.timezone.utc).timestamp())
                except (ValueError, TypeError):
                    source_epoch = 0.0
            source_age = time.time() - source_epoch
            if source_age > MAC_POSITIONS_MAX_AGE_S or source_age < -5:
                signal = (acct, ss, "source_stale", source_epoch)
                if signal not in rung:
                    rung.add(signal)
                    self.alert("VD_MAC_POSITION_SOURCE_STALE", account=acct, sym_side=ss, source_age_s=round(source_age, 1), source_file=row.get("source_file"), written_at=doc.get("written_at"))
                broker_row["position_source"] = "broker_verified_mac_source_stale"
                continue
            if broker_qty > 0 and abs(mac_qty - broker_qty) > max(1e-8, broker_qty * 1e-6):
                signal = (acct, ss, "qty", round(mac_qty, 8), round(broker_qty, 8))
                if signal not in rung:
                    rung.add(signal)
                    self.alert("VD_MAC_POSITION_QTY_DISAGREES", account=acct, sym_side=ss, broker_qty=broker_qty, mac_qty=mac_qty, written_at=doc.get("written_at"))
                broker_row["mac_qty"] = mac_qty
                broker_row["position_source"] = "broker_verified_mac_quantity_mismatch"
                continue
            broker_row["mac"] = {k: v for k, v in row.items() if k != "qty"}
            broker_row["mac_qty"] = mac_qty
            broker_row["mac_age_s"] = round(source_age, 1)
            broker_row["position_source"] = "mac_snapshot_broker_verified"
            if not float(broker_row.get("entry") or 0) and row.get("entry_price") is not None:
                broker_row["entry"] = float(row["entry_price"])
            for field in ("opened_at", "max_gain", "gain", "last_signal"):
                if row.get(field) is not None and broker_row.get(field) is None:
                    broker_row[field] = row[field]
        for ss, row in book.items():
            if ss in pos or float(row.get("qty") or 0) <= 0:
                continue
            signal = (acct, ss, "broker_flat", doc.get("written_at"))
            if signal not in rung:
                rung.add(signal)
                self.alert("VD_MAC_HOLDS_WHAT_BROKER_DOES_NOT", account=acct, sym_side=ss, mac_qty=float(row.get("qty") or 0), written_at=doc.get("written_at"))
                log(f"mac holds {acct} {ss} qty={row.get('qty')} the broker does not show — no order derived from it, the broker is the authority")
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
                    states[ss] = reconcile_terminal_lifecycle(d)
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
            self._remember_held_anchors(acct, pos, states)
            orders, _ = plan_account(acct, self.venue, keys.get(acct, ()), states, pos, bar, self.paper_done, allowed, wt15=self.wt15, noinfo_age=self.noinfo_age, halted=dict.fromkeys(getattr(self, "halted_now", ()), {}))
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
                self.capture_order_states(orders, states)
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
        global START_POSITION_SIZE, MAX_OPEN_NOTIONAL, ENTRY_NOTIONAL_CEILING_USD
        now = time.time()
        bar = last_closed_bar(now)
        if self.venue == "stocks":
            mode, why = self.stock_mode(now)
            START_POSITION_SIZE, ENTRY_NOTIONAL_CEILING_USD, MAX_OPEN_NOTIONAL = stock_mode_sizing(mode)
            if mode != getattr(self, "stock_mode_now", None):
                log(f"STOCK MODE {mode} start=${START_POSITION_SIZE:.0f} max_order=${ENTRY_NOTIONAL_CEILING_USD:.0f} max_position=${MAX_OPEN_NOTIONAL:.0f} ({why})")
                self.stock_mode_now = mode
        keys = universe(self.root, self.venue, self.accounts)
        all_sides = set().union(*keys.values()) if keys else set()
        states = self.load_states(all_sides | set().union(*self.held.values()) if self.held else all_sides)
        market_ok = True
        if self.venue == "stocks":
            market_ok = self.broker.market_open() or bool(self.a.assume_open and self.a.dry_run)
        halted = deterioration_halts(self.root)
        if self.venue == "crypto":
            halted.update(rolling_deterioration_halts(self.root, self.state_dir, states, bar, alert=self.alert))
        if set(halted) != getattr(self, "halted_now", set()):
            log(f"DETERIORATION HALT {len(halted)} sym_sides (no OPEN/AUGMENT, exits unchanged): {','.join(sorted(halted))[:600]}")
            self.halted_now = set(halted)
        allowed = lambda acct, ss: ss not in halted and entry_allowed(self.root, self.venue, acct, ss)
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
        # USER 2026-10-31: exactly one trader per venue, and never zero for long. The lease decides; a trader without it
        # places NO order of any kind, because two traders exiting the same position is double-trading just as much as
        # two opening it. Only the ORDER phase is skipped — states, positions, the beat and the status file keep
        # refreshing, so a handover costs nothing and a stood-down trader is still fully observable.
        lease_held = self.lease_ok(now)
        untradeable = (read_json(self.out / f"untradeable_{self.venue}.json", {}) or {}).get("sides") or {}
        untradeable_close = not (self.out / "UNTRADEABLE_CLOSE_DISABLED").exists()
        if self.__dict__.pop("plan_now", False):
            self.last_plan = {}
        consumed = self.__dict__.setdefault("consumed", {})
        consumed_dirty = False
        acct_notional = {a: sum((self.__dict__.get("held_notional") or {}).get(a, {}).values()) for a in active}
        for acct in sorted(active, key=lambda a: -acct_notional.get(a, 0.0)) if (market_ok and lease_held) else []:
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
            self.audit_protection(acct, pos)
            if self.venue == "crypto":
                halted.update(rolling_deterioration_halts(self.root, self.state_dir, states, bar, alert=self.alert))
            self._remember_held_anchors(acct, pos, states)
            t_obs, prev = self.obs.get(acct, (0, {}))
            prev_qty = prev if t_obs and bar - BAR_S <= t_obs < bar + BAR_S else {}
            orders, notes = plan_account(acct, self.venue, keys.get(acct, ()), states, pos, bar, self.done, allowed, prev_qty, wt15=self.wt15, noinfo_age=self.noinfo_age,
                                         untradeable=untradeable, untradeable_close=untradeable_close, halted=halted)
            planned_at = time.time()
            for o in orders:
                if o.get("reason") == "UNTRADEABLE_NO_VERDICT":
                    log(f"CRITICAL UNTRADEABLE CLOSE {acct} {o['ss']} qty={o['qty']} — {o['why']}")
                    self.alert("UNTRADEABLE_CLOSE", account=acct, sym_side=o["ss"], qty=o["qty"], why=o["why"], severity="CRITICAL")
            # consumed_<venue>.json: which state (npz_tail, ts) this account was planned against, first time per state —
            # read by tools/s1_bar_guarantee.py as the per-bar "executed" stage
            for ss in set(keys.get(acct, ())) | set(pos):
                st = states.get(ss) or {}
                sig = [st.get("npz_tail"), st.get("ts"), (untradeable.get(ss) or {}).get("bar")]
                row = consumed.setdefault(ss, {}).get(acct)
                if not row or row.get("sig") != sig or not row.get("decision_provenance"):
                    consumed[ss][acct] = {"sig": sig, "npz_tail": st.get("npz_tail"), "ok": bool(st.get("ok")), "at": planned_at,
                                          "orders": [o["act"] for o in orders if o["ss"] == ss], "keys": [o["key"] for o in orders if o["ss"] == ss],
                                          "held": float((pos.get(ss) or {}).get("qty") or 0.0), "allowed": bool(allowed(acct, ss)) if (ss in keys.get(acct, ()) and st.get("open") and not (pos.get(ss) or {}).get("qty")) else None,
                                          "notes": [n for s, n in notes if s == ss], "untradeable_bar": (untradeable.get(ss) or {}).get("bar")}
                    try:
                        consumed[ss][acct]["decision_provenance"] = persist_decision_state(self.out, st)
                        consumed[ss][acct]["account_execution_context"] = self.capture_account_position(acct, ss, pos.get(ss) or {"qty": 0.0}, consumed[ss][acct]["decision_provenance"], bar)
                        self._ledger({"event": "DECISION", "acct": acct, "ss": ss, "bar": bar, **consumed[ss][acct]})
                    except OSError as exc:
                        self.alert("DECISION_PROVENANCE_WRITE_FAILED", account=acct, sym_side=ss, error=str(exc), severity="CRITICAL")
                    consumed_dirty = True
            self.obs[acct] = (now, {ss: p["qty"] for ss, p in pos.items()})
            for ss, n in notes:
                if n != "HOLD_NO_FRESH_STATE":
                    k = f"{acct}|{ss}|{bar}|NOTE|{n[:24]}"
                    if k not in self.done:
                        self.done.add(k)
                        self._ledger({"event": "NOTE", "acct": acct, "ss": ss, "note": n, "key": k, "final": True})
            # biggest open position first, then the rest of the tradeable keys
            orders.sort(key=lambda o: (0 if (pos.get(o["ss"]) or {}).get("qty") else 1, -float((pos.get(o["ss"]) or {}).get("qty") or 0) * float((pos.get(o["ss"]) or {}).get("price") or 0) or -float((pos.get(o["ss"]) or {}).get("cost") or 0), o["ss"]))
            if self.venue == "stocks" and acct in REBAL_ACCOUNTS:
                orders_ok, rebal_win, forced = self.rebal_phase()
                if rebal_win and orders_ok:
                    dropped = [o["ss"] for o in orders if o["act"] in ("OPEN", "AUGMENT")]
                    orders = [o for o in orders if o["act"] not in ("OPEN", "AUGMENT")] + self.rebalance(acct, pos, bar, forced)
                    if dropped:
                        log(f"{acct} REBAL WINDOW: entries held back {dropped}")
            if self.venue == "stocks" and not self.a.dry_run and not killed:
                try:
                    desired = {o["ss"] for o in orders if o["act"] in ("OPEN", "AUGMENT") and not self._entry_banned(acct)}
                    for ss, order_id, result in self.broker.cancel_stale_protected(acct, desired):
                        self._ledger({"event": "ORDER", "acct": acct, "ss": ss, "act": "CANCEL_PENDING_ENTRY", "status": "PROTECTED_OTO_CANCELLED", "order_id": order_id, "resp": result, "final": True})
                        log(f"PROTECTED_OTO_CANCELLED {acct} {ss} id={order_id}: no longer planned")
                except Exception as exc:
                    self.alert("PROTECTED_OTO_CANCEL_FAILED", account=acct, error=str(exc)[:200], severity="CRITICAL")
                    log(f"PROTECTED_OTO_CANCEL_FAILED {acct}: {exc}")
            self.capture_order_states(orders, states)
            orders_n += self.send_orders(acct, orders, pos, killed)
        if consumed_dirty:
            try:
                atomic_write_json(self.out / f"consumed_{self.venue}.json", {"epoch": time.time(), "pid": os.getpid(), "sides": consumed})
            except OSError as exc:
                log(f"consumed file write failed: {exc}")
        if market_ok and lease_held:
            # the backstop SENDS orders, so it is gated by the lease exactly like the main loop
            self.paper(now, [a for a in self.accounts if a not in active], keys, states, bar, allowed)
            orders_n += self.backstop(now, killed, keys, states, allowed)
        fresh_n = sum(1 for st in states.values() if state_fresh(st, bar))
        unmanaged = sorted(f"{a}:{ss}" for a, sides in self.held.items() for ss in sides if not state_fresh(states.get(ss), bar))
        status = {"epoch": now, "iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "host": socket.gethostname(),
                  "role": self.a.role, "lease_held": bool(lease_held), "lease_why": getattr(self, "lease_why", ""),
                  "venue": self.venue, "armed": not self.a.dry_run and not killed, "dry_run": self.a.dry_run, "killed": killed,
                  "market_open": market_ok, "active_accounts": active, "triggers": trig, "hb_age_s": None if hb_age is None else round(hb_age, 1),
                  "bar": bar, "states_fresh": fresh_n, "states_total": len(all_sides), "held_without_fresh_state": unmanaged, "pending_expectations": len(self.expect),
                  "orders_this_pass": orders_n, "start_position_size": START_POSITION_SIZE, "pin": self.pinned(now),
                  "pass_s": round(time.time() - now, 3), "wake": self.__dict__.get("last_wake"),
                  "untradeable": sorted(untradeable), "untradeable_close_enabled": untradeable_close,
                  "bells": read_json(self.root / "data" / "bar_guarantee" / f"BELLS_{self.venue}.json")}
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

    def _entry_banned(self, acct):
        """Hard entry ban from order_dedupe_guard.entry_banned (tra: exits only). Unreadable guard fails closed for stocks."""
        try:
            for base in (Path.home() / "binance-sandbox", Path(__file__).resolve().parents[1], self.root):
                if (base / "order_dedupe_guard.py").exists() and str(base) not in sys.path:
                    sys.path.insert(0, str(base))
            from order_dedupe_guard import entry_banned
            return bool(entry_banned(acct))
        except Exception as exc:
            log(f"entry_banned unavailable ({type(exc).__name__}): {'refusing' if self.venue == 'stocks' else 'allowing'} {acct} entries")
            return self.venue == "stocks"

    def stock_mode(self, now):
        """(mode, source) from data/market_mode_stocks.json; LIGHT_MODE when missing or older than STOCK_MODE_MAX_AGE_S.

        The market mode is VENUE-wide, not per-root: tools/stock_market_mode.py writes one file into the host's main
        tree. An alternate root (the trc 7D lane runs --root ~/trc7d_root) has no copy of it, so reading only the root
        pinned that account to the LIGHT fallback for good — measured 2026-10-30, trb followed the detector while trc
        read "mode file missing" on every pass. Two accounts on different size tables cannot be compared, so the main
        tree is consulted whenever the root has no fresh file."""
        for path in (self.root / "data" / "market_mode_stocks.json", Path.home() / "binance" / "data" / "market_mode_stocks.json"):
            doc = read_json(path, {}) or {}
            mode = doc.get("mode")
            try:
                ts = dt.datetime.strptime(str(doc.get("timestamp")), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc).timestamp()
            except ValueError:
                ts = 0.0
            if mode in STOCK_MODE_START and now - ts <= STOCK_MODE_MAX_AGE_S:
                where = "root" if path.parent.parent == self.root else "venue tree"
                return mode, f"detector index {doc.get('market_index')} ({where})"
        return "LIGHT_MODE", "FALLBACK LIGHT (mode file missing or stale)"

    def rebal_phase(self, now=None):
        """(orders_ok, rebalance_window, forced) in New York time for the stock venue."""
        from zoneinfo import ZoneInfo
        t = dt.datetime.fromtimestamp(now or time.time(), dt.timezone.utc).astimezone(ZoneInfo("America/New_York"))
        if t.weekday() >= 5:
            return False, False, False
        hm = (t.hour, t.minute)
        if not (9, 30) <= hm < (16, 0):
            return False, False, False
        return True, hm >= (14, 30), hm >= (15, 45)

    def bar15(self, symbol):
        """Last closed 15m bar of symbol from the host's NPZ tail sidecar (fields high_15m, low_15m, close_15m, dc_*_15m)."""
        base = Path(self.a.npz_dir).expanduser() if self.a.npz_dir else Path(self.state_dir).parent.parent / "backtest_v8" / "indicators"
        p = base / ".tail" / f"{symbol}.jsonl"
        try:
            with open(p, "rb") as fh:
                fh.seek(max(0, p.stat().st_size - 8192))
                last = [ln for ln in fh.read().decode("utf-8", "replace").splitlines() if ln.strip()][-1]
            return json.loads(last)
        except Exception:
            return None

    def at_local_extreme(self, symbol, side):
        """side 'top': the last closed 15m bar made the channel high and turned down; 'bottom': channel low and turned up."""
        b = self.bar15(symbol)
        if not b:
            return False
        try:
            if side == "top":
                return float(b["high_15m"]) >= float(b["dc_high_15m"]) * 0.999 and float(b["close_15m"]) < float(b["close_15m_prev"])
            return float(b["low_15m"]) <= float(b["dc_low_15m"]) * 1.001 and float(b["close_15m"]) > float(b["close_15m_prev"])
        except (KeyError, TypeError, ValueError):
            return False

    def rebalance(self, acct, pos, bar, forced):
        """Trim this account's long book to REBAL_LONG_CAP pro-rata with a one-share floor, only at a local top (or forced)."""
        longs = {ss: p for ss, p in pos.items() if ss.endswith("_LONG") and float(p.get("qty") or 0) > 0}
        if not longs:
            return []
        px = {ss: float(self.broker.price(split_ss(ss)[0]) or 0.0) for ss in longs}
        book = sum(float(longs[ss]["qty"]) * px[ss] for ss in longs)
        if book <= REBAL_LONG_CAP:
            return []
        f = REBAL_LONG_CAP / book
        out = []
        for ss in sorted(longs):
            q = float(longs[ss]["qty"])
            tgt = max(1.0, math.floor(q * f))
            cut = q - tgt
            if cut < 1 or px[ss] <= 0:
                continue
            if not (forced or self.at_local_extreme(split_ss(ss)[0], "top")):
                continue
            how = "FORCED 15:45 ET" if forced else "local top confirmed"
            out.append({"ss": ss, "act": "REDUCE", "qty": cut, "key": f"{acct}|{ss}|{bar}|REBAL",
                        "why": f"REBALANCE long book ${book:.0f} > cap ${REBAL_LONG_CAP:.0f} ({how}): {q:.0f} -> {tgt:.0f} sh"})
        return out

    def capture_order_states(self, orders, states):
        for order in orders:
            try:
                order["decision_provenance"] = persist_decision_state(self.out, states.get(order["ss"]) or {})
            except OSError as exc:
                order["decision_provenance"] = {"status": "CAPTURE_FAILED", "error": str(exc)}
                self.alert("DECISION_PROVENANCE_WRITE_FAILED", account=order.get("acct"), sym_side=order["ss"], error=str(exc), severity="CRITICAL")

    def capture_account_position(self, acct, symbol_side, position, provenance, bar_ts):
        try:
            try:
                from tools.account_execution_state import build_snapshot, persist_snapshot
            except ImportError:
                from account_execution_state import build_snapshot, persist_snapshot
            symbol, _side = split_ss(symbol_side)
            if self.venue == "crypto":
                filters_doc = read_json(self.out / "exchange_info_crypto.json", {}) or {}
                filters = (filters_doc.get("symbols") or {}).get(symbol)
                fetched = float(filters_doc.get("_fetched") or 0)
                if not filters or not 0 <= time.time() - fetched <= 6 * 3600:
                    raise ValueError("current broker lot filters unavailable")
                filter_source = {"venue": "crypto", "symbol": symbol, "filters": filters, "fetched_at": fetched}
            else:
                filter_source = {"venue": "stocks", "symbol": symbol, "filters": {"step": 1, "min_qty": 1}, "source": "Tradier.size whole-share execution contract"}
            filter_hash = hashlib.sha256(json.dumps(filter_source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            snapshot = build_snapshot(acct, symbol_side, position, broker_confirmed=True, broker_source="Binance.positionRisk" if self.venue == "crypto" else "Tradier.account.positions", observed_at=(self.__dict__.get("position_observed_at") or {}).get(acct), bar_ts=bar_ts, settings_sha256=provenance.get("settings_sha256"), filter_sha256=filter_hash, state_sha256=provenance.get("state_sha256"))
            reference = persist_snapshot(self.out / "account_execution", snapshot)
            reference.update({"status": "BROKER_OBSERVED", "lifecycle_id": snapshot["lifecycle_id"], "lifecycle_status": snapshot["lifecycle_status"], "filter_source": filter_source})
            return reference
        except (OSError, ValueError, TypeError, ImportError) as exc:
            return {"status": "UNVERIFIED", "error": str(exc)[:240]}

    def send_orders(self, acct, orders, pos, killed):
        orders_n = 0
        submitted_sides = set()
        if self.venue == "stocks" and not self.rebal_phase()[0]:
            for o in orders:
                log(f"{acct} {o['ss']} {o['act']} HELD: stocks orders start at the 09:30 ET open (planning only)")
            return 0
        if self.venue == "stocks":
            held_syms = {}
            for ss in pos:
                held_syms.setdefault(split_ss(ss)[0], set()).add(split_ss(ss)[1])
        for o in orders:
            orders_n += 1
            symbol, side = split_ss(o["ss"])
            if o["act"] in ("REDUCE", "CLOSE") or (self.venue == "crypto" and o["ss"] in submitted_sides):
                try:
                    pos = self.broker.positions(acct)
                    self.__dict__.setdefault("position_observed_at", {})[acct] = time.time()
                    self.annotate_with_mac(acct, pos)
                except Exception as exc:
                    self._ledger({"event": "ORDER", "acct": acct, "ss": o["ss"], "act": o["act"], "key": o["key"], "status": "POSITION_REFRESH_FAILED", "detail": str(exc)[:160], "final": False})
                    log(f"{acct} {o['ss']} {o['act']} held: broker position refresh failed: {str(exc)[:160]}")
                    continue
            held = float((pos.get(o["ss"]) or {}).get("qty") or 0.0)
            row = {"event": "ORDER", "acct": acct, "ss": o["ss"], "act": o["act"], "key": o["key"], "why": o["why"], "held": held, "decision_provenance": o.get("decision_provenance") or {"status": "NOT_CAPTURED"}}
            if self.venue == "crypto" and o["act"] in ("OPEN", "AUGMENT") and (self.out / "EXITS_ONLY").exists():
                if o["key"] not in self.quiet:
                    self.quiet.add(o["key"])
                    self._ledger({**row, "status": "EXITS_ONLY", "final": False})
                    log(f"EXITS_ONLY {acct} {o['ss']} {o['act']} held; CLOSE/REDUCE remain active")
                continue
            if o.get("anchor") is not None:
                row["anchor"] = int(o["anchor"])
            if o["act"] == "OPEN" and (acct, o["ss"], o.get("anchor")) in self.filled_open_anchors:
                row.update({"status": "SKIP_ALREADY_FILLED_ANCHOR", "final": True})
                self.done.add(o["key"])
                self._ledger(row)
                log(f"{acct} {o['ss']} OPEN skipped: board entry {o['anchor']} already filled once")
                continue
            if self.venue == "stocks" and o["act"] == "OPEN" and held_syms.get(symbol, set()) - {side}:
                row.update({"status": "SKIP_OPPOSITE_SIDE_HELD", "final": True})
                self.done.add(o["key"])
                self._ledger(row)
                log(f"{acct} {o['ss']} OPEN skipped: opposite side held on Tradier")
                continue
            if o["act"] in ("OPEN", "AUGMENT") and self._entry_banned(acct):
                row.update({"status": "REFUSED_TRA_EXITS_ONLY", "final": True})
                self.done.add(o["key"])
                self._ledger(row)
                log(f"{acct} {o['ss']} {o['act']} REFUSED_TRA_EXITS_ONLY (order_dedupe_guard.entry_banned)")
                continue
            if o["act"] in ("OPEN", "AUGMENT"):
                allowed_7d, why_7d = self.sevenday_ok(acct, o["ss"], time.time())
                if not allowed_7d:
                    # never final: the moment a fresh 7D set lands, the board's entry must be able to go through
                    row.update({"status": "REFUSED_NOT_FRESH_7D", "detail": why_7d, "final": False})
                    if o["key"] not in self.quiet:
                        self.quiet.add(o["key"])
                        self._ledger(row)
                        log(f"{acct} {o['ss']} {o['act']} REFUSED_NOT_FRESH_7D: {why_7d}")
                    continue
            try:
                price = self.broker.price(symbol) if o["act"] in ("REDUCE", "CLOSE") else (float((pos.get(o["ss"]) or {}).get("price") or 0.0) or self.broker.price(symbol))
                if self.venue == "stocks" and o["ss"] in pos and o["act"] in ("REDUCE", "CLOSE"):
                    pos[o["ss"]]["price"] = price
                qty, why = self.broker.size(o, held, price)
            except RestBlocked as exc:
                log(f"{acct} {o['ss']}: {exc}")
                break
            except Exception as exc:
                qty, why, price = 0.0, f"sizing failed: {str(exc)[:120]}", 0.0
            row.update({"price": price, "qty": qty, "notional": round(qty * price, 4) if price else None})
            row["account_execution_context"] = self.capture_account_position(acct, o["ss"], pos.get(o["ss"]) or {"qty": 0.0}, row["decision_provenance"], row["decision_provenance"].get("npz_tail") or (int(time.time()) // BAR_S - 1) * BAR_S)
            if o["act"] in ("REDUCE", "CLOSE"):
                profitable, profit_check = actual_reduction_is_profitable(o["ss"], pos.get(o["ss"]) or {}, price)
                row["actual_profit_check"] = profit_check
                stop_trigger = verified_fixed_stop_trigger(o["ss"], pos.get(o["ss"]) or {}) if o["act"] == "CLOSE" and not profitable else None
                if stop_trigger:
                    row["stop_trigger_authorization"] = stop_trigger
                wt_trigger = verified_deterioration_wt15_trigger(o, self.venue, getattr(self, "halted_now", set()), self.wt15(o["ss"]), time.time()) if o["act"] == "CLOSE" and not profitable and o.get("deterioration_wt15_bar") is not None else None
                if wt_trigger:
                    row["wt15_exit_authorization"] = wt_trigger
                if not profitable and not stop_trigger and not wt_trigger:
                    row.update({"status": "REFUSED_NONPROFIT_REDUCE" if o["act"] == "REDUCE" else "REFUSED_NONSTOP_LOSS_CLOSE", "final": False})
                    if o["key"] not in self.quiet:
                        self.quiet.add(o["key"])
                        self._ledger(row)
                        self.alert(row["status"], account=acct, sym_side=o["ss"], profit_check=profit_check, severity="CRITICAL")
                    continue
            if qty <= 0:
                retryable = o["act"] == "REDUCE"
                row.update({"status": "REFUSED_REDUCE_SIZE" if retryable else "SKIP_SIZE", "detail": why, "final": not retryable})
                if not retryable:
                    self.done.add(o["key"])
                if o["key"] not in self.quiet:
                    self.quiet.add(o["key"])
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
            if self.venue == "crypto" and o["act"] in ("OPEN", "AUGMENT") and (self.out / "EXITS_ONLY").exists():
                if o["key"] not in self.quiet:
                    self.quiet.add(o["key"])
                    self._ledger({**row, "status": "EXITS_ONLY", "final": False})
                continue
            if not hasattr(self, "stops"):
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from vector_direct_stops import Stops
                self.stops = Stops(self)
            if self.venue == "stocks" and o["act"] in ("OPEN", "AUGMENT"):
                try:
                    pending = self.broker.pending_protected(acct, symbol, side)
                    if pending:
                        row.update({"status": "PROTECTED_ENTRY_PENDING", "detail": str(pending.get("id")), "final": False})
                        if o["key"] not in self.quiet:
                            self.quiet.add(o["key"])
                            self._ledger(row)
                        continue
                    stop_level = self.stops.entry_level(o, price)
                    response = self.broker.send_protected(acct, o, qty, stop_level)
                    row.update({"status": "PROTECTED_OTO_SUBMITTED", "resp": response, "stop_level": stop_level, "final": False})
                    self._ledger(row)
                    log(f"PROTECTED_OTO_SUBMITTED {acct} {o['ss']} {o['act']} qty={qty} limit={response['limit']} stop={stop_level} id={response['id']}")
                except Exception as exc:
                    row.update({"status": "REFUSED_UNPROTECTED_ENTRY", "detail": str(exc)[:300], "final": False})
                    self._ledger(row)
                    self.alert("UNPROTECTED_ENTRY_REFUSED", account=acct, sym_side=o["ss"], error=str(exc)[:200], severity="CRITICAL")
                    log(f"REFUSED_UNPROTECTED_ENTRY {acct} {o['ss']} {o['act']}: {exc}")
                continue
            stop_level_armed = None
            # A protective stop can only PRE-exist where something is already held. Binance refuses a
            # closePosition/GTE stop against a flat book (-4509 "Time in Force (TIF) GTE can only be used with open
            # positions") and Tradier rejects a bare sell/buy_to_cover the same way, so prearming an OPEN from flat is
            # impossible on both venues, not merely unlucky. Measured 2026-10-30: with the EXITS_ONLY flag lifted every
            # crypto OPEN from flat died on -4509, which is why the whole book stayed empty. An OPEN from flat
            # therefore arms its stop immediately AFTER the fill and is closed again if that fails (below) — the
            # behaviour that placed today's 100 crypto stops successfully.
            if o["act"] in ("OPEN", "AUGMENT") and held > 0:
                try:
                    stop_level_armed = self.stops.prearm_entry(acct, o, qty, held, price)
                except Exception as exc:
                    row.update({"status": "REFUSED_UNPROTECTED_ENTRY", "detail": str(exc)[:300], "final": False})
                    self._ledger(row)
                    self.alert("UNPROTECTED_ENTRY_REFUSED", account=acct, sym_side=o["ss"], error=str(exc)[:200], severity="CRITICAL")
                    log(f"REFUSED_UNPROTECTED_ENTRY {acct} {o['ss']} {o['act']}: {exc}")
                    continue
            try:
                submitted_sides.add(o["ss"])
                resp = self.broker.send(acct, o, qty)
                row.update({"status": "SENT", "resp": resp, "final": True})
                if o["act"] == "OPEN" and o.get("anchor") is not None and (str(resp.get("status") or "").upper() == "FILLED" or float(resp.get("executedQty") or 0) > 0):
                    self.filled_open_anchors.add((acct, o["ss"], int(o["anchor"])))
                self.done.add(o["key"])
                if o["act"] == "OPEN":
                    self.done_ts[o["key"]] = time.time()
                log(f"SENT {acct} {o['ss']} {o['act']} qty={qty} px~{price} -> {resp}")
                if stop_level_armed is not None:
                    try:
                        self.stops.verify_entry(acct, o, qty, held, stop_level_armed)
                        log(f"STOP {acct} {o['ss']} {o['act']}: verified resting before and after fill at {stop_level_armed}")
                    except Exception as exc:
                        self.alert("STOP_GONE_AFTER_ENTRY", account=acct, sym_side=o["ss"], error=str(exc)[:200], severity="CRITICAL")
                        log(f"STOP_GONE_AFTER_ENTRY {acct} {o['ss']}: {exc}; closing any held quantity now")
                        try:
                            actual = float((self.broker.positions(acct).get(o["ss"]) or {}).get("qty") or 0.0)
                            if actual > 0:
                                close_order = {"ss": o["ss"], "act": "CLOSE", "qty": actual, "key": f"{o['key']}|STOP_GONE", "why": "STOP_GONE_AFTER_ENTRY"}
                                close_resp = self.broker.send(acct, close_order, actual)
                                self._ledger({"event": "ORDER", "acct": acct, "ss": o["ss"], "act": "CLOSE", "key": close_order["key"], "qty": actual, "status": "SENT", "resp": close_resp, "why": close_order["why"], "final": True})
                                log(f"STOP_GONE_AFTER_ENTRY_CLOSE {acct} {o['ss']} qty={actual}: {close_resp}")
                        except Exception as close_exc:
                            self.alert("STOP_GONE_CLOSE_FAILED", account=acct, sym_side=o["ss"], error=str(close_exc)[:200], severity="CRITICAL")
                else:
                    try:
                        log(f"STOP {acct} {o['ss']} {o['act']}: {self.stops.after_fill(acct, o, qty, held, price)}")
                    except Exception as exc:
                        log(f"STOP {acct} {o['ss']} {o['act']}: hook failed {str(exc)[:160]}")
                    if o["act"] in ("OPEN", "AUGMENT"):
                        # The entry could not be pre-protected, so the guarantee is enforced here instead: a position
                        # that came out of this with no resting stop is closed again rather than left bare.
                        try:
                            if self.stops.resting_level(acct, symbol, side):
                                log(f"STOP {acct} {o['ss']} {o['act']}: resting after the fill")
                            else:
                                self.alert("STOP_MISSING_AFTER_ENTRY", account=acct, sym_side=o["ss"], severity="CRITICAL")
                                log(f"STOP_MISSING_AFTER_ENTRY {acct} {o['ss']}: no resting stop after the fill; closing any held quantity now")
                                actual = float((self.broker.positions(acct).get(o["ss"]) or {}).get("qty") or 0.0)
                                if actual > 0:
                                    close_order = {"ss": o["ss"], "act": "CLOSE", "qty": actual, "key": f"{o['key']}|STOP_MISSING", "why": "STOP_MISSING_AFTER_ENTRY"}
                                    close_resp = self.broker.send(acct, close_order, actual)
                                    self._ledger({"event": "ORDER", "acct": acct, "ss": o["ss"], "act": "CLOSE", "key": close_order["key"], "qty": actual,
                                                  "status": "SENT", "resp": close_resp, "why": close_order["why"], "final": True})
                                    log(f"STOP_MISSING_AFTER_ENTRY_CLOSE {acct} {o['ss']} qty={actual}: {close_resp}")
                        except Exception as exc:
                            self.alert("STOP_MISSING_CHECK_FAILED", account=acct, sym_side=o["ss"], error=str(exc)[:200], severity="CRITICAL")
                            log(f"STOP_MISSING_CHECK_FAILED {acct} {o['ss']}: {str(exc)[:160]}")
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
    ap.add_argument("--role", choices=("primary", "backup"), default="primary",
                    help="primary (s1 crypto / s5 stocks) trades unless it SEES the backup active; backup trades only on a witnessed primary failure")
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
    log(f"vector_direct_trader {a.venue} start: accounts={t.accounts} dry_run={a.dry_run} START_POSITION_SIZE=${START_POSITION_SIZE} MAX_OPEN_NOTIONAL=${MAX_OPEN_NOTIONAL} ENTRY_CEILING=${ENTRY_NOTIONAL_CEILING_USD} ONE_SHARE_MAX=${STOCK_ONE_SHARE_MAX} sizing={_size_rep['source']} root={t.root} states={t.state_dir}")
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
