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
3. NOT-TRADING TAKEOVER (2026-10-14 USER "s1 ... should also detect other reasons (like ip bans) so it starts trading
   whenever macbook doesnt for whatever reason"): the Mac heartbeat carries trading_health per venue
   (live_failover_common.TradingHealth). While the Mac is REACHABLE (fresh heartbeat) a venue whose kinds persist —
   broker_unreachable >= LF_NT_BROKER_S (60 s, after the Mac's own 120 s no-successful-poll threshold = ~3 min total),
   order_path_dead >= LF_NT_ORDER_S (300 s), traders_dead >= LF_TRADING_DEAD_S — for >= LF_TAKEOVER_CONFIRM ticks is taken
   over ONLY after S1 verified its OWN broker access for that venue (tools/live_failover_state_check.py --probe: one
   read-only positions call per account; S1 may share the ban). The lease then carries venues="crypto" / "stocks" /
   "crypto,stocks": the Mac fences only those venues' gated traders; S1 runs a venue only once it is in run_venues, which
   is set after the Mac acknowledged the fence for that gen (or is silent >= LF_FENCE_WAIT_S). A held lease grows (gen+1)
   when another venue stops trading or the Mac goes fully down. pin=mac blocks every takeover; the arm file is required.
   Failback: Mac heartbeat + data fresh AND no not-trading kind in any leased venue for LF_FAILBACK_S x backoff
   (x2 per earlier not-trading takeover in the last 6 h, max x8 = no ping-pong).
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
TAKEOVER_HB_S = float(os.environ.get("LF_TAKEOVER_HB_S", "60"))  # USER 2026-10-15 "ARM s1 SO IT CAN ALWAYS ENTER
# INTO ACTION AT ANY INSTANT": 300 -> 60. The Mac agent writes mac_data_heartbeat.json every LF_TICK_S (15 s), so
# 60 s absent = 4 missed writes = unambiguous. With TAKEOVER_CONFIRM=4 at a 15 s tick a dead Mac is taken over in
# ~120 s instead of ~360 s. It is not set lower because the Mac demonstrably stalls 30-45 s under load
# ([OFFLOOP_SLOW] took 44.3s, 2026-10-15) and sub-minute thresholds are what flapped the failover 16x/day when
# KLINE_STALE_S was 180. The failure that actually cost money today — ONE wedged position writer while the host
# stayed up — is now repaired locally in <=60 s by tools/mac_pos_writer_keepalive.sh, which is both faster and
# safer than moving the whole venue to another host; this threshold is the whole-host backstop behind it.
TAKEOVER_CONFIRM = int(os.environ.get("LF_TAKEOVER_CONFIRM", "4"))
FAILBACK_S = float(os.environ.get("LF_FAILBACK_S", "600"))
FENCE_WAIT_S = float(os.environ.get("LF_FENCE_WAIT_S", "120"))
WITNESS_S = float(os.environ.get("LF_WITNESS_S", "300"))
TRADING_DEAD_S = float(os.environ.get("LF_TRADING_DEAD_S", "600"))  # > max normal ez_manage restart (300 s backoff cap + ~80 s startup) + margin
UNITS = {"crypto": "live-failover-crypto-producers.service", "stocks": "live-failover-stocks-producers.service", "trading": "live-failover-trading.service"}
TRADER_PATTERN = r"python.*(ez_manage\.py --account|tradier_manage\.py --accounts)"
HOST = socket.gethostname()
NT_BROKER_S = float(os.environ.get("LF_NT_BROKER_S", "60"))
NT_ORDER_S = float(os.environ.get("LF_NT_ORDER_S", "300"))
NT_PROBE_EVERY_S = float(os.environ.get("LF_NT_PROBE_EVERY_S", "120"))
NT_BACKOFF_WINDOW_S = float(os.environ.get("LF_NT_BACKOFF_WINDOW_S", "21600"))
NT_BACKOFF_MAX = int(os.environ.get("LF_NT_BACKOFF_MAX", "3"))
NT_ACCT_S = float(os.environ.get("LF_NT_ACCT_S", "0"))  # 2026-10-16 USER ("NO account can be inactive for >3min
# without s1 kicking in"): the account_dead kind (live_failover_common, ANY ONE crypto account whose decision engine
# has been dead/wedged >= LF_ACCT_DEAD_S=240 s while the others still trade) needs no extra S1-side dwell — the bar
# is already in the kind itself, and TAKEOVER_CONFIRM still demands 4 consecutive ticks (~60 s) before anything moves.
# Before this the kind did not exist: one or two stopped accounts out of five were invisible to S1 forever, because
# traders_dead requires ALL FIVE engines gone and broker_unreachable requires BROKER_DEAD_FRAC=0.6 (3 of 5).
NT_IND_S = float(os.environ.get("LF_NT_INDICATORS_S", "0"))  # 2026-10-19 USER ("...or has old indicators"):
# the indicators_stale kind (mac_failover_agent: >LF_INDICATORS_STALE_FRAC of the traded stock-NPZ universe older
# than LF_INDICATORS_STALE_S=2h — on 10-19 it was 160/178 Friday-frozen, 285 twin fallbacks, one open all morning)
# already encodes the bar, so like account_dead it needs no extra S1-side dwell beyond TAKEOVER_CONFIRM.
NT_POS_S = float(os.environ.get("LF_NT_POSITIONS_S", "0"))  # 2026-10-19 USER ("positions information"): the
# positions_stale kind (oldest per-loop position file per venue older than LF_POSITIONS_STALE_S=600 s — a wedged
# writer loop no engine/broker kind can see) carries its own bar; TAKEOVER_CONFIRM still gates the move.
NT_NEED = {"broker_unreachable": NT_BROKER_S, "order_path_dead": NT_ORDER_S, "traders_dead": TRADING_DEAD_S, "account_dead": NT_ACCT_S,
           "indicators_stale": NT_IND_S, "positions_stale": NT_POS_S, "stock_account_dead": 0.0}
STOCK_ACCT_MISSING_S = float(os.environ.get("LF_STOCK_ACCT_MISSING_S", "240"))  # S1-side dwell for a
# vanished single stock engine (no Mac-side age exists for a dead process; 240 s clears ordinary restarts,
# mirroring LF_ACCT_DEAD_S on crypto).


def stock_single_dead(accounts, miss_state, now):
    """S1-side derivation: ONE stock account dead while its siblings are fine.

    2026-10-19 USER ("if any account fails it should be taken over by s1 ... If trb fails tradier_
    has to take over on s1 immediately"): Mac-side order_path_dead needs ORDER_DEAD_FRAC=0.5 of the
    three stock accounts, so one dead account (trb stalls while trc/tra submit) was invisible to S1
    forever — the crypto blind spot account_dead closed on 10-16, unmirrored on stocks. Derived HERE
    (not in common.py) from the per-account heartbeat dict Mac already ships.
    Two evidences, one kind:
      A. Mac-flagged order_path_dead on account(s) while a sibling shows NO distress (alive, no
         order_dead, no broker_bad — quiet-but-healthy counts; distress isolated = single pathology).
      B. engine process vanished on account(s) while siblings live — S1-side timer in miss_state
         (restart-flap guard, STOCK_ACCT_MISSING_S); timers reset the tick every sibling set is whole.
    Returns (kinds, reasons, named) — kinds is [] or ["stock_account_dead"]. Pure: fully testable.
    """
    accounts = accounts or {}
    alive = sorted(a for a, v in accounts.items() if isinstance(v, dict) and int(v.get("trader_alive") or 0) > 0)
    missing = sorted(a for a in accounts if a not in alive)
    for a in list(miss_state):
        if a not in missing:
            miss_state.pop(a, None)
    flagged = sorted(a for a, v in accounts.items() if isinstance(v, dict) and v.get("order_path_dead"))
    calm = sorted(a for a, v in accounts.items()
                  if isinstance(v, dict) and a not in flagged and int(v.get("trader_alive") or 0) > 0
                  and not v.get("order_path_dead") and not v.get("broker_unreachable"))
    if flagged and calm:
        return (["stock_account_dead"],
                [f"stock_account_dead ({','.join(flagged)}) order path dead with stalls/lag while siblings ({','.join(calm)}) show no distress — isolated single-account pathology"],
                flagged)
    if missing and alive:
        for a in missing:
            miss_state.setdefault(a, now)
        ready = sorted(a for a in missing if now - float(miss_state.get(a, now)) >= STOCK_ACCT_MISSING_S)
        if ready:
            return (["stock_account_dead"],
                    [f"stock_account_dead ({','.join(ready)}) engine vanished while siblings ({','.join(alive)}) live for >= {int(STOCK_ACCT_MISSING_S)}s"],
                    ready)
    else:
        miss_state.clear()
    return ([], [], [])
STATE_CHECK = Path(__file__).resolve().parent / "live_failover_state_check.py"


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

    def probe(self, venue):
        """S1's OWN broker access for a venue (read-only positions call per account). Fixture: LF_FAKE_PROBE[_<VENUE>]=ok|fail."""
        if self.dry:
            verdict = os.environ.get(f"LF_FAKE_PROBE_{venue.upper()}", os.environ.get("LF_FAKE_PROBE", "ok"))
            self.calls.append(("probe", venue))
            return {"ok": verdict == "ok", "detail": f"fixture {verdict}"}
        try:
            result = subprocess.run([sys.executable, str(STATE_CHECK), "--probe", "--venue", venue, "--json"], capture_output=True, text=True, timeout=150)
            report = json.loads(result.stdout or "{}")
            verdict = (report.get("venues") or {}).get(venue) or {}
            return {"ok": result.returncode == 0 and bool(verdict.get("ok")), "detail": verdict.get("summary") or result.stderr[-300:]}
        except Exception as exc:
            return {"ok": False, "detail": repr(exc)[:300]}

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


def write_lease(lease, holder, reason, venues=None, nt=False):
    """holder=s1: venues = what the Mac must fence (default all); run_venues = what S1 may run, set by set_run_venues()
    only once the Mac acknowledged the fence for this gen. A growing S1 lease keeps its current run_venues."""
    new = dict(lease, holder=holder, gen=int(lease.get("gen", 0)) + 1, since=C.iso(), since_epoch=C.now_s(), reason=reason, by=HOST)
    if holder == "s1":
        new["venues"] = C.venues_str(venues or C.VENUES)
        new["run_venues"] = lease.get("run_venues", "") if lease.get("holder") == "s1" else ""
        new["nt"] = bool(nt or (lease.get("holder") == "s1" and lease.get("nt")))
    else:
        for key in ("venues", "run_venues", "nt"):
            new.pop(key, None)
    C.atomic_write_json(LF / "lease.json", new)
    alert("LEASE_" + holder.upper(), gen=new["gen"], reason=reason, venues=new.get("venues"))
    return new


def set_run_venues(lease, venues):
    new = dict(lease, run_venues=C.venues_str(venues))
    C.atomic_write_json(LF / "lease.json", new)
    alert("LEASE_RUN_VENUES", gen=new["gen"], run_venues=new["run_venues"])
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
        if not staged.is_file() or staged.name.startswith(".") or staged.name.endswith("_positions.json"):  # USER law: position data only from ez_positions_service (broker), never copied
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
    seeded = {}
    for acct_dir in sorted(p for p in staged_root.iterdir() if p.is_dir() and p.name != "root"):
        files = [acct_dir / "long_positions.json", acct_dir / "short_positions.json"]
        if any(f.exists() for f in files):
            seeded[acct_dir.name] = C.write_identity_seed(LIVE / acct_dir.name, files, f"mac_state staged {C.iso(max(f.stat().st_mtime for f in files if f.exists()))}")
    log(f"STATE_MERGE: {merged} newer Mac state files applied to {LIVE}; identity seeds (held opened_at carried, positions files untouched): {seeded}")
    return merged


class Watchdog:
    def __init__(self, actions=None):
        self.actions = actions or Actions()
        self.state = C.read_json(LF / "state.json", {}) or {}
        self.state.setdefault("venues", {"crypto": {"mode": "NORMAL"}, "stocks": {"mode": "NORMAL"}})
        self.state.setdefault("takeover", {"confirm": 0})
        self.state.setdefault("failback", {})
        self.state.setdefault("not_trading", {})
        self.state.setdefault("probe", {})
        self.state.setdefault("nt_history", [])

    def armed(self):
        return (LF / "TRADING_FAILOVER_ENABLED").exists()

    def tick(self):
        now = C.now_s()
        view = mac_view(now)
        lease = load_lease()
        readings = {}
        for venue in ("crypto", "stocks"):
            readings[venue] = venue_reading(view, venue, now)
            hb_ok = view["hb"] if (view["hb_age"] is not None and view["hb_age"] <= HB_FRESH_S) else None
            self.data_failover(venue, readings[venue], lease, now, hb_ok)
        lease = self.trading_failover(view, readings, lease, now)
        if lease["holder"] == "s1":
            rows = bridge_local(now)
            if rows:
                log(f"BRIDGE {len(rows)} klines files refreshed into {LIVE}")
        self.state.update({"updated": C.iso(now), "updated_epoch": now, "host": HOST, "dry_run": self.actions.dry, "armed": self.armed(), "view": {k: v for k, v in view.items() if k != "hb"}, "readings": readings, "lease": lease})
        C.atomic_write_json(LF / "state.json", self.state)
        return self.state

    def data_failover(self, venue, reading, lease, now, view_hb=None):
        vstate = self.state["venues"].setdefault(venue, {"mode": "NORMAL"})
        unit = UNITS[venue]
        need = reading["applicable"] and reading["stale"]
        hold = lease["holder"] == "s1" and reading["applicable"] and venue in C.lease_venues(lease)
        if vstate.get("mode") != "ACTIVE":
            requested = bool(((view_hb or {}).get("pull_request") or {}).get(venue)) and reading["applicable"]
            if requested and not (need or hold):
                build_outbox(venue, now)
                if not vstate.get("outbox"):
                    alert("PULL_OUTBOX_ON", venue=venue, reason="Mac pull request (>60 s behind)")
                vstate["outbox"] = True
            elif vstate.get("outbox") and not (need or hold):
                clear_outbox(venue)
                vstate["outbox"] = False
                alert("PULL_OUTBOX_OFF", venue=venue)
            if need or hold:
                vstate.update(outbox=True, mode="ACTIVE", since=C.iso(now), since_epoch=now, fresh_since=None, activations=int(vstate.get("activations", 0)) + 1, trigger=reading["reasons"])
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
        vstate.update(mode="NORMAL", outbox=False, since=C.iso(now), since_epoch=now, fresh_since=None)
        alert("DATA_FAILOVER_OFF", venue=venue, reason=why)

    def not_trading(self, view, now):
        """Per venue: is the REACHABLE Mac not trading (trading_health kinds), for how long, and is it takeover-ready."""
        hb = view["hb"] if (view["hb"] is not None and view["hb_age"] is not None and view["hb_age"] <= HB_FRESH_S) else None
        health = (hb or {}).get("trading_health") or {}
        out = {}
        for venue in C.VENUES:
            st = self.state["not_trading"].setdefault(venue, {})
            reading = health.get(venue) or {}
            kinds = [k for k in reading.get("kinds") or [] if k in NT_NEED and not (reading.get("fenced") and k == "traders_dead")]
            if venue == "stocks" and hb and reading.get("applicable") and not reading.get("fenced"):
                miss = self.state.setdefault("stock_acct_missing", {})
                extra, xreasons, _named = stock_single_dead(reading.get("accounts"), miss, now)
                kinds = kinds + [k for k in extra if k not in kinds]
                reading = dict(reading, reasons=list(reading.get("reasons") or []) + xreasons)
            bad = bool(hb) and bool(reading.get("applicable")) and bool(kinds)
            if bad:
                st["since"] = st.get("since") or now
                st["confirm"] = int(st.get("confirm", 0)) + 1
                st.update(kinds=kinds, reasons=reading.get("reasons"))
            else:
                if st.get("alerted"):
                    alert("MAC_TRADING_AGAIN", venue=venue, after_s=int(now - (st.get("since") or now)))
                st.update(since=None, confirm=0, kinds=[], reasons=None, alerted=False)
            need = min(NT_NEED[k] for k in kinds) if bad else None
            st["need_s"] = need
            st["ready"] = bool(bad and now - st["since"] >= need and st["confirm"] >= TAKEOVER_CONFIRM)
            if st["ready"] and not st.get("alerted"):
                alert("MAC_NOT_TRADING", venue=venue, kinds=kinds, reasons=reading.get("reasons"), for_s=int(now - st["since"]))
                st["alerted"] = True
            out[venue] = st
        return out

    def s1_broker_ok(self, venue, now):
        """S1 must reach the broker itself before it takes a venue (it may share the Mac's IP ban). Cached NT_PROBE_EVERY_S."""
        cache = self.state["probe"].get(venue) or {}
        if now - float(cache.get("epoch", 0)) < NT_PROBE_EVERY_S:
            return bool(cache.get("ok"))
        result = self.actions.probe(venue)
        if bool(result.get("ok")) != bool(cache.get("ok")) or not cache:
            alert("S1_BROKER_PROBE_OK" if result.get("ok") else "S1_BROKER_PROBE_FAILED", venue=venue, detail=result.get("detail"))
        self.state["probe"][venue] = {"epoch": now, "at": C.iso(now), "ok": bool(result.get("ok")), "detail": result.get("detail")}
        return bool(result.get("ok"))

    def nt_candidates(self, nt, pin, scope, now):
        if pin == "mac":
            return []
        wanted = [v for v, st in nt.items() if st.get("ready") and v not in scope]
        if wanted and not self.armed():
            for venue in wanted:
                st = nt[venue]
                if not st.get("disarmed_alerted"):
                    alert("TRADING_FAILOVER_WOULD_TAKE_OVER_DISARMED", venue=venue, kinds=st.get("kinds"), reasons=st.get("reasons"))
                    st["disarmed_alerted"] = True
            return []
        return [v for v in wanted if self.s1_broker_ok(v, now)]

    def failback_needed_s(self, lease, now):
        if not lease.get("nt"):
            return FAILBACK_S
        recent = [t for t in self.state["nt_history"] if now - float(t) <= NT_BACKOFF_WINDOW_S]
        return FAILBACK_S * (2 ** min(max(len(recent) - 1, 0), NT_BACKOFF_MAX))

    def _nt_reason(self, nt, venues):
        return "Mac reachable but NOT TRADING: " + "; ".join(f"{v}: {', '.join(nt[v].get('reasons') or nt[v].get('kinds') or [])} for {int(C.now_s() - (nt[v].get('since') or C.now_s()))}s" for v in venues)

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
        trading_dead = bool(tk.get("dead_since")) and now - tk["dead_since"] >= TRADING_DEAD_S
        cond = hb_absent and pushes_stopped and (unreachable or trading_dead)
        tk.update(hb_absent=hb_absent, pushes_stopped=pushes_stopped, unreachable=unreachable, trading_dead=trading_dead, cond=cond)
        nt = self.not_trading(view, now)
        pin = lease.get("pin")
        if pin == "mac_once":
            pin = "mac"
        if lease["holder"] == "mac":
            if lease.get("pin") == "mac_once":
                lease = dict(lease, pin=None)
                C.atomic_write_json(LF / "lease.json", lease)
                pin = None
            fb.clear()
            tk["confirm"] = (int(tk.get("confirm", 0)) + 1) if cond else 0
            want = pin == "s1" or (pin != "mac" and tk["confirm"] >= TAKEOVER_CONFIRM)
            if want and not self.armed() and pin != "s1":
                if tk["confirm"] == TAKEOVER_CONFIRM:
                    alert("TRADING_FAILOVER_WOULD_TAKE_OVER_DISARMED", detail={k: tk[k] for k in ("hb_absent", "pushes_stopped", "unreachable", "trading_dead")})
                return lease
            if want:
                lease = write_lease(lease, "s1", "manual force-s1" if pin == "s1" else f"Mac down: hb_age={None if hb_age is None else int(hb_age)}s pushes_stopped unreachable={unreachable} trading_dead={trading_dead}")
                taken = list(C.VENUES)
            else:
                taken = self.nt_candidates(nt, pin, [], now)
                if not taken:
                    return lease
                lease = write_lease(lease, "s1", self._nt_reason(nt, taken), venues=taken, nt=True)
                self.state["nt_history"] = [t for t in self.state["nt_history"] if now - float(t) <= NT_BACKOFF_WINDOW_S] + [now]
            merge_mac_state()
            build_outbox("crypto", now)
            if C.stocks_applicable(now) or readings["stocks"]["applicable"]:
                build_outbox("stocks", now)
            bridge_local(now)
        # holder == s1
        scope = C.lease_venues(lease)
        grow = []
        if pin != "mac" and set(scope) != set(C.VENUES):
            if cond:
                grow = [v for v in C.VENUES if v not in scope]
                why = f"Mac down while S1 held {scope}: grow to all venues"
            else:
                grow = self.nt_candidates(nt, pin, scope, now)
                why = self._nt_reason(nt, grow) if grow else ""
            if grow:
                lease = write_lease(lease, "s1", why, venues=scope + grow, nt=not cond)
                if not cond:
                    self.state["nt_history"] = [t for t in self.state["nt_history"] if now - float(t) <= NT_BACKOFF_WINDOW_S] + [now]
                merge_mac_state()
                scope = C.lease_venues(lease)
        mac_fenced = isinstance(view["hb"], dict) and view["hb"].get("fence_ack_gen") == lease["gen"] and view["hb"].get("trading_procs") == 0
        mac_silent = hb_absent or (hb_age is not None and hb_age >= FENCE_WAIT_S)
        nt_left = [v for v in scope if nt.get(v, {}).get("kinds")]
        healthy = (hb_age is not None and hb_age <= HB_FRESH_S and not any(r["applicable"] and r["stale"] for r in readings.values()) and not nt_left)
        if pin == "mac" or (pin != "s1" and healthy):
            fb["healthy_since"] = fb.get("healthy_since") or now
            need = self.failback_needed_s(lease, now)
            fb["needed_s"] = need
            if pin == "mac" or now - fb["healthy_since"] >= need:
                return self.failback(lease, "manual force-mac" if pin == "mac" else f"Mac healthy {int(now - fb['healthy_since'])}s (needed {int(need)}s) venues={scope}")
        else:
            fb["healthy_since"] = None
            fb["blocked_by"] = nt_left
        if mac_fenced or mac_silent:
            if C.lease_venues(dict(venues=lease.get("run_venues"))) != scope or lease.get("run_venues") is None:
                lease = set_run_venues(lease, scope)
            self.actions.start(UNITS["trading"])
        else:
            log(f"TRADING_FAILOVER: lease gen {lease['gen']} held by S1 (venues={scope}) but Mac not yet fenced (hb_age={hb_age}) — waiting before S1 runs them")
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
        self.state["not_trading"] = {}
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
    parser.add_argument("--pin", choices=["mac", "mac_once", "s1", "auto"])
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
