#!/usr/bin/env python3
"""
Archive old backups to Google Drive using service account.
Usage: python3 gdrive_backup_archive.py --dry-run [--config /path/to/creds.json]
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import subprocess

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GDRIVE_AVAILABLE = True
except ImportError:
    GDRIVE_AVAILABLE = False
    print("⚠️  google-auth and google-api-python-client not installed.")
    print("   Install with: pip install google-auth google-auth-oauthlib google-api-python-client")


def get_gdrive_service(creds_path):
    """Authenticate and return Google Drive service."""
    if not GDRIVE_AVAILABLE:
        return None

    if not os.path.exists(creds_path):
        print(f"❌ Credentials not found: {creds_path}")
        return None

    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        return None


def create_gdrive_folder(service, folder_name, parent_id=None):
    """Create a folder in Google Drive, return folder ID."""
    try:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')
    except Exception as e:
        print(f"❌ Failed to create folder: {e}")
        return None


def upload_file_to_gdrive(service, file_path, folder_id):
    """Upload a file to Google Drive folder."""
    try:
        file_name = os.path.basename(file_path)
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }

        media = MediaFileUpload(file_path, resumable=True)
        file_obj = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        return file_obj.get('id')
    except Exception as e:
        print(f"❌ Upload failed for {file_path}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Archive old backups to Google Drive'
    )
    parser.add_argument(
        '--config',
        default='/Users/niels/Downloads/civil-ripple-439101-j3-d50fb6537cdb.json',
        help='Path to service account JSON credentials'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be archived without uploading'
    )
    parser.add_argument(
        '--backup-dir',
        default='/Users/niels/Documents/binance/backups',
        help='Backup directory to archive'
    )
    parser.add_argument(
        '--days-old',
        type=int,
        default=10,
        help='Archive files older than N days (default: 10)'
    )

    args = parser.parse_args()

    print("═══════════════════════════════════════════════════════")
    print("📤 GOOGLE DRIVE BACKUP ARCHIVE")
    print("═══════════════════════════════════════════════════════")
    print("")

    # Check backups directory
    if not os.path.isdir(args.backup_dir):
        print(f"❌ Backup directory not found: {args.backup_dir}")
        return 1

    # Find old files (pre-Oct-16 from today 2026-10-26)
    cutoff = datetime.now() - timedelta(days=args.days_old)
    old_files = []
    total_size = 0

    print(f"🔍 Scanning backups older than {cutoff.strftime('%Y-%m-%d')}...")

    for root, dirs, files in os.walk(args.backup_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))

            if mtime < cutoff:
                size = os.path.getsize(fpath)
                old_files.append((fpath, size))
                total_size += size

    if not old_files:
        print(f"✅ No files older than {args.days_old} days found")
        return 0

    total_gb = total_size / (1024**3)
    print(f"✅ Found {len(old_files)} old files (~{total_gb:.1f} GB)")
    print("")

    if args.dry_run:
        print("[DRY RUN] Sample files to be archived:")
        for fpath, size in old_files[:10]:
            print(f"  • {os.path.basename(fpath)} ({size/(1024**2):.1f} MB)")
        if len(old_files) > 10:
            print(f"  ... and {len(old_files) - 10} more")
        print("")
        print(f"Total to archive: {total_gb:.1f} GB")
        print("(Run without --dry-run to upload to Google Drive)")
        return 0

    # Authenticate
    if not GDRIVE_AVAILABLE:
        print("❌ Google Drive libraries not available. Install with:")
        print("   pip install google-auth google-api-python-client")
        return 1

    service = get_gdrive_service(args.config)
    if not service:
        print("❌ Failed to authenticate with Google Drive")
        return 1

    print("✅ Google Drive authenticated")
    print("")

    # Create archive folder
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    folder_name = f"binance_backups_archive_{timestamp}"

    print(f"📁 Creating Google Drive folder: {folder_name}")
    folder_id = create_gdrive_folder(service, folder_name)

    if not folder_id:
        print("❌ Failed to create Google Drive folder")
        return 1

    print(f"✅ Folder created (ID: {folder_id})")
    print("")
    print("📤 Uploading old backups to Google Drive...")

    uploaded = 0
    failed = 0

    for fpath, size in old_files:
        fname = os.path.basename(fpath)
        size_mb = size / (1024**2)

        print(f"  Uploading {fname} ({size_mb:.1f} MB)...", end=' ', flush=True)
        file_id = upload_file_to_gdrive(service, fpath, folder_id)

        if file_id:
            print("✅")
            uploaded += 1
        else:
            print("❌")
            failed += 1

    print("")
    print("═══════════════════════════════════════════════════════")
    print(f"✅ Archive complete:")
    print(f"   Uploaded: {uploaded} files (~{total_gb:.1f} GB)")
    if failed:
        print(f"   Failed: {failed} files")
    print(f"   Google Drive folder: {folder_name}")
    print("")
    print("📍 You can now safely delete local backups if needed.")
    print("═══════════════════════════════════════════════════════")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
