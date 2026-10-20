#!/usr/bin/env python3
"""klines_truncation_guard — find kline JSON files that were cut off mid-write, restore or delete them.

USER 2026-10-16: "fix the gateway fetcher if it has a problem probably just deleting them from the gateway
obligates it to refetch them so if possible pls dont change the script, you can also put the good copies in the
gateway's klines folder so it syncs good data to servers instead of crap".

MEASURED that day: 111 unparseable files in `klines_cache_gateway` on the Mac AND the same 111 on S1 (rsync
propagated them), 222 on the gateway host itself. Every size was an exact multiple of 4096 (245760, 229376,
282624 …) — a write that stopped at a block boundary, i.e. ENOSPC, not bad content. Both ends were full: the
gateway at 99 % (620 MB free) and S1 at 100 % (2.5 GB). The loader logs and skips them, so they are invisible
except as degraded HTF frames for those symbols, and a symbol whose ONLY store is the gateway cache
(1000RATSUSDT, PROVEUSDT, BASUSDT, SAHARAUSDT …) has no source at all.

This does NOT touch the fetcher. Per file:
  RESTORE  a parseable copy of the same symbol+TF in another local store with at least as many rows wins.
  SALVAGE  nothing to restore from -> keep every complete row before the cut, in place (2026-10-20: never delete —
           a delete forced a Binance refetch; the Mac pulls missing bars from S1, never the API).
A file is only ever replaced by a LONGER one, never a shorter one.

usage:
  python3 tools/klines_truncation_guard.py                 # report only
  python3 tools/klines_truncation_guard.py --apply
  python3 tools/klines_truncation_guard.py --apply --roots klines_cache_gateway:klines_cache
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("KLINES_GUARD_ROOT", "")) if os.environ.get("KLINES_GUARD_ROOT") else None
if ROOT is None:
    for c in (Path(__file__).resolve().parents[1], Path.home() / "binance-sandbox", Path.home() / "binance"):
        if (c / "klines_cache").is_dir() or (c / "klines_cache_gateway").is_dir():
            ROOT = c
            break
    ROOT = ROOT or Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ["klines_cache_gateway", "klines_cache", "klines_cache_backtest", "klines_cache_macbook", "klines_canonical"]
REPORT = ROOT / "data" / "reports" / "klines_truncation.json"


def log(msg):
    print(f"[klines_trunc {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def tail_ok(p):
    """cheap test: a complete JSON array/object ends with ] or }. A cut-off write does not."""
    try:
        sz = os.path.getsize(p)
        if sz == 0:
            return False
        with open(p, "rb") as f:
            f.seek(max(0, sz - 64))
            t = f.read().strip()
        return t.endswith(b"]") or t.endswith(b"}")
    except OSError:
        return False


def rows_of(p):
    try:
        d = json.load(open(p))
        return len(d) if isinstance(d, list) else len(d.get("data") or d.get("klines") or [])
    except Exception:
        return None


def salvage_rows(p):
    """complete rows of a JSON list cut off mid-write: cut after the last '}' that closes a row, close the array."""
    try:
        raw = open(p, "rb").read()
    except OSError:
        return None
    end = len(raw)
    for _ in range(64):
        end = raw.rfind(b"}", 0, end)
        if end < 0:
            return None
        try:
            rows = json.loads(raw[:end + 1].decode("utf8", "replace").rstrip().rstrip(",") + "]")
            return rows if isinstance(rows, list) and rows else None
        except ValueError:
            continue
    return None


def scan(roots):
    bad, good = [], {}
    for r in roots:
        base = ROOT / r
        if not base.is_dir():
            continue
        for p in list(base.glob("*.json")) + list(base.glob("*/*.json")):
            rel = p.relative_to(base).as_posix()
            if tail_ok(p):
                n = rows_of(p)
                if n:
                    cur = good.get(rel)
                    if cur is None or n > cur[1]:
                        good[rel] = (p, n)
            else:
                bad.append((p, rel, os.path.getsize(p)))
    return bad, good


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default=":".join(DEFAULT_ROOTS))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    roots = [r for r in a.roots.split(":") if r.strip()]
    bad, good = scan(roots)
    log(f"host={os.uname().nodename} root={ROOT} roots={len(roots)} truncated={len(bad)} parseable_index={len(good)}")
    restored, deleted, freed, kept = [], [], 0, []
    for p, rel, sz in bad:
        src = good.get(rel)
        if src and src[0] != p:
            if a.apply:
                tmp = p.with_suffix(".json.restoretmp")
                try:
                    shutil.copy2(src[0], tmp)
                    os.replace(tmp, p)
                except OSError as e:
                    log(f"restore failed {rel}: {e}")
                    kept.append(rel)
                    continue
            restored.append({"file": rel, "from": str(src[0]), "rows": src[1]})
        else:
            # USER 2026-10-20: "make sure NOTHING destroys klines … NO unnecessary api calls". A delete forced a REST refetch
            # (bans). Salvage every complete row before the cut instead; an unsalvageable file is left in place untouched.
            rows = salvage_rows(p)
            if not rows:
                kept.append(rel)
                continue
            if a.apply:
                tmp = p.with_suffix(".json.salvagetmp")
                try:
                    tmp.write_text(json.dumps(rows))
                    os.replace(tmp, p)
                except OSError as e:
                    log(f"salvage failed {rel}: {e}")
                    kept.append(rel)
                    continue
            deleted.append({"file": str(p), "bytes": sz, "salvaged_rows": len(rows)})
    log(f"{'APPLIED' if a.apply else 'DRY-RUN'}: restored {len(restored)} from a longer local copy, salvaged {len(deleted)} in place (never deleted), {len(kept)} left untouched")
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"at": time.time(), "host": os.uname().nodename, "apply": a.apply, "root": str(ROOT),
                                      "truncated": len(bad), "restored": restored[:50], "deleted": deleted[:50],
                                      "freed_bytes": freed, "unhandled": kept[:50]}, indent=1, sort_keys=True, default=str))
        log(f"report -> {REPORT}")
    except OSError as e:
        log(f"report write failed: {e}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
