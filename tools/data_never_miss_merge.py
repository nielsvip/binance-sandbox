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


def canon(ts):
    """one key for every stamp style seen in the stores: "2026-10-20", "2026-10-20T19:30", "2026-10-20 19:30:00",
    "2026-10-20T19:30:00.000000Z" -> "2026-10-20T19:30:00" (2026-10-21: three styles coexist in stock files)."""
    s = str(ts).replace(" ", "T")
    if len(s) >= 19 and s[16] == ":":
        return s[:19]
    if len(s) >= 16 and s[13] == ":":
        return s[:16] + ":00"
    return s[:10] + "T00:00:00"


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
    if "/tradier/" in path.replace(os.sep, "/") and any(isinstance(r, dict) and len(str(r.get("timestamp", ""))) > 10 and str(r["timestamp"])[10] == " " for r in cur[-60:]):
        return "et_labelled_skip"  # New-York-labelled stock file: UTC rows would duplicate its session (USO/BNO 2026-10-21)
    last_ts = canon(last["timestamp"])[:n]
    raw = str(last["timestamp"])
    core = 10 if len(raw) < 16 else (16 if len(raw) < 19 or raw[16] != ":" else 19)  # target's own stamp style
    suffix = raw[core:]
    keys = list(last.keys())
    add = {}
    for r in rows:
        if not isinstance(r, dict) or any(k not in r for k in REQUIRED):
            continue
        t = canon(r["timestamp"])
        if t[:n] <= last_ts:
            continue
        row = {k: r[k] for k in keys if k in r}
        row["timestamp"] = t[:core] + suffix
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
