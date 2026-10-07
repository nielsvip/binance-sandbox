#!/usr/bin/env python3
"""monitor_production - every server must produce new and better results or alert.
Checks S1/S2/S5/S6 via ssh: new V15 files, hustle-ends, gain monotonic. Writes alert if stalled.
Cron: */10 * * * * /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/monitor_production.py >> /tmp/monitor_production.log 2>&1
"""
import subprocess, json, pathlib, time, sys
HOSTS = {
    "S1": "10.0.0.3",
    "S2": "10.0.0.4",
    "S5": "10.0.0.5",
    "S6": "10.0.0.6",
}
ALERT_LOG = pathlib.Path("/tmp/production_alert.log")
STATE_PATH = pathlib.Path("/tmp/monitor_production_state.json")

def ssh(host, cmd, timeout=12):
    try:
        out = subprocess.check_output(["ssh","-o","ConnectTimeout=5","-o","StrictHostKeyChecking=no",f"niels@{host}",cmd], text=True, timeout=timeout)
        return out.strip()
    except Exception as e:
        return f"ERR:{e}"

def check_host(name, host):
    # new files last 60m, winning last 60m, hustle-ends last 60m, last gain delta
    v15_new = ssh(host, "find ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -maxdepth 1 -name '*.xlsx' -mmin -60 2>/dev/null | wc -l")
    winning_new = ssh(host, "find ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -maxdepth 1 -name '*bh*_*gain*.xlsx' -mmin -60 2>/dev/null | wc -l")
    hustle_60 = ssh(host, "grep -c 'hustle-end' /tmp/hustle_cron.log 2>/dev/null | head -1; grep 'hustle-end' /tmp/hustle_cron.log 2>/dev/null | tail -5 | head -1")
    # last delta
    last_delta = ssh(host, "grep 'hustle-end' /tmp/hustle_cron.log 2>/dev/null | tail -1 | sed 's/.*delta //' | cut -d' ' -f1")
    # current FINAL count
    final_cnt = ssh(host, "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/*.xlsx 2>/dev/null | wc -l")
    # pilots running
    pilots = ssh(host, "ps aux | grep v15_pilot | grep -v grep | wc -l 2>/dev/null | head -1")
    load = ssh(host, "cat /proc/loadavg 2>/dev/null | cut -d' ' -f1-3")
    return {
        "host": host,
        "v15_new_60m": v15_new,
        "winning_new_60m": winning_new,
        "hustle_tail": hustle_60,
        "last_delta": last_delta,
        "final_cnt": final_cnt,
        "pilots": pilots,
        "load": load,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

def main():
    results = {}
    alerts = []
    for name, host in HOSTS.items():
        r = check_host(name, host)
        results[name] = r
        # parse numbers
        try:
            v15n = int(r["v15_new_60m"].split()[0]) if r["v15_new_60m"][0].isdigit() else -1
        except: v15n = -1
        try:
            winn = int(r["winning_new_60m"].split()[0]) if r["winning_new_60m"][0].isdigit() else -1
        except: winn = -1
        try:
            pilots = int(r["pilots"].split()[0]) if r["pilots"][0].isdigit() else -1
        except: pilots = -1
        # alerts: no new files or no pilots
        if v15n == 0:
            alerts.append(f"ALERT {name}@{r['host']}: NO new V15 in 60m (stalled) load={r['load']} pilots={pilots}")
        if pilots == 0:
            alerts.append(f"ALERT {name}@{r['host']}: NO pilots running (hustle dead) load={r['load']}")
        # winning should be >0 per hour at fleet scale (per-server 1-2/hr is ok, but 0 for 2h is alert)
        # we check per host: if 0 winning in 60m and not S1 crypto-only (S1 may be 0 for stocks), warn
        if name != "S1" and winn == 0:
            # check if host is stock host but no winning in 60m -> warn
            alerts.append(f"WARN {name}@{r['host']}: 0 winning bh+gain in 60m (needs better) last_delta={r['last_delta']}")
        # monotonic: last_delta should be >=0 (never worse) - if negative, alert
        try:
            d = float(r["last_delta"].split()[0].replace("+","")) if r["last_delta"] and r["last_delta"][0] not in "E" else 0
            if d < -0.01:
                alerts.append(f"ALERT {name}: last delta {d} <0 (deteriorated vs previous best) — monotonic violated")
        except: pass

    # compare to previous state for growth
    prev = {}
    if STATE_PATH.exists():
        try: prev = json.loads(STATE_PATH.read_text())
        except: prev = {}
    for name, r in results.items():
        try:
            cur_final = int(r["final_cnt"].split()[0])
            prev_final = int(prev.get(name, {}).get("final_cnt", -1))
            if prev_final >=0 and cur_final < prev_final:
                alerts.append(f"ALERT {name}: FINAL count dropped {prev_final}->{cur_final} (worse than previous)")
            if prev_final >=0 and cur_final == prev_final and r["winning_new_60m"].strip()=="0":
                # no growth for 10m is ok, but for 60m with 6 checks = 60m, alert if still same after 1h?
                pass
        except: pass
    STATE_PATH.write_text(json.dumps(results, indent=2))
    # log
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] " + " | ".join([f"{k}:{v['v15_new_60m']}v15 {v['winning_new_60m']}win {v['final_cnt']}final {v['pilots']}pil load{v['load']}" for k,v in results.items()])
    print(line, flush=True)
    if alerts:
        for a in alerts:
            print(a, flush=True)
            try:
                with open(ALERT_LOG, "a") as f: f.write(f"[{ts}] {a}\n")
            except: pass
        # also surface to S1 log
        return 1
    else:
        print(f"[{ts}] OK all servers producing", flush=True)
        return 0

if __name__ == "__main__":
    sys.exit(main())
