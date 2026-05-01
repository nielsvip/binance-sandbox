"""tradier_oi_history_recorder — daily snapshot of stocks_oi_cache → history.

READ-ONLY. Never places options orders. Builds a historical OI dataset by
copying the current snapshot of every {sym}.json in data/stocks_oi_cache/ to
data/stocks_oi_history/{YYYY-MM-DD}/{sym}.json once per day.

After ~30+ days of daily snapshots, the v8_quick options-OI gate can use the
NEAREST-BY-DATE historical snapshot per backtest bar, instead of today's
stale walls. This is the path to making the OI signal backtest-able.

Run via cron once daily (e.g., 23:50 UTC after end-of-day):
    50 23 * * * /home/niels/.conda/envs/binance_env/bin/python /home/niels/binance-sandbox/tradier_oi_history_recorder.py

Or run manually any time. Idempotent: if today's directory already exists,
script exits without overwriting (use --force to overwrite).
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default="data/stocks_oi_cache",
                    help="source cache directory")
    ap.add_argument("--history-dir", default="data/stocks_oi_history",
                    help="destination history directory")
    ap.add_argument("--date", default=None, help="override date (YYYY-MM-DD); default = today UTC")
    ap.add_argument("--force", action="store_true", help="overwrite if today's dir exists")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent
    src = (repo / args.cache_dir) if not Path(args.cache_dir).is_absolute() else Path(args.cache_dir)
    dst_root = (repo / args.history_dir) if not Path(args.history_dir).is_absolute() else Path(args.history_dir)
    if not src.exists():
        print(f"[OI_HIST] source {src} missing — abort.", file=sys.stderr)
        return 2

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dst = dst_root / date_str
    if dst.exists() and not args.force:
        print(f"[OI_HIST] {dst} already exists — skip (use --force to overwrite)")
        return 0

    if args.dry_run:
        files = sorted(src.glob("*.json"))
        print(f"[OI_HIST] dry-run: would copy {len(files)} files from {src} → {dst}")
        return 0

    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.json"))
    n_copied = 0
    n_stale = 0
    stale_max_h = 24.0  # don't snapshot stale-fetched cache (fetcher missed yesterday → today's snapshot would be a duplicate)
    now = time.time()
    for f in files:
        try:
            with f.open() as fp:
                doc = json.load(fp)
            ts = float(doc.get("ts") or 0)
            age_h = (now - ts) / 3600.0 if ts > 0 else 9999
            if age_h > stale_max_h:
                n_stale += 1
                continue
            shutil.copy2(f, dst / f.name)
            n_copied += 1
        except Exception as e:
            print(f"[OI_HIST] {f.name}: skip — {type(e).__name__}: {e}", file=sys.stderr)
    # Write index manifest for fast lookup
    manifest = {
        "date": date_str,
        "ts_utc": now,
        "n_copied": n_copied,
        "n_stale_skipped": n_stale,
        "n_total_in_src": len(files),
        "src": str(src),
    }
    (dst / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[OI_HIST] {date_str}: copied {n_copied}/{len(files)} ({n_stale} stale skipped) → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
