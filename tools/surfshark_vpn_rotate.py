#!/usr/bin/env python3
import json, os, re, subprocess, sys, time, urllib.request
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WL_PATH = os.path.join(BASE, "data", "vpn_whitelist.json")
STATE_PATH = os.path.join(BASE, "data", "vpn_rotate_state.json")
LOG_PATH = os.path.expanduser("~/logs/vpn_rotate.log")
BAN_FILES = [os.path.join(BASE, "data", "ez_klines_ban_state.json"), os.path.join(BASE, "data", "klines_canonical_ban.json")]
FAPI = "https://fapi.binance.com"
COOLDOWN_S = 120
FAIL_SKIP_S = 300
RESTORE_TRY_S = 600
def log(m):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + m
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        open(LOG_PATH, "a").write(line + "\n")
    except Exception:
        pass
def sh(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
def whitelist():
    return set(json.load(open(WL_PATH))["binance_ip_whitelist"])
def slots():
    out, res = [], sh(["scutil", "--nc", "list"])
    for line in res.stdout.splitlines():
        m = re.match(r"\*?\s*\((\w+)\)\s+\S+ \S+ \(.+?\) \"(.+?)\"", line)
        if m and "Surfshark" in m.group(2):
            out.append({"name": m.group(2), "status": m.group(1)})
    return out
def slot_server(name):
    res = sh(["scutil", "--nc", "show", name])
    srv, sip = None, None
    for line in res.stdout.splitlines():
        a = re.search(r"server_(?:hostname|address)\s*:\s*(\S+)", line)
        b = re.search(r"server_ip\s*:\s*([\d.]+)", line)
        c = re.search(r"remote_identifier\s*:\s*(\S+)", line)
        if a and not srv:
            srv = a.group(1)
        if b:
            sip = b.group(1)
        if c and not srv:
            srv = c.group(1)
    return srv, sip
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
def egress_ip():
    for url in ("https://ifconfig.me/ip", "https://api.ipify.org"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vpn-rotate"})
            ip = urllib.request.urlopen(req, timeout=10).read().decode().strip()
            if IP_RE.match(ip):
                return ip
        except Exception:
            continue
    return None
def binance_probe():
    try:
        req = urllib.request.Request(FAPI + "/fapi/v1/ping", headers={"User-Agent": "vpn-rotate"})
        r = urllib.request.urlopen(req, timeout=10)
        probe = {"ping": r.status, "weight": 0}
    except urllib.error.HTTPError as e:
        return {"ping": e.code, "weight": 0, "body": e.read(200).decode("utf8", "ignore")}
    try:
        req = urllib.request.Request(FAPI + "/fapi/v1/time", headers={"User-Agent": "vpn-rotate"})
        r = urllib.request.urlopen(req, timeout=10)
        probe["weight"] = int(r.headers.get("X-MBX-USED-WEIGHT-1M", "0") or 0)
    except Exception:
        pass
    return probe
def ban_active():
    now = time.time() * 1000.0
    for path in BAN_FILES:
        try:
            d = json.load(open(path))
        except Exception:
            continue
        until = float(d.get("ban_until_ms") or d.get("until_ms") or 0)
        if until > now:
            return True, os.path.basename(path), (until - now) / 1000.0
    return False, None, 0.0
def quit_app():
    try:
        sh(["osascript", "-e", 'tell application "Surfshark" to quit'], timeout=15)
    except Exception as e:
        log(f"app quit attempt: {e}")
def stop_all():
    for s in slots():
        if s["status"] != "Disconnected":
            sh(["scutil", "--nc", "stop", s["name"]])
def try_naked(wl, state, old_ip, home_only=True):
    cfg = net_cfg()
    stop_all()
    if not wait_state([s["name"] for s in slots()], "Disconnected", old_ip, timeout=25):
        return False
    time.sleep(3)
    ip = egress_ip()
    probe = binance_probe()
    state["slot_exit"]["(no-vpn)"] = {"ip": ip, "server": None, "ts": time.time()}
    ok_ips = cfg.get("home_ips") or [] if home_only else list(wl)
    if not ip or ip not in ok_ips:
        log(f"naked egress={ip} not a home IP — cannot use");
        save_state(state)
        return False
    if probe.get("ping") == 418:
        log(f"naked egress={ip} Binance 418 — cannot use");
        save_state(state)
        return False
    state["last_rotate_ts"] = time.time()
    state["rotations"] = state.get("rotations", 0) + 1
    save_state(state)
    log(f"ROTATED OK slot=(no-vpn) egress={ip} binance={probe}")
    return True
def ensure_tunnel(wl, state):
    if [s for s in slots() if s["status"] == "Connected"]:
        return True
    if (egress_ip() or "") in wl and binance_probe().get("ping") != 418:
        return True
    cands = eligible_slots(wl, state, None)
    if not cands:
        log("INVARIANT FAIL-CLOSED: no whitelisted slot available, leaving link down (no non-whitelisted egress)");
        return False
    name = cands[0]["name"]
    log(f"ensure_tunnel: restoring {name}, verifying whitelist before accepting")
    sh(["scutil", "--nc", "start", name])
    if not wait_state([name], "Connected", egress_ip(), timeout=45):
        log("INVARIANT FAIL-CLOSED: restored slot did not connect, leaving link down");
        return False
    ip = egress_ip()
    probe = binance_probe()
    if ip not in wl or probe.get("ping") == 418:
        log(f"INVARIANT FAIL-CLOSED: restored egress={ip} not usable (whitelisted={ip in wl} binance={probe}) — stopping it");
        sh(["scutil", "--nc", "stop", name])
        return False
    log(f"ensure_tunnel OK egress={ip} binance={probe}")
    return True
def load_state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {"slot_exit": {}, "slot_fail": {}, "last_rotate_ts": 0, "rotations": 0}
def save_state(s):
    tmp = STATE_PATH + ".tmp"
    open(tmp, "w").write(json.dumps(s, indent=1))
    os.replace(tmp, STATE_PATH)
def cmd_status():
    wl = whitelist()
    print("slots:")
    for s in slots():
        srv, sip = slot_server(s["name"])
        print(f"  {s['status']:12s} {s['name']:22s} server={srv} server_ip={sip}")
    ip = egress_ip()
    print(f"egress={ip} whitelisted={ip in wl if ip else '?'}")
    print(f"binance={binance_probe()}")
    print(f"ban_active={ban_active()}")
def net_cfg():
    try:
        return json.load(open(WL_PATH))
    except Exception:
        return {}
def slot_rank(server):
    cc = (server or "").split("-")[0].lower()
    if cc in ("nl", "us"):
        return None
    return {"uk": 1, "sg": 2}.get(cc, 3)
def gateway_mac():
    try:
        gw = None
        r = sh(["route", "-n", "get", "default"], timeout=10).stdout
        m = re.search(r"gateway:\s*([\d.]+)", r)
        if m:
            gw = m.group(1)
        if not gw:
            return None
        a = sh(["arp", "-n", gw], timeout=10).stdout
        m = re.search(r"at\s+([0-9a-f:]{17})", a)
        return m.group(1).lower() if m else None
    except Exception:
        return None
def wifi_ssid():
    try:
        r = sh(["networksetup", "-getairportnetwork", "en0"], timeout=10).stdout
        m = re.search(r"Current Wi-Fi Network:\s*(.+)", r)
        return m.group(1).strip() if m else None
    except Exception:
        return None
def at_home(cfg):
    if wifi_ssid() in (cfg.get("home_ssids") or []):
        return True
    mac = gateway_mac()
    if mac and mac in [g.lower() for g in cfg.get("home_gateways") or []]:
        return True
    if not [s for s in slots() if s["status"] == "Connected"]:
        if egress_ip() in (cfg.get("home_ips") or []):
            return True
    return False
def eligible_slots(wl, state, skip):
    out = []
    for s in slots():
        if s["name"] == skip:
            continue
        fail_ts = state["slot_fail"].get(s["name"], 0)
        if time.time() - fail_ts < FAIL_SKIP_S:
            continue
        srv, _ = slot_server(s["name"])
        rank = slot_rank(srv)
        if rank is None:
            continue
        out.append((rank, s["name"], s))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out]
def wait_state(names, want, old_ip, timeout=75):
    end = time.time() + timeout
    while time.time() < end:
        cur = {s["name"]: s["status"] for s in slots()}
        if all(cur.get(n) == want for n in names):
            if want != "Connected":
                return True
            ip = egress_ip()
            if ip and ip != old_ip:
                return True
        time.sleep(3)
    return False
def do_rotate(target=None, force_tunnel=False):
    wl, state = whitelist(), load_state()
    quit_app()
    time.sleep(2)
    if target == "none":
        return try_naked(wl, state, egress_ip())
    cur = [s for s in slots() if s["status"] == "Connected"]
    cur_name = cur[0]["name"] if cur else None
    old_ip = egress_ip()
    log(f"rotate from={cur_name} egress={old_ip}")
    if not target and not force_tunnel and at_home(net_cfg()) and old_ip not in (net_cfg().get("home_ips") or []):
        log("home network — naked home IP is preferred, trying it first")
        if try_naked(wl, state, old_ip):
            return True
    cands = eligible_slots(wl, state, cur_name)
    if target:
        cands = [s for s in slots() if s["name"] == target]
        if not cands:
            log(f"no such slot {target}");
            return False
    if not cands:
        log("no eligible slot — falling back to naked egress");
        return try_naked(wl, state, old_ip)
    for cand in cands:
        name = cand["name"]
        srv, _ = slot_server(name)
        log(f"trying slot {name} server={srv}")
        sh(["scutil", "--nc", "start", name])
        if not wait_state([name], "Connected", old_ip):
            log(f"slot {name} did not connect with new IP");
            state["slot_fail"][name] = time.time()
            save_state(state)
            sh(["scutil", "--nc", "stop", name])
            continue
        if cur_name and cur_name != name:
            sh(["scutil", "--nc", "stop", cur_name])
        ip = egress_ip()
        probe = binance_probe()
        state["slot_exit"][name] = {"ip": ip, "server": srv, "ts": time.time()}
        if ip not in wl:
            log(f"slot {name} egress={ip} NOT in Binance whitelist — skipping (orders would -2015)")
            state["slot_fail"][name] = time.time()
            save_state(state)
            sh(["scutil", "--nc", "stop", name])
            continue
        if probe.get("ping") == 418:
            log(f"slot {name} egress={ip} Binance still 418 — skipping")
            state["slot_fail"][name] = time.time()
            save_state(state)
            sh(["scutil", "--nc", "stop", name])
            continue
        state["last_rotate_ts"] = time.time()
        state["rotations"] = state.get("rotations", 0) + 1
        state["slot_fail"].pop(name, None)
        save_state(state)
        log(f"ROTATED OK slot={name} server={srv} egress={ip} binance={probe}")
        if ip != old_ip:
            for path in BAN_FILES:
                try:
                    if os.path.exists(path):
                        log(f"clearing stale per-IP ban state {os.path.basename(path)} (ban was against {old_ip})")
                        os.remove(path)
                except Exception as e:
                    log(f"could not clear {path}: {e}")
        return True
    log("all candidate slots failed — trying naked egress, then ensuring the link is not dark");
    if try_naked(wl, state, old_ip):
        return True
    ensure_tunnel(wl, state)
    return False
def cmd_check():
    wl = whitelist()
    ip = egress_ip()
    if not ip:
        print("CHECK FAIL: no egress IP");
        return 2
    if ip not in wl:
        print(f"CHECK FAIL: egress {ip} not in Binance whitelist (orders would -2015)");
        return 2
    probe = binance_probe()
    if probe.get("ping") == 418:
        print(f"CHECK FAIL: Binance 418 on {ip}");
        return 3
    if probe.get("weight", 0) > 5500:
        print(f"CHECK FAIL: usedWeight {probe['weight']} near limit");
        return 4
    b, f, rem = ban_active()
    if b:
        print(f"CHECK FAIL: ban file {f} active {rem:.0f}s");
        return 5
    print(f"CHECK OK egress={ip} binance={probe}");
    return 0
def cmd_watch(interval=15):
    lock = "/tmp/vpn_rotate_watch.lock"
    try:
        old = open(lock).read().strip()
        os.kill(int(old), 0)
        print(f"watch already running as pid {old}");
        return 1
    except Exception:
        try:
            open(lock, "w").write(str(os.getpid()))
        except Exception as e:
            print(f"cannot write lock: {e}");
            return 1
    log(f"watch start interval={interval}s")
    n = 0
    while True:
        try:
            rc = cmd_check_quiet()
            n += 1
            if n % 120 == 0:
                log(f"AUDIT egress={egress_ip()} binance={binance_probe()} ban={ban_active()}")
            if rc != 0 and time.time() - load_state().get("last_rotate_ts", 0) >= COOLDOWN_S:
                log(f"watch: check rc={rc} -> rotating")
                do_rotate()
            if rc == 0 and not [s for s in slots() if s["status"] == "Connected"]:
                st = load_state()
                if time.time() - st.get("last_restore_try", 0) >= RESTORE_TRY_S:
                    log("watch: link healthy but no tunnel — restoring always-on tunnel")
                    st["last_restore_try"] = time.time()
                    save_state(st)
                    do_rotate(force_tunnel=True)
            time.sleep(interval)
        except Exception as e:
            log(f"watch error: {e}")
            time.sleep(interval)
def cmd_check_quiet():
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        return cmd_check()
if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "status"
    if c == "status":
        cmd_status()
    elif c == "check":
        sys.exit(cmd_check())
    elif c == "rotate":
        tgt = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else None
        sys.exit(0 if do_rotate(tgt) else 1)
    elif c == "watch":
        iv = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        sys.exit(cmd_watch(iv))
    else:
        print("usage: surfshark_vpn_rotate.py status|check|rotate [slot|none]|watch [interval]");
        sys.exit(2)
