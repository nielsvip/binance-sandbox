#!/usr/bin/env python3
"""LIVE FAILOVER S1 watchdog (2026-10-13 USER, FIRST PRIORITY). Runs ONLY on S1 under systemd
live-failover-watchdog.service (enabled, nice 10, ~15 s tick, stdlib + live_failover_common).

1. DATA FAILOVER (per venue, <= 3 min): reads data/mac_data_heartbeat.json (pushed by the Mac
   agent tools/mac_failover_agent.py every ~15 s). A venue goes ACTIVE on the FIRST reading where
   the Mac reports its own data stale (>180 s behind the last closed bar / snapshot not written /
   418-429 ban lines / Tradier timeouts) or the heartbeat is absent (> LF_HB_FRESH_S). ACTIVE =
   start the on-demand producer unit (live-failover-{crypto,stocks}-producers.service, NOT
   enabled at boot) and publish the freshest S1 klines + snapshot into
   data/live_failover/outbox/<venue>/ (hardlinks + MANIFEST.json provenance) which the Mac agent
   pulls and applies atomically (newer-bar-only). Back to NORMAL after the Mac is fresh for
   LF_RECOVER_S (600 s) continuously (stocks also when the session is closed).
2. FULL TRADING FAILOVER with a LEASE (data/live_failover/lease.json = single source of truth):
   S1 takes the lease ONLY when ALL hold for LF_TAKEOVER_CONFIRM consecutive ticks:
     heartbeat absent >= LF_TAKEOVER_HB_S (300) AND Mac pushes of klines/indicators stopped
     (newest Mac market_data_* and klines_cache_macbook >= 300 s) AND (Mac unreachable = no
     independent Mac witness (mac_live_heartbeat.json / mac_order_pulse.json) < 300 s, OR Mac
     reachable but every crypto ez_manage dead for >= 300 s), AND the arm file
     data/live_failover/TRADING_FAILOVER_ENABLED exists.
   Then: lease holder=s1 gen+1 -> merge the Mac state staged in data/live_failover/mac_state/
   (newer-mtime-wins, never deletes/zeroes) -> bridge S1 data into ~/binance -> start
   live-failover-trading.service (tools/s1_trading_supervisor.sh, refuses without the lease).
   The Mac never trades while holder=s1 (run_with_watchdog.sh FENCED gate + agent SIGTERMs gated
   procs + ez_manage SERVER_HEARTBEAT_BLOCK). Failback: Mac heartbeat + data fresh for
   LF_FAILBACK_S (600 s) or pin=mac -> stop the trading unit, verify no S1 trader is left, THEN
   lease holder=mac gen+1 (the Mac agent pulls S1 account state before un-fencing).
   Manual override: tools/live_failover_ctl.sh {status|force-s1|force-mac|auto|arm|disarm}.
Every switch -> ~/logs/live_failover.log + data/live_failover/ALERTS.jsonl + ALERT_LATEST.json.
Fixtures/tests: LF_LIVE_DIR / LF_SANDBOX_DIR / LF_LOG / LF_DRY_RUN=1 / LF_FAKE_NOW.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_failover_common as C  # noqa: E402

LIVE = Path(os.environ.get("LF_LIVE_DIR", "/home/niels/binance"))
SANDBOX = Path(os.environ.get("LF_SANDBOX_DIR", "/home/niels/binance-sandbox"))
STACK = Path(os.environ.get("LF_STACK_DIR", str(LIVE / "ez_s1_stack")))
LF = LIVE / "data" / "live_failover"
LOG = Path(os.environ.get("LF_LOG", "/home/niels/logs/live_failover.log"))
DRY = os.environ.get("LF_DRY_RUN", "0") == "1"
TICK_S = float(os.environ.get("LF_TICK_S", "15"))
HB_FRESH_S = float(os.environ.get("LF_HB_FRESH_S", "90"))
RECOVER_S = float(os.environ.get("LF_RECOVER_S", "600"))
TAKEOVER_HB_S = float(os.environ.get("LF_TAKEOVER_HB_S", "300"))
TAKEOVER_CONFIRM = int(os.environ.get("LF_TAKEOVER_CONFIRM", "4"))
FAILBACK_S = float(os.environ.get("LF_FAILBACK_S", "600"))
FENCE_WAIT_S = float(os.environ.get("LF_FENCE_WAIT_S", "120"))
WITNESS_S = float(os.environ.get("LF_WITNESS_S", "300"))
UNITS = {"crypto": "live-failover-crypto-producers.service", "stocks": "live-failover-stocks-producers.service", "trading": "live-failover-trading.service"}
TRADER_PATTERN = r"python.*(ez_manage\.py --account|tradier_manage\.py --accounts)"
HOST = socket.gethostname()


def log(msg):
    line = f"[{C.iso()}] {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def alert(kind, **fields):
    row = {"ts": C.iso(), "epoch": C.now_s(), "host": HOST, "kind": kind, **fields}
    LF.mkdir(parents=True, exist_ok=True)
    with open(LF / "ALERTS.jsonl", "a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    C.atomic_write_json(LF / "ALERT_LATEST.json", row)
    log(f"ALERT {kind} {json.dumps(fields, default=str)}")


class Actions:
    """Side effects, isolated so the test harness can record them instead (LF_DRY_RUN=1)."""

    def __init__(self, dry=DRY):
        self.dry = dry
        self.calls = []
        self.fake_active = set()

    def _systemctl(self, verb, unit):
        self.calls.append((verb, unit))
        if self.dry:
            if verb == "start":
                self.fake_active.add(unit)
            elif verb == "stop":
                self.fake_active.discard(unit)
            return 0
        return subprocess.run(["sudo", "-n", "systemctl", verb, unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120).returncode

    def unit_active(self, unit):
        if self.dry:
            return unit in self.fake_active
        return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0

    def start(self, unit):
        if not self.unit_active(unit):
            rc = self._systemctl("start", unit)
            log(f"systemctl start {unit} rc={rc}")

    def stop(self, unit):
        if self.unit_active(unit):
            rc = self._systemctl("stop", unit)
            log(f"systemctl stop {unit} rc={rc}")

    def traders_alive(self):
        if self.dry:
            return 1 if UNITS["trading"] in self.fake_active else 0
        out = subprocess.run(["pgrep", "-f", TRADER_PATTERN], capture_output=True, text=True).stdout.split()
        return len([pid for pid in out if pid.strip() and int(pid) != os.getpid()])


def load_lease():
    lease = C.read_json(LF / "lease.json")
    if not isinstance(lease, dict) or lease.get("holder") not in ("mac", "s1"):
        lease = {"holder": "mac", "gen": 1, "since": C.iso(), "reason": "initialised", "pin": None}
        C.atomic_write_json(LF / "lease.json", lease)
    return lease


def write_lease(lease, holder, reason):
    new = dict(lease, holder=holder, gen=int(lease.get("gen", 0)) + 1, since=C.iso(), since_epoch=C.now_s(), reason=reason, by=HOST)
    C.atomic_write_json(LF / "lease.json", new)
    alert("LEASE_" + holder.upper(), gen=new["gen"], reason=reason)
    return new


def age_of(path, now):
    return C.file_age(path, now)


def newest_mtime_age(directory, now, prefix=None, exclude_suffix=None, limit=None):
    newest = 0.0
    try:
        with os.scandir(directory) as entries:
            for count, entry in enumerate(entries):
                if limit and count > limit:
                    break
                if prefix and not entry.name.startswith(prefix):
                    continue
                if exclude_suffix and entry.name.endswith(exclude_suffix):
                    continue
                try:
                    newest = max(newest, entry.stat().st_mtime)
                except OSError:
                    pass
    except OSError:
        return None
    return (now - newest) if newest else None


def mac_view(now):
    """Everything the S1 watchdog knows about the Mac, from files the Mac pushes here."""
    hb = C.read_json(LF / "mac_data_heartbeat.json") or C.read_json(LIVE / "data" / "mac_data_heartbeat.json")
    hb_age = (now - float(hb.get("epoch", 0))) if isinstance(hb, dict) and hb.get("epoch") else None
    live_hb = C.read_json(LIVE / "data" / "mac_live_heartbeat.json")
    live_hb_age = (now - float(live_hb.get("epoch", 0))) if isinstance(live_hb, dict) and live_hb.get("epoch") else None
    pulse_ages = [a for a in (age_of(LIVE / "data" / "mac_order_pulse.json", now), age_of(SANDBOX / "data" / "mac_order_pulse.json", now)) if a is not None]
    witnesses = [a for a in [live_hb_age] + pulse_ages if a is not None]
    md_age = newest_mtime_age(LIVE / "data", now, prefix="market_data_2", exclude_suffix="_s1.json")
    kl_age = newest_mtime_age(SANDBOX / "klines_cache_macbook", now)
    crypto_accounts_alive = None
    if isinstance(live_hb, dict) and live_hb_age is not None and live_hb_age < 180:
        accounts = live_hb.get("accounts") or {}
        crypto_accounts_alive = sum(1 for acct in C.CRYPTO_ACCOUNTS if accounts.get(acct))
    return {"hb": hb if isinstance(hb, dict) else None, "hb_age": hb_age, "live_hb_age": live_hb_age, "witness_min_age": min(witnesses) if witnesses else None,
            "mac_market_data_push_age": md_age, "mac_klines_push_age": kl_age, "crypto_accounts_alive": crypto_accounts_alive}


def venue_reading(view, venue, now):
    hb = view["hb"]
    if hb is not None and view["hb_age"] is not None and view["hb_age"] <= HB_FRESH_S:
        reading = (hb.get("venues") or {}).get(venue) or {}
        return {"applicable": bool(reading.get("applicable")), "stale": bool(reading.get("applicable") and reading.get("stale")), "reasons": reading.get("reasons", []), "source": "mac_heartbeat"}
    applicable = True if venue == "crypto" else C.stocks_applicable(now)
    age = view["hb_age"]
    return {"applicable": applicable, "stale": applicable, "reasons": [f"heartbeat_absent age={None if age is None else int(age)}s"] if applicable else ["market_closed"], "source": "s1_inference"}


def _link_or_copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        os.link(src, tmp)
    except OSError:
        shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def build_outbox(venue, now):
    """Publish the freshest S1 copy of every live symbol/TF (+ snapshot) for the Mac to pull."""
    out = LF / "outbox" / venue
    manifest = {"venue": venue, "generated_at": C.iso(now), "generated_epoch": now, "host": HOST, "provenance": f"S1:{HOST}", "files": {}, "snapshot": None}
    if venue == "crypto":
        symbols = C.symbols_for(LIVE, C.CRYPTO_ACCOUNTS)
        sources = [STACK / "klines_cache"]
        tfs = C.CRYPTO_DELIVER_TFS
        snaps = [STACK / "data" / "latest_market_data.json"]
        extras = [STACK / f"price_cache_{n}.json" for n in (1, 2, 3)]
    else:
        symbols = C.symbols_for(LIVE, C.STOCK_ACCOUNTS)
        sources = [LIVE / "klines_cache" / "tradier", SANDBOX / "klines_cache" / "tradier"]
        tfs = C.STOCK_DELIVER_TFS
        snaps = [LIVE / "data" / "tradier" / "tradier_indicators_latest.json", SANDBOX / "data" / "tradier" / "tradier_indicators_latest.json"]
        extras = []
    for symbol in symbols:
        for tf in tfs:
            best, best_ts = None, None
            for src_dir in sources:
                path = src_dir / f"{symbol}_{tf}.json"
                ts = C.newest_bar_open(path)
                if ts is not None and (best_ts is None or ts > best_ts):
                    best, best_ts = path, ts
            if best is None:
                continue
            dst = out / "klines" / best.name
            if not dst.exists() or C.newest_bar_open(dst) != best_ts:
                _link_or_copy(best, dst)
            manifest["files"][best.name] = {"newest_bar": C.iso(best_ts), "src": str(best)}
    live_snaps = [(p.stat().st_mtime, p) for p in snaps if p.exists()]
    if live_snaps:
        mtime, snap = max(live_snaps)
        _link_or_copy(snap, out / "snapshot" / snap.name)
        manifest["snapshot"] = {"name": snap.name, "src": str(snap), "written_at": C.iso(mtime), "written_epoch": mtime}
    for extra in extras:
        if extra.exists():
            _link_or_copy(extra, out / "extras" / extra.name)
    C.atomic_write_json(out / "MANIFEST.json", manifest)
    return manifest


def clear_outbox(venue):
    target = LF / "outbox" / venue
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def bridge_local(now):
    """While S1 holds the lease: feed ~/binance (where the S1 traders read) from the S1 producers."""
    rows = []
    out = LF / "outbox"
    C.apply_newer_klines(out / "crypto" / "klines", LIVE / "klines_cache", rows, provenance=f"S1:{STACK}")
    C.apply_newer_klines(out / "stocks" / "klines", LIVE / "klines_cache" / "tradier", rows, provenance="S1:tradier")
    snap = STACK / "data" / "latest_market_data.json"
    if snap.exists():
        label = time.strftime("%Y%m%d_%H%M%S", time.gmtime(snap.stat().st_mtime))
        target = LIVE / "data" / f"market_data_{label}_s1.json"
        if not target.exists():
            _link_or_copy(snap, target)
            for old in sorted((LIVE / "data").glob("market_data_*_s1.json"), reverse=True)[20:]:
                old.unlink(missing_ok=True)
    for n in (1, 2, 3):
        src, dst = STACK / f"price_cache_{n}.json", LIVE / f"price_cache_{n}.json"
        if src.exists() and (not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime + 5):
            _link_or_copy(src, dst)
    return rows


def merge_mac_state():
    """Mac state staged on S1 -> ~/binance/<acct>/ (newer mtime wins, never deletes)."""
    staged_root = LF / "mac_state"
    merged = 0
    if not staged_root.is_dir():
        log("STATE_MERGE: no staged Mac state found (using S1 copies as-is)")
        return 0
    for staged in staged_root.rglob("*"):
        if not staged.is_file() or staged.name.startswith("."):
            continue
        rel = staged.relative_to(staged_root)
        dst = LIVE / (rel.relative_to("root") if rel.parts[0] == "root" else rel)
        if dst.exists() and dst.stat().st_mtime >= staged.stat().st_mtime:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.lfmerge.tmp")
        shutil.copy2(staged, tmp)
        os.replace(tmp, dst)
        merged += 1
    log(f"STATE_MERGE: {merged} newer Mac state files applied to {LIVE}")
    return merged


class Watchdog:
    def __init__(self, actions=None):
        self.actions = actions or Actions()
        self.state = C.read_json(LF / "state.json", {}) or {}
        self.state.setdefault("venues", {"crypto": {"mode": "NORMAL"}, "stocks": {"mode": "NORMAL"}})
        self.state.setdefault("takeover", {"confirm": 0})
        self.state.setdefault("failback", {})

    def armed(self):
        return (LF / "TRADING_FAILOVER_ENABLED").exists()

    def tick(self):
        now = C.now_s()
        view = mac_view(now)
        lease = load_lease()
        readings = {}
        for venue in ("crypto", "stocks"):
            readings[venue] = venue_reading(view, venue, now)
            self.data_failover(venue, readings[venue], lease, now)
        lease = self.trading_failover(view, readings, lease, now)
        if lease["holder"] == "s1":
            rows = bridge_local(now)
            if rows:
                log(f"BRIDGE {len(rows)} klines files refreshed into {LIVE}")
        self.state.update({"updated": C.iso(now), "updated_epoch": now, "host": HOST, "dry_run": self.actions.dry, "armed": self.armed(), "view": {k: v for k, v in view.items() if k != "hb"}, "readings": readings, "lease": lease})
        C.atomic_write_json(LF / "state.json", self.state)
        return self.state

    def data_failover(self, venue, reading, lease, now):
        vstate = self.state["venues"].setdefault(venue, {"mode": "NORMAL"})
        unit = UNITS[venue]
        need = reading["applicable"] and reading["stale"]
        hold = lease["holder"] == "s1" and reading["applicable"]
        if vstate.get("mode") != "ACTIVE":
            if need or hold:
                vstate.update(mode="ACTIVE", since=C.iso(now), since_epoch=now, fresh_since=None, activations=int(vstate.get("activations", 0)) + 1, trigger=reading["reasons"])
                alert("DATA_FAILOVER_ON", venue=venue, reasons=reading["reasons"], source=reading["source"])
                self.actions.start(unit)
                manifest = build_outbox(venue, now)
                vstate["first_outbox_epoch"] = C.now_s()
                log(f"DATA_FAILOVER {venue}: producers started, outbox {len(manifest['files'])} files snapshot={bool(manifest['snapshot'])} in {C.now_s() - now:.2f}s")
            return
        if not reading["applicable"] and not hold:
            self._data_off(venue, vstate, "venue not applicable (market closed)", now)
            return
        if need or hold:
            vstate["fresh_since"] = None
        else:
            vstate["fresh_since"] = vstate.get("fresh_since") or now
            if now - vstate["fresh_since"] >= RECOVER_S:
                self._data_off(venue, vstate, f"Mac fresh for {int(now - vstate['fresh_since'])}s", now)
                return
        self.actions.start(unit)
        build_outbox(venue, now)

    def _data_off(self, venue, vstate, why, now):
        self.actions.stop(UNITS[venue])
        clear_outbox(venue)
        vstate.update(mode="NORMAL", since=C.iso(now), since_epoch=now, fresh_since=None)
        alert("DATA_FAILOVER_OFF", venue=venue, reason=why)

    def trading_failover(self, view, readings, lease, now):
        tk = self.state["takeover"]
        fb = self.state["failback"]
        hb_age = view["hb_age"]
        hb_absent = hb_age is None or hb_age >= TAKEOVER_HB_S
        pushes_stopped = all(a is None or a >= TAKEOVER_HB_S for a in (view["mac_market_data_push_age"], view["mac_klines_push_age"]))
        unreachable = view["witness_min_age"] is None or view["witness_min_age"] >= WITNESS_S
        if view["crypto_accounts_alive"] == 0:
            tk["dead_since"] = tk.get("dead_since") or now
        else:
            tk["dead_since"] = None
        trading_dead = bool(tk.get("dead_since")) and now - tk["dead_since"] >= TAKEOVER_HB_S
        cond = hb_absent and pushes_stopped and (unreachable or trading_dead)
        tk.update(hb_absent=hb_absent, pushes_stopped=pushes_stopped, unreachable=unreachable, trading_dead=trading_dead, cond=cond)
        pin = lease.get("pin")
        if lease["holder"] == "mac":
            fb.clear()
            tk["confirm"] = (int(tk.get("confirm", 0)) + 1) if cond else 0
            want = pin == "s1" or (pin != "mac" and tk["confirm"] >= TAKEOVER_CONFIRM)
            if want and not self.armed() and pin != "s1":
                if tk["confirm"] == TAKEOVER_CONFIRM:
                    alert("TRADING_FAILOVER_WOULD_TAKE_OVER_DISARMED", detail={k: tk[k] for k in ("hb_absent", "pushes_stopped", "unreachable", "trading_dead")})
                return lease
            if want:
                lease = write_lease(lease, "s1", "manual force-s1" if pin == "s1" else f"Mac down: hb_age={None if hb_age is None else int(hb_age)}s pushes_stopped unreachable={unreachable} trading_dead={trading_dead}")
                merge_mac_state()
                build_outbox("crypto", now)
                if C.stocks_applicable(now) or readings["stocks"]["applicable"]:
                    build_outbox("stocks", now)
                bridge_local(now)
            else:
                return lease
        # holder == s1
        mac_fenced = isinstance(view["hb"], dict) and int(view["hb"].get("fence_ack_gen", -1)) == int(lease["gen"]) and int(view["hb"].get("trading_procs", 1)) == 0
        mac_silent = hb_absent or (hb_age is not None and hb_age >= FENCE_WAIT_S)
        healthy = (hb_age is not None and hb_age <= HB_FRESH_S and not any(r["applicable"] and r["stale"] for r in readings.values()))
        if pin == "mac" or (pin != "s1" and healthy):
            fb["healthy_since"] = fb.get("healthy_since") or now
            if pin == "mac" or now - fb["healthy_since"] >= FAILBACK_S:
                return self.failback(lease, "manual force-mac" if pin == "mac" else f"Mac healthy {int(now - fb['healthy_since'])}s")
        else:
            fb["healthy_since"] = None
        if mac_fenced or mac_silent:
            self.actions.start(UNITS["trading"])
        else:
            log(f"TRADING_FAILOVER: lease gen {lease['gen']} held by S1 but Mac not yet fenced (hb_age={hb_age}) — waiting before starting S1 traders")
        return lease

    def failback(self, lease, why):
        self.actions.stop(UNITS["trading"])
        deadline = time.time() + (0 if self.actions.dry else 90)
        while self.actions.traders_alive() and time.time() < deadline:
            time.sleep(2)
        left = self.actions.traders_alive()
        if left:
            alert("FAILBACK_BLOCKED", reason=f"{left} S1 trader processes still alive after stop — lease stays with S1")
            return lease
        self.state["failback"] = {}
        self.state["takeover"] = {"confirm": 0}
        return write_lease(lease, "mac", why)


def set_pin(pin):
    lease = load_lease()
    lease["pin"] = pin
    C.atomic_write_json(LF / "lease.json", lease)
    alert("LEASE_PIN", pin=pin, holder=lease["holder"], gen=lease["gen"])
    return lease


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pin", choices=["mac", "s1", "auto"])
    args = parser.parse_args()
    LF.mkdir(parents=True, exist_ok=True)
    if args.pin:
        print(json.dumps(set_pin(None if args.pin == "auto" else args.pin), indent=1))
        return 0
    if args.status:
        print(json.dumps({"lease": load_lease(), "state": C.read_json(LF / "state.json"), "armed": (LF / "TRADING_FAILOVER_ENABLED").exists()}, indent=1, default=str))
        return 0
    watchdog = Watchdog()
    if args.once:
        print(json.dumps(watchdog.tick(), indent=1, default=str))
        return 0
    log(f"START live_failover_s1 tick={TICK_S}s dry={DRY} armed={watchdog.armed()} live={LIVE}")
    while True:
        started = time.time()
        try:
            watchdog.tick()
        except Exception as exc:  # never die: the watchdog IS the failover
            log(f"ERROR tick failed: {exc!r}")
        time.sleep(max(1.0, TICK_S - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
