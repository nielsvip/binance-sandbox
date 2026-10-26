#!/bin/bash
# Archive old backups to Google Drive using rclone (efficient for large transfers)
# Usage: bash archive_backups_rclone.sh [--dry-run] [--config-json /path/to/creds.json]

set -e

BACKUP_DIR="/Users/niels/Documents/binance/backups"
CREDS_JSON="${2:--/Users/niels/Downloads/civil-ripple-439101-j3-d50fb6537cdb.json}"
DRY_RUN=false
DAYS_OLD=10

if [[ "$1" == "--dry-run" ]]; then
  DRY_RUN=true
fi

echo "═══════════════════════════════════════════════════════"
echo "📤 BACKUP ARCHIVE TO GOOGLE DRIVE (rclone)"
echo "═══════════════════════════════════════════════════════"
echo ""

# Check if rclone is installed
if ! command -v rclone &> /dev/null; then
  echo "⚠️  rclone not found. Installing..."
  brew install rclone || {
    echo "❌ Failed to install rclone. Try: brew install rclone"
    exit 1
  }
fi

RCLONE_VERSION=$(rclone version | head -1)
echo "✅ Using: $RCLONE_VERSION"
echo ""

# Check credentials
if [ ! -f "$CREDS_JSON" ]; then
  echo "❌ Credentials not found: $CREDS_JSON"
  exit 1
fi

echo "✅ Using credentials: $CREDS_JSON"
echo ""

# Create temporary rclone config
RCLONE_CONFIG="/tmp/rclone_gdrive_$(date +%s).conf"

cat > "$RCLONE_CONFIG" << 'EOF'
[binance-gdrive]
type = drive
service_account_file = SERVICE_ACCOUNT_PATH
team_drive =
EOF

# Replace placeholder with actual path
sed -i '' "s|SERVICE_ACCOUNT_PATH|$CREDS_JSON|g" "$RCLONE_CONFIG"

echo "🔍 Scanning for old backups (>$DAYS_OLD days)..."
echo ""

# Count old files
OLD_COUNT=$(find "$BACKUP_DIR" -mtime +$DAYS_OLD -type f 2>/dev/null | wc -l)
OLD_SIZE=$(find "$BACKUP_DIR" -mtime +$DAYS_OLD -type f 2>/dev/null -exec du -c {} \; | tail -1 | awk '{print $1}')
OLD_SIZE_GB=$((OLD_SIZE / 1024 / 1024 / 1024))

if [ "$OLD_COUNT" -eq 0 ]; then
  echo "✅ No backups older than $DAYS_OLD days found"
  rm -f "$RCLONE_CONFIG"
  exit 0
fi

echo "Found: $OLD_COUNT files (~${OLD_SIZE_GB}GB)"
echo ""

if $DRY_RUN; then
  echo "[DRY RUN] Sample old files:"
  find "$BACKUP_DIR" -mtime +$DAYS_OLD -type f | head -10 | sed 's/^/  • /'
  echo "  ... and $((OLD_COUNT - 10)) more"
  echo ""
  echo "To upload: rclone sync --config $RCLONE_CONFIG $BACKUP_DIR binance-gdrive:binance_backups_archive"
  rm -f "$RCLONE_CONFIG"
  exit 0
fi

# Create remote folder name
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REMOTE_FOLDER="binance_backups_archive_$TIMESTAMP"

echo "📤 Uploading to Google Drive: $REMOTE_FOLDER"
echo "   This may take several minutes for $OLD_SIZE_GB GB..."
echo ""

# Run rclone sync for old files
# Note: rclone doesn't have easy --mtime filtering, so we'll copy each old file
UPLOAD_COUNT=0
UPLOAD_FAILED=0

while IFS= read -r fpath; do
  fname=$(basename "$fpath")

  if ! rclone --config "$RCLONE_CONFIG" copyto "$fpath" "binance-gdrive:$REMOTE_FOLDER/$fname" 2>/dev/null; then
    ((UPLOAD_FAILED++))
    echo "  ❌ Failed: $fname"
  else
    ((UPLOAD_COUNT++))
    echo "  ✅ Uploaded: $fname"
  fi
done < <(find "$BACKUP_DIR" -mtime +$DAYS_OLD -type f)

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ Archive complete:"
echo "   Uploaded: $UPLOAD_COUNT files"
if [ $UPLOAD_FAILED -gt 0 ]; then
  echo "   Failed: $UPLOAD_FAILED files"
fi
echo "   Remote folder: $REMOTE_FOLDER"
echo ""
echo "📍 Old local backups can now be deleted:"
echo "   find /Users/niels/Documents/binance/backups -mtime +$DAYS_OLD -delete"
echo "═══════════════════════════════════════════════════════"

# Cleanup
rm -f "$RCLONE_CONFIG"
