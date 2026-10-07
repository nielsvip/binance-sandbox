#!/usr/bin/env python3
"""Smart permission fixer for Binance directories"""
import os
import sys
import subprocess
import pwd
import grp
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def fix_permissions_recursive(path: Path, uid: int, gid: int, dir_mode: int = 0o755, file_mode: int = 0o644):
    """Recursively fix permissions for a directory tree"""
    fixed_count = 0
    error_count = 0
    
    try:
        # Fix directory itself
        os.chown(str(path), uid, gid)
        os.chmod(str(path), dir_mode)
        fixed_count += 1
        
        # Walk through all subdirectories and files
        for root, dirs, files in os.walk(path):
            # Fix directories
            for d in dirs:
                dir_path = Path(root) / d
                try:
                    os.chown(str(dir_path), uid, gid)
                    os.chmod(str(dir_path), dir_mode)
                    fixed_count += 1
                except Exception as e:
                    logger.error(f"Failed to fix directory {dir_path}: {e}")
                    error_count += 1
            
            # Fix files
            for f in files:
                file_path = Path(root) / f
                try:
                    os.chown(str(file_path), uid, gid)
                    
                    # Determine appropriate mode based on file extension
                    file_ext = file_path.suffix.lower()
                    if file_ext in ['.sh', '.py', '.bash']:
                        # Preserve execute permissions for scripts
                        os.chmod(str(file_path), 0o755)
                    elif file_ext in ['.json', '.log', '.txt', '.md', '.ini', '.conf']:
                        # Data files get read-only mode
                        os.chmod(str(file_path), file_mode)
                    else:
                        # For other files, check if they're currently executable and preserve that
                        current_stat = os.stat(str(file_path))
                        if current_stat.st_mode & 0o111:  # Has execute bits set
                            os.chmod(str(file_path), 0o755)
                        else:
                            os.chmod(str(file_path), file_mode)
                    
                    fixed_count += 1
                except Exception as e:
                    logger.error(f"Failed to fix file {file_path}: {e}")
                    error_count += 1
                    
    except Exception as e:
        logger.error(f"Failed to fix root directory {path}: {e}")
        error_count += 1
    
    return fixed_count, error_count

def main():
    # Check if running with sufficient privileges
    if os.geteuid() != 0:
        print("⚠️  This script needs to be run with sudo to fix permissions properly.")
        print("   Run: sudo python3 fix_permissions_smart.py")
        print("   Or the systemd service will handle it automatically.")
        return 1
    
    # Get niels user info
    try:
        user_info = pwd.getpwnam("niels")
        uid = user_info.pw_uid
        gid = user_info.pw_gid
    except KeyError:
        logger.error("User 'niels' not found!")
        sys.exit(1)
    
    # Critical directories to fix
    directories = [
        "/home/niels/binance",
        "/home/niels/binance/klines_cache",
        "/home/niels/binance/klines_cache_macbook",
        "/home/niels/binance/klines_cache_gateway",
        "/home/niels/binance/data",
        "/home/niels/binance/backups",
        "/home/niels/logs",
        "/home/niels/.cache"
    ]
    
    total_fixed = 0
    total_errors = 0
    
    print("\n🔧 Starting smart permission fix...\n")
    
    for dir_path in directories:
        path = Path(dir_path)
        if not path.exists():
            logger.warning(f"Creating missing directory: {path}")
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create {path}: {e}")
                continue
        
        print(f"Fixing permissions for: {path}")
        fixed, errors = fix_permissions_recursive(path, uid, gid)
        total_fixed += fixed
        total_errors += errors
        
        if errors == 0:
            print(f"  ✓ Fixed {fixed} items successfully")
        else:
            print(f"  ⚠ Fixed {fixed} items, {errors} errors")
    
    # Also fix specific problematic files if they exist
    problem_files = []
    klines_dir = Path("/home/niels/binance/klines_cache")
    if klines_dir.exists():
        for json_file in klines_dir.glob("*.json"):
            try:
                os.chown(str(json_file), uid, gid)
                os.chmod(str(json_file), 0o644)
                total_fixed += 1
            except Exception as e:
                logger.error(f"Failed to fix {json_file}: {e}")
                total_errors += 1
    
    print(f"\n✅ Permission fix complete!")
    print(f"   Total items fixed: {total_fixed}")
    print(f"   Total errors: {total_errors}")
    
    # Test write access
    print("\n🧪 Testing write access...")
    test_passed = True
    
    for dir_path in ["/home/niels/binance/klines_cache", "/home/niels/logs"]:
        test_file = Path(dir_path) / ".permission_test"
        try:
            # Switch to niels user for test
            os.setegid(gid)
            os.seteuid(uid)
            
            test_file.write_text("test")
            test_file.unlink()
            print(f"  ✓ Write test passed for {dir_path}")
            
            # Switch back to root
            os.seteuid(0)
            os.setegid(0)
        except Exception as e:
            print(f"  ✗ Write test failed for {dir_path}: {e}")
            test_passed = False
            # Make sure we're back to root
            try:
                os.seteuid(0)
                os.setegid(0)
            except:
                pass
    
    if test_passed:
        print("\n✅ All permission tests passed!")
    else:
        print("\n⚠️  Some permission tests failed. You may need to check filesystem issues.")
    
    return 0 if total_errors == 0 and test_passed else 1

if __name__ == "__main__":
    sys.exit(main())
