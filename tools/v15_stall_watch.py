#!/usr/bin/env python3
"""v15_stall_watch — every 10 min on s1: flag stalled/blocked pilots on s1/s2/s5 (log idle >20 min while its pilot runs, Traceback in the last 60 min,
BadZip/DATA_ERROR/DEFAULTS-GATE refusals). Writes ~/binance-sandbox/logs/stall_watch.jsonl (one JSON line per run) and stall_watch_alert.log. Detection only."""
import json, subprocess, time
HOSTS = {"s1": None, "s2": "s2", "s5": "s5"}
SCRIPT = r'''
now=$(date +%s)
for f in $(find /tmp -maxdepth 1 -name "sweep_*_30D.log" -mmin -180); do
  ss=$(basename $f _30D.log | sed "s/^sweep_//"); newest=$(ls -t /tmp/sweep_${ss}_*.log /tmp/v14_heartbeat_${ss}.txt 2>/dev/null | head -1); age=$(( (now-$(stat -c %Y $newest))/60 ))
  run=$(pgrep -f "[s]ym-side $ss" | wc -l)
  tb=$(tail -n 400 $f | grep -c "^Traceback")
  bad=$(tail -n 400 $f | grep -ciE "BadZip|DATA_ERROR|DEFAULTS-GATE.*(refus|REFUS)")
  if [ "$run" -gt 0 ] && [ "$age" -gt 20 ]; then echo "STALLED $ss idle=${age}m"; fi
  if [ "$tb" -gt 0 ] && [ "$age" -lt 60 ]; then echo "TRACEBACK $ss age=${age}m $(grep -A14 '^Traceback' $f | grep -E '^[A-Za-z]*(Error|Exception)' | tail -1 | cut -c1-120)"; fi
  if [ "$bad" -gt 0 ] && [ "$age" -lt 60 ]; then echo "BLOCKED $ss age=${age}m"; fi
done
'''
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hosts": {}}
for name, ssh in HOSTS.items():
    cmd = ["bash", "-c", SCRIPT] if ssh is None else ["ssh", "-o", "ConnectTimeout=8", ssh, "bash -s"]
    try:
        r = subprocess.run(cmd, input=None if ssh is None else SCRIPT, capture_output=True, text=True, timeout=60)
        out["hosts"][name] = [l for l in r.stdout.splitlines() if l.strip()]
    except Exception as e:
        out["hosts"][name] = [f"CHECK_FAILED {e}"]
import os
d = os.path.expanduser("~/binance-sandbox/logs"); os.makedirs(d, exist_ok=True)
open(os.path.join(d, "stall_watch.jsonl"), "a").write(json.dumps(out) + "\n")
if any(out["hosts"].values()):
    open(os.path.join(d, "stall_watch_alert.log"), "a").write(json.dumps(out) + "\n")
print(json.dumps(out))
