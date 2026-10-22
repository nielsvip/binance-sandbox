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
6. TRADING HEALTH (2026-10-14 USER "take over whenever macbook doesnt trade for whatever reason, like ip bans"): every tick
   heartbeat["trading_health"] = live_failover_common.TradingHealth per venue (broker reachability from the single position
   writer's poll log incl. 401/-2015/418/429, order path ORDER_STALL_SKIP / LOOP_LAG / BLOCKED_POSITIONS_BROKER_UNREACHABLE /
   orders SUBMITTED, trader processes alive). Read from existing logs incrementally: zero broker calls, zero API weight.
   A holder=s1 lease may cover only some venues (lease "venues"): only those venues' gated traders are fenced.
Fixtures/tests: LF_BASE, LF_STAGE, LF_NO_SSH=1, LF_REMOTE_DIR, LF_LOGDIR.
"""
import glob
import json
import os
import re
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
GAG_ALARM_EVERY_S = float(os.environ.get("LF_GAG_ALARM_EVERY_S", "300"))  # a disarmed failover is announced at least this often
LOG = LOGDIR / "live_failover_agent.log"
REMOTE_LIVE = os.environ.get("LF_REMOTE_LIVE", "/home/niels/binance")
REMOTE_LF = f"{REMOTE_LIVE}/data/live_failover"
HOSTS = tuple(os.environ.get("LF_HOSTS", "s1-pub s1-int").split())
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", "-o", "ControlMaster=no", "-o", "ControlPath=none"]
TICK_S = float(os.environ.get("LF_TICK_S", "15"))
STATE_PUSH_S = float(os.environ.get("LF_STATE_PUSH_S", "30"))
SNAP_PUSH_S = float(os.environ.get("LF_SNAP_PUSH_S", "120"))
IND_UNPRODUCIBLE_S = float(os.environ.get("LF_IND_UNPRODUCIBLE_S", "300"))  # 2026-10-20: live snapshot not written this long = the Mac cannot produce indicators (the only kind that may take the lease)  # 2026-10-19 USER ("ez/tradier_indicators rsync to s1
# so when it has to take over it has all data (incl positions)"): stage the Mac's LIVE indicator snapshots on S1
# continuously, so a takeover starts from data that is minutes old instead of waiting out S1's own ez_indicators
# warm-up. Measured 2026-10-19 23:1xZ, with S1's crypto producers ACTIVE: S1's own latest_market_data.json was
# 2838 s old and its tradier_indicators_latest.json 7780 s old, because ez_indicators does not write the snapshot
# at all until all five timeframes finish one pass (25-47 min) and it had just restarted. tools/
# mac_market_data_push_s1.sh already pushes the TIMESTAMPED snapshots, but those are ez_indicators' HOURLY backup
# files, so that seed is up to an hour stale by construction.
# These land in mac_state/root/data/, so live_failover_s1.merge_mac_state applies them with its existing
# newer-mtime-wins rule: S1 adopts the Mac's snapshot only while the Mac's is actually fresher, and keeps its own
# the moment S1 is producing better data. That IS "the system with the best data should trade", as data.
# 120 s not 30 s: 13.8 MB of JSON per round, and no takeover can happen in under ~120 s anyway (heartbeat absent
# 60 s + TAKEOVER_CONFIRM 4 ticks). Compressed (-z) because these are large and highly compressible.
SNAP_SOURCES = ("data/latest_market_data.json", "data/tradier/tradier_indicators_latest.json")
# 2026-10-15 (USER live takeover test, Mac disconnected ~15 min): ez_positions_service_main.py / ez_positions_realtime.py
# were NOT gated. Since the 2026-10-14 single-writer refactor the writer is also an ORDER PATH — its FIXED_STOP live
# leg calls trade_manager.execute_now() directly — so while S1 held the lease and traded, the Mac could still have
# fired a FIXED_STOP close on the same account: two hosts, one position. In this test no breach happened on the Mac
# (0 breaches 19:27-19:39Z) so nothing double-fired, but the path was open. The fence now closes it.
GATED = ("ez_manage.py", "tradier_manage.py", "ez_positions_watchdog.py", "ez_positions.py", "ez_copilot.py", "ez_reentry_daemon.py", "tradier_positions.py", "ez_positions_service_main.py", "ez_positions_realtime.py")
# 2026-10-19 USER ("s1 ... instantly takes over whenever macbook is not trading or has old indicators"):
# S1 decides takeovers from trading_health kinds, but the two data-freshness signals that stranded
# 2026-10-19's stock entries (160/178 NPZs Friday-frozen -> 285 VEC_EXACT_STALE_FALLBACK, one open all
# morning) were never kinds. Measured HERE — the Mac owns the files, S1 must not infer them — and
# shipped inside trading_health[venue], so live_failover_s1.not_trading() picks them up via NT_NEED
# with no common.py change. Advisory + fail-closed: any error leaves existing kinds untouched.
LF_INDICATORS_STALE_S = float(os.environ.get("LF_INDICATORS_STALE_S", "7200"))
LF_INDICATORS_STALE_FRAC = float(os.environ.get("LF_INDICATORS_STALE_FRAC", "0.5"))
LF_INDICATORS_MIN_N = int(os.environ.get("LF_INDICATORS_MIN_N", "10"))
LF_POSITIONS_STALE_S = float(os.environ.get("LF_POSITIONS_STALE_S", "600"))
# 2026-10-22 USER: "ANY account that is not trading for >3min has to initiate a launch of all ez_ scripts on s1 plus
# the trading scripts for the account and start trading from s1 until macbook is proven to trade again, then s1 stands
# down immediately" — and this agent's own 10-20 note already quotes the same intent ("if any binance account stops
# trading its watchdog should fix it but if it stays down s1 should start trading that account only"). The per-account
# machinery existed (live_failover_s1._scoped_accounts narrows a takeover to a strict subset) but NO kind ever fired
# on "this account is not trading", so a wedged engine was invisible to the failover: measured 2026-10-22, men and fin
# executed nothing for hours while the lease sat holder=mac, 34.6 h stale, and S1 never moved.
LF_NOT_TRADING_S = float(os.environ.get("LF_NOT_TRADING_S", "180"))
# The lane must be alive before silence means anything: tools/pure_board_trader.py rewrites vec_driven.json every
# pass, so a fresh registry proves dictates are still being published and an idle live_exec is the ACCOUNT's fault.
LF_LANE_FRESH_S = float(os.environ.get("LF_LANE_FRESH_S", "2400"))
_STOCK_POSITION_FILES = ("state.json", "long_positions.json")
_CRYPTO_POSITION_FILES = ("long_positions.json", "short_positions.json")
TRADER_RX = r"python[^ ]* .*(" + "|".join(name.replace(".", r"\.") for name in GATED) + r")"
ACCOUNTS = C.CRYPTO_ACCOUNTS + C.STOCK_ACCOUNTS
VERSION = "2026-10-14.2"
PULL_LAG_S = float(os.environ.get("LF_PULL_LAG_S", "60"))
SEED_EVERY_S = float(os.environ.get("LF_SEED_EVERY_S", "120"))  # re-seed from S1 at most this often while fenced (was: every tick)
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


def _cmd_account(cmd):
    m = re.search(r"--accounts?\s+(\S+)", cmd or "")
    return m.group(1) if m else None


def _acct_venue(account):
    return "stocks" if account in C.STOCK_ACCOUNTS else "crypto"


def trader_pids(venues=None, accounts=None):
    """Gated trader pids, optionally only those of `venues` (tradier_* = stocks, the rest = crypto)
    and, with 2026-10-19 account scope, only those of `accounts` ({venue: [accts]}).
    Scripts without an --account(s) flag (venue-wide daemons) follow the venue rule: they are fenced
    whenever their venue is leased at all — shared-daemon degradation for siblings is bounded, while
    a venue daemon firing for an S1-held account would be dual trading."""
    out = subprocess.run(["pgrep", "-f", TRADER_RX], capture_output=True, text=True).stdout.split()
    pids = [int(pid) for pid in out if pid.strip().isdigit() and int(pid) != os.getpid()]
    if venues is None or set(venues) >= set(C.VENUES):
        venues = list(C.VENUES)
    if not accounts:
        accounts = None
    keep = []
    for pid in pids:
        try:
            cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        except Exception:
            continue
        name = next((g for g in GATED if g in cmd), None)
        if not name or C.script_venue(name) not in venues:
            continue
        if accounts is None:
            keep.append(pid)
            continue
        acct = _cmd_account(cmd)
        venue = C.script_venue(name)
        if acct is None or acct in (accounts.get(venue) or ()):
            keep.append(pid)
    return keep


def fenced_venues(local):
    return C.lease_venues(local) if (local or {}).get("holder") == "s1" else []


def _acct_proc_patterns(venue, account):
    if venue == "stocks":
        return [f"tradier_manage.py --accounts {account}", f"tradier_positions.py --accounts {account}"]
    return [f"ez_manage.py --account {account}", f"ez_positions_service_main.py {account}",
            f"ez_positions_realtime.py {account}"]


def scoped_fence_ack(local):
    """2026-10-19 ACCOUNT SCOPE: (gen, acked_accounts). gen when S1 holds the lease AND every scoped
    account's order-placing/writing processes are gone — siblings keep running, so the legacy
    trading_procs==0 bar can never clear on a scoped lease. Unscoped (legacy) keeps the old bar."""
    local = local or {}
    if local.get("holder") != "s1":
        return (None, [])
    scope = {k: list(v) for k, v in (local.get("accounts") or {}).items() if v}
    if not scope:
        pids = [] if os.environ.get("LF_NO_KILL") == "1" else trader_pids(C.lease_venues(local) or None)
        return (local.get("gen") if not pids else None, [])
    if os.environ.get("LF_NO_KILL") == "1":
        return (local.get("gen"), sorted({a for v in scope.values() for a in v}))
    acked = []
    for venue, accts in scope.items():
        for account in accts:
            alive = False
            for pat in _acct_proc_patterns(venue, account):
                try:
                    out = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True).stdout
                except Exception:
                    continue
                if any(pid.strip().isdigit() for pid in out.split()):
                    alive = True
                    break
            if not alive:
                acked.append(account)
    want = sorted({a for v in scope.values() for a in v})
    if sorted(acked) == want:
        return (local.get("gen"), want)
    return (None, acked)


def _npz_tail_ok(ts):
    """Same currency standard the dispatch gate holds workers to (tools/npz_cutoffs.tail_currency):
    a tail is current iff it reaches the latest RTH close for stocks — so Friday bars read FRESH at
    Monday's bell (nothing newer can exist; boards decided Friday) and stale mid-session. Fail-closed
    to the wall-clock bar on import error (never silently fresh). Monkeypatchable in tests."""
    try:
        from npz_cutoffs import tail_currency as _tc
        ok, _ = _tc(float(ts), "stocks")
        return bool(ok)
    except Exception:
        return (time.time() - float(ts)) <= LF_INDICATORS_STALE_S


def _in_open_grace(now):
    """First 60 min of the RTH session: Monday bars are still landing fleet-wide (S1 builds +
    5-min pulls), so NEITHER host can have fresh tails yet — firing here would hand S1 a venue
    nobody can enter on while churning exits through a startup gap. The kind arms at 10:30 ET."""
    try:
        import datetime as _dt
        from npz_cutoffs import ET as _ET
        et = _dt.datetime.fromtimestamp(float(now), _dt.timezone.utc).astimezone(_ET)
        if et.weekday() >= 5:
            return False
        mm = et.hour * 60 + et.minute
        return 570 <= mm < 630
    except Exception:
        return False


def _stock_indicator_universe():
    """Traded stock symbols whose NPZs the VEC_EXACT twin needs (lists + held + candidates)."""
    syms = set()
    for f in glob.glob(str(BASE / "symbols_tr[abc]_*.json")):
        try:
            d = json.load(open(f))
            l = d if isinstance(d, list) else (d.get("symbols") or list(d.keys()))
            syms |= {str(x).upper() for x in l if str(x).strip()}
        except Exception:
            pass
    for f in glob.glob(str(BASE / "tr[abc]" / "*_positions.json")) + glob.glob(str(BASE / "tr[abc]" / "reentry_candidates.json")):
        try:
            d = json.load(open(f))
            rows = d if isinstance(d, list) else list(d.keys())
            for r in rows:
                s = str(r).split(":")[-1].rsplit("_", 1)[0].upper()
                if s:
                    syms.add(s)
        except Exception:
            pass
    return syms


class Agent:
    def __init__(self):
        LF.mkdir(parents=True, exist_ok=True)
        self.delivered = C.read_json(LF / "delivered_index.json", {}) or {}
        self.last_state_push = 0.0
        self.last_snap_push = 0.0
        self.host_idx = 0
        self.published_s1 = []
        self.pull = C.read_json(LF / "pull_state.json", {}) or {}
        self.health = C.TradingHealth(LOGDIR)
        self._npz_bar = {}

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

    def seed_from_s1(self, host, venues=None):
        """While S1 holds the lease its ez_positions_service is the authoritative writer: pull its position files
        to a stage (never into the account dirs) and hand the Mac writer their opened_at via .identity_seed.json.
        2026-10-19: scoped to leased venues (seeding a Mac-live sibling from S1's stale files would corrupt it)."""
        stage = STAGE / "s1_positions"
        seeded = {}
        wanted = set(ACCOUNTS)
        if venues:
            wanted = {a for a in ACCOUNTS if _acct_venue(a) in venues}
        for account in sorted(wanted):
            target = stage / account
            target.mkdir(parents=True, exist_ok=True)
            if os.environ.get("LF_NO_SSH") == "1":
                src = Path(os.environ.get("LF_REMOTE_ACCOUNTS", str(LF / "_fake_remote_accounts"))) / account
                result = subprocess.run(["rsync", "-a", "--include=long_positions.json", "--include=short_positions.json", "--exclude=*", str(src) + "/", str(target) + "/"], capture_output=True, text=True)
            else:
                result = rsync(["--include=long_positions.json", "--include=short_positions.json", "--exclude=*", f"{host}:{REMOTE_LIVE}/{account}/", str(target) + "/"])
            files = [target / "long_positions.json", target / "short_positions.json"]
            if result.returncode in (0, 23, 24) and any(f.exists() for f in files) and (BASE / account).is_dir():
                seeded[account] = C.write_identity_seed(BASE / account, files, f"S1 lease writer via {host}")
        return seeded

    def fence(self, lease, host=None):
        # 2026-10-19 ACCOUNT SCOPE: lease "accounts" narrows the fence to those accounts; siblings keep
        # trading. A shrunken scope (yield) unfences the dropped accounts: best-effort state pull, then
        # the watchdog respawns them via the scope-aware gate. Scope lives in lease_local.json.
        local = C.read_json(LF / "lease_local.json", {}) or {}
        venues = C.lease_venues(lease)
        scope = {k: sorted(v) for k, v in (lease.get("accounts") or {}).items() if v}
        prev_scope = {k: sorted(v) for k, v in (local.get("accounts") or {}).items() if v}
        # 2026-10-20 fetched_epoch MUST BE A HEARTBEAT, NOT A CHANGE STAMP. This block previously wrote
        # lease_local.json only when holder/gen/venues/scope CHANGED, so fetched_epoch recorded the last CHANGE.
        # tools/live_failover_lease_gate.sh then refuses to fence when that stamp is older than LF_LEASE_MAX_AGE_S
        # (default 600 s) — the 2026-10-16 ghost-lease guard, added so a DEAD agent could not fence forever. Together
        # they mean a perfectly healthy S1 lease that lasts longer than 10 minutes stops fencing the Mac, the Mac's
        # watchdogs respawn their traders, and BOTH HOSTS TRADE THE SAME ACCOUNTS — the one outcome the lease exists to
        # prevent ("Mac and S1 must never both trade", USER 2026-10-13). Observed 2026-10-20 04:20Z: lease_local.json
        # stamped 03:50Z, i.e. 30 min old, while the agent was alive and writing its other state files every tick.
        # Re-stamping every tick keeps the ghost-lease protection intact — a dead agent still stops refreshing, the age
        # still grows, and the gate still unfences — while a live lease can no longer expire under a running agent.
        # The ALERT stays on CHANGE only, so this adds no alert noise.
        _changed = (local.get("holder") != "s1" or local.get("gen") != lease.get("gen")
                    or C.lease_venues(local) != venues or prev_scope != scope)
        if not _changed and local.get("holder") == "s1":
            C.atomic_write_json(LF / "lease_local.json", dict(local, fetched_epoch=C.now_s(), fetched_at=C.iso()))
        if _changed:
            C.atomic_write_json(LF / "lease_local.json", {"holder": "s1", "gen": lease.get("gen"), "venues": C.venues_str(venues), "run_venues": (lease.get("run_venues") or ""), "accounts": scope, "fetched_epoch": C.now_s(), "fetched_at": C.iso(), "reason": lease.get("reason")})
            alert("MAC_FENCED", gen=lease.get("gen"), venues=venues, accounts=scope or "all", reason=lease.get("reason"))
        yielded = sorted({a for v in venues for a in (prev_scope.get(v) or []) if a not in (scope.get(v) or [])})
        if yielded and local.get("holder") == "s1" and local.get("gen") == lease.get("gen"):
            ok = self.pull_state_back(host, venues, accounts=yielded) if host else True
            log(f"UNFENCE scoped accounts {yielded} (state pull ok={ok}) — watchdog respawns via gate")
        # THE MAC NEVER KILLS ITS OWN TRADERS (USER 2026-10-22: S1 "can NEVER block any mac action it needs to STAND
        # DOWN as soon as mac trades"). This loop SIGTERMed every gated Mac trader the moment S1 took the lease.
        # MEASURED 2026-10-22: "FENCE: SIGTERM 13 gated trader pids [50189, ...]" at 04:31:34Z killed fin's engine
        # (50189), and 05:00:37-41Z killed ang, men and fin (exit=143, signal 15) — while S1 ran zero ez_manage
        # processes. Combined with the spawn gate in tools/live_failover_lease_gate.sh (now Mac-exempt), accounts were
        # killed here and then refused a restart there. Dedup belongs to S1, which does not spawn without the lease.
        # LF_FENCE_KILL=1 restores the kill for the failover harness only.
        pids = trader_pids(venues, scope or None) if os.environ.get("LF_FENCE_KILL") == "1" else []
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if pids:
            log(f"FENCE: SIGTERM {len(pids)} gated trader pids {pids}")
        else:
            _alive = trader_pids(venues, scope or None)
            if _alive:
                log(f"FENCE: NOT killing {len(_alive)} Mac trader pids {_alive} — the Mac keeps trading; S1 must stand down (USER 2026-10-22)")
        if host:
            # 2026-10-15: seed at most once per lease generation / SEED_EVERY_S. This ran an rsync from S1 on EVERY
            # tick while fenced, so the 15 s tick stretched to ~180 s and the heartbeat this agent pushes to S1 went
            # stale — S1 then read "heartbeat_absent" from a perfectly healthy Mac, which both BLOCKS FAILBACK (the
            # Mac can never prove it is alive because it is busy proving it) and, at LF_TAKEOVER_HB_S=60, could
            # re-trigger a takeover. Liveness must never be starved by the work that liveness gates.
            _sg = getattr(self, "_seed_gen", None)
            _st = float(getattr(self, "_seed_ts", 0.0) or 0.0)
            if _sg != lease.get("gen") or C.now_s() - _st >= SEED_EVERY_S:
                self._seed_gen = lease.get("gen")
                self._seed_ts = C.now_s()
                self.seed_from_s1(host)
        return 0 if os.environ.get("LF_NO_KILL") == "1" else len(trader_pids(venues))

    def unfence(self, lease, host):
        local = C.read_json(LF / "lease_local.json", {}) or {}
        if local.get("holder") == "s1":
            ok = self.pull_state_back(host, fenced_venues(local))
            # 2026-10-14: S1's files carried takeover-time opened_at + phantom March crypto rows; the Mac writers re-read the broker
            # themselves, so failback never seeds from S1 (LF_UNFENCE_SEED=1 re-enables).
            seeded = self.seed_from_s1(host) if os.environ.get("LF_UNFENCE_SEED", "0") == "1" else {}
            log(f"UNFENCE identity seeds from S1 writer (opened_at carried, positions files untouched): {seeded}")
            if not ok:
                log("UNFENCE deferred: S1 account state pull failed — staying fenced")
                return False
            alert("MAC_UNFENCED", gen=lease.get("gen"), reason=lease.get("reason"))
        if local.get("holder") != "mac" or local.get("gen") != lease.get("gen"):
            C.atomic_write_json(LF / "lease_local.json", {"holder": "mac", "gen": lease.get("gen"), "fetched_epoch": C.now_s(), "fetched_at": C.iso(), "reason": lease.get("reason")})
        return True

    def pull_state_back(self, host, venues=C.VENUES, accounts=None):
        """Only the accounts of venues S1 actually traded (a partial-venue lease never touches the Mac's other venue).
        2026-10-19: with account scope, only the yielded accounts (pulling S1's stale files for a Mac-live
        sibling would corrupt it — --update skips older, but the explicit list is exact)."""
        if os.environ.get("LF_NO_SSH") == "1":
            return True
        ok = True
        if accounts is None:
            accounts = (C.CRYPTO_ACCOUNTS if "crypto" in venues else ()) + (C.STOCK_ACCOUNTS if "stocks" in venues else ())
        for account in accounts:
            result = rsync(["--update", "--exclude=.*", "--exclude=backups/", "--exclude=*.bak", "--exclude=*_positions.json", f"{host}:{REMOTE_LIVE}/{account}/", str(BASE / account) + "/"])
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

    def push_snapshots(self, host, now):
        """Stage the Mac's live indicator snapshots on S1 for merge_mac_state (see SNAP_PUSH_S)."""
        if os.environ.get("LF_NO_SSH") == "1" or now - self.last_snap_push < SNAP_PUSH_S:
            return
        self.last_snap_push = now
        for rel in SNAP_SOURCES:
            src = BASE / rel
            if not src.is_file():
                continue
            dest_dir = f"{REMOTE_LF}/mac_state/root/{os.path.dirname(rel)}"
            result = rsync(["-z", "--rsync-path", f"mkdir -p {dest_dir} && rsync", str(src), f"{host}:{dest_dir}/"], timeout=120)
            if result.returncode not in (0, 23, 24):
                log(f"SNAP_PUSH {rel} rc={result.returncode} {result.stderr[-200:]}")

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

    def fake_procs(self):
        """Fixture: LF_FAKE_PROCS='ang,inf,trb' = trader processes alive."""
        alive = set(os.environ.get("LF_FAKE_PROCS", "").split(","))
        return {"crypto": {a: int(a in alive) for a in C.CRYPTO_ACCOUNTS}, "stocks": {a: int(a in alive) for a in C.STOCK_ACCOUNTS}}

    def note_health(self, health):
        """Log every change of the per-venue not-trading verdict (the S1 decision input) once."""
        for venue in C.VENUES:
            verdict = (health.get(venue) or {}).get("kinds") if isinstance(health, dict) else None
            key = f"_health_{venue}"
            if verdict != getattr(self, key, None):
                setattr(self, key, verdict)
                log(f"TRADING_HEALTH {venue}: kinds={verdict} reasons={(health.get(venue) or {}).get('reasons')}")

    def _npz_last_ts(self, sym):
        """Last bar ts of a stock NPZ, cached on (mtime, size) so unchanged files cost one stat."""
        p = BASE / "backtest_v8" / "indicators" / f"{sym}.npz"
        try:
            st = p.stat()
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = self._npz_bar.get(sym)
        if hit and hit[0] == key:
            return hit[1]
        try:
            import numpy as _np
            with _np.load(str(p), mmap_mode="r", allow_pickle=True) as z:
                ts = _np.asarray(z["timestamps"], dtype="float64").ravel()
                ts = ts[_np.isfinite(ts)]
                last = float(ts[-1]) if ts.size else None
        except Exception:
            return hit[1] if hit else None
        self._npz_bar[sym] = (key, last)
        return last

    def augment_data_freshness(self, health, now, fenced):
        """Append indicators_stale / positions_stale kinds to trading_health (see module constants)."""
        try:
            stocks = health.get("stocks") if isinstance(health, dict) else None
            if isinstance(stocks, dict) and stocks.get("applicable") and "stocks" not in (fenced or ()):
                syms = _stock_indicator_universe()
                old, mx, n = 0, 0.0, 0
                for s in syms:
                    ts = self._npz_last_ts(s)
                    if ts is None:
                        continue
                    n += 1
                    age = now - ts
                    mx = max(mx, age)
                    if not _npz_tail_ok(ts):
                        old += 1
                frac = (old / n) if n else 0.0
                stocks["indicators_age_s"] = round(mx, 1)
                stocks["indicators_stale_frac"] = round(frac, 3)
                stocks["indicators_universe_n"] = n
                stocks["indicators_open_grace"] = bool(_in_open_grace(now))
                # 2026-10-20 NPZ FRESHNESS IS NOT A LIVE-TRADING HEALTH SIGNAL. USER, verbatim: "NPZ has FUCK ALL to
                # do with live trading, live trading indicators are NEVER more than 30s behind or s1 also kicks in to
                # supply indicators to macbook". The reason string below even states the dead premise — "twin falls
                # back to native -> entries die" — which was true only while PARITY_VEC_EXACT_MODE=True made the twin
                # decide on NPZs. Since that flipped False (USER 2026-10-20 "NOTHING can live trade an npz"), native
                # producers decide from data/tradier/tradier_indicators_latest.json and NPZ tails gate nothing live.
                # MEASURED COST: 2026-10-20 16:26Z this probe declared "stocks: indicators_stale 122/219 stock NPZ
                # tails behind the latest RTH close" -> not_trading -> S1 took the stocks venue -> the Mac's trb/trc/tra
                # were fenced. Stock trading stopped during market hours over the freshness of files live never reads.
                # The kind and the numbers are KEPT as advisory (they are real, and they matter for BACKTESTS — a sweep
                # on a stale NPZ is §115.8's STALE_NPZ_SKIPPED), but they no longer set not_trading and no longer
                # appear in the trading-health kinds that drive a takeover. Live staleness is judged by the live
                # snapshot probes in live_failover_common (latest_market_data.json / tradier_indicators_latest.json),
                # which is the data live actually reads.
                if n >= LF_INDICATORS_MIN_N and frac > LF_INDICATORS_STALE_FRAC and not _in_open_grace(now):
                    stocks["npz_tails_stale"] = f"{old}/{n}"
                    stocks["advisory"] = list(stocks.get("advisory") or []) + [
                        f"npz_tails_stale {old}/{n} stock NPZ tails behind the latest RTH close — BACKTEST signal only, "
                        f"live reads tradier_indicators_latest.json (not a not_trading condition since 2026-10-20)"]
            # ═══ 2026-10-20 THE ONE KIND THAT MAY MOVE THE LEASE ═══════════════════════════════════════════
            # USER: "the ONLY reason s1 should ever have to kick in is if macbook cant produce indicators for some
            # reason or if I switch off macbook or it goes offline" and "live trading indicators are NEVER more than
            # 30s behind or s1 also kicks in to supply indicators to macbook".
            # This measures the files the LIVE ENGINES READ — data/latest_market_data.json for crypto
            # (config.LATEST_MARKET_DATA_FILE) and data/tradier/tradier_indicators_latest.json for stocks — NOT NPZ
            # tails, which gate nothing live (see the npz_tails_stale advisory above).
            # ON THE 30 s BAR, honestly: the write cadence of latest_market_data.json is dedupe-gated and was MEASURED
            # at 60-140 s between writes, so a 30 s FILE-AGE bar is unsatisfiable and would fire constantly — the
            # opposite failure. IND_UNPRODUCIBLE_S is therefore set well beyond the cadence (300 s default), where a
            # missing write is unambiguously a production failure rather than a quiet tick. If the 30 s figure refers
            # to the freshness of the VALUES inside the file rather than its write time, that needs a different probe
            # (per-key bar timestamps), and this one should not be tightened to 30 s until that exists.
            for _v, _rel in (("crypto", "latest_market_data.json"), ("stocks", "tradier/tradier_indicators_latest.json")):
                _sec = health.get(_v) if isinstance(health, dict) else None
                if not isinstance(_sec, dict) or _v in (fenced or ()) or _sec.get("applicable") is False:
                    continue
                try:
                    _age = now - (BASE / "data" / _rel).stat().st_mtime
                except OSError:
                    _age = None
                _sec["live_indicator_age_s"] = None if _age is None else round(_age, 1)
                if _age is None or _age > IND_UNPRODUCIBLE_S:
                    _sec["kinds"] = list(_sec.get("kinds") or []) + ["indicators_unproducible"]
                    _sec["reasons"] = list(_sec.get("reasons") or []) + [
                        f"indicators_unproducible {_rel} "
                        + ("MISSING" if _age is None else f"age {int(_age)}s > {int(IND_UNPRODUCIBLE_S)}s")
                        + " — the Mac is not producing the indicator snapshot its engines read"]
                    _sec["not_trading"] = True
            for venue, accts, files in (("stocks", C.STOCK_ACCOUNTS, _STOCK_POSITION_FILES),
                                        ("crypto", C.CRYPTO_ACCOUNTS, _CRYPTO_POSITION_FILES)):
                sec = health.get(venue) if isinstance(health, dict) else None
                if not isinstance(sec, dict) or venue in (fenced or ()) or sec.get("applicable") is False:
                    continue
                # 2026-10-20 NAME THE STALE ACCOUNTS so a wedged single account is taken over ALONE.
                # USER: "if any binance account stops trading its watchdog should fix it but if it stays down s1
                # should start trading that account only". The per-account machinery already exists on both sides
                # (live_failover_s1._scoped_accounts narrows a takeover when the driving kinds name a STRICT SUBSET
                # of the venue's accounts, with universe = CRYPTO_ACCOUNTS / STOCK_ACCOUNTS), but positions_stale
                # only ever recorded the single OLDEST file and named no account, so it always escalated the WHOLE
                # venue. MEASURED 2026-10-20 14:23Z: "stocks: positions_stale tra/state.json age 666s > 600s" took
                # the entire stocks venue — lease gen 70 venues="crypto,stocks", accounts={} — while S1 ran zero
                # tradier_manage processes, so trb/trc/tra all sat [LEASE_FENCED] and stocks traded NOWHERE from
                # 13:30 to 14:28 with the market open. One account's wedged loop must not stop its siblings.
                # Tracking per-account ages (not just the max) is what lets the scope be a strict subset.
                oldest, detail, seen = 0.0, None, 0
                per_acct = {}
                for a in accts:
                    for fn in files:
                        try:
                            age = now - (BASE / a / fn).stat().st_mtime
                        except OSError:
                            continue
                        seen += 1
                        if age > per_acct.get(a, -1.0):
                            per_acct[a] = age
                        if age >= oldest:
                            oldest, detail = age, f"{a}/{fn}"
                if not seen:
                    continue
                sec["positions_age_s"] = round(oldest, 1)
                if oldest > LF_POSITIONS_STALE_S:
                    _stale = sorted(a for a, age in per_acct.items() if age > LF_POSITIONS_STALE_S)
                    sec["kinds"] = list(sec.get("kinds") or []) + ["positions_stale"]
                    sec["positions_stale_accounts"] = _stale
                    sec["reasons"] = list(sec.get("reasons") or []) + [
                        f"positions_stale {detail} age {int(oldest)}s > {int(LF_POSITIONS_STALE_S)}s (position loop wedged)"
                        + (f" — stale accounts: {','.join(_stale)} of {len(per_acct)}" if _stale else "")]
                    sec["not_trading"] = True
            # ═══ 2026-10-22 PER-ACCOUNT "NOT TRADING" (the kind that was missing) ═══════════════════════════
            # One stat per account: data/vec_live/live_exec_<acct>.jsonl gets a row for EVERY vec decision outcome,
            # including the skips — so if it is idle the account's consumer is not executing the board at all. Only
            # judged when the lane itself is fresh AND the account actually has live keys, so "the vector dictated
            # nothing" can never be mistaken for "the engine is wedged". Named per account so the takeover scope is
            # a strict subset and one dead engine never fences its siblings.
            _sec_c = health.get("crypto") if isinstance(health, dict) else None
            if isinstance(_sec_c, dict) and "crypto" not in (fenced or ()) and _sec_c.get("applicable") is not False:
                try:
                    _vl = BASE / "data" / "vec_live"
                    _reg_age = now - (_vl / "vec_driven.json").stat().st_mtime
                    _reg = json.loads((_vl / "vec_driven.json").read_text())
                    _live_for = {}
                    for _ss, _e in _reg.items():
                        if not isinstance(_e, dict) or str(_e.get("mode") or "").lower() != "live":
                            continue
                        _acct_f = _e.get("account")
                        for _a in ([_acct_f] if isinstance(_acct_f, str) else list(_acct_f or [])):
                            _live_for[_a] = _live_for.get(_a, 0) + 1
                    _idle, _detail = [], []
                    if _reg_age <= LF_LANE_FRESH_S:
                        for _a in C.CRYPTO_ACCOUNTS:
                            if _live_for.get(_a, 0) <= 0:
                                continue
                            try:
                                _ex_age = now - (_vl / f"live_exec_{_a}.jsonl").stat().st_mtime
                            except OSError:
                                _ex_age = 10 ** 9
                            if _ex_age > LF_NOT_TRADING_S:
                                _idle.append(_a)
                                _detail.append(f"{_a} idle {int(_ex_age)}s ({_live_for[_a]} live keys)")
                    _sec_c["not_trading_idle_accounts"] = sorted(_idle)
                    if _idle:
                        _sec_c["kinds"] = list(_sec_c.get("kinds") or []) + ["not_trading_account"]
                        _sec_c["not_trading_account_accounts"] = sorted(_idle)
                        _sec_c["reasons"] = list(_sec_c.get("reasons") or []) + [
                            f"not_trading_account: {', '.join(_detail)} — no vec decision executed for >"
                            f"{int(LF_NOT_TRADING_S)}s while the board lane is live (registry age {int(_reg_age)}s)"]
                        _sec_c["not_trading"] = True
                except Exception as _nt_exc:
                    log(f"not_trading_account probe error: {_nt_exc!r}")
        except Exception as exc:
            log(f"data_freshness advisory error: {exc!r}")

    def alarm_if_gagged(self, lease, state, now):
        """Scream, every single tick, while the failover cannot take over (USER 2026-10-15).

        2026-10-14T16:14:27Z someone ran force-mac: S1's lease went to pin="mac" and the arm file was never created.
        pin="mac" blocks EVERY takeover (live_failover_s1.nt_candidates: `if pin == "mac": return []`) and the arm file
        is required on top of that. All the older S1 watchdog crons had just been retired (#RETIRED_BY_LEASE_20261014#),
        so from that moment the standby was gagged — and nothing said so anywhere. On 2026-10-15 the Mac's position
        writers were wedged 13:58->18:2xZ; this agent's own health meter DID detect it ("broker_unreachable 5/5
        accounts", "traders_dead", from 17:59:50Z) and no takeover could follow. A safety net that is off must never be
        quiet: this logs CRITICAL and writes data/alerts/failover_gagged.jsonl so live_guardian surfaces it.
        """
        try:
            pin = (lease or {}).get("pin")
            armed = None
            if isinstance(state, dict):
                armed = state.get("armed", state.get("arm", None))
            gagged = []
            if pin:
                gagged.append(f'pin={pin} (set {(lease or {}).get("since")} by {(lease or {}).get("by")}: {(lease or {}).get("reason")})')
            if armed is False:
                gagged.append("arm file absent")
            if not gagged:
                self._gag_last = 0.0
                return
            if now - float(getattr(self, "_gag_last", 0.0) or 0.0) >= GAG_ALARM_EVERY_S:
                self._gag_last = now
                log(f"CRITICAL FAILOVER_GAGGED — S1 CANNOT TAKE OVER: {'; '.join(gagged)}. Release with tools/live_failover_ctl.sh auto (and arm) on S1 once S1 data is fresh.")
                try:
                    d = Path(BASE) / "data" / "alerts"
                    d.mkdir(parents=True, exist_ok=True)
                    with open(d / "failover_gagged.jsonl", "a") as fh:
                        fh.write(json.dumps({"ts": C.iso(now), "gagged": gagged, "lease": lease}, default=str) + "\n")
                except Exception:
                    pass
        except Exception as exc:
            log(f"alarm_if_gagged error: {exc!r}")

    def tick(self):
        now = C.now_s()
        venues = self.measure(now)
        pulls = self.update_pull(venues, now)
        local = C.read_json(LF / "lease_local.json", {}) or {}
        fenced = fenced_venues(local)
        procs = 0 if os.environ.get("LF_NO_KILL") == "1" else len(trader_pids(fenced or None))
        try:
            health = self.health.measure(now, procs=self.fake_procs() if os.environ.get("LF_FAKE_PROCS") else None, fenced=fenced)
        except Exception as exc:  # health is advisory: never let it stop the heartbeat
            health = {"error": repr(exc)}
        try:
            self.augment_data_freshness(health, now, fenced)
        except Exception as exc:  # advisory on top of advisory: the heartbeat must always go out
            log(f"augment_data_freshness outer error: {exc!r}")
        ack_gen, ack_accts = scoped_fence_ack(local)
        heartbeat = {"epoch": now, "ts": C.iso(now), "host": "macbook", "version": VERSION, "venues": venues, "trading_procs": procs,
                     "lease_local": local, "pull_request": pulls, "fence_ack_gen": ack_gen, "fence_ack_accounts": ack_accts,
                     "trading_health": health}
        self.note_health(health)
        C.atomic_write_json(LF / "mac_data_heartbeat.json", heartbeat)
        lease, state, host = self.exchange(heartbeat)
        self.alarm_if_gagged(lease, state, now)
        if host is None:
            log(f"S1 unreachable via {HOSTS} — keeping local lease {local.get('holder')} gen={local.get('gen')}")
            return heartbeat
        if lease and lease.get("holder") == "s1":
            left = self.fence(lease, host)
            heartbeat["trading_procs"] = left
        elif lease and lease.get("holder") == "mac":
            if self.unfence(lease, host):
                self.push_state(host, now)
            # Every tick while the Mac holds the lease, not only on an unfence transition: the point is that S1 is
            # ALREADY warm when it has to take over, and a takeover happens without any unfence first.
            self.push_snapshots(host, now)
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
