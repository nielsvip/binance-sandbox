"""data_never_miss_dupscan — find (and with --fix remove) rows whose CANONICAL timestamp repeats in a kline file.

2026-10-21: before canon() existed, data_never_miss compared "2026-10-20T19:30" (minute stamp, some stock files) with
"2026-10-20T19:30:00.000000Z" as different text and could append a second copy of a bar. --fix keeps the FIRST row
per canonical timestamp (the file's own original) and drops only later repeats; nothing else changes. Stdlib-only.

  python3 tools/data_never_miss_dupscan.py BASE [--fix]
"""
import glob
import json
import os
import sys


def canon(ts):
    s = str(ts).replace(" ", "T")
    if len(s) >= 19 and s[16] == ":":
        return s[:19]
    if len(s) >= 16 and s[13] == ":":
        return s[:16] + ":00"
    return s[:10] + "T00:00:00"


def main(base, fix):
    base = os.path.expanduser(base)
    report = {}
    for p in glob.glob(os.path.join(base, "klines_cache", "*.json")) + glob.glob(os.path.join(base, "klines_cache", "tradier", "*.json")):
        try:
            if os.path.getmtime(p) < 1792540800:  # 2026-10-21T00:00Z — only files written since this tool existed
                continue
            with open(p) as fh:
                rows = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        n = 10 if p.endswith("_D.json") else 19
        seen, keep, dups = set(), [], 0
        for r in rows:
            k = canon(r.get("timestamp", ""))[:n] if isinstance(r, dict) else None
            if k is not None and k in seen:
                dups += 1
                continue
            if k is not None:
                seen.add(k)
            keep.append(r)
        if dups:
            report[os.path.relpath(p, base)] = dups
            if fix:
                tmp = p + ".dupfix.tmp"
                with open(tmp, "w") as fh:
                    json.dump(keep, fh)
                os.chmod(tmp, os.stat(p).st_mode & 0o777)
                os.replace(tmp, p)
    print(json.dumps({"files": len(report), "dup_rows": sum(report.values()), "fixed": fix, "sample": dict(list(report.items())[:10])}))


if __name__ == "__main__":
    main(sys.argv[1], "--fix" in sys.argv)
