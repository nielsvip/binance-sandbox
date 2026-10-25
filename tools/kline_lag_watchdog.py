#!/usr/bin/env python3
"""kline_lag_watchdog — no lane is ever >60s behind the kline (USER 2026-10-25).

Measures every hop from bar close to executed position, per venue, every minute:
  hop kline     last closed 15m bar present in the local kline cache
  hop snapshot  data/latest_market_data.json advanced past the close (+30s settle)
  hop tracker   live position store (flz/tracker.json) written past the close
  hop vecstate  freshest live-mode executor STATE bar vs the close
  hop backstop  backstop consumer ran recently + intervention queue drained
  hop tunnels   s1-int/s2/s5/s6 reachable (state/pilot sync depends on them)

Budget: each hop must land within 60s of (last_close + 30s settle). Stocks are
checked only inside RTH (Mon-Fri 13:30-20:00 UTC); crypto is 24/7. A breach
logs a VEC_LAG_BREACH decision to the disparity queue and attempts only
tool-level repair (tunnel re-establish); anything deeper is reported, never
edited (live files stay untouched).
"""

from __future__ import annotations
import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUDGET_S = 60.0
SETTLE_S = 30.0
GRID_S = 900.0
QUEUE_LOG = ROOT / "data" / "vector_disparity_queue.jsonl"

CRYPTO_PROBE = ("BTCUSDC", "ETHUSDC", "SOLUSDC")
STOCK_PROBE = ("AAPL", "NVDA", "TSLA")
TUNNEL_HOSTS = ("s1-int", "s2", "s5", "s6")


def last_closed_15m_epoch(now_s, settle_s=SETTLE_S):
    eff = float(now_s) - settle_s
    return int(eff // GRID_S) * int(GRID_S)


def bar_close_lag_s(last_close_epoch, now_s, settle_s=SETTLE_S):
    return max(0.0, float(now_s) - (float(last_close_epoch) + settle_s))


def file_lag_s(last_close_epoch, mtime_s, now_s=None, settle_s=SETTLE_S):
    """A file that must advance past each close: lag vs (last_close + settle)."""
    if mtime_s is None:
        return 10**9
    return max(0.0, (float(last_close_epoch) + settle_s) - float(mtime_s))


def bar_label_lag_s(last_close_epoch, bar_label_ts, now_s, settle_s=SETTLE_S):
    """A bar-labeled feed (kline tails, vec STATE bars): current when label >= last close."""
    if bar_label_ts is None:
        return 10**9
    if float(bar_label_ts) >= float(last_close_epoch):
        return 0.0
    return max(0.0, float(now_s) - (float(bar_label_ts) + GRID_S + settle_s))


def check_hop(name, lag_s, budget_s=BUDGET_S):
    try:
        ok = bool(float(lag_s) <= float(budget_s))
    except Exception:
        ok = False
    return (name, round(float(lag_s), 1), ok)


def stocks_in_hours(now_s=None):
    t = dt.datetime.fromtimestamp(now_s or time.time(), tz=dt.timezone.utc)
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return 13 * 60 + 30 <= mins < 20 * 60


def _parse_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e11 else float(v)
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def kline_last_bar(root, symbol):
    best = None
    for f in glob.glob(str(Path(root) / f"{symbol}_15m.json")):
        try:
            rows = json.load(open(f))
        except Exception:
            continue
        rows = (
            rows if isinstance(rows, list) else rows.get("data", rows.get("klines", []))
        )
        if not rows:
            continue
        ts = _parse_ts(
            rows[-1].get("timestamp") if isinstance(rows[-1], dict) else None
        )
        if ts and (best is None or ts > best):
            best = ts
    return best


def file_mtime(path):
    try:
        return os.path.getmtime(str(path))
    except OSError:
        return None


def vecstate_fresh_bar(state_dir):
    best = None
    for f in glob.glob(str(Path(state_dir) / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("mode") != "live" or not d.get("actionable"):
            continue
        ts = d.get("bar_ts")
        try:
            ts = float(ts)
        except Exception:
            continue
        if best is None or ts > best:
            best = ts
    return best


def tunnel_ok(host, timeout_s=8):
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=%d" % timeout_s,
                host,
                "echo OK",
            ],
            capture_output=True,
            timeout=timeout_s + 5,
        )
        return r.returncode == 0
    except Exception:
        return False


def measure(base=None, now_s=None):
    base = Path(base) if base else ROOT
    now_s = now_s if now_s is not None else time.time()
    lc = last_closed_15m_epoch(now_s)
    hops = []
    kline_lags = []
    for sym in CRYPTO_PROBE:
        ts = kline_last_bar(base / "klines_cache", sym)
        if ts is None:
            ts = kline_last_bar(base / "klines_cache_gateway", sym)
        kline_lags.append(bar_label_lag_s(lc, ts, now_s))
    hops.append(check_hop("kline_crypto", min(kline_lags) if kline_lags else 10**9))
    if stocks_in_hours(now_s):
        slags = []
        for sym in STOCK_PROBE:
            ts = kline_last_bar(base / "klines_cache" / "tradier", sym)
            slags.append(bar_label_lag_s(lc, ts, now_s))
        hops.append(check_hop("kline_stocks", min(slags) if slags else 10**9))
    for name, path in (
        ("snapshot", base / "data" / "latest_market_data.json"),
        ("tracker", base / "flz" / "tracker.json"),
    ):
        hops.append(check_hop(name, file_lag_s(lc, file_mtime(path))))
    vb = vecstate_fresh_bar(base / "data" / "vec_live" / "state")
    hops.append(check_hop("vecstate", bar_label_lag_s(lc, vb, now_s)))
    clog = base / "logs" / "vec_backstop_consumer.out"
    mt = file_mtime(clog)
    hops.append(
        check_hop("backstop", (now_s - mt) if mt is not None else 10**9, budget_s=75.0)
    )
    try:
        import redis as _redis

        qlen = _redis.Redis(host="127.0.0.1", port=6379, decode_responses=True).llen(
            "intervention_queue:flz"
        )
        hops.append(
            check_hop("intervention_drained", 0.0 if int(qlen or 0) == 0 else 10**9)
        )
    except Exception:
        hops.append(("intervention_drained", -1.0, False))
    down = [h for h in TUNNEL_HOSTS if not tunnel_ok(h)]
    hops.append(("tunnels", 0.0 if not down else 10**9, not down))
    return {"t": now_s, "last_close": lc, "hops": hops, "tunnels_down": down}


def log_breach(name, lag_s):
    try:
        QUEUE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(str(QUEUE_LOG), "a") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "venue": "all",
                        "kind": "VEC_LAG_BREACH",
                        "position_key": "lane:" + name,
                        "action": "NONE",
                        "reason": f"VEC_LAG_BREACH_{name}_lag{lag_s:.0f}s_over60s",
                        "vec_path": f"VEC_LAG_BREACH_{name}",
                        "fix": "restore the lagging hop; positions held flat-safe until fresh",
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair-tunnels", action="store_true")
    a = ap.parse_args()
    if a.repair_tunnels:
        subprocess.run(["ssh", "-fNT", "s1-sftp"], capture_output=True)
        time.sleep(2)
    rec = measure()
    breaches = [(n, l) for n, l, ok in rec["hops"] if not ok]
    for n, l in breaches:
        log_breach(n, l)
    rec["breaches"] = breaches
    print(json.dumps(rec, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
