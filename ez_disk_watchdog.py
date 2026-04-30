#!/usr/bin/env python3
"""Disk space watchdog — monitors /home/niels partition, triggers staged cleanup at >90% usage.
Runs as a standalone daemon. Never touches: position files, history JSONL, symbols.json, .env.gpg.
Cleanup cascade (each stage runs until disk < 85%):
  Stage 0 — quarantine dirs (data/quarantine_*) — explicitly marked for deletion
  Stage 1 — old logs (>3 days)
  Stage 2 — old plots (>4h, keep at least 3)
  Stage 3 — old backups (keep last 10 per stem)
  Stage 4 — clip klines JSON to 1200 bars (last resort, server only)
"""
import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_DIR = Path("/home/niels/logs")
BASE = Path("/home/niels/binance")
LOGS_DIR = Path("/home/niels/logs")
KLINES_DIR = BASE / "klines_cache"
PLOTS_DIRS = [BASE / "plots", BASE / "plots_tradier"]
BACKUPS_DIR = BASE / "backups"
CHECK_INTERVAL = 300  # 5 minutes
TRIGGER_PCT = 90.0
TARGET_PCT = 85.0
PROTECTED = {"long_positions.json", "short_positions.json", "symbols.json", "symbol_configs.json", ".env.gpg", ".env"}
logging.basicConfig(level=logging.INFO, format="%(asctime)s [disk_watchdog] %(levelname)s %(message)s", handlers=[logging.FileHandler(str(LOG_DIR / "ez_disk_watchdog.log")), logging.StreamHandler()])
logger = logging.getLogger("disk_watchdog")

def format_size(b):
    for u in ["B", "K", "M", "G", "T"]:
        if b < 1024.0: return f"{b:.1f}{u}"
        b /= 1024.0
    return f"{b:.1f}P"

def disk_pct():
    u = shutil.disk_usage("/home/niels")
    return u.used / u.total * 100

def is_protected(path: Path) -> bool:
    if path.name in PROTECTED: return True
    parts = path.parts
    if "history" in parts: return True
    for p in parts:
        if p in ("ang", "inf", "flz", "men", "fin", "trb", "trc"):
            if path.name in PROTECTED: return True
    return False

def stage0_quarantine_dirs() -> int:
    """Delete quarantine_* dirs under data/ — explicitly flagged for deletion on disk pressure."""
    freed = 0
    for base in [BASE, BASE.parent / "binance-sandbox"]:
        data_dir = base / "data"
        if not data_dir.exists():
            continue
        for q in sorted(data_dir.glob("quarantine_*")):
            if not q.is_dir():
                continue
            try:
                sz = sum(f.stat().st_size for f in q.rglob("*") if f.is_file())
                shutil.rmtree(q)
                freed += sz
                logger.info(f"[S0] deleted quarantine dir {q.name} ({format_size(sz)})")
            except Exception as e:
                logger.warning(f"[S0] failed {q}: {e}")
    logger.info(f"[S0] stage0_quarantine_dirs freed {format_size(freed)}")
    return freed

def stage1_old_logs(max_days=3) -> int:
    """Delete log files older than max_days."""
    freed = 0
    cutoff = time.time() - max_days * 86400
    for f in LOGS_DIR.glob("*.log*"):
        try:
            if f.stat().st_mtime < cutoff:
                sz = f.stat().st_size
                f.unlink()
                freed += sz
                logger.info(f"[S1] deleted old log {f.name} ({format_size(sz)})")
        except Exception as e:
            logger.warning(f"[S1] failed {f}: {e}")
    logger.info(f"[S1] stage1_old_logs freed {format_size(freed)}")
    return freed

def stage2_old_plots(max_hours=4) -> int:
    """Delete plots older than max_hours, keep at least 3 per dir."""
    freed = 0
    cutoff = time.time() - max_hours * 3600
    for plot_dir in PLOTS_DIRS:
        if not plot_dir.exists(): continue
        files = sorted([f for f in plot_dir.iterdir() if f.is_file()], key=lambda x: x.stat().st_mtime, reverse=True)
        for i, f in enumerate(files):
            try:
                if i < 3: continue
                if f.stat().st_mtime < cutoff:
                    sz = f.stat().st_size
                    f.unlink()
                    freed += sz
            except Exception as e:
                logger.warning(f"[S2] failed {f}: {e}")
    logger.info(f"[S2] stage2_old_plots freed {format_size(freed)}")
    return freed

def stage3_old_backups(keep=10) -> int:
    """Keep last N files per stem in backups/."""
    freed = 0
    if not BACKUPS_DIR.exists(): return 0
    stems: dict[str, list] = {}
    for f in BACKUPS_DIR.iterdir():
        if not f.is_file(): continue
        base_stem = f.name.split("_before_")[0] if "_before_" in f.name else f.stem[:20]
        stems.setdefault(base_stem, []).append(f)
    for stem, files in stems.items():
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                sz = old.stat().st_size
                old.unlink()
                freed += sz
                logger.info(f"[S3] deleted old backup {old.name} ({format_size(sz)})")
            except Exception as e:
                logger.warning(f"[S3] failed {old}: {e}")
    logger.info(f"[S3] stage3_old_backups freed {format_size(freed)}")
    return freed

def stage4_clip_klines(target_bars=1200) -> int:
    """Last resort: clip kline JSON files to target_bars. Never clips position files. On server, only clip 15m+ TFs (preserve 1m/3m/5m for backtesting)."""
    freed = 0
    clipped = 0
    _is_server = Path("/home/niels/binance").exists()
    if not KLINES_DIR.exists(): return 0
    for json_file in KLINES_DIR.rglob("*.json"):
        if is_protected(json_file): continue
        # Protect ALL timeframes with deep history from clipping
        _protected_tfs = ("1m", "3m", "5m", "15m", "1h", "4h", "D", "W", "M")
        if any(json_file.name.endswith(f"_{tf}.json") for tf in _protected_tfs):
            continue
        try:
            orig_size = json_file.stat().st_size
            with open(json_file, "r") as fh:
                data = json.load(fh)
            if not isinstance(data, list) or len(data) <= target_bars: continue
            clipped_data = data[-target_bars:]
            tmp = json_file.with_suffix(".tmp")
            with open(tmp, "w") as fh:
                json.dump(clipped_data, fh)
            tmp.replace(json_file)
            new_size = json_file.stat().st_size
            freed += orig_size - new_size
            clipped += 1
        except Exception as e:
            logger.warning(f"[S4] failed {json_file}: {e}")
    logger.info(f"[S4] stage4_clip_klines clipped {clipped} files, freed {format_size(freed)}")
    return freed

def run_cleanup():
    pct = disk_pct()
    logger.warning(f"Disk at {pct:.1f}% — starting cleanup cascade (target <{TARGET_PCT}%)")
    for stage_fn in [stage0_quarantine_dirs, stage1_old_logs, stage2_old_plots, stage3_old_backups, stage4_clip_klines]:
        stage_fn()
        pct = disk_pct()
        logger.info(f"After {stage_fn.__name__}: disk at {pct:.1f}%")
        if pct < TARGET_PCT:
            logger.info(f"Disk now at {pct:.1f}% — below target, stopping cleanup")
            return
    logger.error(f"Cleanup exhausted all stages — disk still at {pct:.1f}%. Manual intervention required.")

def main():
    logger.info(f"Disk space watchdog started. Trigger={TRIGGER_PCT}%, Target={TARGET_PCT}%, Interval={CHECK_INTERVAL}s")
    while True:
        try:
            pct = disk_pct()
            if pct >= TRIGGER_PCT:
                run_cleanup()
            else:
                logger.debug(f"Disk OK: {pct:.1f}%")
        except Exception as e:
            logger.error(f"Watchdog loop error: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
