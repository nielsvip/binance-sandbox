#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""tradeable_refresh_loop.py — fast 1-min local loop for fin+ang agent.

Walks tradeable_keys.json for fin and ang, reads Redis `latest_market_data`,
scores each (symbol, side) pair on:
  1. MTF WaveTrend alignment across {3m, 15m, 1h, 4h, D}
  2. S/R proximity (dc_4h, bb_1h, sma200_D, sma200_1h)
  3. Distance to current price (entry quality)

Writes ~/binance-agent-handoff/tradeable_refresh.json with top candidates per account.
The cloud `fin-hourly-supervisor` reads this for fast tradeable awareness between its
hourly iterations. agent_snapshot_writer also inlines this into snapshot.json on its 5-min push.

NOT a trade executor. Read-only producer of advisory ranking data.

Cron: */1 * * * *
"""
import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
HANDOFF_REPO = Path.home() / "binance-agent-handoff"
OUT_PATH = HANDOFF_REPO / "tradeable_refresh.json"
LOG_PATH = Path.home() / "logs" / "tradeable_refresh_loop.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
ACCOUNTS = ("fin", "ang")
TF_ORDER = ("3m", "15m", "1h", "4h", "D")
TOP_N_PER_ACCOUNT = 25
SR_NEAR_PCT = 1.5
SCHEMA_VERSION = 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("tradeable_refresh")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redis_market_data():
    import redis
    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=False)
    raw = r.get("latest_market_data")
    if raw is None:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            return pickle.loads(raw)
        except Exception:
            return {}


def _redis_positions(acct):
    """Return inner positions dict for an account ({} if none). Keys look like 'ang:SYMBOL_LONG'."""
    import redis
    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=False)
    raw = r.get(f"positions:{acct}")
    if raw is None:
        return {}
    try:
        d = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            d = pickle.loads(raw)
        except Exception:
            return {}
    if isinstance(d, dict):
        inner = d.get("positions")
        if isinstance(inner, dict):
            return inner
    return {}


def _position_gain(positions_inner, acct, symbol, side):
    """Look up gain for an account-prefixed key. Returns (gain_pct_or_None, is_held_bool)."""
    if not positions_inner:
        return None, False
    key = f"{acct}:{symbol}_{side}"
    p = positions_inner.get(key)
    if not isinstance(p, dict):
        return None, False
    qty = p.get("positionAmt")
    try:
        if qty is not None and abs(float(qty)) <= 0:
            return None, False
    except (TypeError, ValueError):
        pass
    g = p.get("gain")
    if g is None:
        g = p.get("gain_pct")
    try:
        return (float(g) if g is not None else None), True
    except (TypeError, ValueError):
        return None, True


def _load_tradeable_keys():
    path = BASE / "tradeable_keys.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log.error("tradeable_keys read failed: %s", e)
        return []


def _per_account(keys, acct):
    prefix = f"{acct}:"
    out = []
    for k in keys:
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        if rest.endswith("_LONG"):
            out.append((rest[:-5], "LONG"))
        elif rest.endswith("_SHORT"):
            out.append((rest[:-6], "SHORT"))
    return out


def _wt_aligned(fields, tf, side):
    w1 = fields.get(f"wt1_{tf}")
    w2 = fields.get(f"wt2_{tf}")
    if w1 is None or w2 is None:
        return None
    if side == "LONG":
        return w1 > w2
    return w1 < w2


def _wt_alignment_count(fields, side):
    n = 0
    seen = 0
    per_tf = {}
    for tf in TF_ORDER:
        a = _wt_aligned(fields, tf, side)
        per_tf[tf] = a
        if a is None:
            continue
        seen += 1
        if a:
            n += 1
    return n, seen, per_tf


def _level_distance_pct(price, level):
    if not price or not level or level <= 0:
        return None
    return abs(price - level) / level * 100.0


def _sr_proximity(fields, side, price):
    """Return (closest_level_name, distance_pct, supportive: bool)."""
    if not price:
        return None, None, False
    levels = {
        "dc_high_4h": fields.get("dc_high_4h"),
        "dc_low_4h": fields.get("dc_low_4h"),
        "bb_high_1h": fields.get("bb_high_1h"),
        "bb_low_1h": fields.get("bb_low_1h"),
        "sma200_1h": fields.get("sma200_1h"),
        "sma200_D": fields.get("sma200_D"),
    }
    best_name = None
    best_dist = None
    for name, lvl in levels.items():
        d = _level_distance_pct(price, lvl)
        if d is None:
            continue
        if best_dist is None or d < best_dist:
            best_dist = d
            best_name = name
    if best_name is None:
        return None, None, False
    is_low = "low" in best_name
    is_high = "high" in best_name
    is_sma = best_name.startswith("sma200")
    supportive = False
    if side == "LONG" and (is_low or is_sma) and price >= (levels[best_name] or 0):
        supportive = True
    elif side == "SHORT" and (is_high or is_sma) and price <= (levels[best_name] or 0):
        supportive = True
    return best_name, best_dist, supportive


def _score(mtf_count, mtf_seen, sr_dist, sr_supportive):
    if mtf_seen == 0:
        return 0
    align_pct = mtf_count / mtf_seen
    base = align_pct * 60.0
    if sr_dist is not None and sr_dist <= SR_NEAR_PCT:
        base += 25.0 * (1.0 - (sr_dist / SR_NEAR_PCT))
    if sr_supportive:
        base += 15.0
    return round(base, 2)


def _score_pair(symbol, side, market_data, acct, positions_inner):
    fields = market_data.get(symbol)
    if not isinstance(fields, dict):
        return None
    price = fields.get("current_price") or fields.get("mark_price")
    mtf_count, mtf_seen, per_tf = _wt_alignment_count(fields, side)
    sr_name, sr_dist, sr_supportive = _sr_proximity(fields, side, price)
    score = _score(mtf_count, mtf_seen, sr_dist, sr_supportive)
    gain_pct, is_held = _position_gain(positions_inner, acct, symbol, side)
    bb_high_1h = fields.get("bb_high_1h")
    if bb_high_1h is None:
        bb_high_1h = fields.get("bb_upper_1h")
    bb_low_1h = fields.get("bb_low_1h")
    if bb_low_1h is None:
        bb_low_1h = fields.get("bb_lower_1h")
    return {
        "key": f"{symbol}_{side}",
        "symbol": symbol,
        "side": side,
        "score": score,
        "mtf_align": mtf_count,
        "mtf_seen": mtf_seen,
        "mtf_per_tf": {tf: per_tf.get(tf) for tf in TF_ORDER},
        "near_level": sr_name,
        "distance_pct": round(sr_dist, 4) if sr_dist is not None else None,
        "supportive": sr_supportive,
        "current_price": price,
        "ranking_points": fields.get("0ranking_points"),
        "sentiment_class": fields.get("0sentiment_classification"),
        "gain_pct": gain_pct,
        "is_held": is_held,
        "dc_high_4h": fields.get("dc_high_4h"),
        "dc_low_4h": fields.get("dc_low_4h"),
        "bb_high_1h": bb_high_1h,
        "bb_low_1h": bb_low_1h,
        "wt_velocity_3m": fields.get("wt_velocity_3m"),
        "wt_velocity_15m": fields.get("wt_velocity_15m"),
    }


def _refresh_account(acct, tradeable_keys, market_data):
    pairs = _per_account(tradeable_keys, acct)
    positions_inner = _redis_positions(acct)
    rows = [r for r in (_score_pair(s, side, market_data, acct, positions_inner) for s, side in pairs) if r is not None]
    rows.sort(key=lambda r: r["score"], reverse=True)
    fresh_setups = [r for r in rows if r["score"] >= 70 and r["mtf_align"] >= 4]
    return {
        "tradeable_count": len(pairs),
        "scored_count": len(rows),
        "positions_count": sum(1 for r in rows if r.get("is_held")),
        "candidates": rows[:TOP_N_PER_ACCOUNT],
        "fresh_setups": fresh_setups,
        "warnings": [] if rows else ["no_market_data_for_any_symbol"],
    }


def build():
    market_data = _redis_market_data()
    tradeable_keys = _load_tradeable_keys()
    accounts_out = {acct: _refresh_account(acct, tradeable_keys, market_data) for acct in ACCOUNTS}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "generator": "tradeable_refresh_loop.py",
        "ttl_sec": 120,
        "market_data_symbols": len(market_data),
        "accounts": accounts_out,
    }


def write_only(payload):
    HANDOFF_REPO.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n")
    log.info("wrote tradeable_refresh.json (%d bytes)", OUT_PATH.stat().st_size)


def main():
    t0 = time.time()
    try:
        payload = build()
    except Exception as e:
        log.exception("build failed: %s", e)
        sys.exit(1)
    try:
        write_only(payload)
    except Exception as e:
        log.exception("write failed: %s", e)
        sys.exit(2)
    elapsed = time.time() - t0
    fin_count = len(payload["accounts"]["fin"]["candidates"])
    ang_count = len(payload["accounts"]["ang"]["candidates"])
    fin_fresh = len(payload["accounts"]["fin"]["fresh_setups"])
    ang_fresh = len(payload["accounts"]["ang"]["fresh_setups"])
    log.info("refresh ok in %.1fs: fin top=%d fresh=%d | ang top=%d fresh=%d", elapsed, fin_count, fin_fresh, ang_count, ang_fresh)


if __name__ == "__main__":
    main()
