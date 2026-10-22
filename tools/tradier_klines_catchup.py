#!/usr/bin/env python3
"""tradier_klines_catchup — fill TODAY's session for the stock symbols the main pass has not reached yet.

USER 2026-10-16 (NPZ freshness): ~60 stock NPZs sat ~95 bars behind the clock, and the cause was upstream — their
klines ended at 2026-10-15T20:00Z while WMT/AAPL had 2026-10-16T20:00Z. MEASURED why:
  * A DIRECT Tradier timesales call for the lagging symbols (T, USO) returns 27 bars for TODAY in 1.0 s. The
    vendor is healthy and has the data.
  * tradier_klines.py's own log shows `15min fetch failed/timeout:` with an EMPTY message (asyncio.wait_for's
    TimeoutError stringifies to nothing) and 1-3 MINUTES per symbol: during RTH it runs every 60 s with
    sem=64 over ~220 symbols x 6 timeframes, and after the close it runs one 7-day repair pass at sem=16. The
    timeouts are self-inflicted concurrency, not a vendor outage, and the alphabetical crawl simply never
    reached T/STZ/USO/TTD/LDOS/NKE today.
  * Massive.com cannot cover it: its plan embargoes the current day (HTTP 403 for today, 60 bars for yesterday),
    so it backfills history only.

This does NOT modify tradier_klines.py: it imports that module and calls its own process_symbol() at low
concurrency for the symbols that are actually behind, so the bars are written by the proven path (same
Time&Sales source, same export_atomic, same never-shrink guard).

usage:
  python3 tools/tradier_klines_catchup.py                 # report which symbols are behind
  python3 tools/tradier_klines_catchup.py --apply [--max 80] [--conc 4]
"""
import argparse
import asyncio
import datetime as dt
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def log(msg):
    print(f"[tradier_catchup {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def last_bar(p):
    """last bar timestamp of a kline json by tail-read (never a full parse)."""
    try:
        sz = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, sz - 4096))
            t = f.read().decode("utf-8", "ignore")
    except OSError:
        return None
    m = re.findall(r'"timestamp":\s*"([0-9T:\-\.]+)Z?"', t)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m[-1][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def last_final_bar(p):
    """last NON-PROVISIONAL bar timestamp. tradier_price_tracker writes tick-built bars with
    `"provisional": true` for the forming bar, and klines_append._final_rows (correctly) drops those — a
    provisional bar appended into S1's long store would freeze a half-formed bar into backtest truth. So a
    file full of provisional rows has NO usable coverage for today, and last_bar() counting them was the
    second half of why this tool did nothing. Tail-read, newest final row wins."""
    try:
        sz = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, sz - 262144))
            blobs = re.findall(r'\{[^{}]*\}', f.read().decode("utf-8", "ignore"))
    except OSError:
        return None
    newest = None
    for blob in blobs:
        if '"provisional"' in blob and re.search(r'"provisional":\s*true', blob):
            continue
        m = re.search(r'"timestamp":\s*"([0-9T:\-\.]+)Z?"', blob)
        if not m:
            continue
        try:
            ts = dt.datetime.strptime(m.group(1)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if newest is None or ts > newest:
            newest = ts
    return newest


def last_rth_close(now=None):
    """The newest 15m RTH bar that has CLOSED — today's while the session is open, else the previous close.

    USER 2026-10-20 ("npz's still stale in most cases"). This returned "the most recent weekday 20:00Z that
    has passed", so during RTH — the only time catch-up matters — `now < today 20:00Z` sent it back to
    YESTERDAY's close. Measured live, every 20 minutes, with 12 allowed symbols holding nothing but
    provisional bars for today:
        [tradier_catchup 15:00:18Z] 0 of 223 stock symbols are missing bars up to 2026-10-19T20:00:00+00:00
    The tool was a guaranteed no-op for the whole session it exists to repair. tools/npz_cutoffs is the single
    cutoff authority the dispatch gate and tail_currency already use, so defer to it and keep the old
    end-of-session rule only as the fallback when it cannot be imported."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        sys.path.insert(0, str(ROOT))
        from tools.npz_cutoffs import last_rth_close_bar

        bar = last_rth_close_bar(now)  # takes a tz-aware datetime, NOT an epoch: passing now.timestamp()
        if bar:                        # raised AttributeError('float' has no 'tzinfo') straight into the
            return dt.datetime.fromtimestamp(float(bar), dt.timezone.utc)  # except below, silently keeping
    except Exception as exc:           # yesterday's close — the very bug this function was being fixed for.
        print(f"[tradier_catchup] npz_cutoffs unavailable ({type(exc).__name__}: {exc}) — falling back to the previous session close", flush=True)
    d = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now < d:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def allowed_stock_symbols():
    """USER 2026-10-20: "stocks can only have npz's for the symbols_trb_long/short of that moment plus
    [TRADIER_MANDATORY_LONG_TRB / TRADIER_MANDATORY_SHORT_TRB]". Same law as
    v15_fleet_scheduler._reachable_sym_sides, so this tool never repairs a symbol the fleet may not compute:
    114 symbols instead of 223. None = unavailable, and then nothing is narrowed (fail-open)."""
    syms = set()
    try:
        sys.path.insert(0, str(ROOT))
        for side in ("long", "short"):
            with open(ROOT / f"symbols_trb_{side}.json") as fh:
                v = json.load(fh)
            syms |= {str(x).strip().upper() for x in (v if isinstance(v, list) else v.get("symbols") or [])}
        from config_tradier import TradierConfig

        cfg = TradierConfig()
        for attr in ("TRADIER_MANDATORY_LONG_TRB", "TRADIER_MANDATORY_SHORT_TRB"):
            syms |= {str(x).strip().upper() for x in (getattr(cfg, attr, None) or [])}
    except Exception as exc:
        print(f"[tradier_catchup] allowed universe unavailable ({type(exc).__name__}: {exc}) — not narrowing", flush=True)
        return None
    return syms or None


def behind(cache_dir, symbols, cutoff, min_bars=None):
    """Symbols whose newest FINAL 15m bar is more than min_bars behind `cutoff`.

    min_bars exists because every symbol is 1 bar behind for the minute after a bar closes — reporting 211 of
    223 "missing" and then re-fetching 110 of them every 20 minutes is how this tool would hammer Tradier for
    nothing. Default 2 bars (30 min): below that the main tradier_klines pass gets there on its own."""
    tol = dt.timedelta(minutes=15 * (int(os.environ.get("TRADIER_CATCHUP_MIN_BARS", "2")) if min_bars is None else min_bars))
    out = []
    for s in symbols:
        p = cache_dir / f"{s}_15m.json"
        lb = last_final_bar(p) if p.exists() else None
        if lb is None or lb < cutoff - tol:
            out.append((s, lb))
    return out


async def run(symbols, conc):
    import tradier_klines as tk
    await tk.api_client.connect()
    sem = asyncio.Semaphore(conc)
    done = {}

    async def one(s):
        async with sem:
            try:
                done[s] = bool(await tk.process_symbol(s, repair=False))
            except Exception as e:
                done[s] = f"{type(e).__name__}: {e}"[:120]
    try:
        await asyncio.gather(*(one(s) for s in symbols))
    finally:
        await tk.api_client.close()
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--conc", type=int, default=4, help="low on purpose: sem 64 is what produced the 20 s timeouts")
    ap.add_argument("--symbols")
    a = ap.parse_args()
    # 2026-10-22 SINGLE FLIGHT (USER live-lane): the 20-min cron outlives itself under load
    # (measured 6 copies + 24 workers). flock is self-cleaning — a killed run releases on death,
    # so no stale-lock takeover logic is needed. No crontab change needed.
    _lock_fh = open("/tmp/.tradier_klines_catchup.lock", "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another catchup run holds the lock — exiting (single flight)")
        return 0
    import tradier_klines as tk
    cache = Path(tk.config.KLINES_CACHE_DIR)  # already .../klines_cache/tradier
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        syms = [str(s).upper() for s in json.loads(Path(tk.config.SYMBOLS_FILE).read_text())]
        allowed = allowed_stock_symbols()
        if allowed:
            dropped = [s for s in syms if s not in allowed]
            syms = [s for s in syms if s in allowed]
            log(f"allowed universe {len(allowed)} (trb_long/short + TRADIER_MANDATORY_*_TRB): {len(syms)} kept, {len(dropped)} outside the law and never repaired")
    cutoff = last_rth_close()
    late = behind(cache, syms, cutoff)
    log(f"{len(late)} of {len(syms)} stock symbols are missing bars up to {cutoff.isoformat()} (cache {cache})")
    log("worst: " + ", ".join(f"{s}@{(lb.isoformat() if lb else 'NONE')}" for s, lb in late[:8]))
    if not a.apply or not late:
        return 1 if late else 0
    todo = [s for s, _ in late[: max(1, a.max)]]
    t0 = time.time()
    res = asyncio.run(run(todo, a.conc))
    ok = [s for s, v in res.items() if v is True]
    bad = {s: v for s, v in res.items() if v is not True}
    still = behind(cache, todo, cutoff)
    log(f"ran {len(todo)} symbols at conc={a.conc} in {time.time()-t0:.0f}s — all-6-timeframes ok {len(ok)}, partial/err {len(bad)}, still behind {len(still)}")
    if bad:
        log("errors: " + json.dumps({k: str(v)[:60] for k, v in list(bad.items())[:6]}))
    if still:
        log("still behind: " + ", ".join(s for s, _ in still[:10]))
    return 1 if still else 0


if __name__ == "__main__":
    sys.exit(main())
