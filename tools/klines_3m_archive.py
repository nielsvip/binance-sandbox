#!/usr/bin/env python3
"""klines_3m_archive — year-deep 3m store on ext drives + bounded local files.

USER 2026-10-20: "keep saving 3m klines to an external drive whenever connected
but ALWAYS keep them up to date — you can clip the macbook and s1 3m klines to
1800 but you need to have a year of them on ext drive for when we start using
them in the npz files again. So whenever an ext drive is connected you APPEND
the latest 3m klines to every symbol and then clip the macbook ones."

Rules, in order:
  1. APPEND-only, final bars only. The archive copy of every SYMBOL_3m.json
     only ever grows: bars are unioned by timestamp, an existing REAL bar's
     OHLCV is never overwritten (a real-vs-real disagreement is counted, never
     applied), and a write that would narrow the span or drop bars is refused.
     Mark-price SYNTHETIC bars (USER 2026-10-20: flat OHLC, volume 0, written
     by ez_market_data.klines_cache_writeback when a kline is late) are never
     archived as truth — they are skipped — EXCEPT that a real kline arriving
     for a timestamp the archive holds synthetically REPLACES it (counts as
     added). The local fallback itself is untouched: this tool only decides
     what enters the year-deep store.
  2. CLIP second. A local 3m file is clipped to KEEP newest bars only after at
     least one real ext drive confirmed the append this run. No drive connected
     -> no clipping anywhere, nothing is lost.
  3. NO freshness gate. Stale files, maxed markers (klines_maxed_symbols.json),
     inception records — none of them skip a symbol. Every local 3m file is
     archived on every run, no exceptions.

The archive dir is shared with tools/klines_ext_archive.py
(<drive>/binance_archive/klines_archive) so both tools grow the same store.
Mac-only by construction: ext drives are Mac-mounted (tools/data_locations.py
EXT_DRIVES). S1 clipping rides the normal code push once this file ships.

Usage:
    python3 tools/klines_3m_archive.py                 # append + clip (keep 1800)
    python3 tools/klines_3m_archive.py --keep 1200     # clip tighter
    python3 tools/klines_3m_archive.py --no-clip       # archive only
    KLINES_3M_ARCHIVE_EXT=/tmp/d1:/tmp/d2 python3 tools/klines_3m_archive.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCH_SUBDIR = Path("binance_archive") / "klines_archive"
KEEP_DEFAULT = 1800
MIN_FREE_GB = 3.0
EXT_DEFAULTS = ("/Volumes/TOSHIBA_EXT", "/Volumes/SSD2T",
                "/Volumes/E3T", "/Volumes/WD25")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}",
          flush=True)


def _key(ts) -> str:
    return str(ts).replace(" ", "T")[:19]


def load_list(p: Path) -> list:
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    return [b for b in d if isinstance(b, dict) and b.get("timestamp")] \
        if isinstance(d, list) else []


def is_synthetic(bar: dict) -> bool:
    """A mark-price placeholder (USER 2026-10-20 fallback): flat OHLC with no
    volume, written by ez_market_data.klines_cache_writeback when the exchange
    kline is late. The real kline replaces it in the local file when it lands;
    the archive must treat it the same way. Missing volume -> real (never drop
    data on a technicality)."""
    try:
        if float(bar.get("volume", 1) or 0) != 0:
            return False
    except (TypeError, ValueError):
        return False
    try:
        px = {float(bar.get(k, float("nan"))) for k in
              ("open", "high", "low", "close")}
        return len(px) == 1
    except (TypeError, ValueError):
        return False


def _atomic_write_list(p: Path, rows: list) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.atom")
    tmp.write_text(json.dumps(rows, indent=2))
    if not isinstance(json.loads(tmp.read_text()), list):
        tmp.unlink(missing_ok=True)
        raise OSError(f"re-read failed for {p}")
    os.replace(tmp, p)


def real_drives(roots=None) -> list[Path]:
    if roots is None:
        env = os.environ.get("KLINES_3M_ARCHIVE_EXT")
        roots = [Path(r) for r in env.split(":") if r] if env \
            else [Path(r) for r in EXT_DEFAULTS]
    try:
        boot = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        boot = None
    out = []
    for r in roots:
        try:
            if not r.is_dir() or not os.access(r, os.W_OK):
                continue
            if str(r).startswith("/Volumes") and boot is not None \
                    and os.stat(r).st_dev == boot:
                continue  # phantom dir on the boot volume, not a drive
            st = os.statvfs(r)
            if (st.f_bavail * st.f_frsize) / 1e9 < MIN_FREE_GB:
                log(f"  {r}: low space, skipped (never risk an ENOSPC write)")
                continue
            out.append(r)
        except OSError:
            continue
    return out


def append_newer(arch: Path, src_rows: list) -> tuple[int, int, int]:
    """Union src rows into the archive file. Returns (added, total, conflicts)."""
    cur = load_list(arch)
    have = {_key(b["timestamp"]): b for b in cur}
    added, conflicts = 0, 0
    for b in src_rows:
        k = _key(b.get("timestamp", ""))
        if not k:
            continue
        if k not in have:
            if is_synthetic(b):
                continue  # placeholder, not truth — the real bar comes next cycle
            have[k] = {kk: b[kk] for kk in
                       ("timestamp", "open", "high", "low", "close", "volume")
                       if kk in b}
            added += 1
        elif is_synthetic(have[k]) and not is_synthetic(b):
            have[k] = {kk: b[kk] for kk in
                       ("timestamp", "open", "high", "low", "close", "volume")
                       if kk in b}
            added += 1  # the real kline arrived and replaced the placeholder
        elif not is_synthetic(b) and any(have[k].get(f) != b.get(f) for f in
                 ("open", "high", "low", "close", "volume")):
            conflicts += 1  # real-vs-real: archive value stands, only counted
    if not added:
        return 0, len(cur), conflicts
    merged = [have[k] for k in sorted(have)]
    if len(merged) < len(cur):
        return 0, len(cur), conflicts  # never shrink, no exceptions
    _atomic_write_list(arch, merged)
    return added, len(merged), conflicts


def clip_local(local: Path, keep: int) -> tuple[int, int]:
    """Trim a local 3m file to its newest `keep` bars. Returns (before, after)."""
    rows = load_list(local)
    if len(rows) <= keep:
        return len(rows), len(rows)
    rows.sort(key=lambda b: _key(b.get("timestamp", "")))
    _atomic_write_list(local, rows[-keep:])
    return len(rows), keep


def run(repo: Path = REPO, drives=None, keep: int = KEEP_DEFAULT,
        do_clip: bool = True) -> dict:
    cache = Path(repo) / "klines_cache"
    drives = real_drives() if drives is None else list(drives)
    stats = {"drives": [str(d) for d in drives], "scanned": 0,
             "archived_files": 0, "bars_added": 0, "conflicts": 0,
             "clipped_files": 0, "bars_clipped": 0}
    if cache.is_dir():
        files = sorted(cache.glob("*_3m.json"))
    else:
        files = []
    for fp in files:
        if fp.name.startswith(("_", ".")):
            continue
        rows = load_list(fp)
        if not rows:
            continue
        stats["scanned"] += 1
        confirmed = False
        for d in drives:
            try:
                added, _, conf = append_newer(
                    d / ARCH_SUBDIR / fp.name, rows)
                stats["bars_added"] += added
                stats["conflicts"] += conf
                confirmed = True
            except OSError as e:
                log(f"  archive write failed {fp.name} -> {d}: {e}")
        if confirmed:
            stats["archived_files"] += 1
            if do_clip:
                before, after = clip_local(fp, keep)
                if after < before:
                    stats["clipped_files"] += 1
                    stats["bars_clipped"] += before - after
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="append 3m klines to ext drives, "
                                 "then clip local 3m files")
    ap.add_argument("--keep", type=int, default=KEEP_DEFAULT)
    ap.add_argument("--no-clip", action="store_true")
    ap.add_argument("--repo", default=str(REPO))
    args = ap.parse_args(argv)
    stats = run(Path(args.repo), keep=args.keep, do_clip=not args.no_clip)
    log(f"drives={len(stats['drives'])} scanned={stats['scanned']} "
        f"archived={stats['archived_files']} +{stats['bars_added']} bars "
        f"conflicts={stats['conflicts']} clipped={stats['clipped_files']} "
        f"-{stats['bars_clipped']} bars (keep={args.keep})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
