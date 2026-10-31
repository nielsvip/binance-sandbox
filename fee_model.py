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
import math
import os
import time
from datetime import datetime
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


def _quote_asset(row: dict) -> str:
    asset = str(row.get("marginAsset") or "").upper()
    if asset in ("USDT", "USDC"):
        return asset
    symbol = str(row.get("symbol") or row.get("pair") or "").upper()
    if symbol.endswith("USDT"):
        return "USDT"
    if symbol.endswith("USDC"):
        return "USDC"
    return ""


def _timestamp_ms(row: dict) -> int:
    stamp = _finite_float(row.get("time") or row.get("timestamp_ms") or 0)
    if stamp is None:
        return 0
    try:
        return int(stamp)
    except (TypeError, ValueError, OverflowError):
        return 0


def _maker_liquidity(row: dict) -> str:
    value = row.get("maker")
    if isinstance(value, bool):
        return "MAKER" if value else "TAKER"
    if isinstance(value, int) and value in (0, 1):
        return "MAKER" if value == 1 else "TAKER"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1"):
            return "MAKER"
        if normalized in ("false", "0"):
            return "TAKER"
    return "UNVERIFIED"


def _finite_float(value):
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _candle_time_ms(row) -> int:
    if isinstance(row, (list, tuple)) and row:
        try:
            return int(row[0]) // 60000 * 60000
        except (TypeError, ValueError):
            return 0
    if not isinstance(row, dict):
        return 0
    try:
        stamp = row.get("timestamp_ms") or row.get("open_time") or row.get("openTime")
        if stamp:
            return int(stamp) // 60000 * 60000
        stamp = row.get("timestamp")
        if stamp:
            return (
                int(
                    datetime.fromisoformat(
                        str(stamp).replace("Z", "+00:00")
                    ).timestamp()
                    * 1000
                )
                // 60000
                * 60000
            )
    except (TypeError, ValueError, OverflowError):
        return 0
    return 0


def _candle_bounds(row):
    try:
        if isinstance(row, (list, tuple)) and len(row) > 4:
            low, high = float(row[3]), float(row[2])
        elif isinstance(row, dict):
            low, high = float(row.get("low")), float(row.get("high"))
        else:
            return None
        if low > 0 and high >= low:
            return low, high
    except (TypeError, ValueError):
        pass
    return None


def _load_bnb_minute_candles(root: Path) -> dict:
    out = {"BNBUSDT": {}, "BNBUSDC": {}}
    for pair in out:
        for folder in ("klines_cache", "klines_cache_gateway"):
            path = root / folder / f"{pair}_1m.json"
            try:
                rows = json.loads(path.read_text())
            except Exception:
                continue
            for row in rows if isinstance(rows, list) else []:
                minute = _candle_time_ms(row)
                bounds = _candle_bounds(row)
                if minute and bounds:
                    out[pair][minute] = bounds
    return out


def _rates_from_stats(stats, complete=True) -> dict:
    out = {
        "fills": stats["fills"],
        "fee_bearing_fills": stats["fee_bearing_fills"],
        "quote_notional": round(stats["notional"], 8),
        "bnb_fee": round(stats["bnb_fee"], 12),
        "quote_fee_low": round(stats["fee_low"], 8),
        "quote_fee_high": round(stats["fee_high"], 8),
        "conversion_complete": bool(complete),
    }
    if complete and stats["notional"] > 0:
        out["fee_pct_per_leg_low"] = 100.0 * stats["fee_low"] / stats["notional"]
        out["fee_pct_per_leg_high"] = 100.0 * stats["fee_high"] / stats["notional"]
        out["round_trip_pct_low"] = 200.0 * stats["fee_low"] / stats["notional"]
        out["round_trip_pct_high"] = 200.0 * stats["fee_high"] / stats["notional"]
    return out


def measure_execution_rates(
    window_days: int = LONG_WINDOW_DAYS,
    base_path: Path = None,
    now_ms: int = None,
    bnb_minute_candles: dict = None,
) -> dict:
    """Measure historical quote/liquidity rates without substituting a live BNB price.

    `bnb_minute_candles` may inject `{pair: {minute_ms: (low, high)}}` for offline audits.
    Missing historical BNB minutes make the affected rate group UNVERIFIED.
    """
    root = Path(base_path) if base_path else _base_path()
    upper_bound_ms = (
        _finite_float(now_ms) if now_ms is not None else time.time() * 1000.0
    )
    if upper_bound_ms is None:
        raise ValueError("now_ms must be a finite timestamp")
    days = _finite_float(window_days)
    if days is None or days <= 0:
        raise ValueError("window_days must be a positive finite number")
    cutoff_ms = upper_bound_ms - days * 86400.0 * 1000.0
    candles = (
        bnb_minute_candles
        if bnb_minute_candles is not None
        else _load_bnb_minute_candles(root)
    )
    groups = {}
    missing = {}
    unknown_liquidity = {}
    seen_trade_ids = set()
    duplicate_fill_count = 0
    fill_count = fee_bearing_count = 0
    unknown_quote_count = 0
    for path in glob.glob(str(root / "data" / "exchange_fills" / "*" / "*.json")):
        try:
            rows = json.loads(Path(path).read_text()).get("rows") or []
        except Exception:
            continue
        for row in rows:
            try:
                ts = _timestamp_ms(row)
                if not ts or ts < cutoff_ms or ts > upper_bound_ms:
                    continue
                notional_value = _finite_float(row.get("quoteQty") or 0.0)
                if notional_value is None:
                    continue
                notional = abs(notional_value)
                if notional <= 0:
                    continue
                commission_value = _finite_float(row.get("commission") or 0.0)
                if commission_value is None:
                    unknown_quote_count += 1
                    continue
                commission = abs(commission_value)
            except (TypeError, ValueError, OverflowError):
                continue
            account = str(Path(path).parent.name)
            symbol = str(row.get("symbol") or row.get("pair") or "").upper()
            trade_id = row.get("id") or row.get("tradeId")
            if trade_id is not None:
                identity = (account, symbol, str(trade_id))
                if identity in seen_trade_ids:
                    duplicate_fill_count += 1
                    continue
                seen_trade_ids.add(identity)
            quote = _quote_asset(row)
            if quote not in ("USDT", "USDC"):
                unknown_quote_count += 1
                continue
            liquidity = _maker_liquidity(row)
            if liquidity == "UNVERIFIED":
                unknown_liquidity[quote] = unknown_liquidity.get(quote, 0) + 1
            order_type = str(
                row.get("orderType") or row.get("type") or "UNSPECIFIED"
            ).upper()
            client_id = str(
                row.get("clientOrderId") or row.get("client_order_id") or ""
            )
            prefix = (
                "vds"
                if client_id.startswith("vds")
                else (
                    "vd"
                    if client_id.startswith("vd")
                    else client_id[:2] or "UNSPECIFIED"
                )
            )
            keys = (
                ("quote", quote),
                ("liquidity", quote, liquidity),
                ("execution", quote, order_type, liquidity),
                ("prefix", quote, prefix, liquidity),
            )
            fill_count += 1
            if commission > 0:
                fee_bearing_count += 1
            converted = None
            bnb_fee = (
                commission
                if str(row.get("commissionAsset") or "").upper() == "BNB"
                else 0.0
            )
            asset = str(row.get("commissionAsset") or "").upper()
            if commission > 0 and asset == "BNB":
                minute = ts // 60000 * 60000
                candle = (candles.get("BNB" + quote) or {}).get(minute)
                if candle:
                    bounds = (
                        _candle_bounds({"low": candle[0], "high": candle[1]})
                        if isinstance(candle, (list, tuple))
                        else _candle_bounds(candle)
                    )
                    if bounds:
                        converted = commission * bounds[0], commission * bounds[1]
                if converted is None:
                    missing["BNB" + quote] = missing.get("BNB" + quote, 0) + 1
            elif commission > 0 and asset == quote:
                converted = (commission, commission)
            elif commission > 0:
                unknown_quote_count += 1
            if converted is None:
                converted = (0.0, 0.0)
            for key in keys:
                stats = groups.setdefault(
                    key,
                    {
                        "fills": 0,
                        "fee_bearing_fills": 0,
                        "notional": 0.0,
                        "bnb_fee": 0.0,
                        "fee_low": 0.0,
                        "fee_high": 0.0,
                    },
                )
                stats["fills"] += 1
                stats["fee_bearing_fills"] += int(commission > 0)
                stats["notional"] += notional
                stats["bnb_fee"] += bnb_fee
                stats["fee_low"] += converted[0]
                stats["fee_high"] += converted[1]
    result = {
        "window_days": window_days,
        "fills": fill_count,
        "fee_bearing_fills": fee_bearing_count,
        "duplicate_fill_rows_skipped": duplicate_fill_count,
        "unconverted_bnb_fee_fills": sum(missing.values()),
        "unconverted_by_pair": missing,
        "unknown_liquidity_fills_by_quote": unknown_liquidity,
        "unknown_quote_or_fee_asset_fills": unknown_quote_count,
        "conversion_method": "same-minute, quote-specific BNB candle low/high; no live mark fallback",
        "by_quote": {},
        "by_quote_liquidity": {},
        "by_quote_execution_type": {},
        "by_quote_client_prefix": {},
    }
    for key, stats in groups.items():
        kind = key[0]
        if kind == "quote":
            quote = key[1]
            complete = (
                missing.get("BNB" + quote, 0) == 0
                and unknown_quote_count == 0
                and unknown_liquidity.get(quote, 0) == 0
            )
            result["by_quote"][quote] = _rates_from_stats(stats, complete)
        elif kind == "liquidity":
            _, quote, liquidity = key
            complete = (
                missing.get("BNB" + quote, 0) == 0
                and unknown_quote_count == 0
                and unknown_liquidity.get(quote, 0) == 0
            )
            result["by_quote_liquidity"].setdefault(quote, {})[liquidity] = (
                _rates_from_stats(stats, complete)
            )
        elif kind == "execution":
            _, quote, order_type, liquidity = key
            complete = (
                missing.get("BNB" + quote, 0) == 0
                and unknown_quote_count == 0
                and unknown_liquidity.get(quote, 0) == 0
            )
            result["by_quote_execution_type"].setdefault(quote, {}).setdefault(
                order_type, {}
            )[liquidity] = _rates_from_stats(stats, complete)
        else:
            _, quote, prefix, liquidity = key
            complete = (
                missing.get("BNB" + quote, 0) == 0
                and unknown_quote_count == 0
                and unknown_liquidity.get(quote, 0) == 0
            )
            result["by_quote_client_prefix"].setdefault(quote, {}).setdefault(
                prefix, {}
            )[liquidity] = _rates_from_stats(stats, complete)
    result["conversion_complete"] = (
        not missing and unknown_quote_count == 0 and not unknown_liquidity
    )
    return result


def round_trip_pct_for_execution(
    measurement: dict, quote: str, execution_type: str
) -> float:
    """Select an observed quote-specific rate; MARKET is charged at observed taker rate."""
    quote = str(quote or "").upper()
    execution_type = str(execution_type or "").upper()
    if execution_type == "MARKET":
        liquidity = "TAKER"
    elif execution_type == "LIMIT_MAKER":
        liquidity = "MAKER"
    else:
        raise ValueError("LIMIT execution needs its actual maker/taker outcome")
    row = ((measurement.get("by_quote_liquidity") or {}).get(quote) or {}).get(
        liquidity
    )
    if (
        not row
        or not row.get("conversion_complete")
        or "round_trip_pct_high" not in row
    ):
        raise ValueError(f"historical {quote} {liquidity} fee rate is unverified")
    return float(row["round_trip_pct_high"])


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
