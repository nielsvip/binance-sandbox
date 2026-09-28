# KLINES INFRASTRUCTURE FIX — 2026-09-28

## 🎯 NEW ARCHITECTURE

```
        API (Binance)
            ↓
        S1 (157.180.125.52 / 10.0.0.3)
    ┌─────┴──────────────┐
    ↓                    ↓
 FULL HISTORY        (backup to)
 (1+ year @ 15m+)    Gateway
    ↓                    ↓
    └────────────────┬───┘
                     ↓
                Mac (trimmed for trading)
                    1200-1800 bars
```

## ✅ WHAT WAS CHANGED

### 1. **S1: Aggressive 1-Year Backfill (RUNNING NOW)**
   - **Script**: `backfill_klines_1year_aggressive.py` (just deployed)
   - **Status**: Process ID 1782193 (started 04:50:xx on s1-int)
   - **Duration**: 2-4 hours (rate-limited, respects API)
   - **Target**: 365+ days for 15m, 1h, 4h, D timeframes
   - **Environment**: Detected as 'server' ✓ (no trimming)

### 2. **Mac → S1 rsync: DISABLED**
   - **Reason**: Was syncing trimmed data from Mac to S1 (data corruption)
   - **Killed**: PID 29548 (Mac→S1 rsync)
   - **Result**: S1 now independent, keeps full history

### 3. **Mac klines source: Changed from S1 → Gateway**
   - **File**: `/Users/niels/Documents/binance/rsync_klines_from_server.sh`
   - **Changed**: `SRC="s1-int:/home/niels/binance/klines_cache/"` 
   - **To**: `SRC="gateway-internal:/home/niels/binance/klines_cache/"`
   - **Result**: Mac now pulls from Gateway (backup source, not primary)
   - **Reason**: Isolates trading klines (Mac 1200-1800) from backtest klines (S1 1+ year)

### 4. **ez_klines.py on S1: PAUSED**
   - **Reason**: Backfill script in flight, don't interfere
   - **When to restart**: After backfill completes (check logs)
   - **Auto-behavior**: Will NOT trim on S1 (env='server') ✓

---

## 📊 CURRENT STATE

### **S1 (source of truth for backtesting)**
- **Status**: Backfill running (PID 1782193)
- **Est. completion**: 2-4 hours from 04:50
- **Target coverage**: 365+ days @ 15m+
- **Klines location**: `/home/niels/binance-sandbox/klines_cache/`
- **Environment**: 'server' (no trimming) ✓

### **Gateway (backup for Mac trading)**
- **Status**: Has latest klines (synced from Mac before)
- **Coverage**: Unknown (assume 18-20 days like Mac was)
- **Role**: Fallback source for Mac if S1 unavailable

### **Mac (trading only)**
- **Status**: Pulling from Gateway
- **Coverage**: ~1200-1800 bars per timeframe (designed for trading lag)
- **Trimming**: Enabled (by design: `env != 'server'` in ez_klines.py)
- **Location**: `/Users/niels/Documents/binance/klines_cache/`

---

## 🔧 MONITORING & NEXT STEPS

### **Real-time Backfill Progress**
```bash
# Check if running
ssh s1-int 'ps aux | grep backfill_klines_1year | grep -v grep'

# Live log tail
ssh s1-int 'tail -f /home/niels/logs/backfill_1year_*.log'

# Check current size
ssh s1-int 'du -sh /home/niels/binance-sandbox/klines_cache'

# Sample coverage (after ~30 min)
ssh s1-int 'python3 -c "
import json, os
from datetime import datetime
from pathlib import Path
files = list(Path(\"/home/niels/binance-sandbox/klines_cache\").glob(\"*_15m.json\"))
for f in files[:3]:
    try:
        d = json.load(open(f))
        if len(d) > 1:
            days = (datetime.fromisoformat(d[-1][\"timestamp\"].replace(\"Z\",\"+00:00\")) - datetime.fromisoformat(d[0][\"timestamp\"].replace(\"Z\",\"+00:00\"))).days
            print(f\"{f.name}: {len(d)} bars, {days} days\")
    except: pass
"'
```

### **After Backfill Completes (2-4 hours)**
1. **Restart ez_klines.py** on S1 (for continuous updates)
   ```bash
   ssh s1-int 'nohup python3 /home/niels/binance-sandbox/ez_klines.py > /home/niels/logs/ez_klines_$(date +%Y%m%d_%H%M%S).log 2>&1 &'
   ```

2. **Verify S1 coverage**
   ```bash
   ssh s1-int 'python3 - << PY'
   import json
   from pathlib import Path
   from datetime import datetime
   base = Path('/home/niels/binance-sandbox/klines_cache')
   for tf in ['15m','1h','4h','D']:
       files = list(base.glob(f'*_{tf}.json'))
       coverage = []
       for f in files[:20]:
           try:
               d = json.load(open(f))
               if len(d) > 1:
                   days = (datetime.fromisoformat(d[-1]['timestamp'].replace('Z','+00:00')) - 
                           datetime.fromisoformat(d[0]['timestamp'].replace('Z','+00:00'))).days
                   coverage.append(days)
           except: pass
       if coverage:
           avg = sum(coverage)/len(coverage)
           print(f'{tf}: {len(files)} files, avg {avg:.0f} days')
   PY
   ```

3. **Run v15_pilot.py** to test backtest data
   ```bash
   # On Mac, try a test sweep to confirm NPZ generation works
   python3 v15_pilot.py --symbol BTCUSDT --sides LONG --mode test
   ```

---

## 🚨 TROUBLESHOOTING

| Symptom | Cause | Fix |
|---------|-------|-----|
| S1 backfill slow | API rate limiting | Normal, let it run. ~400 symbols × 4 TF = 1600 fetches |
| Backfill crashes | Bad JSON write | Check `/home/niels/logs/backfill_1year_*.log` for errors |
| S1 still has 18d | Backfill not running | Check `ps aux \| grep backfill_klines_1year` |
| Mac klines empty | Gateway down | Check `rsync_klines_from_server.sh` log in `/Users/niels/logs/` |
| v15_pilot DATA_ERROR | NPZ still short | Wait for backfill to finish, then restart ez_klines.py on S1 |

---

## 📝 SUMMARY OF CHANGES

| Component | Before | After | Why |
|-----------|--------|-------|-----|
| S1 klines | 18d (clipped via rsync) | 365+ d (backfill) | Need 1yr for NPZ generation |
| Mac source | S1 (full history) | Gateway (trimmed copy) | Isolate trading from backtest |
| Mac trimming | N/A | Still trimmed to 1200-1800 | Correct for trading efficiency |
| ez_klines on S1 | Not running | Will run after backfill | Maintain/extend history |
| rsync direction | Mac → S1 | S1 → (implied, on demand) | S1 is authority, Mac is consumer |

---

## ⏱️ ETA FOR FULL FUNCTIONALITY

- **Now (04:50)**: Backfill started on S1
- **In 2-4 hours (~07:00-08:50)**: Backfill completes, S1 has 365+ days
- **At completion**: Restart ez_klines.py on S1
- **Verification**: Run v15_pilot.py test sweep
- **Full production**: ~8:00-09:00 AM (awaiting user confirmation)

