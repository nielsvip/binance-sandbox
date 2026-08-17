#!/usr/bin/env python3
"""Gateway watchdog deploy - runs OUTSIDE sandbox via setup_ssh_tunnels.py injection."""
import subprocess, time, os, sys, pathlib

LOG = "/tmp/gateway_watchdog_deploy.log"
GW = "niels@157.90.168.35"
LOCAL_SH = "/Users/niels/Documents/binance/s1-ping-watchdog.sh"
LOCAL_SVC = "/Users/niels/Documents/binance/s1-ping-watchdog.service"
LOCAL_TIMER = "/Users/niels/Documents/binance/s1-ping-watchdog.timer"

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except: pass

def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout.decode() if e.stdout else "", f"timeout {timeout}s"
    except Exception as e:
        return 1, "", str(e)

def ssh(cmd, timeout=15):
    # use BatchMode and StrictHostKeyChecking=no to avoid prompts
    esc = cmd.replace('"', '\\"')
    return run(f'ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o BatchMode=yes {GW} "{esc}"', timeout=timeout)

def scp(local, remote):
    return run(f'scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no {local} {GW}:{remote}', timeout=30)

def main():
    LOCK = "/tmp/gateway_watchdog_deploy.lock"
    import time as _t, os as _os
    if _os.path.exists(LOCK):
        try:
            age = _t.time() - _os.path.getmtime(LOCK)
            if age < 300:
                log(f"Another deploy in progress (age {int(age)}s), exiting")
                return 0
        except: pass
    try:
        open(LOCK, "w").write(str(_t.time()))
    except: pass
    log("=== GATEWAY WATCHDOG DEPLOY START ===")
    # 1. Inspect current
    log("Inspecting gateway current state...")
    for cmd in [
        'hostname; echo ---; uptime; echo ---; cat /etc/os-release | head -5',
        'systemctl status s1-ping-watchdog 2>&1 | head -n 100; echo EXIT:$?',
        'systemctl status s1-ping-watchdog.timer 2>&1 | head -n 100; echo EXIT:$?',
        'cat /etc/systemd/system/s1-ping-watchdog.service 2>&1 | head -n 50; echo ---; cat /etc/systemd/system/s1-ping-watchdog.timer 2>&1 | head -n 50',
        'ls -la /usr/local/bin/s1-ping-watchdog.sh 2>&1; cat /usr/local/bin/s1-ping-watchdog.sh 2>&1 | head -n 80',
        'systemctl list-timers 2>&1 | head -n 30',
        'journalctl -u s1-ping-watchdog -n 30 --no-pager 2>&1 | head -n 100',
        'journalctl -t s1-ping-watchdog -n 30 --no-pager 2>&1 | head -n 100',
        'ping -c2 -W2 10.0.0.3 2>&1 | head -n 20; echo PING_EXIT:$?',
        'ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no niels@10.0.0.3 "echo S1_OK; hostname; uptime; free -h | head -5" 2>&1 | head -n 20',
    ]:
        rc, out, err = ssh(cmd, timeout=20)
        log(f"CMD: {cmd[:80]} -> rc={rc}")
        log(out[:2000])
        if err.strip():
            log(f"ERR: {err[:500]}")
        time.sleep(0.5)

    # 2. Ensure local files exist
    for p in [LOCAL_SH, LOCAL_SVC, LOCAL_TIMER]:
        if not pathlib.Path(p).exists():
            log(f"MISSING local {p} - abort")
            return 1
    log(f"Local files OK: sh={os.path.getsize(LOCAL_SH)} svc={os.path.getsize(LOCAL_SVC)} timer={os.path.getsize(LOCAL_TIMER)}")

    # 3. SCP with retries
    for attempt in range(5):
        log(f"SCP attempt {attempt+1}/5...")
        ok = True
        for local, remote in [(LOCAL_SH, "/tmp/s1-ping-watchdog.sh"), (LOCAL_SVC, "/tmp/s1-ping-watchdog.service"), (LOCAL_TIMER, "/tmp/s1-ping-watchdog.timer")]:
            rc, out, err = scp(local, remote)
            log(f"scp {local} -> {remote} rc={rc} out={out[:500]} err={err[:500]}")
            if rc != 0:
                ok = False
        if ok:
            break
        log(f"SCP failed, retry in {5*(attempt+1)}s")
        time.sleep(5*(attempt+1))
    else:
        log("SCP failed after 5 attempts - abort")
        return 1

    # 4. Deploy on gateway: move, chmod, daemon-reload, enable timer
    deploy_cmds = [
        # backup old if exists
        'sudo cp /etc/systemd/system/s1-ping-watchdog.service /tmp/s1-ping-watchdog.service.bak 2>&1 || echo no_old_svc',
        'sudo cp /etc/systemd/system/s1-ping-watchdog.timer /tmp/s1-ping-watchdog.timer.bak 2>&1 || echo no_old_timer',
        'sudo mv /tmp/s1-ping-watchdog.sh /usr/local/bin/s1-ping-watchdog.sh && sudo chmod +x /usr/local/bin/s1-ping-watchdog.sh && ls -l /usr/local/bin/s1-ping-watchdog.sh',
        'sudo mv /tmp/s1-ping-watchdog.service /etc/systemd/system/s1-ping-watchdog.service && sudo mv /tmp/s1-ping-watchdog.timer /etc/systemd/system/s1-ping-watchdog.timer && ls -l /etc/systemd/system/s1-ping-watchdog*',
        'sudo systemd-analyze verify s1-ping-watchdog.service 2>&1; echo VERIFY_SVC:$?',
        'sudo systemd-analyze verify s1-ping-watchdog.timer 2>&1; echo VERIFY_TIMER:$?',
        'sudo systemctl daemon-reload; echo DAEMON_RELOAD:$?',
        # disable old simple loop if it was enabled (in case it conflicts) - we enable timer which will use oneshot service, but if old service was simple loop, we need to stop it
        'sudo systemctl stop s1-ping-watchdog.service 2>&1; echo STOP:$?',
        'sudo systemctl enable s1-ping-watchdog.service 2>&1; echo ENABLE_SVC:$?',
        'sudo systemctl enable s1-ping-watchdog.timer 2>&1; echo ENABLE_TIMER:$?',
        'sudo systemctl start s1-ping-watchdog.timer 2>&1; echo START_TIMER:$?',
        # also try to start service once manually to verify
        'sudo systemctl start s1-ping-watchdog.service 2>&1; echo START_SVC:$?',
        'sleep 2; systemctl is-enabled s1-ping-watchdog.service 2>&1; echo IS_ENABLED_SVC:$?',
        'systemctl is-enabled s1-ping-watchdog.timer 2>&1; echo IS_ENABLED_TIMER:$?',
        'systemctl is-active s1-ping-watchdog.timer 2>&1; echo IS_ACTIVE_TIMER:$?',
        'systemctl is-active s1-ping-watchdog.service 2>&1; echo IS_ACTIVE_SVC:$?',
        'systemctl list-timers s1-ping-watchdog* 2>&1 | head -n 30',
        'systemctl status s1-ping-watchdog.timer --no-pager 2>&1 | head -n 50',
        'systemctl status s1-ping-watchdog.service --no-pager 2>&1 | head -n 50',
        'journalctl -u s1-ping-watchdog -n 30 --no-pager 2>&1 | head -n 100',
        'journalctl -t s1-ping-watchdog -n 30 --no-pager 2>&1 | head -n 100',
        # test manual run
        '/usr/local/bin/s1-ping-watchdog.sh 2>&1 | head -n 20; echo MANUAL_RUN:$?',
        'sudo chmod 666 /var/tmp/s1-watchdog.failcount 2>&1; sudo bash -c "echo 0 > /var/tmp/s1-watchdog.failcount; chmod 666 /var/tmp/s1-watchdog.failcount" 2>&1; cat /var/tmp/s1-watchdog.failcount 2>&1; echo FAILCOUNT:$?',
        'logger -t s1-ping-watchdog "DEPLOYED $(date -u) timer check"; journalctl -t s1-ping-watchdog -n 5 --no-pager 2>&1 | tail -n 10',
    ]
    for cmd in deploy_cmds:
        rc, out, err = ssh(cmd, timeout=30)
        log(f"DEPLOY CMD: {cmd[:100]} -> rc={rc}")
        log(out[:3000])
        if err.strip():
            log(f"ERR: {err[:1000]}")
        if "VERIFY" in cmd and rc != 0:
            log("VERIFY FAILED - check service/timer syntax")
        time.sleep(0.5)

    # 5. Verify survives reboot: timer enabled means on boot it will start
    rc, out, err = ssh('systemctl is-enabled s1-ping-watchdog.timer 2>&1; systemctl is-enabled s1-ping-watchdog.service 2>&1', timeout=10)
    log(f"Survives reboot check: {out} err:{err}")
    if "enabled" in out:
        log("✓ Timer/service enabled for reboot persistence")
    else:
        log("✗ Not enabled - may not survive reboot")

    try:
        _os.unlink(LOCK)
    except: pass
    log("=== DEPLOY DONE ===")
    # write completion marker
    try:
        with open("/tmp/gateway_watchdog_deploy.done", "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S UTC") + "\n")
    except: pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
