"""Kline freshness watchdog (USER 2026-10-12: never 1 minute behind, never silent again).

Tradier klines went 8+ days stale (all Timesales fetches timing out) with zero
alerts — mtimes kept refreshing while bars froze. This tool checks probe bars
against the clock every 5 min via cron and exits 1 + writes BREACH lines the
moment a venue falls behind. Crypto is 24/7 (35 min tolerance); stocks are
RTH-only in America/New_York (35 min in session; off-hours the last session
close must be present).
"""
from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CRYPTO_PROBES = ("BTCUSDC", "ETHUSDC", "BNBUSDC", "XRPUSDC", "SOLUSDC", "ENAUSDC")
STOCK_PROBES = ("SPY", "AAPL", "MSFT", "TSLA", "GDX", "JNJ")
TOL_MIN = 35
OUT = ROOT / "data" / "reports" / "klines_freshness.json"
ET = ZoneInfo("America/New_York")


def in_rth(now: dt.datetime) -> bool:
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return False
    mins = et.hour * 60 + et.minute
    return 570 <= mins < 960


def last_session_close(now: dt.datetime) -> dt.datetime:
    et = now.astimezone(ET)
    d = et.date()
    if et.weekday() >= 5 or (et.weekday() == 0 and (et.hour * 60 + et.minute) < 570):
        days = (et.weekday() - 4) % 7 or 7
        if et.weekday() >= 5:
            days = et.weekday() - 4
        d -= dt.timedelta(days=days)
    elif (et.hour * 60 + et.minute) < 570:
        d -= dt.timedelta(days=1 if et.weekday() > 0 else 3)
    return dt.datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)


def last_bar(cachedir: Path, sym: str) -> float | None:
    try:
        d = json.loads((cachedir / f"{sym}_15m.json").read_text())
    except Exception:
        return None
    rows = d if isinstance(d, list) else d.get("klines", d.get("data", []))
    if not rows:
        return None
    try:
        return dt.datetime.fromisoformat(str(rows[-1].get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def check_venue(cachedir: Path, probes: tuple, now_ts: float, stocks: bool, now: dt.datetime) -> dict:
    lags, missing = {}, []
    for s in probes:
        t = last_bar(cachedir, s)
        if t is None:
            missing.append(s)
        else:
            lags[s] = (now_ts - t) / 60.0
    if stocks and not in_rth(now):
        want = last_session_close(now).timestamp()
        stale = sorted(s for s, lag in lags.items() if now_ts - lag * 60.0 < want - 900)
        status = "BREACH" if (missing or stale) else "OK_CLOSED"
        return {"status": status, "mode": "off-hours", "missing": missing, "stale_vs_close": stale,
                "worst_lag_min": round(max(lags.values()), 1) if lags else None}
    worst = max(lags.values()) if lags else float("inf")
    status = "BREACH" if (missing or worst > TOL_MIN) else "OK"
    return {"status": status, "mode": "live", "missing": missing,
            "worst_lag_min": round(worst, 1) if lags else None,
            "worst_sym": max(lags, key=lags.get) if lags else None}


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    now_ts = now.timestamp()
    rep = {"generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "tol_min": TOL_MIN,
           "crypto": check_venue(ROOT / "klines_cache", CRYPTO_PROBES, now_ts, False, now),
           "stocks": check_venue(ROOT / "klines_cache" / "tradier", STOCK_PROBES, now_ts, True, now)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=1))
    ec = 0
    for venue in ("crypto", "stocks"):
        r = rep[venue]
        line = f"[KLINES_FRESH] {venue} {r['status']} mode={r['mode']} worst_lag={r.get('worst_lag_min')}m missing={r['missing']}"
        print(line, flush=True)
        if r["status"] == "BREACH":
            ec = 1
    return ec


if __name__ == "__main__":
    raise SystemExit(main())
