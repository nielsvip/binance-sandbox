#!/usr/bin/env python3
"""Content-freshness gate for S1 indicator snapshots pulled to the Mac (2026-10-06).

A fresh file mtime does not prove fresh indicators: on 2026-10-06 03:43-04:03 S1 ez_indicators
re-saved frozen prices (no mark-price feed) every few seconds. This gate checks the CONTENT:
bar ages (timestamp_3m / timestamp_15m) across all symbols. The Mac's own snapshot is itself often 3m-stale (median timestamp_3m age ~13 min measured
2026-10-06 04:03), so the S1 snapshot is judged RELATIVE to the Mac's newest own snapshot:
publish only when S1 is not worse than the Mac on ALL of: median 3m-bar age, median 15m-bar
age (each within tolerance) and count of symbols whose 15m bar is >30 min old (within +10).
Without a Mac reference only the absolute 3m-median limit applies.
Usage: s1_snapshot_fresh_gate.py <s1_snapshot.json> [mac_reference.json] [tolerance_s=60] [abs_max_median_s=900]
Exit 0 = publish, 1 = reject. Prints one summary line.
"""
import sys
import time
from datetime import datetime

import orjson


def bar_age(value, now):
    try:
        return now - datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def bar_ages(path, now):
    with open(path, "rb") as handle:
        data = orjson.loads(handle.read())
    ages = {"3m": {}, "15m": {}}
    for symbol, values in data.items():
        if isinstance(values, dict):
            for tf in ages:
                age = bar_age(values.get("timestamp_" + tf), now)
                if age is not None:
                    ages[tf][symbol] = age
    return ages


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def stats(ages):
    return {
        "m3": median(ages["3m"].values()),
        "m15": median(ages["15m"].values()),
        "stale15": sum(1 for age in ages["15m"].values() if age > 1800),
        "n": len(ages["3m"]),
    }


def main():
    s1_path = sys.argv[1]
    mac_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] not in ("", "-") else None
    tolerance = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
    abs_max = float(sys.argv[4]) if len(sys.argv) > 4 else 900.0
    now = time.time()
    s1_ages = bar_ages(s1_path, now)
    if not s1_ages["3m"] or not s1_ages["15m"]:
        print("REJECT no timestamp_3m/timestamp_15m in S1 snapshot")
        return 1
    s1 = stats(s1_ages)
    mac = None
    if mac_path:
        try:
            mac = stats(bar_ages(mac_path, now))
        except (OSError, ValueError):
            mac = None
    if mac is not None and mac["m3"] is not None and mac["m15"] is not None:
        checks = {
            "3m_median": s1["m3"] <= mac["m3"] + tolerance,
            "15m_median": s1["m15"] <= mac["m15"] + tolerance,
            "15m_stale_count": s1["stale15"] <= mac["stale15"] + 10,
        }
        mac_txt = f"mac(m3={int(mac['m3'])}s m15={int(mac['m15'])}s stale15={mac['stale15']})"
    else:
        checks = {"3m_median_abs": s1["m3"] <= abs_max}
        mac_txt = "mac=NA"
    ok = all(checks.values())
    failed = ",".join(name for name, passed in checks.items() if not passed) or "none"
    majors = " ".join(f"{symbol}_3m={int(s1_ages['3m'][symbol]) if symbol in s1_ages['3m'] else 'NA'}s" for symbol in ("BTCUSDC", "ETHUSDC"))
    print(f"{'PUBLISH' if ok else 'REJECT'} s1(m3={int(s1['m3'])}s m15={int(s1['m15'])}s stale15={s1['stale15']}) {mac_txt} failed={failed} n={s1['n']} {majors}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
