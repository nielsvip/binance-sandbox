#!/usr/bin/env python3
"""
v15_warn_daemon — desktop warning + AI alert + auto-heal if any server drops below
  CPU >95% or RAM >80% (no OOM, max workers/max parallel).

Runs on MacBook (Darwin) as LaunchAgent: polls s1/s2/s3/s5 every 30s,
  sends macOS Notification Center alert + sound, logs, and auto-heals.

Thresholds (user request):
  - CPU < 95%  → WARN  (under-utilized, should be saturated)
  - RAM used < 80% → WARN  (under-utilized, should be >80% per herd spec)
  - RAM avail < 1200M or OOM in dmesg → CRITICAL (near OOM)
  - herd daemon down or pilots == 0 → CRITICAL
  - any server unreachable → CRITICAL

Desktop warning: osascript display notification + `say` + `afplay` sound + terminal-notifier if present.
AI warning: appends JSONL to /tmp/v15_ai_alert.jsonl and /Users/niels/Documents/binance/logs/v15_warn.log,
            and auto-heals by re-launching herd via ssh.

LaunchAgent: ~/Library/LaunchAgents/com.binance.v15-warn.plist
  KeepAlive true, RunAtLoad true, poll 30s inside daemon (not StartInterval).

Usage:
  python3 -u tools/v15_warn_daemon.py                # foreground
  python3 -u tools/v15_warn_daemon.py --once         # single check (for cron)
  python3 -u tools/v15_warn_daemon.py --test-notify  # test desktop notification
"""
from __future__ import annotations
import json
import time
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
ORDER = ROOT / "SPREADSHEETS" / "V15_RUNNING_ORDER_TRB_FLZ.txt"
SERVERS = {
    "s1": {"hosts": ["s1-int", "s1-pub"], "nproc": 16, "mem_gb": 30, "expect_pilots": 16, "hcloud_name": "niels"},
    "s2": {"hosts": ["s2", "s2-pub"], "nproc": 4, "mem_gb": 7.6, "expect_pilots": 13, "hcloud_name": "htz-v15-s2"},
    "s5": {"hosts": ["s5", "s5-pub"], "nproc": 16, "mem_gb": 30, "expect_pilots": 13, "hcloud_name": "htz-v15-s5"},
    "s6": {"hosts": ["s6", "s6-pub"], "nproc": 16, "mem_gb": 30, "expect_pilots": 13, "hcloud_name": "htz-v15-s6"},
}
# hcloud ephemeral map — if server not in `hcloud server list`, it's deleted, never warn
_UNREACHABLE_COUNT: dict[str, int] = {}
_DELETED_CACHE: dict[str, float] = {}
LOG_FILE = pathlib.Path("/tmp/v15_warn.log")
AI_ALERT = pathlib.Path("/tmp/v15_ai_alert.jsonl")
PERSIST_LOG = ROOT / "logs" / "v15_warn.log"
COOLDOWN_SEC = 300  # per-host per-reason cooldown to avoid spam
CPU_WARN = 80.0
RAM_WARN = 80.0
OOM_AVAIL = 1200  # MB

_last_warn: dict[str, float] = {}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    for p in [LOG_FILE, PERSIST_LOG]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(line + "\n")
        except: pass


def ai_alert(host: str, reason: str, details: dict):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "host": host, "reason": reason, **details}
    for p in [AI_ALERT, PERSIST_LOG]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except: pass
    log(f"AI_ALERT {host} {reason} {details}")


def desktop_notify(title: str, message: str, sound: bool = True, critical: bool = False):
    # escape for osascript
    t = title.replace('"', '\\"').replace("\n", " ")
    m = message.replace('"', '\\"').replace("\n", " ")
    # sound: Submarine (default), or Basso for critical; also `say` for audible while AFK
    snd = "Basso" if critical else "Submarine"
    try:
        # primary: osascript display notification (always works on macOS)
        subprocess.run(["osascript", "-e", f'display notification "{m}" with title "{t}" subtitle "V15 herd — fix now" sound name "{snd}"'], timeout=5)
    except Exception as e:
        log(f"notify osascript failed: {e}")
    # also try terminal-notifier if installed (richer)
    try:
        if pathlib.Path("/opt/homebrew/bin/terminal-notifier").exists() or pathlib.Path("/usr/local/bin/terminal-notifier").exists():
            subprocess.run(["terminal-notifier", "-title", title, "-message", message, "-sound", snd], timeout=5)
    except: pass
    # audible `say`/`afplay` DISABLED 2026-09-15 per user request: no speaking/noise from MacBook
    # (kept osascript visual notification; sound/speech removed)
    if False and critical:  # muted
        try:
            subprocess.Popen(["say", f"Warning {title}. {message}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass
        try:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Basso.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass


def should_warn(key: str) -> bool:
    now = time.time()
    last = _last_warn.get(key, 0)
    if now - last < COOLDOWN_SEC:
        return False
    _last_warn[key] = now
    return True


def ssh(host: str, cmd: str, timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", host, cmd],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 124, "ssh timeout"
    except Exception as e:
        return 127, str(e)


def pick_host(server: str) -> str | None:
    for h in SERVERS[server]["hosts"]:
        rc, _ = ssh(h, "echo ok", timeout=5)
        if rc == 0:
            return h
    return None


def server_stats(host: str) -> dict:
    # one ssh does all: load, free, pilots, herd, oom (recent only — ignore stale dmesg)
    cmd = (
        "cat /proc/loadavg; echo __FREE__; free -m; echo __PILOTS__; pgrep -a -f v15_pilot 2>&1 | head -n 20; echo __HERD__; pgrep -a -f v15_local_herd 2>&1 | head -n 5; "
        "echo __OOM__; dmesg --time-format iso 2>&1 | tail -n 30 | grep -i -E 'out of memory|oom-killer' | tail -n 3; echo __END__; "
        "echo __AVAIL_RAW__; cat /proc/meminfo | grep -E 'MemAvailable|MemTotal' | head -n 2"
    )
    rc, out = ssh(host, cmd, timeout=12)
    if rc != 0:
        return {"ok": False, "raw": out, "host": host}
    try:
        load_line = out.splitlines()[0] if out.splitlines() else ""
        load1 = float(load_line.split()[0]) if load_line else 0.0
        # free -m line: Mem: total used free shared buff/cache available
        import re
        m = re.search(r"Mem:\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", out)
        if m:
            total = int(m.group(1)); avail = int(m.group(2))
            used_pct = (total - avail) / total * 100 if total else 0
        else:
            total, avail, used_pct = 0, 0, 0
        # pilots = count lines containing v15_pilot and --sym-side
        pilots = len(re.findall(r"v15_pilot\.py[^\n]*--sym-side", out))
        herd_ok = "v15_local_herd" in out and "python" in out.split("__HERD__")[1].split("__OOM__")[0] if "__HERD__" in out else False
        # crude herd check: after __HERD__ section contains python.*v15_local_herd
        herd_section = out.split("__HERD__")[1].split("__OOM__")[0] if "__HERD__" in out and "__OOM__" in out else ""
        herd_ok = "v15_local_herd" in herd_section
        # only treat OOM as current if dmesg iso timestamp is today (not stale boot history)
        oom_section = out.split("__OOM__")[1].split("__END__")[0] if "__OOM__" in out and "__END__" in out else ""
        # iso line contains current date like 2026-09-15; ignore old dates and empty
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        oom = False
        for line in oom_section.splitlines():
            if ("out of memory" in line.lower() or "oom-killer" in line.lower()) and today in line:
                oom = True
                break
        # nproc
        nproc = SERVERS.get(host.split("-")[0], {}).get("nproc", 16)  # fallback, will override per server
        # actual nproc via ssh would be better but we use server config
        return {"ok": True, "load1": load1, "total_m": total, "avail_m": avail, "used_pct": used_pct, "pilots": pilots, "herd_ok": herd_ok, "oom": oom, "raw": out}
    except Exception as e:
        return {"ok": False, "err": str(e), "raw": out}


def auto_heal(server: str, host: str, reason: str):
    log(f"AUTO-HEAL {server} ({host}) reason={reason} → restarting herd + launching")
    # 1) ensure herd daemon running
    log("herd retired 2026-10-06: v15_fleet_scheduler admits work (s1 cron */2); nothing to restart here")
    # 2) also run --cron-check
    # 3) if pilots low and cpu/ram low, the herd will auto-launch in next 12s loop — no need to manually launch
    ai_alert(server, f"auto_heal_{reason}", {"host": host})


def _is_deleted(server: str) -> bool:
    """Ephemeral hcloud nodes (s2/s3/s5) are deleted after backtest — never warn for deleted."""
    cfg = SERVERS.get(server, {})
    name = cfg.get("hcloud_name", "")
    # s1 (niels) is permanent, never deleted
    if server == "s1":
        return False
    # cache positive deletions for 10 min to avoid hcloud hammering
    now = time.time()
    if server in _DELETED_CACHE and now - _DELETED_CACHE[server] < 600:
        return True
    try:
        out = subprocess.run(["hcloud", "server", "list", "-o", "noheader", "-o", "columns=name"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            names = out.stdout.strip().split()
            if name and name not in names:
                _DELETED_CACHE[server] = now
                log(f"DELETED detected {server} ({name}) not in hcloud list — silencing warnings")
                return True
            # also clear unreachable count if reachable via hcloud
            _UNREACHABLE_COUNT.pop(server, None)
            return False
    except: pass
    # if hcloud unavailable, fall back to consecutive-unreachable heuristic
    return False


def check_once() -> list[dict]:
    warnings = []
    for server, cfg in SERVERS.items():
        # if we already know it's deleted, skip entirely
        if _is_deleted(server):
            continue
        host = pick_host(server)
        if not host:
            # ephemeral? check if deleted before warning
            if _is_deleted(server):
                continue
            _UNREACHABLE_COUNT[server] = _UNREACHABLE_COUNT.get(server, 0) + 1
            # only warn after 3 consecutive failures and not deleted; deleted nodes stay silent
            if _UNREACHABLE_COUNT[server] < 3:
                log(f"unreachable {server} attempt {_UNREACHABLE_COUNT[server]}/3 — not yet warning (may be transient)")
                continue
            msg = f"{server} unreachable (tried {', '.join(cfg['hosts'])}) — after 3 tries"
            warnings.append({"server": server, "host": "none", "reason": "unreachable", "msg": msg, "critical": True})
            if should_warn(f"{server}:unreachable"):
                desktop_notify(f"🚨 {server} UNREACHABLE", msg, critical=True)
                ai_alert(server, "unreachable", {"hosts": cfg["hosts"]})
            continue
        else:
            _UNREACHABLE_COUNT.pop(server, None)

        st = server_stats(host)
        if not st.get("ok"):
            msg = f"{server} ({host}) stats failed: {st.get('raw','')[:120]}"
            warnings.append({"server": server, "host": host, "reason": "stats_failed", "msg": msg, "critical": True})
            if should_warn(f"{server}:stats"):
                desktop_notify(f"⚠️ {server} stats failed", msg, critical=False)
                ai_alert(server, "stats_failed", {"host": host, "raw": st.get("raw","")[:300]})
            continue

        nproc = cfg["nproc"]
        load1 = st["load1"]
        cpu_pct = load1 / max(1, nproc) * 100
        ram_pct = st["used_pct"]
        avail = st["avail_m"]
        pilots = st["pilots"]
        herd_ok = st["herd_ok"]
        oom = st["oom"]

        # global progress (occasionally)
        # evaluate warnings
        issues = []
        critical = False
        if cpu_pct < CPU_WARN:
            issues.append(f"CPU {cpu_pct:.0f}% < {CPU_WARN:.0f}% (load {load1:.1f}/{nproc})")
        if ram_pct < RAM_WARN:
            issues.append(f"RAM {ram_pct:.0f}% < {RAM_WARN:.0f}% (avail {avail}M)")
        if avail < OOM_AVAIL:
            issues.append(f"RAM avail {avail}M < {OOM_AVAIL}M — near OOM")
            critical = True
        if oom:
            issues.append("OOM killer seen in dmesg")
            critical = True
        if not herd_ok:
            issues.append("herd daemon DOWN")
            critical = True
        if pilots == 0:
            issues.append("0 pilots — idle")
            critical = True
        elif pilots < cfg["expect_pilots"] * 0.6:
            issues.append(f"pilots {pilots} < {cfg['expect_pilots']} expected")

        if issues:
            msg = f"{server} ({host}) " + " | ".join(issues) + f" — pilots={pilots} heal in 12s"
            warnings.append({"server": server, "host": host, "reason": "|".join(issues), "msg": msg, "critical": critical,
                             "cpu": cpu_pct, "ram": ram_pct, "avail": avail, "pilots": pilots, "herd_ok": herd_ok})
            # cooldown key generic per server/type to avoid spam on every CPU wiggle
            kind = "critical" if critical else "below80"
            key = f"{server}:{kind}"
            if should_warn(key):
                title = f"{'🚨' if critical else '⚠️'} {server} below 80%" if ram_pct < RAM_WARN else f"{'🚨' if critical else '⚠️'} {server} WARNING"
                desktop_notify(title, msg, critical=critical)
                ai_alert(server, "below_threshold" if not critical else "critical", {"host": host, "cpu": round(cpu_pct,1), "ram": round(ram_pct,1), "avail": avail, "pilots": pilots, "issues": issues})
                # only auto-heal for critical or severe under-utilization (pilots low / cpu <50 / herd down), not pure RAM <80% at max pilots
                need_heal = critical or (pilots < cfg["expect_pilots"] * 0.6) or (cpu_pct < 50)
                if need_heal:
                    auto_heal(server, host, "below_threshold")
        else:
            log(f"OK {server} ({host}) cpu {cpu_pct:.0f}% ram {ram_pct:.0f}% avail {avail}M pilots {pilots} herd={'up' if herd_ok else 'down'}")

    # poll S1 desktop notify from v15_pilot (baseline + first POS per sym_side) and show on Mac
    try:
        import pathlib as _pl_n
        _seen = _pl_n.Path("/tmp/v15_desktop_notify_seen")
        _seen_offset = 0
        if _seen.exists():
            try: _seen_offset = int(_seen.read_text().strip() or "0")
            except: _seen_offset = 0
        for h in ["s1-int", "s1-pub"]:
            rc, out = ssh(h, "cat /tmp/v15_desktop_notify.jsonl 2>/dev/null; cat ~/binance-sandbox/data/v15_desktop_notify.jsonl 2>/dev/null; cat ~/binance-sandbox/data/reports/v15_flags/v15_desktop_notify.jsonl 2>/dev/null", timeout=8)
            if rc == 0 and out.strip():
                lines = [l for l in out.splitlines() if l.strip().startswith("{")]
                new = lines[_seen_offset:]
                for l in new[-8:]:  # at most 8 per poll to avoid spam
                    try:
                        rec = json.loads(l)
                        ttl = rec.get("title") or "v15 pilot"
                        msg = rec.get("msg") or rec.get("message") or l[:120]
                        crit = bool(rec.get("critical"))
                        desktop_notify(ttl, msg, critical=crit)
                        log(f"DESKTOP-FWD {ttl}: {msg[:80]}")
                    except: 
                        desktop_notify("v15 pilot", l[:120], critical=False)
                if new:
                    _seen.write_text(str(len(lines)))
                break
    except Exception as e:
        log(f"desktop-fwd warn: {e}")
    # also check global 104/104
    try:
        import subprocess as sp
        order = [l.strip() for l in ORDER.read_text().splitlines() if l.strip()] if ORDER.exists() else []
        combined = set()
        for srv in SERVERS:
            h = pick_host(srv)
            if not h: continue
            rc, out = ssh(h, "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {}", timeout=10)
            if rc == 0:
                for n in out.splitlines():
                    for s in order:
                        if s in n:
                            combined.add(s)
        if order:
            todo = len(order) - len(combined)
            if todo == 0:
                if should_warn("global:done"):
                    desktop_notify("✅ V15 COMPLETE", f"104/104 sym_sides done — all sheets pushed to S1", critical=False)
                    ai_alert("global", "complete_104", {"done": len(combined)})
            elif todo > 35:
                # stalled?
                pass
            log(f"GLOBAL {len(combined)}/104 done, {todo} todo")
    except Exception as e:
        log(f"global check failed: {e}")

    return warnings


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single check then exit")
    ap.add_argument("--test-notify", action="store_true", help="send test notification and exit")
    ap.add_argument("--interval", type=int, default=30, help="poll seconds (default 30)")
    args = ap.parse_args()

    if args.test_notify:
        desktop_notify("🧪 V15 TEST WARNING", "If you see this, desktop warnings work — s1 below 80% test", critical=False)
        desktop_notify("🚨 V15 TEST CRITICAL", "Critical path also works — you would hear Basso + say", critical=True)
        print("sent 2 test notifications", flush=True)
        return

    log(f"V15 warn daemon starting — CPU>{CPU_WARN}% RAM>{RAM_WARN}% avail>{OOM_AVAIL}M workers max — interval {args.interval}s")
    # ensure log file exists for `tail -F`
    for p in [LOG_FILE, PERSIST_LOG]:
        try: p.parent.mkdir(parents=True, exist_ok=True)
        except: pass

    if args.once:
        warns = check_once()
        print(json.dumps(warns, indent=2))
        return

    while True:
        try:
            check_once()
        except Exception as e:
            log(f"check loop error: {e}")
            ai_alert("daemon", "loop_error", {"err": str(e)})
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
