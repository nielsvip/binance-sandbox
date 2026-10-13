"""LIVE FAILOVER S1 (2026-10-13) test harness — no real orders, no real processes touched.

Drives the REAL code paths (live_failover_common measure/apply, mac_failover_agent.Agent.tick,
live_failover_s1.Watchdog.tick) against fixture Mac/S1 directories with a fake clock
(LF_FAKE_NOW), LF_DRY_RUN=1 (systemctl recorded, not executed), LF_NO_SSH=1 (the Mac agent
talks to the fixture S1 dir instead of ssh) and LF_NO_KILL=1 (the fence never signals anything).
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
T_SESSION = 1791914400.0  # Tue 2026-10-13 18:00:00Z (NYSE session)
T_SATURDAY = 1791640800.0  # Sat 2026-10-10 15:00:00Z
CRYPTO = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
STOCKS = ["AAPL", "MSFT", "NVDA"]
TF = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def write_klines(path, newest_open, tf, mtime, n=6, price=100.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    sec = TF[tf]
    rows = [{"timestamp": iso(newest_open - (n - 1 - i) * sec), "open": price, "high": price, "low": price, "close": price, "volume": 1.0} for i in range(n)]
    path.write_text(json.dumps(rows))
    os.utime(path, (mtime, mtime))


def touch(path, mtime, content="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.utime(path, (mtime, mtime))


def floor(ts, sec):
    return (ts // sec) * sec


class Env:
    """Fixture world: Mac base dir, S1 live dir, S1 sandbox dir, S1 ez_s1_stack."""

    def __init__(self, tmp, monkeypatch):
        self.tmp = tmp
        self.mac = tmp / "mac"
        self.s1 = tmp / "s1live"
        self.sb = tmp / "s1sandbox"
        self.stack = self.s1 / "ez_s1_stack"
        self.logs = tmp / "maclogs"
        for base in (self.mac, self.s1):
            (base / "symbols_ang_long.json").parent.mkdir(parents=True, exist_ok=True)
            (base / "symbols_ang_long.json").write_text(json.dumps(CRYPTO))
            (base / "symbols_trb_long.json").write_text(json.dumps(STOCKS))
        self.logs.mkdir(parents=True)
        (self.logs / "ez_klines.log").write_text("")
        (self.logs / "tradier_klines.log").write_text("")
        env = {"LF_BASE": str(self.mac), "LF_STAGE": str(tmp / "stage"), "LF_NO_SSH": "1", "LF_NO_KILL": "1",
               "LF_REMOTE_DIR": str(self.s1 / "data" / "live_failover"), "LF_LOGDIR": str(self.logs),
               "LF_LIVE_DIR": str(self.s1), "LF_SANDBOX_DIR": str(self.sb), "LF_STACK_DIR": str(self.stack),
               "LF_LOG": str(tmp / "live_failover.log"), "LF_DRY_RUN": "1"}
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        self.monkeypatch = monkeypatch
        sys.path.insert(0, str(TOOLS))
        for name in ("live_failover_common", "live_failover_s1", "mac_failover_agent"):
            sys.modules.pop(name, None)
        self.C = importlib.import_module("live_failover_common")
        self.S1 = importlib.import_module("live_failover_s1")
        self.MA = importlib.import_module("mac_failover_agent")

    def clock(self, now):
        self.monkeypatch.setenv("LF_FAKE_NOW", repr(float(now)))

    def mac_produce(self, now, price=100.0):
        """The Mac's own producers write fresh data as of `now` (in-session bars for stocks)."""
        for sym in CRYPTO:
            write_klines(self.mac / "klines_cache" / f"{sym}_15m.json", floor(now, 900) - 900, "15m", now, price=price)
            write_klines(self.mac / "klines_cache" / f"{sym}_3m.json", floor(now, 180) - 180, "3m", now, price=price)
        label = __import__("time").strftime("%Y%m%d_%H%M%S", __import__("time").gmtime(now))
        touch(self.mac / "data" / f"market_data_{label}.json", now)
        for sym in STOCKS:
            write_klines(self.mac / "klines_cache" / "tradier" / f"{sym}_5m.json", floor(now, 300) - 300, "5m", now, price=price)
            write_klines(self.mac / "klines_cache" / "tradier" / f"{sym}_15m.json", floor(now, 900) - 900, "15m", now, price=price)
        touch(self.mac / "data" / "tradier" / "tradier_indicators_latest.json", now)

    def s1_produce(self, now):
        """S1 producers (ez_s1_stack + tradier) have bars up to `now`, price 200 = provenance marker."""
        for sym in CRYPTO:
            for tf in ("3m", "15m", "1h", "4h"):
                write_klines(self.stack / "klines_cache" / f"{sym}_{tf}.json", floor(now, TF[tf]) - TF[tf], tf, now, price=200.0)
        touch(self.stack / "data" / "latest_market_data.json", now, json.dumps({"BTCUSDC": {"timestamp_3m": iso(now - 60), "timestamp_15m": iso(now - 300)}}))
        for sym in STOCKS:
            for tf in ("1m", "5m", "15m", "1h"):
                write_klines(self.sb / "klines_cache" / "tradier" / f"{sym}_{tf}.json", floor(now, TF[tf]) - TF[tf], tf, now, price=200.0)
        touch(self.s1 / "data" / "tradier" / "tradier_indicators_latest.json", now, json.dumps({"AAPL": {"timestamp": iso(now)}}))

    def lease(self):
        return json.loads((self.s1 / "data" / "live_failover" / "lease.json").read_text())

    def state(self):
        return json.loads((self.s1 / "data" / "live_failover" / "state.json").read_text())


@pytest.fixture
def world(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def gate(workdir, script, linux=False):
    shim = "uname(){ echo Linux; }; " if linux else "uname(){ echo Darwin; }; "
    cmd = f"{shim}source {TOOLS}/live_failover_lease_gate.sh; lf_lease_fenced {script} {workdir}"
    return subprocess.run(["bash", "-c", cmd]).returncode


# ---------------------------------------------------------------- freshness measurement
def test_crypto_fresh_then_stale_after_180s(world):
    world.clock(T_SESSION)
    world.mac_produce(T_SESSION)
    fresh = world.C.measure_crypto(world.mac, T_SESSION)
    assert fresh["stale"] is False, fresh
    stale = world.C.measure_crypto(world.mac, T_SESSION + 181)
    assert stale["stale"] is True and any(r.startswith("snapshot_age=") for r in stale["reasons"])


def test_crypto_ban_lines_mark_stale(world):
    world.mac_produce(T_SESSION)
    (world.logs / "ez_klines.log").write_text(f"[{iso(T_SESSION - 30)[:19].replace('T', ' ')}] API Error for BTCUSDC 1m: 418 {{\"code\":-1003,\"msg\":\"Way too many requests; IP banned until 1\"}}\n")
    reading = world.C.measure_crypto(world.mac, T_SESSION, world.logs / "ez_klines.log")
    assert reading["stale"] and reading["producer_ban_lines"] >= 1


def test_stocks_not_applicable_when_market_closed(world):
    world.mac_produce(T_SATURDAY - 86400 * 2)
    reading = world.C.measure_stocks(world.mac, T_SATURDAY)
    assert reading == {"applicable": False, "stale": False, "reasons": ["market_closed"]}


def test_stocks_session_stale_klines_and_timeouts(world):
    world.mac_produce(T_SESSION)
    assert world.C.measure_stocks(world.mac, T_SESSION)["stale"] is False
    log_line = f"[{iso(T_SESSION + 400)[:19].replace('T', ' ')},123] ERROR AAPL 15min fetch failed/timeout: TimeoutError: \n"
    (world.logs / "tradier_klines.log").write_text(log_line * 6)
    later = world.C.measure_stocks(world.mac, T_SESSION + 420, world.logs / "tradier_klines.log")
    assert later["stale"] and any(r.startswith("5m_behind=") for r in later["reasons"]) and later["producer_error_lines"] == 6


def test_apply_newer_klines_is_newer_only_and_atomic(world):
    stage, dest = world.tmp / "st", world.tmp / "dst"
    write_klines(stage / "A_15m.json", T_SESSION, "15m", T_SESSION, price=2)
    write_klines(dest / "A_15m.json", T_SESSION - 900, "15m", T_SESSION, price=1)
    write_klines(stage / "B_15m.json", T_SESSION - 900, "15m", T_SESSION, price=2)
    write_klines(dest / "B_15m.json", T_SESSION, "15m", T_SESSION, price=1)
    (stage / "C_15m.json").write_text('[{"timestamp": "2026-10-13T18:00:00Z"')  # truncated -> never applied
    rows = []
    replaced = world.C.apply_newer_klines(stage, dest, rows, provenance="S1:test")
    assert list(replaced) == [str(dest / "A_15m.json")]
    assert json.loads((dest / "A_15m.json").read_text())[-1]["close"] == 2
    assert json.loads((dest / "B_15m.json").read_text())[-1]["close"] == 1
    assert not (dest / "C_15m.json").exists() and rows[0]["src"] == "S1:test"
    assert not list(dest.glob(".*tmp"))


# ---------------------------------------------------------------- data failover end to end
def run_pair(world, agent, watchdog, now, mac_alive=True):
    world.clock(now)
    if mac_alive:
        agent.tick()
    return watchdog.tick()


def test_crypto_data_failover_latency_delivery_and_recovery(world):
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    t0 = T_SATURDAY  # stocks closed -> isolates crypto
    world.mac_produce(t0)
    world.s1_produce(t0)
    run_pair(world, agent, watchdog, t0)
    assert world.state()["venues"]["crypto"]["mode"] == "NORMAL"
    detected = delivered = None
    for k in range(1, 40):
        now = t0 + 15 * k
        world.s1_produce(now)  # S1 keeps producing; the Mac's producers stopped at t0
        state = run_pair(world, agent, watchdog, now)
        if detected is None and state["venues"]["crypto"]["mode"] == "ACTIVE":
            detected = now - t0
            assert ("start", "live-failover-crypto-producers.service") in watchdog.actions.calls
        if detected is not None and delivered is None:
            world.clock(now + 1)
            agent.tick()
            target = world.mac / "klines_cache" / "BTCUSDC_3m.json"
            if json.loads(target.read_text())[-1]["close"] == 200.0:
                delivered = now + 1 - t0
                break
    assert detected is not None and detected <= 195, detected  # >180 s stale + one 15 s tick
    assert delivered is not None and delivered <= detected + 16
    rows = [json.loads(line) for line in (world.mac / "data" / "live_failover" / "delivery.jsonl").read_text().splitlines()]
    assert rows and all(row["src"].startswith("S1:") for row in rows)
    assert list((world.mac / "data").glob("market_data_*_s1.json"))
    assert world.state()["lease"]["holder"] == "mac"  # data failover never touches the lease
    # Mac producers come back (newer bars than S1, price 300): must stay fresh 600 s before stand-down
    t1 = t0 + 15 * 41
    k = 0
    while world.state()["venues"]["crypto"]["mode"] == "ACTIVE" and k < 60:
        now = t1 + 15 * k
        world.mac_produce(now, price=300.0)
        world.s1_produce(now)
        run_pair(world, agent, watchdog, now)
        k += 1
    assert world.state()["venues"]["crypto"]["mode"] == "NORMAL"
    assert 600 <= 15 * (k - 1) <= 630
    assert ("stop", "live-failover-crypto-producers.service") in watchdog.actions.calls
    assert not (world.s1 / "data" / "live_failover" / "outbox" / "crypto").exists()
    world.clock(t1 + 15 * k)
    agent.tick()
    assert not list((world.mac / "data").glob("market_data_*_s1.json"))


def test_stocks_data_failover_only_in_session(world):
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(T_SATURDAY)
    for k in range(30):  # Mac dead on a Saturday: crypto ACTIVE, stocks must stay NORMAL
        run_pair(world, agent, watchdog, T_SATURDAY + 15 * k, mac_alive=False)
    assert world.state()["venues"]["stocks"]["mode"] == "NORMAL"
    agent2, watchdog2 = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(T_SESSION)
    world.s1_produce(T_SESSION)
    detected = None
    for k in range(1, 30):
        now = T_SESSION + 15 * k
        world.s1_produce(now)
        state = run_pair(world, agent2, watchdog2, now)
        if state["venues"]["stocks"]["mode"] == "ACTIVE":
            detected = now - T_SESSION
            break
    assert detected is not None and detected <= 195
    assert ("start", "live-failover-stocks-producers.service") in watchdog2.actions.calls
    later = T_SESSION + detected + 120  # next 5m bar closed on S1 -> strictly newer than the Mac's
    world.s1_produce(later)
    run_pair(world, agent2, watchdog2, later)
    world.clock(later + 1)
    agent2.tick()
    assert json.loads((world.mac / "klines_cache" / "tradier" / "AAPL_5m.json").read_text())[-1]["close"] == 200.0


# ---------------------------------------------------------------- full trading failover + fencing
def arm(world):
    (world.s1 / "data" / "live_failover").mkdir(parents=True, exist_ok=True)
    (world.s1 / "data" / "live_failover" / "TRADING_FAILOVER_ENABLED").write_text("")


def test_takeover_requires_arm_and_all_conditions(world):
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(T_SATURDAY)
    run_pair(world, agent, watchdog, T_SATURDAY)
    for k in range(1, 40):  # Mac dies at T, NOT armed
        run_pair(world, agent, watchdog, T_SATURDAY + 15 * k, mac_alive=False)
    assert world.lease()["holder"] == "mac"
    alerts = (world.s1 / "data" / "live_failover" / "ALERTS.jsonl").read_text()
    assert "TRADING_FAILOVER_WOULD_TAKE_OVER_DISARMED" in alerts


def test_takeover_blocked_while_independent_mac_witness_fresh(world):
    arm(world)
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(T_SATURDAY)
    run_pair(world, agent, watchdog, T_SATURDAY)
    for k in range(1, 40):
        now = T_SATURDAY + 15 * k
        touch(world.s1 / "data" / "mac_live_heartbeat.json", now, json.dumps({"epoch": now, "accounts": {a: True for a in ("ang", "inf", "flz", "men", "fin")}}))
        run_pair(world, agent, watchdog, now, mac_alive=False)
    assert world.lease()["holder"] == "mac"


def test_full_takeover_fence_and_failback_cycle(world):
    arm(world)
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(T_SATURDAY)
    staged = world.s1 / "data" / "live_failover" / "mac_state" / "ang" / "tracker.json"
    touch(world.s1 / "ang" / "tracker.json", T_SATURDAY - 600, '{"old": 1}')
    touch(world.s1 / "ang" / "extra_s1_only.json", T_SATURDAY - 600, '{"keep": 1}')
    touch(staged, T_SATURDAY - 10, '{"from_mac": 1}')
    run_pair(world, agent, watchdog, T_SATURDAY)
    taken = None
    for k in range(1, 60):
        now = T_SATURDAY + 15 * k
        world.s1_produce(now)
        run_pair(world, agent, watchdog, now, mac_alive=False)
        if world.lease()["holder"] == "s1":
            taken = now - T_SATURDAY
            break
    assert taken is not None and 300 <= taken <= 360, taken
    assert ("start", "live-failover-trading.service") in watchdog.actions.calls
    assert json.loads((world.s1 / "ang" / "tracker.json").read_text()) == {"from_mac": 1}
    assert (world.s1 / "ang" / "extra_s1_only.json").exists()
    assert list((world.s1 / "data").glob("market_data_*_s1.json"))  # S1 traders read bridged S1 data
    gen_s1 = world.lease()["gen"]
    # the Mac comes back: first agent tick must fence (lease_local s1) -> run_with_watchdog gate blocks traders
    t2 = T_SATURDAY + 3600
    world.mac_produce(t2)
    run_pair(world, agent, watchdog, t2)
    local = json.loads((world.mac / "data" / "live_failover" / "lease_local.json").read_text())
    assert local["holder"] == "s1" and local["gen"] == gen_s1
    assert gate(world.mac, "ez_manage.py") == 0 and gate(world.mac, "tradier_manage.py") == 0
    assert gate(world.mac, "ez_klines.py") == 1  # data feeds never gated
    # healthy for 600 s -> S1 stops its traders FIRST, then hands the lease back (gen+1)
    k = 1
    while world.lease()["holder"] == "s1" and k < 60:
        now = t2 + 15 * k
        world.mac_produce(now)
        run_pair(world, agent, watchdog, now)
        k += 1
    assert world.lease()["holder"] == "mac" and world.lease()["gen"] == gen_s1 + 1
    assert 600 <= 15 * (k - 1) <= 645
    calls = watchdog.actions.calls
    assert calls.index(("stop", "live-failover-trading.service")) > calls.index(("start", "live-failover-trading.service"))
    world.clock(t2 + 15 * k)
    agent.tick()
    assert json.loads((world.mac / "data" / "live_failover" / "lease_local.json").read_text())["holder"] == "mac"
    assert gate(world.mac, "ez_manage.py") == 1


def test_trading_dead_path_and_force_pins(world):
    arm(world)
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(T_SATURDAY)
    run_pair(world, agent, watchdog, T_SATURDAY)
    taken = None
    for k in range(1, 60):  # Mac reachable (live heartbeat pushed) but every ez_manage dead
        now = T_SATURDAY + 15 * k
        touch(world.s1 / "data" / "mac_live_heartbeat.json", now, json.dumps({"epoch": now, "accounts": {a: False for a in ("ang", "inf", "flz", "men", "fin")}}))
        run_pair(world, agent, watchdog, now, mac_alive=False)
        if world.lease()["holder"] == "s1":
            taken = now - T_SATURDAY
            break
    assert taken is not None and taken <= 360
    # force-mac one-shot: immediate failback, pin cleared afterwards
    world.S1.set_pin("mac_once")
    world.clock(T_SATURDAY + taken + 15)
    watchdog.tick()
    assert world.lease()["holder"] == "mac"
    world.clock(T_SATURDAY + taken + 30)
    watchdog.tick()
    assert world.lease().get("pin") is None


def test_force_s1_waits_for_mac_fence_ack(world):
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    t = T_SATURDAY
    world.mac_produce(t)
    run_pair(world, agent, watchdog, t)
    world.S1.set_pin("s1")
    world.clock(t + 15)
    watchdog.tick()  # lease -> s1 but the Mac is alive and not yet fenced
    assert world.lease()["holder"] == "s1"
    assert ("start", "live-failover-trading.service") not in watchdog.actions.calls
    world.mac_produce(t + 30)
    run_pair(world, agent, watchdog, t + 30)  # agent fences (writes lease_local s1, reports ack next tick)
    world.mac_produce(t + 45)
    run_pair(world, agent, watchdog, t + 45)
    assert ("start", "live-failover-trading.service") in watchdog.actions.calls
    for k in range(4, 60):  # pinned to S1: never fails back even though the Mac is healthy
        world.mac_produce(t + 15 * k)
        run_pair(world, agent, watchdog, t + 15 * k)
    assert world.lease()["holder"] == "s1"


def test_s1_side_gate_refuses_without_lease(world):
    lf = world.s1 / "data" / "live_failover"
    lf.mkdir(parents=True, exist_ok=True)
    (lf / "lease.json").write_text(json.dumps({"holder": "mac", "gen": 3}, indent=1, sort_keys=True))
    assert gate(world.s1, "ez_manage.py", linux=True) == 0
    (lf / "lease.json").write_text(json.dumps({"holder": "s1", "gen": 4}, indent=1, sort_keys=True))
    assert gate(world.s1, "ez_manage.py", linux=True) == 1
    (lf / "lease.json").unlink()
    assert gate(world.s1, "tradier_manage.py", linux=True) == 0  # no lease file -> S1 never trades


def test_run_with_watchdog_contains_lease_gate():
    text = (REPO / "run_with_watchdog.sh").read_text()
    gate_at = text.index("LIVE FAILOVER LEASE GATE")
    assert gate_at < text.index("S1 FAILOVER GATE (MacBook only)")
    assert "lf_lease_fenced \"$SCRIPT\" \"$WORKDIR\"" in text
    start = (TOOLS / "s1_failover_start.sh").read_text()
    assert "lf_lease_fenced ez_manage.py /home/niels/binance" in start
