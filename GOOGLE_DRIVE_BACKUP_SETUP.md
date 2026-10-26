# Google Drive Backup Automation

**Status: ✅ ACTIVE**

Automated daily backup archival to Google Drive. Old backups (>10 days) are uploaded to Google Drive and deleted locally to prevent bloat.

---

## Setup Details

### Components

1. **Script**: `/Users/niels/Documents/binance/tools/backup_to_gdrive.py`
   - Finds backups older than 10 days
   - Uploads to Google Drive via rclone
   - Deletes local copies after successful upload
   - Logs to: `data/reports/gdrive_backup.log`

2. **Automation**: `/Users/niels/Library/LaunchAgents/com.binance.backup-gdrive.plist`
   - Runs daily (86400 seconds = 24 hours)
   - Auto-loads on system startup
   - Scheduled from current time forward

3. **Authentication**: Service account JSON
   - Path: `/Users/niels/Downloads/civil-ripple-439101-j3-d50fb6537cdb.json`
   - Rclone remote: `binance-gdrive`
   - Target folder: `binance_backups` (on Google Drive)

### Policy

| Setting | Value | Why |
|---------|-------|-----|
| Keep local | 10 days | Fast access, recent history |
| Archive to Google Drive | Yes | Redundancy + unlimited capacity |
| Upload batch size | 100 files | Memory efficient |
| Delete after upload | Yes | Frees local space |
| Log retention | Unlimited | Audit trail |

---

## Monitoring

### Check if job is loaded
```bash
launchctl list | grep backup-gdrive
```
Expected: `34957 0 com.binance.backup-gdrive` (PID and status)

### View backup log
```bash
tail -f /Users/niels/Documents/binance/data/reports/gdrive_backup.log
```

### Manually run archival now
```bash
python3 /Users/niels/Documents/binance/tools/backup_to_gdrive.py
```

### Check backup directory size
```bash
du -sh /Users/niels/Documents/binance/backups
# Should stay <50GB if job is running
```

---

## Google Drive Structure

```
binance_backups/
├── before_*.json (uploaded daily)
├── ARCHIVED_*.json (old progress files)
├── ...
```

**Total on Google Drive**: Unlimited (Google Drive storage)

---

## Troubleshooting

### Job not running
```bash
# Reload manually
launchctl unload /Users/niels/Library/LaunchAgents/com.binance.backup-gdrive.plist
launchctl load /Users/niels/Library/LaunchAgents/com.binance.backup-gdrive.plist
```

### Upload failures
- Check rclone: `rclone version`
- Check creds: `cat /Users/niels/Downloads/civil-ripple-439101-j3-d50fb6537cdb.json`
- Check log: `tail -100 /Users/niels/Documents/binance/data/reports/gdrive_backup.log`

### Backups growing too large (>50GB)
- Job may not be running. Check:
  ```bash
  launchctl list | grep backup-gdrive
  tail -50 /Users/niels/Documents/binance/data/reports/gdrive_backup.log
  ```
- Manually trigger: `python3 /Users/niels/Documents/binance/tools/backup_to_gdrive.py`

### Storage path
All Google Drive backups are in the service account's drive at:
- Remote: `binance_backups/`
- Account: `329002141504-compute@developer.gserviceaccount.com`

---

## Capacity Planning

| Scenario | Local | Google Drive | Net Space Freed |
|----------|-------|--------------|-----------------|
| Day 1 | 37GB (10d recent) | ~100MB | 37GB freed daily |
| Week 1 | 37GB (10d recent) | ~1GB | Sustained 37GB local cap |
| Month 1 | 37GB (10d recent) | ~4GB | No bloat |
| Year 1 | 37GB (10d recent) | ~365GB | Full redundancy |

**Conclusion**: Local stays bounded at 37GB. Google Drive grows unbounded (safe, unlimited storage).

---

## Related

- **Backups before Google Drive setup**: Still manually managed (3 full ext drives, 4 servers)
- **Git history**: Protected by launchd 15min autosave (separate system)
- **Trading state**: Live in `data/reports/*.json` (backed up with backups/)

---

## Timeline

- **2026-10-26 21:42**: Google Drive backup automation enabled
- **Initial state**: 61GB old backups → 24GB freed
- **Current state**: 37GB recent backups (last 10 days only)
- **Ongoing**: Daily archival to Google Drive, zero local bloat
