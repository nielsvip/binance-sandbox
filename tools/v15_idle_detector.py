"""V15 idle detector (USER 2026-10-25): if any fleet host sits under 90% CPU,
force-pin duty-side symbols onto it that same minute via the scheduler's
operator-pin file (data/v15_sched_pinned_sides.json). Pins rotate through the
host's own duty sides list; detector pins expire after 20 min. Read-only
except the pin file + its own state file. Never touches live trading."""
import datetime
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOSTS_FILE = ROOT / "tools" / "fleet_hosts_final.json"
PIN_FILE = ROOT / "data" / "v15_sched_pinned_sides.json"
STATE_FILE = ROOT / "data" / "v15_idle_detector.json"
FLOOR = float(os.environ.get("V15_IDLE_FLOOR", "0.9"))
PIN_TTL_MIN = int(os.environ.get("V15_IDLE_PIN_TTL_MIN", "20"))
N_PIN = int(os.environ.get("V15_IDLE_N_PIN", "2"))
SIDES_FILES = {"s1": None, "s2": "/tmp/npz_sides_s2.txt", "s5": "/tmp/npz_sides_s5.txt", "s6": "/tmp/npz_sides_s6.txt"}
S1_CRYPTO_SHORT = "/tmp/npz_sides_s6_crypto.txt"


def _ssh(host_ssh, remote_cmd):
    for ssh in host_ssh:
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, remote_cmd], capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            continue
    return ""


def _sat(host):
    out = _ssh(host.get("ssh", []), "cut -d' ' -f1 /proc/loadavg; nproc")
    try:
        load, n = out.split()
        return float(load) / max(1, int(n))
    except Exception:
        return None


def _duty_sides(host):
    name = host.get("name", "")
    sf = SIDES_FILES.get(name)
    if name == "s1":
        try:
            return [l.strip() for l in open(S1_CRYPTO_SHORT) if l.strip()]
        except Exception:
            return []
    if not sf:
        return []
    out = _ssh(host.get("ssh", []), "cat %s 2>/dev/null" % sf)
    return [l.strip() for l in out.split("\n") if l.strip()]


def main():
    hosts = json.loads(HOSTS_FILE.read_text()).get("hosts", [])
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        state = {}
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        pins = json.loads(PIN_FILE.read_text())
    except Exception:
        pins = {"sides": [], "hosts": []}
    mine = {h: v for h, v in state.items() if (now - datetime.datetime.fromisoformat(v.get("ts", "2000-01-01+00:00"))).total_seconds() < PIN_TTL_MIN * 60}
    fired = {}
    for host in hosts:
        name = host.get("name", "")
        if host.get("drain"):
            continue
        sat = _sat(host) if name != "s1" else _local_sat()
        if sat is None:
            continue
        if sat >= FLOOR:
            continue
        sides = _duty_sides(host)
        if not sides:
            continue
        start = mine.get(name, {}).get("cursor", 0)
        pick = []
        for i in range(len(sides)):
            s = sides[(start + i) % len(sides)]
            if s not in pins.get("sides", []) and s not in pick:
                pick.append(s)
            if len(pick) >= N_PIN:
                break
        if pick:
            mine[name] = {"cursor": (start + len(pick)) % len(sides), "ts": now.isoformat(), "sides": pick}
            fired[name] = (round(sat, 2), pick)
    if fired:
        keep_hosts = sorted({h for h in pins.get("hosts", [])} | {h for h in mine})
        keep_sides = sorted(set(pins.get("sides", [])) | {s for v in mine.values() for s in v.get("sides", [])})
        _atomic_write(PIN_FILE, {"sides": keep_sides, "hosts": keep_hosts})
    STATE_FILE.write_text(json.dumps(mine, indent=1))
    print("idle_detector sat-floor=%s fired=%s" % (FLOOR, json.dumps(fired)))


def _local_sat():
    try:
        load = float(open("/proc/loadavg").read().split()[0])
        n = os.cpu_count() or 1
        return load / n
    except Exception:
        return None


def _atomic_write(path, obj):
    tmp = str(path) + ".tmp"
    pathlib.Path(tmp).write_text(json.dumps(obj, indent=1))
    os.replace(tmp, str(path))


if __name__ == "__main__":
    main()
