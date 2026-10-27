#!/usr/bin/env python3
"""klines_macbook_drain — ONE copy of klines on S1 (USER 2026-10-27: "keep only one copy of klines (the big one that the
macbook klines append to) and once appended delete the small version in the macbook folder").

klines_cache_macbook/ is the Mac's feed buffer; klines_append.py merges it into klines_cache_backtest/ (the big store, a
symlink to klines_cache/ on S1). A macbook file is deleted only when EVERY bar in it is accounted for in the big store:
its timestamp is present in the target file, or lies inside the canonical file's range (klines_canonical/ supersedes the
Mac feed inside its own range — klines_append never appends those bars by design). Unparseable, open, younger than
--min-age-s, or not-fully-covered files are kept and reported. Every readers of the folder (utils consolidation,
backtest_v8_precompute, npz_bar_refresh, klines_master) uses it only as a supplement (union / gap fill / freshest bar), so
an emptied buffer falls through to the big store.

Since 2026-10-27 the Mac pushes only a rolling TAIL per file (tools/klines_tail_stage.py), so the buffer is small; deleting
a tail just makes the Mac resend it, so --min-mb (default 3) keeps tails and drains the legacy full copies.

The Mac's tails land in klines_cache_macbook_inbox/ (rsync delta, never deleted there) and --merge-inbox folds each changed
tail into klines_cache_macbook/<same path> (union by timestamp, the newer Mac row wins on the same timestamp), so a file that
still holds un-appended history keeps it and every reader sees exactly what it saw before.

usage: klines_macbook_drain.py [--merge-inbox] [--drain] [--apply] [--min-mb 3] [--min-age-s 300]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MACBOOK = REPO / "klines_cache_macbook"
TARGET = REPO / "klines_cache_backtest"
CANONICAL = REPO / "klines_canonical"
INBOX = REPO / "klines_cache_macbook_inbox"
MERGED_STAMP = INBOX / ".merged_mtimes.json"
LOG = Path.home() / "logs" / "klines_macbook_drain.jsonl"


def load(p: Path):
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, list) else None
    except Exception:
        return None


def log(rec: dict):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec}
    print(json.dumps(rec), flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def merge_inbox(apply: bool) -> dict:
    """Fold every inbox tail changed since its last merge into the macbook buffer (atomic write)."""
    try:
        done = json.loads(MERGED_STAMP.read_text())
    except Exception:
        done = {}
    c = {"inbox": 0, "merged": 0, "created": 0, "unchanged": 0, "bad": 0, "bars_added": 0}
    for p in list(INBOX.glob("*_*.json")) + list(INBOX.glob("tradier/*_*.json")) if INBOX.exists() else []:
        c["inbox"] += 1
        rel = str(p.relative_to(INBOX))
        m = p.stat().st_mtime
        if done.get(rel) == m:
            c["unchanged"] += 1
            continue
        tail = load(p)
        if not tail:
            c["bad"] += 1
            continue
        out = MACBOOK / rel
        cur = load(out) if out.exists() else []
        if cur is None:
            cur = []
        by = {b.get("timestamp"): b for b in cur if isinstance(b, dict) and b.get("timestamp")}
        n0 = len(by)
        for b in tail:
            if isinstance(b, dict) and b.get("timestamp"):
                by[b["timestamp"]] = b
        c["bars_added"] += len(by) - n0
        if apply:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(json.dumps([by[k] for k in sorted(by)], separators=(",", ":")))
            os.replace(tmp, out)
            done[rel] = m
        c["created" if not cur else "merged"] += 1
    if apply:
        tmp = MERGED_STAMP.with_name(MERGED_STAMP.name + ".tmp")
        tmp.write_text(json.dumps(done))
        os.replace(tmp, MERGED_STAMP)
    return c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge-inbox", action="store_true")
    ap.add_argument("--drain", action="store_true", help="(default when --merge-inbox is not given)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-mb", type=float, default=3.0)
    ap.add_argument("--min-age-s", type=float, default=300.0)
    a = ap.parse_args(argv)
    if a.merge_inbox:
        log({"event": "merge_inbox" if a.apply else "merge_inbox_dry_run", **merge_inbox(a.apply)})
        if not a.drain:
            return 0
    if not MACBOOK.exists():
        return 0
    files = sorted(list(MACBOOK.glob("*_*.json")) + list(MACBOOK.glob("tradier/*_*.json")))
    c = {"files": len(files), "deleted": 0, "deleted_mb": 0.0, "kept_small": 0, "kept_young": 0, "kept_unparseable": 0, "kept_uncovered": 0, "kept_no_target": 0}
    uncovered = []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        mb = st.st_size / 1048576
        if mb < a.min_mb:
            c["kept_small"] += 1
            continue
        if time.time() - st.st_mtime < a.min_age_s:
            c["kept_young"] += 1
            continue
        rel = p.relative_to(MACBOOK)
        src = load(p)
        if src is None:
            c["kept_unparseable"] += 1
            continue
        tgt_path = TARGET / rel
        tgt = load(tgt_path) if tgt_path.exists() else None
        if tgt is None:
            c["kept_no_target"] += 1
            continue
        have = {b.get("timestamp") for b in tgt if isinstance(b, dict)}
        canon = load(CANONICAL / rel) if (CANONICAL / rel).exists() else None
        lo = hi = None
        if canon:
            ts = [b.get("timestamp") for b in canon if isinstance(b, dict) and b.get("timestamp")]
            if ts:
                lo, hi = min(ts), max(ts)
        missing = [b.get("timestamp") for b in src if isinstance(b, dict) and b.get("timestamp") and b.get("timestamp") not in have and not (lo and lo <= b.get("timestamp") <= hi)]
        if missing:
            c["kept_uncovered"] += 1
            uncovered.append({"file": str(rel), "missing": len(missing), "first_missing": min(missing), "last_missing": max(missing)})
            continue
        if a.apply:
            if p.stat().st_mtime != st.st_mtime:  # the Mac rewrote it while we checked: next run
                c["kept_young"] += 1
                continue
            p.unlink()
        c["deleted"] += 1
        c["deleted_mb"] += mb
    c["deleted_mb"] = round(c["deleted_mb"], 1)
    log({"event": "drain" if a.apply else "drain_dry_run", **c, "uncovered_sample": uncovered[:20]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
