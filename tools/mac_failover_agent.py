#!/usr/bin/env python3
"""Mac side of LIVE FAILOVER S1 (2026-10-13 USER, FIRST PRIORITY). launchd
com.niels.live-failover-agent (KeepAlive). Every LF_TICK_S (15 s):

1. MEASURE the freshness of the LIVE trading inputs per venue (live_failover_common.measure_*):
   crypto klines_cache 15m/3m + Mac-own market_data snapshot + ez_klines.log 418/429 lines;
   stocks (NYSE session only) klines_cache/tradier 5m/15m + tradier_indicators_latest.json +
   tradier_klines.log timeouts/429. Files this agent delivered from S1 are EXCLUDED, so S1 data
   can never make the Mac look healthy.
2. ONE ssh round trip to S1 (s1-pub direct, fallback s1-int via gateway): push
   data/live_failover/mac_data_heartbeat.json, read back lease.json + state.json.
3. FENCE: lease holder=s1 -> write data/live_failover/lease_local.json FIRST (run_with_watchdog.sh
   refuses to spawn gated traders: tools/live_failover_lease_gate.sh), SIGTERM every gated
   trader still running, report fence_ack_gen once none is left. Lease back to mac -> pull the
   S1 account state (rsync --update, never deletes) BEFORE writing holder=mac locally.
4. STATE STAGING (holder=mac only, every 30 s): account dirs + managed_stops.json ->
   S1 data/live_failover/mac_state/ (mtime-preserving) so an S1 takeover continues from them.
5. DELIVERY while S1 reports a venue ACTIVE: rsync S1 outbox/<venue>/ -> /Users/niels/live_failover_stage,
   then atomically replace Mac klines only where the S1 bar is newer; crypto snapshot published as
   data/market_data_<S1 write time>_s1.json (content gate scripts/s1_snapshot_fresh_gate.py);
   tradier_indicators_latest.json / price_cache_N.json only when the Mac's own copy is stale.
   Provenance -> data/live_failover/delivery.jsonl. Never places orders.
Fixtures/tests: LF_BASE, LF_STAGE, LF_NO_SSH=1, LF_REMOTE_DIR, LF_LOGDIR.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_failover_common as C  # noqa: E402

BASE = Path(os.environ.get("LF_BASE", "/Users/niels/Documents/binance"))
LF = BASE / "data" / "live_failover"
STAGE = Path(os.environ.get("LF_STAGE", "/Users/niels/live_failover_stage"))
LOGDIR = Path(os.environ.get("LF_LOGDIR", "/Users/niels/logs"))
LOG = LOGDIR / "live_failover_agent.log"
REMOTE_LIVE = os.environ.get("LF_REMOTE_LIVE", "/home/niels/binance")
REMOTE_LF = f"{REMOTE_LIVE}/data/live_failover"
HOSTS = tuple(os.environ.get("LF_HOSTS", "s1-pub s1-int").split())
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", "-o", "ControlMaster=no", "-o", "ControlPath=none"]
TICK_S = float(os.environ.get("LF_TICK_S", "15"))
STATE_PUSH_S = float(os.environ.get("LF_STATE_PUSH_S", "30"))
GATED = ("ez_manage.py", "tradier_manage.py", "ez_positions_watchdog.py", "ez_positions.py", "ez_copilot.py", "ez_reentry_daemon.py", "tradier_positions.py")
TRADER_RX = r"python[^ ]* .*(" + "|".join(name.replace(".", r"\.") for name in GATED) + r")"
ACCOUNTS = C.CRYPTO_ACCOUNTS + C.STOCK_ACCOUNTS
VERSION = "2026-10-14.1"
PULL_LAG_S = float(os.environ.get("LF_PULL_LAG_S", "60"))
PULL_OFF_S = float(os.environ.get("LF_PULL_OFF_S", "600"))
PULLER_LABEL = "com.binance.ez-s1-indicators-pull"
PULLER_PLIST = Path(os.environ.get("LF_PULLER_PLIST", f"/Users/niels/Library/LaunchAgents/{PULLER_LABEL}.plist"))


def pull_needed(venue, reading, lag_s=None):
    """USER 2026-10-14: pull from S1 whenever the Mac's market data is >60 s behind the last closed bar
    (or 418/429 / Tradier errors are seen). Returns (needed, why)."""
    lag_s = PULL_LAG_S if lag_s is None else lag_s
    if not reading.get("applicable"):
        return False, "not applicable"
    why = []
    if venue == "crypto":
        if reading.get("producer_ban_lines"):
            why.append(f"ban_lines={reading['producer_ban_lines']}")
        age = reading.get("snapshot_age_s")
        if age is None or age > lag_s:
            why.append(f"snapshot_age={None if age is None else int(age)}s")
        keys = ("k15", "k3")
    else:
        if reading.get("producer_error_lines"):
            why.append(f"error_lines={reading['producer_error_lines']}")
        age = reading.get("indicators_age_s")
        if age is None or age > lag_s:
            why.append(f"indicators_age={None if age is None else int(age)}s")
        keys = ("k5", "k15")
    for key in keys:
        lag = (reading.get(key) or {}).get("median_behind_s")
        if lag is not None and lag > lag_s:
            why.append(f"{key}_behind={int(lag)}s")
    return bool(why), ",".join(why)


def log(msg):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as handle:
        handle.write(f"[{C.iso()}] {msg}\n")


def alert(kind, **fields):
    row = {"ts": C.iso(), "epoch": C.now_s(), "host": "macbook", "kind": kind, **fields}
    LF.mkdir(parents=True, exist_ok=True)
    with open(LF / "ALERTS.jsonl", "a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    log(f"ALERT {kind} {json.dumps(fields, default=str)}")


def ssh(host, command, stdin=None, timeout=25):
    return subprocess.run(["ssh", *SSH_OPTS, host, command], input=stdin, capture_output=True, text=True, timeout=timeout)


def rsync(args, timeout=60):
    shell = "ssh " + " ".join(SSH_OPTS)
    return subprocess.run(["rsync", "-a", "--timeout=30", "-e", shell, *args], capture_output=True, text=True, timeout=timeout)


def trader_pids():
    out = subprocess.run(["pgrep", "-f", TRADER_RX], capture_output=True, text=True).stdout.split()
    return [int(pid) for pid in out if pid.strip().isdigit() and int(pid) != os.getpid()]


class Agent:
    def __init__(self):
        LF.mkdir(parents=True, exist_ok=True)
        self.delivered = C.read_json(LF / "delivered_index.json", {}) or {}
        self.last_state_push = 0.0
        self.host_idx = 0
        self.published_s1 = []
        self.pull = C.read_json(LF / "pull_state.json", {}) or {}

    def delivered_set(self):
        live = set()
        for path, mtime in list(self.delivered.items()):
            try:
                if abs(Path(path).stat().st_mtime - mtime) < 0.001:
                    live.add(path)
                    continue
            except OSError:
                pass
            del self.delivered[path]
        return live

    def measure(self, now):
        delivered = self.delivered_set()
        return {"crypto": C.measure_crypto(BASE, now, LOGDIR / "ez_klines.log", delivered),
                "stocks": C.measure_stocks(BASE, now, LOGDIR / "tradier_klines.log", delivered)}

    def exchange(self, heartbeat):
        """Push the heartbeat and read lease+state in one ssh round trip (direct, then gateway)."""
        if os.environ.get("LF_NO_SSH") == "1":
            remote = Path(os.environ.get("LF_REMOTE_DIR", str(LF / "_fake_remote")))
            remote.mkdir(parents=True, exist_ok=True)
            C.atomic_write_json(remote / "mac_data_heartbeat.json", heartbeat)
            return C.read_json(remote / "lease.json"), C.read_json(remote / "state.json"), "fixture"
        payload = json.dumps(heartbeat, default=str)
        command = (f"D={REMOTE_LF}; mkdir -p $D && cat > $D/.mac_hb.tmp && mv -f $D/.mac_hb.tmp $D/mac_data_heartbeat.json"
                   f" && echo \"$SSH_CONNECTION\" > $D/mac_peer.txt; echo ===LEASE; cat $D/lease.json 2>/dev/null; echo; echo ===STATE; cat $D/state.json 2>/dev/null; true")
        for attempt in range(len(HOSTS)):
            host = HOSTS[(self.host_idx + attempt) % len(HOSTS)]
            try:
                result = ssh(host, command, stdin=payload)
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0 and "===LEASE" in result.stdout:
                self.host_idx = (self.host_idx + attempt) % len(HOSTS)
                lease_txt, _, state_txt = result.stdout.partition("===LEASE")[2].partition("===STATE")
                try:
                    lease = json.loads(lease_txt) if lease_txt.strip() else None
                except ValueError:
                    lease = None
                try:
                    state = json.loads(state_txt) if state_txt.strip() else None
                except ValueError:
                    state = None
                return lease, state, host
        return None, None, None

    def fence(self, lease):
        local = C.read_json(LF / "lease_local.json", {}) or {}
        if local.get("holder") != "s1" or local.get("gen") != lease.get("gen"):
            C.atomic_write_json(LF / "lease_local.json", {"holder": "s1", "gen": lease.get("gen"), "fetched_epoch": C.now_s(), "fetched_at": C.iso(), "reason": lease.get("reason")})
            alert("MAC_FENCED", gen=lease.get("gen"), reason=lease.get("reason"))
        pids = [] if os.environ.get("LF_NO_KILL") == "1" else trader_pids()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if pids:
            log(f"FENCE: SIGTERM {len(pids)} gated trader pids {pids}")
        return 0 if os.environ.get("LF_NO_KILL") == "1" else len(trader_pids())

    def unfence(self, lease, host):
        local = C.read_json(LF / "lease_local.json", {}) or {}
        if local.get("holder") == "s1":
            ok = self.pull_state_back(host)
            if not ok:
                log("UNFENCE deferred: S1 account state pull failed — staying fenced")
                return False
            alert("MAC_UNFENCED", gen=lease.get("gen"), reason=lease.get("reason"))
        if local.get("holder") != "mac" or local.get("gen") != lease.get("gen"):
            C.atomic_write_json(LF / "lease_local.json", {"holder": "mac", "gen": lease.get("gen"), "fetched_epoch": C.now_s(), "fetched_at": C.iso(), "reason": lease.get("reason")})
        return True

    def pull_state_back(self, host):
        if os.environ.get("LF_NO_SSH") == "1":
            return True
        ok = True
        for account in ACCOUNTS:
            result = rsync(["--update", "--exclude=.*", "--exclude=backups/", "--exclude=*.bak", f"{host}:{REMOTE_LIVE}/{account}/", str(BASE / account) + "/"])
            if result.returncode not in (0, 23, 24):
                ok = False
                log(f"STATE_PULL {account} rc={result.returncode} {result.stderr[-200:]}")
        result = rsync(["--update", f"{host}:{REMOTE_LIVE}/managed_stops.json", str(BASE) + "/"])
        log(f"STATE_PULL from {host} ok={ok} managed_stops rc={result.returncode}")
        return ok

    def push_state(self, host, now):
        if os.environ.get("LF_NO_SSH") == "1" or now - self.last_state_push < STATE_PUSH_S:
            return
        self.last_state_push = now
        args = ["--exclude=.*", "--exclude=backups/", "--exclude=*.bak", "--exclude=*.atom", "--exclude=*.tmp"]
        sources = [str(BASE / account) for account in ACCOUNTS if (BASE / account).is_dir()]
        result = rsync([*args, "--rsync-path", f"mkdir -p {REMOTE_LF}/mac_state/root && rsync", *sources, f"{host}:{REMOTE_LF}/mac_state/"], timeout=90)
        rsync([str(BASE / "managed_stops.json"), f"{host}:{REMOTE_LF}/mac_state/root/"])
        if result.returncode not in (0, 23, 24):
            log(f"STATE_PUSH rc={result.returncode} {result.stderr[-200:]}")

    def deliver(self, venue, host, now):
        stage = STAGE / venue
        stage.mkdir(parents=True, exist_ok=True)
        if os.environ.get("LF_NO_SSH") == "1":
            src = Path(os.environ.get("LF_REMOTE_DIR", str(LF / "_fake_remote"))) / "outbox" / venue
            result = subprocess.run(["rsync", "-a", "--delete", str(src) + "/", str(stage) + "/"], capture_output=True, text=True)
        else:
            result = rsync(["--delete", "-z", f"{host}:{REMOTE_LF}/outbox/{venue}/", str(stage) + "/"], timeout=120)
        if result.returncode not in (0, 23, 24):
            log(f"DELIVER {venue} pull rc={result.returncode} {result.stderr[-200:]}")
            return 0
        manifest = C.read_json(stage / "MANIFEST.json", {}) or {}
        provenance = manifest.get("provenance", "S1")
        rows = []
        dest = BASE / "klines_cache" if venue == "crypto" else BASE / "klines_cache" / "tradier"
        replaced = C.apply_newer_klines(stage / "klines", dest, rows, provenance=provenance)
        for path in replaced:
            self.delivered[path] = Path(path).stat().st_mtime
        snap = manifest.get("snapshot") or {}
        if venue == "crypto" and snap:
            self.deliver_crypto_snapshot(stage / "snapshot" / snap["name"], snap, rows, provenance)
            for staged in sorted((stage / "extras").glob("price_cache_*.json")) if (stage / "extras").is_dir() else []:
                target = BASE / staged.name
                age = C.file_age(target, now)
                if (age is None or age > 120) and (not target.exists() or staged.stat().st_mtime > target.stat().st_mtime):
                    self._replace(staged, target)
                    self.delivered[str(target)] = target.stat().st_mtime
                    rows.append({"file": str(target), "src": provenance})
        if venue == "stocks" and snap:
            target = BASE / "data" / "tradier" / "tradier_indicators_latest.json"
            staged = stage / "snapshot" / snap["name"]
            age = C.file_age(target, now)
            if staged.exists() and (age is None or age > C.SNAPSHOT_STALE_S) and float(snap.get("written_epoch", 0)) > (now - age if age is not None else 0):
                self._replace(staged, target)
                self.delivered[str(target)] = target.stat().st_mtime
                rows.append({"file": str(target), "src": provenance, "written_at": snap.get("written_at")})
        if rows:
            with open(LF / "delivery.jsonl", "a") as handle:
                for row in rows:
                    handle.write(json.dumps({"ts": C.iso(), "venue": venue, **row}) + "\n")
            log(f"DELIVER {venue}: {len(rows)} files from {provenance} (manifest {manifest.get('generated_at')})")
        C.atomic_write_json(LF / "delivered_index.json", self.delivered)
        return len(rows)

    def _replace(self, src, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.lf.tmp")
        with open(src, "rb") as reader, open(tmp, "wb") as writer:
            writer.write(reader.read())
            writer.flush()
            os.fsync(writer.fileno())
        os.utime(tmp, (src.stat().st_mtime, src.stat().st_mtime))
        os.replace(tmp, target)

    def deliver_crypto_snapshot(self, staged, snap, rows, provenance):
        if not staged.exists():
            return
        label = time.strftime("%Y%m%d_%H%M%S", time.gmtime(float(snap.get("written_epoch", staged.stat().st_mtime))))
        target = BASE / "data" / f"market_data_{label}_s1.json"
        if target.exists():
            return
        own = sorted((p for p in (BASE / "data").glob("market_data_2*.json") if not p.name.endswith("_s1.json") and "worker" not in p.name), key=lambda p: p.stat().st_mtime)
        gate = BASE / "scripts" / "s1_snapshot_fresh_gate.py"
        if gate.exists():
            verdict = subprocess.run([sys.executable, str(gate), str(staged), str(own[-1]) if own else "-"], capture_output=True, text=True)
            if verdict.returncode != 0:
                log(f"SNAPSHOT_GATE_REJECT {staged.name}: {verdict.stdout.strip()[:200]}")
                return
        self._replace(staged, target)
        self.published_s1.append(str(target))
        rows.append({"file": str(target), "src": provenance, "written_at": snap.get("written_at")})
        for old in sorted((BASE / "data").glob("market_data_*_s1.json"), reverse=True)[20:]:
            old.unlink(missing_ok=True)

    def withdraw_s1_snapshots(self):
        for name in self.published_s1:
            Path(name).unlink(missing_ok=True)
        if self.published_s1:
            log(f"WITHDRAW {len(self.published_s1)} S1 snapshots (crypto data failover ended)")
        self.published_s1 = []

    def update_pull(self, venues, now):
        """On-demand S1 pull per venue with 10-min hysteresis; every switch logged + alerted."""
        for venue in ("crypto", "stocks"):
            st = self.pull.setdefault(venue, {"on": False})
            need, why = pull_needed(venue, venues[venue])
            if need:
                st["fresh_since"] = None
                if not st.get("on"):
                    st.update(on=True, since=C.iso(now), why=why)
                    alert("PULL_ON", venue=venue, why=why)
                    if venue == "crypto":
                        self.puller(True)
            elif st.get("on"):
                st["fresh_since"] = st.get("fresh_since") or now
                if now - st["fresh_since"] >= PULL_OFF_S:
                    st.update(on=False, since=C.iso(now), why=f"fresh {int(now - st['fresh_since'])}s", fresh_since=None)
                    alert("PULL_OFF", venue=venue, why=st["why"])
                    if venue == "crypto":
                        self.puller(False)
            if venue == "crypto" and st.get("on") and not self.puller_loaded():
                self.puller(True)
        C.atomic_write_json(LF / "pull_state.json", self.pull)
        return {venue: bool(self.pull[venue].get("on")) for venue in ("crypto", "stocks")}

    def puller_loaded(self):
        if os.environ.get("LF_NO_LAUNCHCTL") == "1":
            return bool(self.pull.get("_fake_loaded"))
        return subprocess.run(["launchctl", "list", PULLER_LABEL], capture_output=True).returncode == 0

    def puller(self, on):
        if os.environ.get("LF_NO_LAUNCHCTL") == "1":
            self.pull["_fake_loaded"] = on
            log(f"PULLER {'load' if on else 'unload'} (fixture)")
            return
        if on and not self.puller_loaded():
            result = subprocess.run(["launchctl", "load", "-w", str(PULLER_PLIST)], capture_output=True, text=True)
            log(f"PULLER load {PULLER_LABEL} rc={result.returncode} {result.stderr.strip()[:120]}")
        elif not on and self.puller_loaded():
            result = subprocess.run(["launchctl", "unload", "-w", str(PULLER_PLIST)], capture_output=True, text=True)
            log(f"PULLER unload {PULLER_LABEL} rc={result.returncode} {result.stderr.strip()[:120]}")
            for stale in (BASE / "data").glob("market_data_*_s1.json"):
                stale.unlink(missing_ok=True)

    def tick(self):
        now = C.now_s()
        venues = self.measure(now)
        pulls = self.update_pull(venues, now)
        local = C.read_json(LF / "lease_local.json", {}) or {}
        procs = 0 if os.environ.get("LF_NO_KILL") == "1" else len(trader_pids())
        heartbeat = {"epoch": now, "ts": C.iso(now), "host": "macbook", "version": VERSION, "venues": venues, "trading_procs": procs,
                     "lease_local": local, "pull_request": pulls, "fence_ack_gen": local.get("gen") if local.get("holder") == "s1" and procs == 0 else None}
        C.atomic_write_json(LF / "mac_data_heartbeat.json", heartbeat)
        lease, state, host = self.exchange(heartbeat)
        if host is None:
            log(f"S1 unreachable via {HOSTS} — keeping local lease {local.get('holder')} gen={local.get('gen')}")
            return heartbeat
        if lease and lease.get("holder") == "s1":
            left = self.fence(lease)
            heartbeat["trading_procs"] = left
        elif lease and lease.get("holder") == "mac":
            if self.unfence(lease, host):
                self.push_state(host, now)
        if isinstance(state, dict):
            C.atomic_write_json(LF / "s1_state.json", state)
            vstates = state.get("venues") or {}
            modes = {v: vstates.get(v, {}).get("mode") for v in ("crypto", "stocks")}
            for venue, mode in modes.items():
                if mode == "ACTIVE" or (pulls.get(venue) and vstates.get(venue, {}).get("outbox")):
                    self.deliver(venue, host, now)
            if modes.get("crypto") != "ACTIVE" and self.published_s1:
                self.withdraw_s1_snapshots()
        return heartbeat


def main():
    agent = Agent()
    once = "--once" in sys.argv
    log(f"START mac_failover_agent v{VERSION} tick={TICK_S}s hosts={HOSTS}")
    while True:
        started = time.time()
        try:
            beat = agent.tick()
            if once:
                print(json.dumps(beat, indent=1, default=str))
                return 0
        except Exception as exc:
            log(f"ERROR tick: {exc!r}")
            if once:
                raise
        time.sleep(max(1.0, TICK_S - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
