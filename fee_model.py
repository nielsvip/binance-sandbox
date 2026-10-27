"""Measured commission model — the ONE source of truth for round-trip fees.

Every surface that reports, simulates, or gates on a gain must charge the SAME
commission, and that commission must be MEASURED from the broker's own fills,
never guessed. Before 2026-10-27 the three surfaces disagreed:

    config.Config.CRYPTO_ROUND_TRIP_COMMISSION_PCT   0.0    (live: fee-free)
    v12_quick_engine.QuickConfig.<same>              0.04   (boards)
    reality (broker fills, 90d)                      0.0575

With live believing crypto was free, the engines reopened for moves that could
not pay their own fee; with the boards charging 0.04 they published gains that
reality taxed at 0.0575. Measured 2026-10-27 over 90 days of
data/exchange_fills/<acct>/*.json: 17,965 fills, $351,492 notional, $101.03
commission -> 0.0287%/leg = 0.0575% round trip (maker 0.0270, taker 0.1145).

`round_trip_pct()` returns the CONSERVATIVE figure: the worst of the long and
short measurement windows, so a maker-heavy week can never make a backtest look
cheaper than recent reality. Commission only — execution slippage is modelled
separately and was measured unbiased (mean -0.036% vs the signal-bar close over
1,911 matched entries), so it carries no mean cost to add here.
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path

CACHE_NAME = "fee_model.json"
CACHE_MAX_AGE_S = 6 * 3600
LONG_WINDOW_DAYS = 90
SHORT_WINDOW_DAYS = 30

# Measured 2026-10-27 from the broker's own fills. These are the last-resort
# values used only when data/fee_model.json is absent AND the fills cannot be
# read (import must never fail and must never return 0.0 for crypto).
FALLBACK_CRYPTO_ROUND_TRIP_PCT = 0.0688  # 30d measurement, the worse of the two
FALLBACK_TRADIER_ROUND_TRIP_PCT = 0.0  # Tradier equities are commission-free

# A round trip can never be cheaper than two maker legs at the venue's best
# published tier. Guards against a window that caught only rebated fills.
HARD_FLOOR_CRYPTO_ROUND_TRIP_PCT = 0.02

# The pin is PUBLISHED rounded, and only re-published when it moves by at least
# this much. `engine_versions()` hashes fee_model_measured.py, so every rewrite
# orphans the entire eval cache -- a raw measurement that jitters in the 5th
# decimal must not cost the fleet its compute. 0.001pp is ~2% of the current
# 0.053 rate: far below anything that changes a board, far above the jitter.
PIN_DECIMALS = 3
PIN_MIN_MOVE_PCT = 0.001

_BNB_USD_FALLBACK = 1100.0


def _base_path() -> Path:
    env = os.environ.get("BINANCE_BASE_PATH")
    if env:
        return Path(env)
    try:
        import config as _c

        p = getattr(_c, "BASE_PATH", None) or getattr(
            getattr(_c, "Config", None), "BASE_PATH", None
        )
        if p:
            return Path(p)
    except Exception:
        pass
    return Path(__file__).resolve().parent


def _cache_path() -> Path:
    return _base_path() / "data" / CACHE_NAME


def _bnb_usd() -> float:
    try:
        pc = _base_path() / "price_cache_2.json"
        d = json.loads(pc.read_text())
        for k in ("BNBUSDT", "BNBUSDC"):
            v = d.get(k)
            if isinstance(v, dict):
                v = v.get("price") or v.get("last") or v.get("mark")
            if v and float(v) > 0:
                return float(v)
    except Exception:
        pass
    return _BNB_USD_FALLBACK


def measure(window_days: int = LONG_WINDOW_DAYS, base_path: Path = None) -> dict:
    """Measure commission from broker fills. Returns {} when there is no data."""
    root = Path(base_path) if base_path else _base_path()
    cutoff_ms = (time.time() - window_days * 86400.0) * 1000.0
    bnb = _bnb_usd()
    notional = fees = 0.0
    mk_notional = mk_fees = tk_notional = tk_fees = 0.0
    fills = 0
    for path in glob.glob(str(root / "data" / "exchange_fills" / "*" / "*.json")):
        try:
            rows = json.loads(Path(path).read_text()).get("rows") or []
        except Exception:
            continue
        for r in rows:
            try:
                if float(r.get("time") or 0) < cutoff_ms:
                    continue
                q = abs(float(r.get("quoteQty") or 0))
                if q <= 0:
                    continue
                c = abs(float(r.get("commission") or 0))
                if str(r.get("commissionAsset") or "") == "BNB":
                    c *= bnb
            except (TypeError, ValueError):
                continue
            notional += q
            fees += c
            fills += 1
            if r.get("maker"):
                mk_notional += q
                mk_fees += c
            else:
                tk_notional += q
                tk_fees += c
    if notional <= 0 or fills <= 0:
        return {}
    return {
        "window_days": window_days,
        "fills": fills,
        "notional_usd": round(notional, 2),
        "commission_usd": round(fees, 4),
        "per_leg_pct": 100.0 * fees / notional,
        "round_trip_pct": 200.0 * fees / notional,
        "maker_round_trip_pct": (200.0 * mk_fees / mk_notional) if mk_notional else None,
        "taker_round_trip_pct": (200.0 * tk_fees / tk_notional) if tk_notional else None,
        "taker_notional_share": (tk_notional / notional) if notional else None,
    }


def _pinned() -> tuple:
    """The pushed constant: (rate, source) or (None, None) when absent."""
    try:
        import fee_model_measured as _m

        v = float(getattr(_m, "CRYPTO_ROUND_TRIP_PCT", 0.0) or 0.0)
        if v > 0.0:
            return v, f"pinned:{getattr(_m, 'MEASURED_AT', '?')}"
    except Exception:
        pass
    return None, None


def _write_pin(root: Path, crypto: float, source: str, long_m: dict, short_m: dict) -> None:
    """Persist the measured rate into the PUSHED module (see its docstring)."""
    body = (
        '"""Generated by fee_model.rebuild(). DO NOT EDIT BY HAND.\n\n'
        "This file exists so every host charges the SAME commission. `fee_model`\n"
        "measures the rate from `data/exchange_fills/**`, but the livecode mirror\n"
        "excludes `data/*`, so the workers have no fills and no `data/fee_model.json`.\n"
        "Without a pushed constant the Mac would price boards at its measured rate while\n"
        "a worker fell back to a literal — the exact board/live fee split this whole\n"
        "fixpack removed. The measuring host writes the number here; the mirror pushes\n"
        "this file; `tools/v15_eval_cache.engine_versions()` hashes it, so a re-measure\n"
        "orphans every cached eval instead of silently mixing two fees.\n"
        '"""\n\n'
        f'MEASURED_AT = "{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}"\n'
        'MEASURED_BY = "fee_model.rebuild"\n'
        f'SOURCE = "{source}"\n'
        f"CRYPTO_ROUND_TRIP_PCT = {round(float(crypto), PIN_DECIMALS)}\n"
        f"TRADIER_ROUND_TRIP_PCT = {FALLBACK_TRADIER_ROUND_TRIP_PCT}\n"
        f'WINDOWS = {{"long_days": {LONG_WINDOW_DAYS}, "short_days": {SHORT_WINDOW_DAYS}}}\n'
    )
    try:
        p = root / "fee_model_measured.py"
        old = p.read_text() if p.exists() else ""
        # Only rewrite when the PUBLISHED rate moves. The raw measurement drifts
        # by a few 1e-5 every window (0.05291 -> 0.05287 within one cron tick),
        # and because engine_versions() hashes this file, a rewrite orphans the
        # whole eval corpus. Publishing at PIN_DECIMALS and requiring a move of
        # at least PIN_MIN_MOVE_PCT keeps the number honest without burning
        # every cached board twice an hour.
        import re as _re

        m = _re.search(r"CRYPTO_ROUND_TRIP_PCT\s*=\s*([0-9.]+)", old)
        if m and abs(float(m.group(1)) - float(crypto)) < PIN_MIN_MOVE_PCT:
            return
        tmp = p.with_suffix(".py.tmp")
        tmp.write_text(body)
        os.replace(tmp, p)
    except Exception:
        pass


def rebuild(base_path: Path = None) -> dict:
    """Measure both windows, pick the conservative rate, write the cache."""
    root = Path(base_path) if base_path else _base_path()
    long_m = measure(LONG_WINDOW_DAYS, root)
    short_m = measure(SHORT_WINDOW_DAYS, root)
    rates = [m["round_trip_pct"] for m in (long_m, short_m) if m]
    if rates:
        crypto = max(max(rates), HARD_FLOOR_CRYPTO_ROUND_TRIP_PCT)
        source = "measured"
        _write_pin(root, crypto, source, long_m, short_m)
    else:
        pin, psrc = _pinned()
        if pin is not None:
            crypto, source = pin, psrc
        else:
            crypto = FALLBACK_CRYPTO_ROUND_TRIP_PCT
            source = "fallback_no_fills"
    doc = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_at_ts": time.time(),
        "by": "fee_model.rebuild",
        "source": source,
        "crypto_round_trip_pct": round(float(crypto), 5),
        "tradier_round_trip_pct": FALLBACK_TRADIER_ROUND_TRIP_PCT,
        "windows": {"long": long_m, "short": short_m},
    }
    try:
        out = root / "data" / CACHE_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
        os.replace(tmp, out)
    except Exception:
        pass
    return doc


def load(max_age_s: float = CACHE_MAX_AGE_S) -> dict:
    """Read the cache, rebuilding when stale or absent. Never raises."""
    try:
        p = _cache_path()
        if p.exists():
            doc = json.loads(p.read_text())
            age = time.time() - float(doc.get("built_at_ts") or 0)
            if age <= max_age_s and float(doc.get("crypto_round_trip_pct") or 0) > 0:
                return doc
    except Exception:
        pass
    try:
        return rebuild()
    except Exception:
        pass
    # A host with no fills (every worker: the mirror excludes data/*) lands here
    # and MUST agree with the measuring host, so the pushed pin wins over the
    # literal fallback.
    pin, psrc = _pinned()
    return {
        "source": psrc or "fallback_exception",
        "crypto_round_trip_pct": pin if pin is not None else FALLBACK_CRYPTO_ROUND_TRIP_PCT,
        "tradier_round_trip_pct": FALLBACK_TRADIER_ROUND_TRIP_PCT,
    }


def round_trip_pct(mode: str = "crypto") -> float:
    """The round-trip commission every host charges, in percent of notional.

    Charge this on a full open+close. Each leg pays half (see the engines'
    `half_fee = round_trip_pct / 200`).

    THE PIN IS THE AUTHORITY, not the local measurement. Only the Mac has
    `data/exchange_fills/**` (the livecode mirror excludes `data/*`), so reading
    the local measurement here would hand the Mac 0.05285 and every worker the
    pinned 0.053 -- re-opening in a new place exactly the board-vs-live fee
    split this module exists to close. `rebuild()` measures and re-publishes the
    pin; everyone, measuring host included, then charges that one number.
    `load()` still carries the full measurement for reporting and audit.
    """
    if str(mode or "crypto").lower() in ("tradier", "stocks", "stock"):
        return float(FALLBACK_TRADIER_ROUND_TRIP_PCT)
    v, _src = _pinned()
    if v is None:
        try:
            v = float(load().get("crypto_round_trip_pct") or 0.0)
        except (TypeError, ValueError):
            v = 0.0
    if not v or v <= 0.0:
        v = FALLBACK_CRYPTO_ROUND_TRIP_PCT
    return max(float(v), HARD_FLOOR_CRYPTO_ROUND_TRIP_PCT)


def half_leg_fraction(mode: str = "crypto") -> float:
    """Per-leg cost as a FRACTION of notional (round_trip_pct / 200)."""
    return round_trip_pct(mode) / 200.0


if __name__ == "__main__":
    doc = rebuild()
    print(json.dumps(doc, indent=1, default=str))
    print("crypto round trip pct =", round_trip_pct("crypto"))
    print("tradier round trip pct =", round_trip_pct("tradier"))
