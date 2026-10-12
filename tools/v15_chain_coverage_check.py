#!/usr/bin/env python3
"""v15_chain_coverage_check — USER 2026-10-12: after each daily chain apply, verify universe
stocks sym_sides still have fresh+valid per_sym settings; alert on regression vs last check.
Runs Mac cron 13:35Z daily (after the 13:00Z Mac apply). State: data/daily_chain/chain_coverage_state.json.
"""
import datetime as dt
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "daily_chain" / "chain_coverage_state.json"
LOG = ROOT / "data" / "daily_chain" / "chain_coverage.log"


def main():
    u = json.loads((ROOT / "data" / "daily_universe" / sorted((ROOT / "data" / "daily_universe").glob("20*.json"))[-1].name).read_text())
    allowed = u.get("allowed_sym_sides", [])
    st = [s for s in allowed if not s.rsplit("_", 1)[0].endswith(("USDT", "USDC"))]
    con = sqlite3.connect(str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"))
    rows = {r[0]: r for r in con.execute("SELECT sym_side,updated_at,wsharpe,trades FROM per_sym_active")}
    now = dt.datetime.now(dt.timezone.utc)

    def ageh(x):
        try:
            return (now - dt.datetime.fromisoformat(x.replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception:
            return 9999

    ok = [s for s in st if (lambda r: r is not None and ageh(r[1]) <= 120 and (r[3] or 0) > 0 and (r[2] or 0) > 0)(rows.get(s))]
    line = f"{now.strftime('%F %TZ')} universe_stocks={len(st)} valid_fresh={len(ok)} missing={len(st) - len(ok)}"
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    prev = {}
    try:
        prev = json.loads(STATE.read_text())
    except Exception:
        pass
    STATE.write_text(json.dumps({"at": now.isoformat(), "universe": len(st), "valid_fresh": len(ok)}))
    if prev and len(ok) < int(prev.get("valid_fresh", 0)):
        msg = f"CHAIN COVERAGE REGRESSION: {prev.get('valid_fresh')} -> {len(ok)} valid fresh stocks sides"
        with open(LOG, "a") as fh:
            fh.write(f"{now.strftime('%F %TZ')} ALERT {msg}\n")
        try:
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "chain coverage"'], timeout=10, check=False)
        except Exception:
            pass
        print("ALERT " + msg)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
