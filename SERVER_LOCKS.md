# SERVER LOCKS — ALL CLAUDE AGENTS MUST READ THIS BEFORE TOUCHING SERVERS

## ⚠️ MANDATORY: Check before ANY server action (ssh, scp, kill, nohup, screen)

**If a lockfile exists on a server, DO NOT kill processes, start new scripts, or interfere.**

### Current Locks

| Server | Lock | Owner | Task | Started | ETA |
|--------|------|-------|------|---------|-----|
| **157.180.125.52 (S1)** | none (advisory) | crypto_funding_oi_ON | autonomous_search 24sym × 1.32yr crypto funding_oi_ON_w50400 (seed 50400) — vec_mass loops PAUSED to free RAM | 2026-04-28 00:19 UTC | open-ended |
| **204.168.181.211 (S2)** | none (advisory) | tradier_funding_oi_ON | autonomous_search 114sym × 2.32yr tradier funding_oi_ON_w70100 (seed 70100) | 2026-04-28 00:11 UTC | open-ended |

### Notes (2026-04-28 funding_oi_ON sweep launch)

- **iter_npz streaming fix** (md5 `9f5e70fc58200c879c786036d635ddab`) deployed across MB+S1+S2 — cuts NPZ memory from view-aliased full-file holds to deep slice copies.
- **Crypto S1 RAM ceiling**: ~24 syms × 1.32 yr is the largest scope that fits S1's 30 GB with funding_oi_ON baseline (which allocates more derived arrays than `genuine` baseline). Larger scopes OOM at ~30.5 GB anon-rss.
- **vec_mass loops on S1 (PIDs 137338 & 895816 + children) WERE KILLED** to free RAM for the funding_oi_ON sweep. To restart: `nohup bash run_vec_mass_loop.sh crypto 3 5000 c_pri &` etc.
- **Tradier baseline JSON patch**: `tradier_3p4361_funding_oi_ON_20260427.json` has v8-shared `FUNDING_GATE_ENABLED=false` and `OI_CONFIRM_ENABLED=false` (vec engine can't read stocks_oi_cache). New TRADIER-suffixed knobs (`FUNDING_GATE_ENABLED_TRADIER`, `OI_CONFIRM_ENABLED_TRADIER`) are ON for live tradier_manage.py P/C ratio analogue but don't affect vec sim.

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
