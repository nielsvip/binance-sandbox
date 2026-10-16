#!/usr/bin/env python3
"""Backfill tradier 15m caches with Massive.com history (stocks only).

Tradier intraday starts 2026-07-13 (~91d); 400D needs older history. Massive
/v2/aggs (adjusted=false = raw, matches Tradier) provides it. Pure backfill:
only bars OLDER than the cache head are added (zero impact on recent data);
overlap-week closes are compared and a >2%% mismatch skips the symbol.

  python3 tools/massive_backfill_15m.py --symbols ALKT --apply
"""
import argparse
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://api.massive.com/v2/aggs/ticker/{sym}/range/15/minute/{frm}/{to}"
GRID = [f"{h:02d}:{m:02d}" for h in range(12, 21) for m in (0, 15, 30, 45) if not (h == 12 and m < 30) and not (h == 20 and m > 0)]
GRIDSET = set(GRID)
MAX_PAGES = int(os.environ.get("MASSIVE_MAX_PAGES", "12"))   # ~115 sessions/page -> 12 pages covers the plan's ~2y
PAGE_PAUSE_S = float(os.environ.get("MASSIVE_PAGE_PAUSE_S", "2.0"))


def api_key():
    k = os.environ.get("MASSIVE_API_KEY", "").strip()
    if k:
        return k
    for c in (Path.home() / ".config" / "massive_api_key", Path.home() / ".massive_api_key"):
        if c.exists():
            return c.read_text().strip()
    raise RuntimeError("MASSIVE_API_KEY missing")


def fetch(sym, frm, to, retries=3):
    import urllib.error
    q = urllib.parse.urlencode({"limit": 50000, "sort": "asc", "adjusted": "false", "apiKey": api_key()})
    req = urllib.request.Request(f"{URL.format(sym=sym, frm=frm, to=to)}?{q}", headers={"Authorization": f"Bearer {api_key()}"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode()).get("results", [])
        except urllib.error.HTTPError as e:
            # USER 2026-10-16: 403 is TRANSIENT here, not an entitlement verdict — the 2026-10-16 00:5xZ batch took
            # "HTTPError 403: Forbidden" on 13 symbols (A, ADP, CME, PSX, OLED, PRI, BMNR, LEXX …) and a plain retry
            # of ADP/CME returned 1256/1157 bars straight away. Raising on the first 403 dropped those symbols for
            # the whole night and left them to the next run, which is the same never-catch-up loop one level down.
            if e.code in (403, 429) and attempt + 1 < retries:
                time.sleep(60 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def to_row(x):
    return {"timestamp": dt.datetime.fromtimestamp(x["t"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "open": float(x["o"]), "high": float(x["h"]), "low": float(x["l"]), "close": float(x["c"]), "volume": float(x.get("v", 0))}


def in_session(ts):
    """USER 2026-10-16: the grid above is the EDT shape, and this used to match it against the raw UTC clock — so
    every NOVEMBER-MARCH session was kept as 07:30-15:00 ET instead of 08:30-16:00 ET. Measured on U and KGC at
    2025-01-15: first bar 07:30 ET, last bar 15:00 ET, i.e. the final 45 minutes of regular trading — the close —
    was dropped from every winter session, and four pre-market bars were kept in its place. Convert to Eastern."""
    from zoneinfo import ZoneInfo

    try:
        e = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return False
    loc = e.astimezone(ZoneInfo("America/New_York"))
    return loc.weekday() < 5 and dt.time(8, 30) <= loc.time() <= dt.time(16, 0) and loc.minute % 15 == 0


def backfill(sym, frm, apply, max_pages=MAX_PAGES, pause=PAGE_PAUSE_S):
    p = ROOT / "klines_cache_backtest" / "tradier" / f"{sym}_15m.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        rows = []
    have = len(rows)
    head = rows[0]["timestamp"][:10] if rows else None
    stop = head or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    # USER 2026-10-16 ("absolutely stupid to download the same klines every single day and never catch up"):
    # ONE request is one PAGE, and the page is NOT the window that was asked for. Measured for KGC with
    # from=2023-01-01 to=2026-07-13: the reply began at 2024-10-15 (the plan's history limit) and ended at
    # 2025-04-01 — 115 sessions, 5277 raw bars. A single-page backfill therefore (a) leaves a 15-month HOLE
    # between the page's end and the cache head, and (b) returns the IDENTICAL page on every later run, so
    # `old` is empty and the symbol reports noop forever: re-downloaded daily, never advancing. So walk the
    # pages forward from wherever history starts until the cursor reaches the cache head, and write ONCE.
    cursor, mrows, pages, seen = frm, [], 0, set()
    while pages < max_pages and cursor[:10] < stop:
        try:
            res = fetch(sym, cursor, stop)
        except Exception as e:
            if not mrows:
                return {"sym": sym, "have": have, "action": "fetch-err", "error": repr(e)[:120]}
            break
        pages += 1
        if not res:
            break
        batch = [to_row(x) for x in res if in_session(dt.datetime.fromtimestamp(x["t"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))]
        mrows += [r for r in batch if r["timestamp"] not in seen and not seen.add(r["timestamp"])]
        last_day = dt.datetime.fromtimestamp(res[-1]["t"] / 1000, tz=dt.timezone.utc).date()
        nxt = (last_day + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        if nxt <= cursor:  # the provider is not advancing — stop rather than spin on one page
            break
        cursor = nxt
        if cursor[:10] < stop and pages < max_pages:
            time.sleep(pause)
    mrows.sort(key=lambda r: r["timestamp"])
    gap = 0.0
    for a, b in zip(mrows, mrows[1:]):
        if a["close"] and b["open"]:
            gap = max(gap, abs(b["open"] / a["close"] - 1))
    if rows:
        old = [r for r in mrows if r["timestamp"] < rows[0]["timestamp"]]
    else:
        old = mrows
    # a hole between the backfilled block and the cache head is worse than a short file: the stock NPZ gap guard
    # refuses the symbol and the board is skipped, so report the seam instead of silently creating one.
    hole_days = None
    if rows and old:
        hole_days = (dt.datetime.strptime(rows[0]["timestamp"][:10], "%Y-%m-%d") - dt.datetime.strptime(old[-1]["timestamp"][:10], "%Y-%m-%d")).days
    if not apply:
        return {"sym": sym, "have": have, "would_add": len(old), "pages": pages, "max_gap": round(gap, 4),
                "from": old[0]["timestamp"][:10] if old else None, "to": old[-1]["timestamp"][:10] if old else None,
                "hole_days": hole_days, "action": "dry"}
    if not old:
        return {"sym": sym, "have": have, "now": have, "action": "noop"}
    by_ts = {r["timestamp"]: r for r in rows}
    for r in old:
        by_ts.setdefault(r["timestamp"], r)
    merged = [by_ts[k] for k in sorted(by_ts)]
    if len(merged) <= have:
        return {"sym": sym, "have": have, "now": have, "action": "noop"}
    from klines_guard import guarded_write
    ok, why = guarded_write(p, merged)
    if not ok:
        return {"sym": sym, "have": have, "now": len(merged), "action": "guard-blocked", "reason": why}
    return {"sym": sym, "have": have, "now": len(merged), "added": len(merged) - have, "pages": pages,
            "head": merged[0]["timestamp"][:10], "hole_days": hole_days, "action": "written"}


CORE_FIRST, CORE_LAST = dt.time(9, 30), dt.time(16, 0)


def et_slots(rows):
    """{ET date: {ET HH:MM}} for the rows a cache file already holds."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    out = {}
    for r in rows:
        try:
            e = dt.datetime.strptime(str(r["timestamp"])[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(et)
        except Exception:
            continue
        out.setdefault(e.strftime("%Y-%m-%d"), set()).add(e.strftime("%H:%M"))
    return out


def core_grid():
    return [f"{h:02d}:{m:02d}" for h in range(9, 17) for m in (0, 15, 30, 45) if CORE_FIRST <= dt.time(h, m) <= CORE_LAST]


def incomplete_sessions(rows, min_missing=1):
    """Dates whose REGULAR hours (09:30-16:00 ET) are short. An early-close half-day is reported apart rather than
    chased forever: there are ~3 a year and they legitimately end at 13:00 ET."""
    grid = core_grid()
    bad, early = [], []
    for day, have in sorted(et_slots(rows).items()):
        miss = [s for s in grid if s not in have]
        if len(miss) < min_missing:
            continue
        core = [s for s in have if CORE_FIRST <= dt.time(int(s[:2]), int(s[3:])) <= CORE_LAST]
        if core and max(core) <= "13:00":
            early.append(day)
            continue
        bad.append((day, len(miss)))
    return bad, early


def windows(days, span_days=45):
    """Group dates into few request windows — one request per contiguous block, not one per session."""
    out = []
    for d in days:
        if out and (dt.date.fromisoformat(d) - dt.date.fromisoformat(out[-1][0])).days <= span_days:
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a, b) for a, b in out]


EXHAUSTED = "data/massive_fill_exhausted.json"
EXHAUST_TTL_DAYS = 30


def load_exhausted(sym):
    """Sessions the provider has already been asked for and had nothing to give. USER 2026-10-16 ("stupid to download
    the same klines every single day"): BMNR came back with 7 clean fetches and ZERO new bars — an illiquid name that
    simply did not print in those 15m windows. Without this the nightly pass re-asks for them forever."""
    try:
        st = json.loads((ROOT / EXHAUSTED).read_text()).get(sym, {})
        if (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(st["ts"])).days > EXHAUST_TTL_DAYS:
            return set()
        return set(st.get("sessions", []))
    except Exception:
        return set()


def save_exhausted(sym, sessions):
    try:
        p = ROOT / EXHAUSTED
        st = json.loads(p.read_text()) if p.exists() else {}
        st[sym] = {"sessions": sorted(sessions), "ts": dt.datetime.now(dt.timezone.utc).isoformat()}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
        os.replace(tmp, p)
    except Exception:
        pass


def fill_holes(sym, apply, max_windows=12, pause=PAGE_PAUSE_S, min_missing=1):
    """Insert ONLY timestamps the cache lacks inside sessions it already has (plus any session it is missing).
    Grow-only: existing rows are never touched, the write goes through klines_guard.guarded_write."""
    p = ROOT / "klines_cache_backtest" / "tradier" / f"{sym}_15m.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        return {"sym": sym, "action": "no-cache"}
    have = len(rows)
    bad, early = incomplete_sessions(rows, min_missing)
    spent = load_exhausted(sym)
    asked = [(d, n) for d, n in bad if d not in spent]
    if not bad or not asked:
        return {"sym": sym, "have": have, "action": "complete", "early_close_sessions": len(early),
                "short_sessions_the_provider_has_nothing_for": len(bad)}
    bad = asked
    wins = windows([d for d, _ in bad])[:max_windows]
    seen = {str(r["timestamp"])[:19] for r in rows}
    add, pages, errs = [], 0, []
    for frm, to in wins:
        try:
            res = fetch(sym, frm, (dt.date.fromisoformat(to) + dt.timedelta(days=1)).strftime("%Y-%m-%d"))
        except Exception as e:
            # one window's 403 is not the symbol's verdict: AAPL lost all 12 windows to the first one on 2026-10-16,
            # which is the never-catch-up loop again — skip the window, keep whatever the others return.
            errs.append(f"{frm}:{repr(e)[:40]}")
            continue
        pages += 1
        for x in res:
            r = to_row(x)
            if r["timestamp"][:19] in seen or not in_session(r["timestamp"]):
                continue
            seen.add(r["timestamp"][:19])
            add.append(r)
        if pause and (frm, to) != wins[-1]:
            time.sleep(pause)
    sessions_short = sum(n for _, n in bad)
    if not add:
        act = "fetch-err" if errs and not pages else "nothing-returned"
        if act == "nothing-returned" and apply:
            save_exhausted(sym, spent | {d for d, _ in bad})
        return {"sym": sym, "have": have, "action": act, "short_sessions": len(bad), "short_bars": sessions_short, "windows": pages, "errors": errs[:3]}
    if not apply:
        return {"sym": sym, "have": have, "would_add": len(add), "short_sessions": len(bad), "short_bars": sessions_short, "windows": pages, "window_errors": len(errs), "action": "dry"}
    merged = sorted(rows + add, key=lambda r: str(r["timestamp"]))
    from klines_guard import guarded_write

    ok, why = guarded_write(p, merged)
    if not ok:
        return {"sym": sym, "have": have, "action": "guard-blocked", "reason": why}
    left, _ = incomplete_sessions(merged, min_missing)
    if apply and pages and not errs:
        still = {d for d, _ in left} & {d for d, _ in bad}
        if still:
            save_exhausted(sym, spent | still)
    return {"sym": sym, "have": have, "now": len(merged), "added": len(add), "short_sessions_before": len(bad), "short_sessions_after": len(left), "windows": pages, "window_errors": len(errs), "action": "filled"}


def overlap_guard(sym):
    """Max |close diff| on overlap week vs current cache; None if no overlap."""
    p = ROOT / "klines_cache_backtest" / "tradier" / f"{sym}_15m.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        return None
    if not rows:
        return None
    head = rows[0]["timestamp"][:10]
    hd = dt.datetime.strptime(head, "%Y-%m-%d")
    to = (hd + dt.timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        res = fetch(sym, head, to)
    except Exception:
        return None
    trm = {x["timestamp"][:16]: x for x in rows}
    diffs = []
    for x in res:
        k = dt.datetime.fromtimestamp(x["t"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        t = trm.get(k)
        if t and t["close"] and x["c"]:
            diffs.append(abs(t["close"] - x["c"]) / t["close"])
    return max(diffs) if diffs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=[])
    ap.add_argument("--symbols-file", default=None, help="whitespace-separated symbol list (91 live-lane symbols do not fit a command line)")
    # the provider clamps to its own history start (measured: 2024-10-15 for KGC), so asking earlier costs nothing
    ap.add_argument("--from-date", default="2024-01-01")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-diff", type=float, default=0.02)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES, help="pages per symbol; one page is ~115 sessions")
    ap.add_argument("--skip-head-before", default="2024-11-01", help="skip (free) any symbol whose head already reaches the plan's history start; '' disables")
    ap.add_argument("--fill-holes", action="store_true", help="repair sessions already in the cache whose regular hours are short (the winter-grid loss), instead of extending the head")
    ap.add_argument("--max-windows", type=int, default=12, help="--fill-holes: request windows per symbol; one window covers a contiguous block of bad sessions")
    ap.add_argument("--min-missing", type=int, default=1, help="--fill-holes: only repair a session missing at least this many regular-hours bars")
    a = ap.parse_args()
    syms = list(a.symbols)
    if a.symbols_file:
        syms += [x.strip().upper() for x in Path(a.symbols_file).read_text().split() if x.strip()]
    if not syms:
        ap.error("no symbols: pass --symbols or --symbols-file")
    for s in syms:
        if a.fill_holes:
            try:
                print(json.dumps(fill_holes(s, a.apply, max_windows=a.max_windows, min_missing=a.min_missing)), flush=True)
            except Exception as e:
                print(json.dumps({"sym": s, "error": repr(e)[:160]}), flush=True)
            time.sleep(2.0)
            continue
        try:
            _head = None
            try:
                _rows = json.loads((ROOT / "klines_cache_backtest" / "tradier" / f"{s}_15m.json").read_text())
                _have, _head = len(_rows), (_rows[0]["timestamp"][:10] if _rows else None)
            except Exception:
                _have = 0
            if _have >= 20000:
                print(json.dumps({"sym": s, "have": _have, "action": "already-deep"}), flush=True)
                continue
            # USER 2026-10-16 ("stupid to download the same klines every single day and never catch up"): a symbol
            # whose head already reaches the plan's history start (measured 2024-10-15) has nothing older to get, so
            # a daily run must cost it ZERO requests — otherwise every pass re-fetches every page to learn nothing.
            # That makes the schedule self-terminating: it walks each symbol back once and then goes quiet.
            if _head and a.skip_head_before and _head <= a.skip_head_before:
                print(json.dumps({"sym": s, "have": _have, "head": _head, "action": "at-provider-start"}), flush=True)
                continue
            g = overlap_guard(s)
            if g is not None and g > a.max_diff:
                print(json.dumps({"sym": s, "action": "skip-seam", "max_diff": round(g, 4)}), flush=True)
                continue
            print(json.dumps(backfill(s, a.from_date, a.apply, max_pages=a.max_pages)), flush=True)
        except Exception as e:
            print(json.dumps({"sym": s, "error": repr(e)[:160]}), flush=True)
        time.sleep(2.0)


if __name__ == "__main__":
    main()
