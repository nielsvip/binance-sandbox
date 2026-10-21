"""data_never_miss_merge — stdlib-only, APPEND-ONLY tail merge; runs locally or piped over ssh.

USER 2026-10-21: "the system can never miss a beat … use macbook s1 and gateway as sources". A patch is
{"base": dir, "patches": {"klines_cache/<rel>.json": [rows]}}. Per file only rows NEWER than the file's own last bar are
appended, in the file's own timestamp format and key set. Existing rows are never changed or dropped; an unreadable or
non-list file is never written (that is a job for the truncation guard). Atomic tmp + os.replace.

  python3 tools/data_never_miss_merge.py patch.json          # local
  ssh host 'python3 - /tmp/patch.json' < tools/data_never_miss_merge.py
"""
import json
import os
import sys

REQUIRED = ("timestamp", "open", "high", "low", "close")


def norm(ts):
    return str(ts).replace(" ", "T")[:19]


def merge_file(path, rows):
    n = 10 if path.endswith("_D.json") else 19  # D: one row per DATE (stock D files mix "2026-10-20" and "...T20:00:00")
    try:
        with open(path) as fh:
            cur = json.load(fh)
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(cur, list) or not cur or not isinstance(cur[-1], dict) or "timestamp" not in cur[-1]:
        return "not_a_kline_list"
    last = cur[-1]
    last_ts = norm(last["timestamp"])[:n]
    core = len(norm(last["timestamp"]))  # write new rows in the target's own stamp style (date-only or full)
    suffix = str(last["timestamp"])[core:]
    keys = list(last.keys())
    add = {}
    for r in rows:
        if not isinstance(r, dict) or any(k not in r for k in REQUIRED):
            continue
        t = norm(r["timestamp"])
        if t[:n] <= last_ts:
            continue
        row = {k: r[k] for k in keys if k in r}
        row["timestamp"] = (t + "T00:00:00")[:core] + suffix
        add.setdefault(t[:n], row)
    if not add:
        return 0
    out = cur + [add[t] for t in sorted(add)]
    tmp = path + ".nevermiss.tmp"
    try:
        mode = os.stat(path).st_mode & 0o777
        with open(tmp, "w") as fh:
            json.dump(out, fh)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"write_failed {e}"[:80]
    return len(add)


def main(patch_path):
    with open(patch_path) as fh:
        patch = json.load(fh)
    base = os.path.expanduser(patch["base"])
    res = {rel: merge_file(os.path.join(base, rel), rows) for rel, rows in patch["patches"].items()}
    print(json.dumps(res))


if __name__ == "__main__":
    main(sys.argv[1])
