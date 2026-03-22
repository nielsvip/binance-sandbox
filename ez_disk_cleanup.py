#!/usr/bin/env python3

"""Comprehensive disk cleanup script - manages backups, temp files, logs, and klines"""

import glob
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import Config

cfg=Config()

BASE_PATH=Path(cfg.BASE_PATH)

def format_size(size_bytes):
    for unit in ['B','K','M','G','T']:
        if size_bytes<1024.0: return f"{size_bytes:.1f}{unit}"
        size_bytes/=1024.0
    return f"{size_bytes:.1f}P"

def get_dir_size(path):
    total=0
    try:
        for entry in Path(path).rglob('*'):
            if entry.is_file(): total+=entry.stat().st_size
    except Exception: pass
    return total

def cleanup_backup_before_save():
    """Cleanup BACKUP_BEFORE_SAVE files, keeping hourly/4h/daily/weekly backups"""
    print("\n1. Cleaning BACKUP_BEFORE_SAVE files (keeping hourly/4h/daily/weekly)...")
    total_size=0
    removed=0
    from datetime import timezone
    for account_dir in ['ang','fin','inf','flz','men']:
        account_path=BASE_PATH/account_dir
        if not account_path.exists(): continue
        for side in ['long','short']:
            pattern=str(account_path/f"{side}_positions_BACKUP_BEFORE_SAVE_*.json")
            all_backups=[]
            for backup_path in glob.glob(pattern):
                try:
                    mtime=os.path.getmtime(backup_path)
                    dt=datetime.fromtimestamp(mtime,tz=timezone.utc)
                    all_backups.append((dt,backup_path))
                except Exception: continue
            if len(all_backups)<=1: continue
            all_backups.sort(reverse=True)
            keep_backups=set()
            # Keep last 24 hourly backups (one per hour)
            hourly_kept={}
            for dt,path in all_backups:
                hour_key=(dt.year,dt.month,dt.day,dt.hour)
                if hour_key not in hourly_kept:
                    hourly_kept[hour_key]=path
                    keep_backups.add(path)
            # Keep last 168 4-hourly backups (one per 4 hours, ~28 days)
            four_hourly_kept={}
            for dt,path in all_backups:
                four_hour=dt.hour//4
                four_hour_key=(dt.year,dt.month,dt.day,four_hour)
                if four_hour_key not in four_hourly_kept:
                    four_hourly_kept[four_hour_key]=path
                    keep_backups.add(path)
            # Keep last 90 daily backups (one per day)
            daily_kept={}
            for dt,path in all_backups:
                day_key=(dt.year,dt.month,dt.day)
                if day_key not in daily_kept:
                    daily_kept[day_key]=path
                    keep_backups.add(path)
            # Keep last 52 weekly backups (one per week)
            weekly_kept={}
            for dt,path in all_backups:
                week_key=(dt.year,dt.isocalendar()[1])
                if week_key not in weekly_kept:
                    weekly_kept[week_key]=path
                    keep_backups.add(path)
            # Remove backups not in keep list
            for dt,backup_path in all_backups:
                if backup_path not in keep_backups:
                    try:
                        size=os.path.getsize(backup_path)
                        total_size+=size
                        os.remove(backup_path)
                        removed+=1
                    except Exception as e:
                        print(f"   ⚠️ Failed to remove {backup_path}: {e}")
    print(f"   ✅ Removed {removed} old BACKUP_BEFORE_SAVE files ({format_size(total_size)})")
    return total_size

def cleanup_backups_dir(keep_count=50):
    """Cleanup backups/ directory, keeping only last N files"""
    print("\n2. Cleaning backups/ directory...")
    backups_dir=BASE_PATH/"backups"
    if not backups_dir.exists():
        print("   ⚠️ backups/ directory not found")
        return 0
    total_size=0
    removed=0
    all_backups=sorted([f for f in backups_dir.iterdir() if f.is_file()],key=lambda x:x.stat().st_mtime,reverse=True)
    for old_backup in all_backups[keep_count:]:
        try:
            size=old_backup.stat().st_size
            total_size+=size
            old_backup.unlink()
            removed+=1
        except Exception as e:
            print(f"   ⚠️ Failed to remove {old_backup}: {e}")
    print(f"   ✅ Removed {removed} old backup files ({format_size(total_size)})")
    return total_size

def cleanup_temp_files():
    """Remove all .tmp files"""
    print("\n3. Cleaning temp files...")
    total_size=0
    removed=0
    for tmp_file in BASE_PATH.rglob("*.tmp"):
        try:
            size=tmp_file.stat().st_size
            total_size+=size
            tmp_file.unlink()
            removed+=1
        except Exception as e:
            print(f"   ⚠️ Failed to remove {tmp_file}: {e}")
    print(f"   ✅ Removed {removed} temp files ({format_size(total_size)})")
    return total_size

def cleanup_old_logs(keep_days=7):
    """Cleanup old log files, keeping last N days"""
    print(f"\n4. Cleaning log files older than {keep_days} days...")
    logs_dir=BASE_PATH/"logs"
    if not logs_dir.exists():
        print("   ⚠️ logs/ directory not found")
        return 0
    cutoff_time=time.time()-(keep_days*86400)
    total_size=0
    removed=0
    for log_file in logs_dir.glob("*.log*"):
        try:
            if log_file.stat().st_mtime<cutoff_time:
                size=log_file.stat().st_size
                total_size+=size
                log_file.unlink()
                removed+=1
        except Exception as e:
            print(f"   ⚠️ Failed to remove {log_file}: {e}")
    print(f"   ✅ Removed {removed} old log files ({format_size(total_size)})")
    return total_size

def cleanup_old_klines(keep_days=30):
    """DISABLED — klines are NEVER deleted. We keep all history for deeper backtests."""
    print(f"\n5. Klines cleanup DISABLED — keeping all history for backtests")
    return 0
    print(f"\n5. Cleaning klines files older than {keep_days} days...")
    klines_dirs=[BASE_PATH/"klines_cache",BASE_PATH/"klines_cache_gateway"]
    if hasattr(cfg,'KLINES_CACHE_DIR') and Path(cfg.KLINES_CACHE_DIR).exists():
        klines_dirs.append(Path(cfg.KLINES_CACHE_DIR))
    cutoff_time=time.time()-(keep_days*86400)
    total_size=0
    removed=0
    for klines_dir in klines_dirs:
        if not klines_dir.exists(): continue
        for kline_file in klines_dir.rglob("*.json"):
            try:
                if kline_file.stat().st_mtime<cutoff_time:
                    size=kline_file.stat().st_size
                    total_size+=size
                    kline_file.unlink()
                    removed+=1
            except Exception as e:
                print(f"   ⚠️ Failed to remove {kline_file}: {e}")
    print(f"   ✅ Removed {removed} old klines files ({format_size(total_size)})")
    return total_size

def cleanup_pycache():
    """Remove __pycache__ directories"""
    print("\n6. Cleaning __pycache__ directories...")
    total_size=0
    removed=0
    for pycache_dir in BASE_PATH.rglob("__pycache__"):
        try:
            size=get_dir_size(pycache_dir)
            total_size+=size
            shutil.rmtree(pycache_dir)
            removed+=1
        except Exception as e:
            print(f"   ⚠️ Failed to remove {pycache_dir}: {e}")
    print(f"   ✅ Removed {removed} __pycache__ directories ({format_size(total_size)})")
    return total_size

def cleanup_data_dir():
    """Strictly cleanup data/ directory: nuke winners/losers/tradier, thin everything else"""
    print("\n7. Strictly cleaning data/ directory...")
    data_dir = BASE_PATH / "data"
    if not data_dir.exists(): return 0
    
    total_size = 0
    removed = 0
    
    # 1. NUKE Winners and Losers (not tradier — kept for backtesting)
    for pattern in ["winners_*", "losers_*"]:
        for p in data_dir.glob(pattern):
            try:
                if p.is_file():
                    total_size += p.stat().st_size
                    p.unlink()
                elif p.is_dir():
                    total_size += get_dir_size(p)
                    shutil.rmtree(p)
                removed += 1
            except Exception: pass
    
    # 2. KEEP one file per 15min bin for backtesting (market_data_*.json, tradier_indicators_*.json, etc.)
    all_files = []
    now = time.time()
    for f in data_dir.glob("*"):
        if not f.is_file(): continue
        if any(char.isdigit() for char in f.name) and (f.suffix in ['.json', '.txt']):
            all_files.append((f.stat().st_mtime, f))
    if not all_files:
        print(f"   ✅ Removed {removed} absolute junk files ({format_size(total_size)})")
        return total_size
    all_files.sort(reverse=True)
    keep_list = set()
    bins_captured = set()
    for mtime, path in all_files:
        age = now - mtime
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        if age < 300:
            keep_list.add(path)
            continue
        # Keep exactly one file per 15min slot across all ages — for backtesting
        bin_key = f"15min_{dt.year}{dt.month:02d}{dt.day:02d}_{dt.hour:02d}_{dt.minute // 15}"
        if bin_key not in bins_captured:
            bins_captured.add(bin_key)
            keep_list.add(path)
    # Delete non-kept files
    for mtime, path in all_files:
        if path not in keep_list:
            try:
                total_size += path.stat().st_size
                path.unlink()
                removed += 1
            except Exception: pass
    print(f"   ✅ Removed {removed} data files ({format_size(total_size)})")
    return total_size

def cleanup_plots():
    """Cleanup plots/ and plots_tradier/ directories, keeping only last 12 hours"""
    print("\n8. Cleaning plots directories (keeping last 12 hours)...")
    total_size = 0
    removed = 0
    cutoff_time = time.time() - (12 * 3600)
    
    for plot_dir_name in ["plots", "plots_tradier"]:
        plot_dir = BASE_PATH / plot_dir_name
        if not plot_dir.exists(): continue
        
        # Get all files, sorted by mtime newest first
        files = sorted([f for f in plot_dir.iterdir() if f.is_file()], 
                       key=lambda x: x.stat().st_mtime, reverse=True)
        
        if not files: continue
        
        # Keep at least the 5 most recent plots regardless of age
        keep_count = 5
        
        for i, plot_file in enumerate(files):
            try:
                if i < keep_count or plot_file.stat().st_mtime > cutoff_time:
                    continue
                
                size = plot_file.stat().st_size
                total_size += size
                plot_file.unlink()
                removed += 1
            except Exception: pass
            
    print(f"   ✅ Removed {removed} old plot files ({format_size(total_size)})")
    return total_size

def main():
    print("="*80)
    print("DISK CLEANUP SCRIPT")
    print("="*80)
    print(f"Base path: {BASE_PATH}")
    total_freed=0
    total_freed+=cleanup_backup_before_save()
    total_freed+=cleanup_backups_dir(keep_count=50)
    total_freed+=cleanup_temp_files()
    total_freed+=cleanup_old_logs(keep_days=7)
    total_freed+=cleanup_old_klines(keep_days=30)
    total_freed+=cleanup_pycache()
    total_freed+=cleanup_data_dir()
    total_freed+=cleanup_plots()
    print("\n"+"="*80)
    print(f"TOTAL SPACE FREED: {format_size(total_freed)}")
    print("="*80)
    print("\n📊 CURRENT DISK USAGE:")
    dirs_to_check=[("backups",BASE_PATH/"backups"),("logs",BASE_PATH/"logs"),("klines_cache",BASE_PATH/"klines_cache"),("klines_cache_gateway",BASE_PATH/"klines_cache_gateway"),("data",BASE_PATH/"data"),("plots",BASE_PATH/"plots"),("plots_tradier",BASE_PATH/"plots_tradier")]
    for name,path in dirs_to_check:
        if path.exists():
            size=get_dir_size(path)
            print(f"   {name}: {format_size(size)}")
    print("\n✅ Cleanup complete!")

if __name__=="__main__":
    main()
