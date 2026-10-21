#!/usr/bin/env python3
"""klines_3m_vision_backfill — a YEAR of 3m crypto klines on the ext drive, from Binance's public bulk archive.

USER 2026-10-21: "mac has only 1899 ext has a year". MEASURED that day: it did not — the ext 3m archive
(<drive>/binance_archive/klines_archive/*_3m.json) started 2026-10-12 (9 days); `klines_ext_archive --deepen` never
covers 3m and pages fapi at limit=1500 (top weight tier, trading IP).

Source here is data.binance.vision (Binance's public S3 dump of futures UM klines) — NOT the trading API, no request
weight, cannot ban the egress IP. Monthly zips are stored AS-IS (exchange-native, immutable, ~0.6 MB per symbol-month)
next to the archive, each verified against Binance's own .CHECKSUM (sha256) before it is kept:

  <drive>/binance_archive/vision_3m/<SYMBOL>/<SYMBOL>-3m-YYYY-MM.zip      (+ daily zips for the current month)

Append-only: an existing verified zip is never re-downloaded or overwritten; a month that does not exist for a symbol
(not listed yet -> HTTP 404) is recorded once in the manifest and skipped. Resumable; safe to re-run any time.

  python3 tools/klines_3m_vision_backfill.py                 # all live crypto symbols, last 12 months
  python3 tools/klines_3m_vision_backfill.py --symbols BTCUSDC --months 3 --dry-run
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE_URL = "https://data.binance.vision/data/futures/um"
SUBDIR = Path("binance_archive") / "vision_3m"
MANIFEST = "_manifest.json"
MIN_FREE_GB = 3.0


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def drive():
    sys.path.insert(0, str(REPO / "tools"))
    from klines_3m_archive import real_drives
    ds = real_drives()
    if not ds:
        return None
    # most free space wins (2026-10-21: SSD2T + TOSHIBA_EXT were 100 % full, E3T had 11 GB)
    return max(ds, key=lambda d: os.statvfs(d).f_bavail * os.statvfs(d).f_frsize)


def free_gb(d):
    st = os.statvfs(d)
    return st.f_bavail * st.f_frsize / 1e9


def get(url, timeout=60):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + attempt * 3)
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2 + attempt * 3)
    raise OSError(f"download failed: {url}")


def fetch_verified(url, dst):
    """download url + url.CHECKSUM, keep only on sha256 match. Returns 'ok'|'missing'|'bad'."""
    blob = get(url)
    if blob is None:
        return "missing"
    chk = get(url + ".CHECKSUM")
    want = chk.decode().split()[0].strip().lower() if chk else None
    if not want or hashlib.sha256(blob).hexdigest() != want:
        return "bad"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.part")
    tmp.write_bytes(blob)
    os.replace(tmp, dst)
    return "ok"


def periods(months):
    """last `months` complete months + every complete day of the current month (UTC)."""
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1)
    out, m = [], first
    for _ in range(months):
        m = (m - timedelta(days=1)).replace(day=1)
        out.append(("monthly", m.strftime("%Y-%m")))
    d = first
    while d < today:
        out.append(("daily", d.isoformat()))
        d += timedelta(days=1)
    return sorted(out, key=lambda p: p[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--pause", type=float, default=0.05)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.symbols:
        symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        symbols = sorted(json.loads((REPO / "symbols.json").read_text()))
        try:
            sys.path.insert(0, str(REPO / "tools"))
            from excluded_tokenized_stocks import is_denied
            symbols = [s for s in symbols if not is_denied(s)]
        except Exception:
            pass
    d = drive()
    if d is None:
        log("no writable ext drive with >= 3 GB free — nothing done")
        return 1
    root = d / SUBDIR
    man_p = root / MANIFEST
    try:
        man = json.loads(man_p.read_text())
    except (OSError, ValueError):
        man = {"missing": {}}
    plan = periods(a.months)
    log(f"drive {d} ({free_gb(d):.1f} GB free) symbols={len(symbols)} periods={len(plan)} dry={a.dry_run}")
    got = skipped = missing = bad = 0
    for symbol in symbols:
        for kind, label in plan:
            name = f"{symbol}-3m-{label}.zip"
            dst = root / symbol / name
            if dst.exists() or name in man["missing"]:
                skipped += 1
                continue
            if a.dry_run:
                got += 1
                continue
            if free_gb(d) < MIN_FREE_GB:
                log(f"STOP: {d} below {MIN_FREE_GB} GB free")
                man_p.write_text(json.dumps(man, indent=1))
                return 2
            res = fetch_verified(f"{BASE_URL}/{kind}/klines/{symbol}/3m/{name}", dst)
            if res == "ok":
                got += 1
            elif res == "missing":
                # a daily file can appear late — only remember monthly misses (pre-listing months never appear)
                if kind == "monthly":
                    man["missing"][name] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                missing += 1
            else:
                bad += 1
                log(f"CHECKSUM MISMATCH {name} — not kept")
            time.sleep(a.pause)
        if not a.dry_run:
            root.mkdir(parents=True, exist_ok=True)
            man_p.write_text(json.dumps(man, indent=1))
        log(f"{symbol}: done (total got={got} skipped={skipped} missing={missing} bad={bad})")
    log(f"FINISHED got={got} skipped={skipped} missing(pre-listing/not yet published)={missing} checksum_bad={bad} free={free_gb(d):.1f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
