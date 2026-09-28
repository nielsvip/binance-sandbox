#!/usr/bin/env python3
"""
Restore stocks (tradier) klines from SSD2T archive to Mac
Carefully merges archive data with existing data
S1 will be synced via rsync afterwards
"""
import json
import os
from pathlib import Path
from datetime import datetime

ARCHIVE_SOURCE = Path("/Volumes/SSD2T/binance_archive/klines_cache_tradier")
MAC_TARGET = Path("/Users/niels/Documents/binance/klines_cache/tradier")

MIN_RESTORE_DAYS = 50  # Only restore if archive has 50+ more days

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

def get_coverage(data):
    """Calculate days of history"""
    if not isinstance(data, list) or len(data) < 2:
        return 0
    try:
        first = datetime.fromisoformat(data[0]['timestamp'].replace('Z', '+00:00'))
        last = datetime.fromisoformat(data[-1]['timestamp'].replace('Z', '+00:00'))
        return (last - first).days
    except:
        return 0

def restore_file(archive_path, mac_path):
    """Restore a single symbol from archive"""
    try:
        with open(archive_path) as f:
            archive_data = json.load(f)
    except:
        return None, "archive read error"

    arch_cov = get_coverage(archive_data)

    if mac_path.exists():
        try:
            with open(mac_path) as f:
                existing_data = json.load(f)
        except:
            existing_data = []

        exist_cov = get_coverage(existing_data)

        # Only restore if archive has significantly more
        if arch_cov <= exist_cov + MIN_RESTORE_DAYS:
            return None, f"archive {arch_cov}d vs existing {exist_cov}d"

        # Merge: archive values override existing for same timestamps
        all_bars = {}
        for bar in existing_data:
            ts = bar.get('timestamp', '')
            if ts:
                all_bars[ts] = bar
        for bar in archive_data:
            ts = bar.get('timestamp', '')
            if ts:
                all_bars[ts] = bar

        merged = sorted(all_bars.values(), key=lambda x: x.get('timestamp', ''))
        final_cov = get_coverage(merged)

        mac_path.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{mac_path}.tmp", 'w') as f:
            json.dump(merged, f, indent=2)
        os.replace(f"{mac_path}.tmp", mac_path)

        return (final_cov, arch_cov, exist_cov), "merged"
    else:
        # No existing, restore archive
        mac_path.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{mac_path}.tmp", 'w') as f:
            json.dump(archive_data, f, indent=2)
        os.replace(f"{mac_path}.tmp", mac_path)

        return (arch_cov,), "new"

def main():
    if not ARCHIVE_SOURCE.exists():
        log(f"❌ Archive not found: {ARCHIVE_SOURCE}")
        return

    log(f"🔄 Restoring stocks from SSD2T archive")
    log(f"   Source: {ARCHIVE_SOURCE}")
    log(f"   Target: {MAC_TARGET}")
    log("")

    # Group by timeframe
    from collections import defaultdict
    by_tf = defaultdict(list)

    for f in ARCHIVE_SOURCE.glob("*_*.json"):
        parts = f.name.rsplit('_', 1)
        if len(parts) == 2:
            symbol, tf = parts
            tf = tf.replace('.json', '')
            by_tf[tf].append(f)

    total_restored = 0
    total_merged = 0
    total_skipped = 0

    for tf in ['15m', '1h', '4h', 'D']:
        if tf not in by_tf:
            continue

        files = by_tf[tf]
        log(f"📥 {tf} ({len(files)} symbols)...")

        restored = 0
        merged = 0
        skipped = 0

        for archive_path in sorted(files):
            symbol = archive_path.name.rsplit('_', 1)[0]
            mac_path = MAC_TARGET / archive_path.name

            result, action = restore_file(archive_path, mac_path)

            if result is None:
                skipped += 1
            elif action == "new":
                restored += 1
                days = result[0]
                if restored % 50 == 0:
                    log(f"   ✅ Restored {symbol}: {days}d")
            elif action == "merged":
                merged += 1
                final, arch, exist = result
                if merged % 50 == 0:
                    log(f"   🔄 Merged {symbol}: {final}d")

        log(f"  ✅ Restored: {restored} | 🔄 Merged: {merged} | ⏭️ Skipped: {skipped}")
        total_restored += restored
        total_merged += merged
        total_skipped += skipped

    log("")
    log("=" * 60)
    log("📊 FINAL SUMMARY")
    log("=" * 60)
    log(f"✅ Restored (new):     {total_restored}")
    log(f"🔄 Merged/Enhanced:    {total_merged}")
    log(f"⏭️  Skipped (not better): {total_skipped}")
    log("")
    log("Next: rsync to S1")
    log("  rsync -avz /Users/niels/Documents/binance/klines_cache/tradier/ s1-int:/home/niels/binance-sandbox/klines_cache/tradier/")

if __name__ == '__main__':
    main()
