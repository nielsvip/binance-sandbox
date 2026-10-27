#!/usr/bin/env python3
"""klines_dedup_symlink — replace byte-identical DUPLICATE kline files by a symlink to the one real copy (USER 2026-10-27:
"any duplicate one can be replaced by a symlink" — klines are sacred, but not four times over while the fleet is short of disk).

Real copies, in order of preference as the link target: ~/binance/klines_cache (USER 2026-10-27: "the one that is constantly
updated, the rest needs to append off that"), then the big store ~/binance-sandbox/klines_cache (klines_cache_backtest is a
symlink to it), then ~/binance-sandbox/klines_canonical. Neither the live cache nor the big store is ever replaced by a link:
the big store holds years of history and must never take on a rolling window's shorter content. A file in a duplicate root is replaced only when the file at the SAME relative path in a
real root has the same size AND the same sha256, and the duplicate was not modified in the last --min-age-s seconds. The
symlink is created beside it and swapped in with os.replace, so a reader sees either the old file or the identical link.

Why this is safe for writers: every writer of these trees (klines_guard.guarded_write, klines_vault, klines_truncation_guard,
rsync pushes) writes a temp file and renames it over the path, which REPLACES a symlink by a real file — it never writes
through the link into the real copy. A rewritten duplicate therefore simply stops being a link.
NOT linked: klines_vault and every .guard_snap (they are the anti-erasure copies; a link would restore from the file it guards).
usage: klines_dedup_symlink.py [--apply] [--min-age-s 120]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

H = Path.home()
REAL = [H / "binance" / "klines_cache", H / "binance-sandbox" / "klines_cache", H / "binance-sandbox" / "klines_canonical"]  # USER 2026-10-27: ~/binance/klines_cache is the constantly updated source; never linked itself
DUPS = [H / "binance-sandbox" / "klines_cache" / "gateway", H / "binance-sandbox" / "klines_cache_macbook",
        H / "binance" / "klines_cache_gateway", H / "binance" / "klines_canonical", H / "binance" / "ez_s1_stack" / "klines_cache",
        H / "klines_master", H / "bars_crypto"]
SKIP_PARTS = (".guard_snap", "klines_vault", ".~tmp~")
LOG = H / "logs" / "klines_dedup_symlink.jsonl"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-age-s", type=float, default=120.0)
    a = ap.parse_args(argv)
    c = {"scanned": 0, "linked": 0, "linked_mb": 0.0, "differ": 0, "no_real": 0, "young": 0, "errors": 0}
    per_root = {}
    for d in DUPS:
        if not d.exists() or d.is_symlink():
            continue
        n_mb = 0.0
        for dp, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x not in SKIP_PARTS and not x.startswith(".")]
            for f in fns:
                p = Path(dp) / f
                if p.is_symlink() or not p.is_file() or any(s in str(p) for s in SKIP_PARTS):
                    continue
                c["scanned"] += 1
                rel = p.relative_to(d)
                real = None
                for r in REAL:
                    q = r / rel
                    if q.is_file() and not q.is_symlink() and q.resolve() != p.resolve() and not str(p).startswith(str(r) + "/" + str(rel)):
                        real = q
                        break
                if real is None:
                    c["no_real"] += 1
                    continue
                try:
                    st = p.stat()
                    if time.time() - st.st_mtime < a.min_age_s:
                        c["young"] += 1
                        continue
                    if real.stat().st_size != st.st_size or sha(real) != sha(p):
                        c["differ"] += 1
                        continue
                    if a.apply:
                        tmp = p.with_name(f".{p.name}.lnk{os.getpid()}")
                        os.symlink(str(real), tmp)
                        if p.stat().st_mtime != st.st_mtime:  # rewritten while we hashed: leave it
                            tmp.unlink()
                            c["young"] += 1
                            continue
                        os.replace(tmp, p)
                    c["linked"] += 1
                    c["linked_mb"] += st.st_size / 1048576
                    n_mb += st.st_size / 1048576
                except Exception as e:
                    c["errors"] += 1
                    print(f"error {p}: {type(e).__name__}: {e}", file=sys.stderr)
        per_root[str(d)] = round(n_mb, 1)
    c["linked_mb"] = round(c["linked_mb"], 1)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "dedup_symlink" if a.apply else "dedup_symlink_dry_run", **c, "mb_by_root": per_root}
    print(json.dumps(rec), flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
