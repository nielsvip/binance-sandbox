"""Daily read-only verification of crypto per_sym live settings and exchange activity.

Checks, never writes:
  1. per_sym_active_config.json entries: how many carry real backtest evidence
     (trades > 0 and a positive sharpe), how many are placeholder stubs
     (winning_tag clean_ONLY_CROSSES_v33 / backfill with trades == 0), and the
     age of each entry's updated_at.
  2. Exchange realized-PnL events per account (fills that closed size) for the
     last 7 days, plus the last fill time and the open-position count.

Keys are read through env_gpg in-process; values are never printed.
Run: /opt/anaconda3/envs/binance_env/bin/python tools/persym_daily_verify.py
"""
import collections
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PER_SYM = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
PLACEHOLDER_TAGS = ("clean_ONLY_CROSSES_v33", "backfill")
ACCOUNTS = ["men", "fin", "inf", "flz", "ang"]
MAX_AGE_HOURS = 36.0


def per_sym_report() -> dict:
    data = json.loads(PER_SYM.read_text())
    entries = {k: v for k, v in data.items() if isinstance(v, dict)}
    now = dt.datetime.now(dt.timezone.utc)
    real = stub = stale = 0
    tags = collections.Counter()
    for v in entries.values():
        trades = v.get("trades") or 0
        sharpe = v.get("pool_sharpe") or v.get("wsharpe") or 0
        tag = str(v.get("winning_tag") or "")
        tags[tag[:32]] += 1
        if trades > 0 and sharpe > 0 and not tag.startswith(PLACEHOLDER_TAGS):
            real += 1
        if trades == 0 or tag.startswith(PLACEHOLDER_TAGS):
            stub += 1
        ua = v.get("updated_at")
        try:
            t = dt.datetime.fromisoformat(str(ua).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if (now - t).total_seconds() / 3600.0 > MAX_AGE_HOURS:
                stale += 1
        except Exception:
            stale += 1
    return {
        "file_mtime_utc": dt.datetime.fromtimestamp(PER_SYM.stat().st_mtime, dt.timezone.utc).isoformat(),
        "entries": len(entries),
        "real_evidence": real,
        "placeholder_or_zero_trade": stub,
        "entries_older_than_36h": stale,
        "top_tags": tags.most_common(4),
    }


def exchange_report() -> dict:
    sys.path.insert(0, str(ROOT))
    import env_gpg

    env_gpg.load_env_gpg()
    from binance.client import Client

    since = int((time.time() - 7 * 86400) * 1000)
    out = {}
    for acct in ACCOUNTS:
        key = os.environ.get(f"{acct}_API_KEY")
        sec = os.environ.get(f"{acct}_API_SECRET")
        if not key or not sec:
            out[acct] = "NO_KEYS"
            continue
        try:
            c = Client(api_key=key, api_secret=sec)
            pos = c.futures_position_information()
            open_n = sum(1 for p in pos if abs(float(p["positionAmt"])) > 0)
            inc = c.futures_income_history(incomeType="REALIZED_PNL", startTime=since, limit=1000)
            last = max((r["time"] for r in inc), default=None)
            out[acct] = {
                "open_positions": open_n,
                "realized_pnl_events_7d": len(inc),
                "last_fill_utc": dt.datetime.utcfromtimestamp(last / 1000).isoformat() if last else None,
            }
        except Exception as e:
            out[acct] = f"ERROR {type(e).__name__}"
    return out


if __name__ == "__main__":
    report = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "per_sym": per_sym_report(),
        "exchange": exchange_report(),
    }
    print(json.dumps(report, indent=2, default=str))
