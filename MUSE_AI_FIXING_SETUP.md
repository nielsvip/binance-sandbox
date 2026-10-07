# Muse AI Fixing Setup — Oct 7 2026

## 🎯 Overview

**All AI-powered fixing is now delegated to Muse (free tier)** instead of Claude, eliminating token drain while preserving AI analysis capabilities.

## 📍 Report Location

**Hourly triage reports:** `/Users/niels/logs/hourly_triage.log` (1.1MB+)

- Runs every hour at :15 (via launchd agent `com.niels.log-triage-hourly.plist`)
- Contains detailed analysis + fixing recommendations
- Format: error grouping, root-cause analysis, "Decisions for you" section, restart recommendations

## ⚙️ How It Works

### Hourly Log Triage Pipeline

```
Launchd trigger (hourly at :15)
    ↓
hourly_log_triage_muse.sh (wrapper)
    ↓
    → Try: /usr/bin/muse (system Muse, aliased with --yolo)
    → Fallback: pure Python analyzer (if Muse unavailable)
    ↓
Process triage prompt
    ↓
5 parallel agents analyze logs (Muse handles parallel spawning)
    ↓
Output → /Users/niels/logs/hourly_triage.log
```

### Key Files

| File | Purpose |
|------|---------|
| `/scripts/hourly_log_triage_muse.sh` | Main wrapper (calls Muse or pure Python) |
| `/scripts/hourly_log_triage_pure.py` | Fallback analyzer (no AI, 0 tokens) |
| `/scripts/hourly_log_triage_prompt.txt` | 5-agent analysis prompt (unchanged) |
| `com.niels.log-triage-hourly.plist` | Launchd scheduler |
| `/Users/niels/logs/hourly_triage.log` | **Report output** |

## 🚀 What Gets Analyzed

**5 parallel agents (Muse spawns these):**

1. **Tradier live trading** — trb/trc order logs
2. **Tradier API + rankings** — API errors, ranking staleness
3. **Crypto orders execution** — ang/flz/fin/men order logs (skip dead inf account)
4. **Price feed health** — market_data/klines/prices (tail only, not full 700MB files)
5. **Position state + reentry** — positions_quick + reentry daemon

**Report includes:**
- NEW errors found this hour (grouped by signature)
- Root-cause analysis per error type
- Safety-checked fixing recommendations (per CLAUDE.md rules)
- Restart recommendations
- Decisions for user (items requiring manual intervention)

## 💰 Token Cost

- **Muse free tier**: Handles this workload with no upfront cost
- **Fallback**: Pure Python analyzer (0 tokens) if Muse unavailable
- **Claude**: Completely removed from this pipeline

## 🔄 Fallback Behavior

If Muse becomes unavailable (uninstalled or credentials revoked):
1. Wrapper script auto-detects missing `muse` command
2. Falls back to pure Python analyzer (no tokens, reports error summaries only)
3. You continue getting hourly error reports (just without AI recommendations)

## ✅ Safety Guarantees

All fixing recommendations still respect:
- ✓ LOCKED_FILES.md (reads before suggesting edits)
- ✓ Mandatory backups (before/YYYYMMDDHHMM.py naming)
- ✓ NO new strategies / NO reversions / NO git reset
- ✓ NO disabling switches to silence errors (find root causes)
- ✓ Compile-check edits before applying

## 📝 Configuration

**To modify triage behavior:**
- Edit `/scripts/hourly_log_triage_prompt.txt` — change what's analyzed
- Edit `/scripts/hourly_log_triage_muse.sh` — change schedule/fallback logic
- Reload launchd: `launchctl unload ~/Library/LaunchAgents/com.niels.log-triage-hourly.plist && launchctl load ...`

**To disable triage entirely:**
```bash
launchctl unload ~/Library/LaunchAgents/com.niels.log-triage-hourly.plist
```

**To force pure Python (no AI):**
```bash
# Edit the wrapper to remove Muse path and call pure Python directly
sed -i 's|$MUSE|/usr/bin/false|g' /Users/niels/Documents/binance/scripts/hourly_log_triage_muse.sh
```

## 📊 History

- **Before Oct 7**: Claude Opus hourly (24 calls/day, 10-15% token budget)
- **Oct 7 19:00Z**: Switched to pure Python (no tokens, no AI analysis)
- **Oct 7 20:15Z**: Deployed Muse wrapper (free AI, full analysis, fallback to pure Python)

## 🔗 Related

- CLAUDE.md — safety rules (READ BEFORE ANY EDIT)
- TOKEN_SAVINGS_SUMMARY.md — overview of all token optimizations
- `/Users/niels/logs/hourly_triage.log` — actual reports
