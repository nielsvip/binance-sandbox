#!/usr/bin/env python3
"""s1_position_failover — S1-side position-data failover supervisor (S1 ONLY via systemd).

Mac pushes data/mac_live_heartbeat.json (with position_min/max_age_s per crypto account) every
60s plus data/mac_order_pulse.json (last broker-confirmed order epoch, shipped on change /
15s liveness by tools/mac_order_pulse.py). This loop reads the S1-local copies and kickstarts
S1's own position monitors (ez_positions_realtime.py, OFF by default — never in cron/systemd)
when Mac's position data goes stale; the Mac PULLS S1's fresh files back (--update).

Dynamic threshold (mirrors live ez_manage.OrderExecutionMonitor: 1s tick, seconds matter):
  - POST_ORDER (order <ORDER_WINDOW_S ago): 1s tick, positions must be <ORDER_FRESH_S (3s)
    old; 2 consecutive breaches -> immediate takeover. An order was JUST placed — poll now.
  - QUIET (no recent order / pulse unknown): 5s tick, POS_STALE_AFTER_S (90s) anti-flap.
Takeover triggers per account (any one fires):
  - heartbeat missing/stale (>HB_STALE_S): Mac unreachable -> all accounts.
  - position_max_age_s > active threshold (3s post-order, 90s quiet): block/failure.
Stand-down: account healthy for STANDDOWN_LOOPS straight loops -> stop S1 monitors we started.
Respawn backoff: >MAX_SPAWNS_PER_HR spawns for one account -> 15 min cooldown (no crash loops).
State return: S1 cannot reach the Mac (NAT, no inbound), so the Mac PULLS S1's fresh files
(mac_posfailover_pull.sh, --update) whenever takeover flags are present; this supervisor never pushes.

Fail-safe notes: position takeover can never double-trade (monitors only fetch+write files, and
the save guard + rsync --update enforce newest-wins both directions). Trading failover stays
owned by s1_failover_monitor.sh (armed separately, debounced, market-gated).
"""
import json
import subprocess
import sys
import time
from pathlib import Path

CRYPTO_ACCOUNTS = ("ang", "inf", "flz", "men", "fin")
LOOP_S = 5
HB_STALE_S = 300
POS_STALE_AFTER_S = 90.0
STANDDOWN_LOOPS = 12
MAX_SPAWNS_PER_HR = 6
COOLDOWN_S = 900
ORDER_WINDOW_S = 180
ORDER_FRESH_S = 3.0
ORDER_GRACE_LOOPS = 2
ORDER_LOOP_S = 1
PULSE_STALE_S = 60
LIVE_DIR = Path("/home/niels/binance")
HEARTBEAT = LIVE_DIR / "data" / "mac_live_heartbeat.json"
PULSE_PATHS = (LIVE_DIR / "data" / "mac_order_pulse.json", Path("/home/niels/binance-sandbox/data/mac_order_pulse.json"))
FLAG_FMT = str(LIVE_DIR / "data" / "s1_posfailover_active_{acct}")
LOG_PREFIX = "[s1_posfailover]"
CONDA_PY = "/home/niels/.conda/envs/binance_env/bin/python"


def log(msg):
    print(f"{LOG_PREFIX} {time.strftime('%H:%M:%S', time.gmtime())}Z {msg}", flush=True)


def load_heartbeat(path=HEARTBEAT):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def heartbeat_ok(hb, now):
    if not isinstance(hb, dict):
        return False
    try:
        return (now - float(hb.get("epoch", 0))) < HB_STALE_S
    except Exception:
        return False


def load_pulse(paths=PULSE_PATHS):
    """Newest-wins pulse read across live+sandbox copies; None when unusable."""
    best, best_epoch = None, -1.0
    for path in paths:
        try:
            payload = json.loads(Path(path).read_text())
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            epoch = float(payload.get("epoch", -1))
        except Exception:
            continue
        if epoch > best_epoch:
            best, best_epoch = payload, epoch
    return best


def last_order_age_s(pulse, now):
    """Seconds since Mac's last broker-confirmed order; None when unknown/stale pulse."""
    if not isinstance(pulse, dict):
        return None
    try:
        if now - float(pulse.get("epoch", 0)) > PULSE_STALE_S:
            return None
        last = pulse.get("last_order_epoch")
        if last is None:
            return None
        return now - float(last)
    except Exception:
        return None


def in_order_window(order_age):
    return order_age is not None and order_age < ORDER_WINDOW_S


def decide(hb_ok, max_ages, active, healthy_streaks, order_age=None, breach_streaks=None):
    """Pure decision: returns (to_start, to_stop, new_streaks, new_breaches).

    max_ages: {acct: float|None}. order_age: seconds since Mac's last confirmed order
    (None = unknown -> QUIET). POST_ORDER window: threshold ORDER_FRESH_S (3s, the live
    OrderExecutionMonitor contract) with ORDER_GRACE_LOOPS consecutive breaches to fire.
    QUIET: threshold POS_STALE_AFTER_S, fires on first breach (unchanged legacy path).
    """
    breach_streaks = breach_streaks or {}
    post_order = in_order_window(order_age)
    threshold = ORDER_FRESH_S if post_order else POS_STALE_AFTER_S
    to_start, to_stop, new_streaks, new_breaches = [], [], {}, {}
    for acct in CRYPTO_ACCOUNTS:
        age = (max_ages or {}).get(acct)
        stale = (not hb_ok) or (age is not None and age > threshold)
        if stale:
            new_streaks[acct] = 0
            breach = breach_streaks.get(acct, 0) + 1
            new_breaches[acct] = breach
            need = ORDER_GRACE_LOOPS if post_order else 1
            if acct not in active and breach >= need:
                to_start.append(acct)
        else:
            streak = healthy_streaks.get(acct, 0) + 1
            new_streaks[acct] = streak
            new_breaches[acct] = 0
            if acct in active and streak >= STANDDOWN_LOOPS:
                to_stop.append(acct)
    return to_start, to_stop, new_streaks, new_breaches


def spawn_times_ok(spawn_log, acct, now):
    recent = [t for t in spawn_log.get(acct, []) if now - t < 3600]
    spawn_log[acct] = recent
    if len(recent) >= MAX_SPAWNS_PER_HR:
        return False
    recent.append(now)
    return True


def realtime_running(acct):
    try:
        r = subprocess.run(["pgrep", "-f", f"ez_positions_realtime.py {acct}"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def start_realtime(acct):
    cmd = ["setsid", "-f", CONDA_PY, "-u", "ez_positions_realtime.py", acct]
    with open(f"/tmp/s1_posfailover_rt_{acct}.log", "ab") as fh:
        subprocess.Popen(cmd, cwd=str(LIVE_DIR), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    Path(FLAG_FMT.format(acct=acct)).touch()


def stop_realtime(acct):
    subprocess.run(["pkill", "-f", f"ez_positions_realtime.py {acct}"], timeout=10)
    try:
        Path(FLAG_FMT.format(acct=acct)).unlink()
    except OSError:
        pass


def active_set():
    return {a for a in CRYPTO_ACCOUNTS if Path(FLAG_FMT.format(acct=a)).exists()}


def run_loop():
    import fcntl
    lk = open("/tmp/s1_posfailover.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("locked (previous supervisor active)")
        return
    streaks, breaches, spawn_log = {}, {}, {}
    log(f"start quiet_s={LOOP_S} order_s={ORDER_LOOP_S} quiet_after={POS_STALE_AFTER_S}s order_after={ORDER_FRESH_S}s window={ORDER_WINDOW_S}s hb_stale={HB_STALE_S}s")
    while True:
        tick = LOOP_S
        try:
            now = time.time()
            hb = load_heartbeat()
            ok = heartbeat_ok(hb, now)
            ages = (hb.get("position_max_age_s") or {}) if isinstance(hb, dict) else {}
            order_age = last_order_age_s(load_pulse(), now)
            post_order = in_order_window(order_age)
            tick = ORDER_LOOP_S if post_order else LOOP_S
            to_start, to_stop, streaks, breaches = decide(ok, ages, active_set(), streaks, order_age, breaches)
            for acct in to_start:
                if realtime_running(acct):
                    Path(FLAG_FMT.format(acct=acct)).touch()
                    continue
                if not spawn_times_ok(spawn_log, acct, now):
                    log(f"{acct}: respawn cooldown (>{MAX_SPAWNS_PER_HR}/hr) — skipping start")
                    continue
                mode = "POST_ORDER" if post_order else "QUIET"
                log(f"{acct}: {mode}_TAKEOVER (hb_ok={ok} max_age={(ages or {}).get(acct)} order_age={order_age}) — starting S1 realtime")
                start_realtime(acct)
            for acct in to_stop:
                log(f"{acct}: healthy {STANDDOWN_LOOPS} loops — stopping S1 realtime (Mac owns it)")
                stop_realtime(acct)
        except Exception as e:
            log(f"loop error (continuing): {e!r}")
        time.sleep(tick)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        now = time.time()
        hb = load_heartbeat()
        ok = heartbeat_ok(hb, now)
        ages = (hb.get("position_max_age_s") or {}) if isinstance(hb, dict) else {}
        order_age = last_order_age_s(load_pulse(), now)
        to_start, to_stop, _, _ = decide(ok, ages, active_set(), {}, order_age, {})
        print(json.dumps({"hb_ok": ok, "ages": ages, "order_age": order_age, "post_order": in_order_window(order_age), "to_start": to_start, "to_stop": to_stop}))
    else:
        run_loop()
