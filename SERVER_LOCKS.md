# SERVER LOCKS — ALL CLAUDE AGENTS MUST READ THIS BEFORE TOUCHING SERVERS

## ⚠️ MANDATORY: Check before ANY server action (ssh, scp, kill, nohup, screen)

**If a lockfile exists on a server, DO NOT kill processes, start new scripts, or interfere.**

### Current Locks

| Server | Lock | Owner | Task | Started | ETA |
|--------|------|-------|------|---------|-----|
| **204.168.181.211** | `/home/niels/SWEEP_RUNNING` | tradier_48h_sweep | 408-config V5 full tradier sweep | 2026-04-01 20:11 UTC | ~16h |
| **157.180.125.52** | `/home/niels/SWEEP_RUNNING` | tradier_48h_sweep_s1 | 204-config V5 full tradier sweep (noloss 0.5/2.0/5.0) | PENDING REBOOT | ~7h after start |

### Rules

1. **Before running ANYTHING on a server**: `cat /home/niels/SWEEP_RUNNING` — if it exists, STOP.
2. **Before killing ANY python process**: check if it's part of a sweep (`ps aux | grep backtest_v5`).
3. **NEVER `killall python3`** on a server without checking locks first.
4. **Screen sessions named `sweep48h`** are PROTECTED — do not kill.
5. **If you need the server**: add your request to this file and WAIT. Do NOT preempt.

### How to Check

```bash
ssh niels@204.168.181.211 'cat /home/niels/SWEEP_RUNNING 2>/dev/null || echo "UNLOCKED"'
ssh niels@157.180.125.52 'cat /home/niels/SWEEP_RUNNING 2>/dev/null || echo "UNLOCKED"'
```

### Coordination Protocol

- Only ONE long-running task per server at a time.
- Lock is created at start, removed on completion.
- If a lock is stale (process dead but lock remains): check `screen -ls` and `ps aux` before removing.
