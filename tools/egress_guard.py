import json, os, subprocess, time, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WL_PATH = os.path.join(BASE, "data", "vpn_whitelist.json")
LOG_PATH = os.path.expanduser("~/logs/egress_guard.log")
INTERVAL_S = 20
ALARM_EVERY_S = 60


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def egress_v4():
    try:
        req = urllib.request.Request("https://ifconfig.me/ip", headers={"User-Agent": "egress-guard"})
        return urllib.request.urlopen(req, timeout=10).read().decode().strip()
    except Exception:
        return None


def whitelist():
    return set(json.load(open(WL_PATH))["binance_ip_whitelist"])


def alarm(msg):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "EGRESS GUARD" sound name "Basso"'], timeout=15)
    subprocess.run(["say", "-v", "Samantha", msg], timeout=60)


def main():
    last_alarm = 0.0
    while True:
        ip = egress_v4()
        ok = ip is not None and ip in whitelist()
        if ok:
            log(f"OK egress={ip} whitelisted")
        else:
            now = time.time()
            log(f"NOT TRADING egress={ip} not on Binance whitelist")
            if now - last_alarm >= ALARM_EVERY_S:
                alarm("Not trading. Egress is not on the Binance whitelist. Switch to London now.")
                last_alarm = now
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
