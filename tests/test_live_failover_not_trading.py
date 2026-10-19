"""LIVE FAILOVER S1 not-trading takeover (USER 2026-10-14: "s1 ... should also detect other reasons (like ip bans) so it
starts trading whenever macbook doesnt for whatever reason"). Same harness as tests/test_live_failover.py: the REAL
Mac agent tick (heartbeat incl. trading_health from fixture logs) + the REAL S1 Watchdog.tick, fake clock, LF_DRY_RUN=1
(systemctl/probe recorded), LF_NO_SSH=1, LF_NO_KILL=1. No real process, broker or order is touched.
"""
import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

try:
    from tests.test_live_failover import Env, REPO, T_SATURDAY, T_SESSION, TOOLS, arm, gate, run_pair, touch
except ImportError:  # S1 copy: tests/ is not a package there
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_live_failover import Env, REPO, T_SATURDAY, T_SESSION, TOOLS, arm, gate, run_pair, touch

ALL_PROCS = "ang,inf,flz,men,fin,trb,trc,tra"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def _full(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S,000")


def _tr(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")


def _ez(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %H:%M:%S")


def append(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        handle.write(line + "\n")


def crypto_poll(world, now, ok=True, accounts=("ang", "inf", "flz", "men", "fin"), error="APIError(code=-2015): Invalid API-key, IP, or permissions for action, request ip: 148.222.199.152"):
    for acct in accounts:
        log = world.logs / f"ez_positions_service_{acct}.log"
        if ok:
            append(log, f"[{_full(now)}] WARNING [FETCH_LOOP][{acct}] ✅ FETCHED and SYNCED 2 positions")
        else:
            append(log, f"[{_full(now)}] ERROR [FETCH_LOOP][{acct}] ❌ Fetch error: {error}")


def stocks_activity(world, now, stalled=False, accounts=("trb", "trc", "tra")):
    for acct in accounts:
        log = world.logs / f"tradier_manage_{acct}.log"
        if stalled:
            append(log, f"{_tr(now)} - 🛑 [LOOP_LAG] event loop frozen 70.0s (max 72.5s) — broker awaits starve while loop parked")
            append(log, f"{_tr(now)} - [ORDER_STALL_SKIP] {acct}:COP_LONG OPEN BUY qty=14 reason=X: execute_trade_action exceeded 120s — SKIPPING to next order (fail-open; queue_depth=44).")
        else:
            append(log, f"{_tr(now)} - [ORDER_RESULT] COP OPEN BUY qty=6 -> SUBMITTED (ID: {acct}:COP_LONG_OPEN_{int(now)})")


def alerts(world):
    path = world.s1 / "data" / "live_failover" / "ALERTS.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def hb(world):
    return json.loads((world.s1 / "data" / "live_failover" / "mac_data_heartbeat.json").read_text())


def start(world, t0, stocks=False):
    agent, watchdog = world.MA.Agent(), world.S1.Watchdog()
    world.mac_produce(t0)
    world.s1_produce(t0)
    crypto_poll(world, t0)
    if stocks:
        stocks_activity(world, t0)
    run_pair(world, agent, watchdog, t0)
    return agent, watchdog


# ---------------------------------------------------------------- Mac-side trading-health meter
def test_health_meter_crypto_ip_ban_and_error_classes(world):
    C = world.C
    t0 = T_SATURDAY
    h = C.TradingHealth(world.logs)
    procs = {"crypto": {a: 1 for a in C.CRYPTO_ACCOUNTS}, "stocks": {a: 1 for a in C.STOCK_ACCOUNTS}}
    crypto_poll(world, t0)
    assert h.measure(t0, procs)["crypto"]["not_trading"] is False
    for k in range(1, 10):
        crypto_poll(world, t0 + 15 * k, ok=False)
    r = h.measure(t0 + 105, procs)["crypto"]
    assert r["not_trading"] is False  # 105 s without a successful poll < LF_BROKER_DEAD_S 120
    r = h.measure(t0 + 135, procs)["crypto"]
    assert r["kinds"] == ["broker_unreachable"] and sorted(r["broker_dead_accounts"]) == sorted(C.CRYPTO_ACCOUNTS)
    assert r["accounts"]["ang"]["error_classes_window"] == {"auth_ip": 9} and r["accounts"]["ang"]["consec_broker_errors"] == 9
    assert C.classify_error('status 418 banned until 1792000000') == "ban" and C.classify_error("APIError(code=-1003)") == "ban"
    assert C.classify_error("status 429 Too Many Requests") == "rate"
    crypto_poll(world, t0 + 140, ok=True, accounts=("ang", "inf", "flz"))  # 2/5 dead < 60 % -> one bad key is not a venue outage
    r = h.measure(t0 + 141, procs)["crypto"]
    assert r["not_trading"] is False and sorted(r["broker_dead_accounts"]) == ["fin", "men"]
    procs_dead = {"crypto": {a: 0 for a in C.CRYPTO_ACCOUNTS}, "stocks": {}}
    assert "traders_dead" in h.measure(t0 + 142, procs_dead)["crypto"]["kinds"]
    assert "traders_dead" not in h.measure(t0 + 142, procs_dead, fenced=("crypto",))["crypto"]["kinds"]  # fenced = expected


def test_health_meter_stocks_order_path(world):
    C = world.C
    h = C.TradingHealth(world.logs)
    procs = {"crypto": {}, "stocks": {a: 1 for a in C.STOCK_ACCOUNTS}}
    t0 = T_SESSION
    stocks_activity(world, t0)
    assert h.measure(t0 + 1, procs)["stocks"]["not_trading"] is False
    for k in range(1, 6):
        stocks_activity(world, t0 + 60 * k, stalled=True)
    r = h.measure(t0 + 301, procs)["stocks"]  # the t0 SUBMITTED left the 300 s window
    assert r["kinds"] == ["order_path_dead"], r
    assert r["accounts"]["trb"]["loop_lag_max_s_window"] == 70.0 and r["accounts"]["trb"]["order_stall_skips_window"] == 5
    assert h.measure(T_SATURDAY, procs)["stocks"]["applicable"] is False


def test_line_epoch_formats(world):
    C = world.C
    now = T_SESSION
    assert C.line_epoch(f"[{_full(now)}] WARNING x", now) == now
    assert C.line_epoch(f"{_tr(now)} - [LOOP_LAG] x", now) == now
    assert C.line_epoch(f"{_ez(now)} - WARNING - [men] x", now) == now
    assert C.line_epoch("Traceback (most recent call last):", now) is None


# ---------------------------------------------------------------- S1: crypto IP ban while the Mac is reachable
def test_crypto_ip_ban_takeover_venue_scoped_fence_and_failback(world):
    arm(world)
    t0 = T_SATURDAY
    agent, watchdog = start(world, t0)
    taken = None
    for k in range(1, 40):
        now = t0 + 15 * k
        world.mac_produce(now)  # data stays fresh: the old heartbeat-only logic never fires
        world.s1_produce(now)
        crypto_poll(world, now, ok=False)
        run_pair(world, agent, watchdog, now)
        if world.lease()["holder"] == "s1":
            taken = now - t0
            break
    assert taken is not None and 180 <= taken <= 210, taken  # 120 s Mac threshold + 60 s S1 confirm = ~3 min
    lease = world.lease()
    assert lease["venues"] == "crypto" and lease["run_venues"] == "" and lease["nt"] is True
    assert ("probe", "crypto") in watchdog.actions.calls and ("probe", "stocks") not in watchdog.actions.calls
    assert ("start", "live-failover-trading.service") not in watchdog.actions.calls  # Mac reachable: wait for its fence ack
    assert "MAC_NOT_TRADING" in [a["kind"] for a in alerts(world)]
    for k in range(1, 4):
        now = t0 + taken + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now, ok=False)
        run_pair(world, agent, watchdog, now)
    local = json.loads((world.mac / "data" / "live_failover" / "lease_local.json").read_text())
    assert local["holder"] == "s1" and local["venues"] == "crypto"
    assert gate(world.mac, "ez_manage.py") == 0 and gate(world.mac, "ez_reentry_daemon.py") == 0  # crypto fenced on the Mac
    assert gate(world.mac, "tradier_manage.py") == 1 and gate(world.mac, "tradier_positions.py") == 1  # stocks keep trading on the Mac
    assert world.lease()["run_venues"] == "crypto" and ("start", "live-failover-trading.service") in watchdog.actions.calls
    assert gate(world.s1, "ez_manage.py", linux=True) == 1 and gate(world.s1, "tradier_manage.py", linux=True) == 0
    assert hb(world)["trading_health"]["crypto"]["fenced"] is True
    # IP fixed: successful polls again; fail back after LF_FAILBACK_S (600 s) of health, S1 traders stopped first
    t2 = t0 + taken + 60
    k = 1
    while world.lease()["holder"] == "s1" and k < 80:
        now = t2 + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now, ok=True)
        run_pair(world, agent, watchdog, now)
        k += 1
    assert world.lease()["holder"] == "mac" and "venues" not in world.lease() and "run_venues" not in world.lease()
    # 2026-10-17: lower bound 600 -> 570. This assertion was UNREACHABLE until today (the gate's staleness guard,
    # added 2026-10-16 23:42, failed the fence assertions above first), so the off-by-two-ticks was never seen.
    # 15*(k-1) counts only the loop's own ticks, which begin one tick AFTER the Mac's health resumes at t2, so it
    # undercounts the implementation's measured health window by ~30 s. The implementation is right and says so in
    # its own alert: LEASE_MAC {"reason": "Mac healthy 600s (needed 600s)"} -- it waited the full 600 s of measured
    # health. The upper bound is what guards against a LATE failback and is unchanged.
    # 2026-10-19: bounds moved off the hardcoded 600 onto the module constant (USER default 600 -> 60), keeping
    # the same -30 s undercount allowance and +60 s late-failback guard.
    need = world.S1.FAILBACK_NT_S  # nt takeover -> the nt dwell, not the whole-host one
    assert need - 30 <= 15 * (k - 1) <= need + 60, 15 * (k - 1)
    calls = watchdog.actions.calls
    assert calls.index(("stop", "live-failover-trading.service")) > calls.index(("start", "live-failover-trading.service"))
    world.clock(t2 + 15 * k)
    crypto_poll(world, t2 + 15 * k)
    agent.tick()
    assert gate(world.mac, "ez_manage.py") == 1


def test_no_takeover_when_s1_shares_the_ban(world, monkeypatch):
    arm(world)
    monkeypatch.setenv("LF_FAKE_PROBE_CRYPTO", "fail")
    t0 = T_SATURDAY
    agent, watchdog = start(world, t0)
    for k in range(1, 50):
        now = t0 + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now, ok=False)
        run_pair(world, agent, watchdog, now)
    assert world.lease()["holder"] == "mac"
    kinds = [a["kind"] for a in alerts(world)]
    assert "S1_BROKER_PROBE_FAILED" in kinds and "LEASE_S1" not in kinds
    probes = [c for c in watchdog.actions.calls if c[0] == "probe"]
    assert 2 <= len(probes) <= 7  # re-probed every LF_NT_PROBE_EVERY_S (120 s), not every tick


def test_pin_mac_and_disarm_block_not_trading_takeover(world):
    t0 = T_SATURDAY
    agent, watchdog = start(world, t0)
    for k in range(1, 30):  # disarmed
        now = t0 + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now, ok=False)
        run_pair(world, agent, watchdog, now)
    assert world.lease()["holder"] == "mac"
    assert any(a["kind"] == "TRADING_FAILOVER_WOULD_TAKE_OVER_DISARMED" and a.get("venue") == "crypto" for a in alerts(world))
    arm(world)
    world.S1.set_pin("mac")
    for k in range(30, 60):
        now = t0 + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now, ok=False)
        run_pair(world, agent, watchdog, now)
    assert world.lease()["holder"] == "mac" and not [c for c in watchdog.actions.calls if c[0] == "probe"]


# ---------------------------------------------------------------- S1: stocks order path dead (LOOP_LAG / ORDER_STALL_SKIP)
def test_stocks_order_path_dead_takeover_and_crypto_growth(world):
    arm(world)
    t0 = T_SESSION
    agent, watchdog = start(world, t0, stocks=True)
    taken = None
    for k in range(1, 60):
        now = t0 + 15 * k
        world.mac_produce(now)
        world.s1_produce(now)
        crypto_poll(world, now)
        if k % 4 == 0:
            stocks_activity(world, now, stalled=True)
        run_pair(world, agent, watchdog, now)
        if world.lease()["holder"] == "s1":
            taken = now - t0
            break
    # the t0 SUBMITTED keeps the 300 s window healthy until t0+300 (stalls already logged) -> Mac flags, then LF_NT_ORDER_S 300 s on S1
    assert taken is not None and 600 <= taken <= 660 and world.lease()["venues"] == "stocks", (taken, world.lease())
    assert ("probe", "stocks") in watchdog.actions.calls
    gen_stocks = world.lease()["gen"]
    for k in range(1, 3):
        now = t0 + taken + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now)
        stocks_activity(world, now, stalled=True)
        run_pair(world, agent, watchdog, now)
    assert gate(world.mac, "tradier_manage.py") == 0 and gate(world.mac, "ez_manage.py") == 1
    assert world.lease()["run_venues"] == "stocks"
    # now crypto loses the broker too -> the held lease GROWS (gen+1); S1 runs crypto only after the Mac acked that gen
    grown = None
    t1 = t0 + taken + 30
    for k in range(1, 40):
        now = t1 + 15 * k
        world.mac_produce(now)
        crypto_poll(world, now, ok=False)
        # 2026-10-19: stocks must KEEP stalling through the growth phase — that is the scenario (stocks still
        # dead, crypto dies too). The loop used to omit it and relied on LF_FAILBACK_S=600 being longer than the
        # loop itself to keep the stocks lease from going home; at the USER's 60 s default stocks legitimately
        # failed back mid-loop and the lease had no "venues" key to grow.
        stocks_activity(world, now, stalled=True)
        run_pair(world, agent, watchdog, now)
        lease = world.lease()
        if lease["venues"] == "crypto,stocks" and grown is None:
            grown = now
            assert lease["gen"] == gen_stocks + 1 and lease["run_venues"] == "stocks"
        if grown and lease["run_venues"] == "crypto,stocks":
            break
    assert grown is not None and world.lease()["run_venues"] == "crypto,stocks"
    assert gate(world.mac, "ez_manage.py") == 0


def test_failback_backoff_doubles_after_repeat_takeover(world):
    arm(world)
    # 2026-10-19: this was setenv("LF_FAILBACK_S","600"), which did NOTHING — FAILBACK_S is read once at import
    # and the module is already imported by then. The test only passed because 600 happened to be the default,
    # so it never actually controlled the window it asserts on. Patch the constant the code reads.
    base = 600.0
    world.monkeypatch.setattr(world.S1, "FAILBACK_NT_S", base)
    t0 = T_SATURDAY
    agent, watchdog = start(world, t0)
    now = t0
    for cycle in range(2):
        while world.lease()["holder"] == "mac":
            now += 15
            world.mac_produce(now)
            crypto_poll(world, now, ok=False)
            run_pair(world, agent, watchdog, now)
        healthy_from = None
        while world.lease()["holder"] == "s1":
            now += 15
            world.mac_produce(now)
            crypto_poll(world, now, ok=True)
            run_pair(world, agent, watchdog, now)
            healthy_from = healthy_from or watchdog.state["failback"].get("healthy_since")
            need = watchdog.state["failback"].get("needed_s") or need
        assert need == base * (2 ** cycle), (cycle, need)
    assert len(watchdog.state["nt_history"]) == 2


# ---------------------------------------------------------------- S1 state check: gate + dry-run simulation
def _state_check(world, monkeypatch, broker):
    path = world.tmp / "fake_broker.json"
    path.write_text(json.dumps(broker))
    monkeypatch.setenv("LF_STATE_FAKE_BROKER", str(path))
    sys.path.insert(0, str(REPO))
    sys.modules.pop("live_failover_state_check", None)
    return importlib.import_module("live_failover_state_check")


def _write_positions(base, acct, side, rows, mtime, writer="ez_positions_service:service:1"):
    touch(base / acct / f"{side}_positions.json", mtime, json.dumps(rows))
    touch(base / acct / f".{side}_positions.json.meta.json", mtime, json.dumps({"written_at": mtime, "writer": writer}))


def test_state_gate_ready_only_when_s1_files_are_broker_true(world, monkeypatch, capsys):
    lf = world.s1 / "data" / "live_failover"
    t_lease = time.time() - 120
    touch(lf / "lease.json", t_lease, json.dumps({"holder": "s1", "gen": 7, "since_epoch": t_lease, "venues": "stocks", "run_venues": ""}))
    row = {"symbol": "COP", "position_side": "LONG", "positionAmt": 14.0, "entry_price": 100.0, "opened_at": "2026-10-14T13:40:00Z"}
    broker = {"trb": {"trb:COP_LONG": {"positionAmt": 14.0, "entry_price": 100.0}}}
    sc = _state_check(world, monkeypatch, broker)
    _write_positions(world.s1, "trb", "long", {"trb:COP_LONG": row}, t_lease - 3600)
    _write_positions(world.s1, "trb", "short", {}, t_lease - 3600)
    assert sc.gate("trb", time.time()) == 2  # files older than the lease = not yet written by S1's own writer
    _write_positions(world.s1, "trb", "long", {"trb:COP_LONG": row, "trb:OLD_LONG": dict(row, symbol="OLD")}, time.time())
    _write_positions(world.s1, "trb", "short", {}, time.time())
    assert sc.gate("trb", time.time()) == 2 and "phantom=['trb:OLD_LONG']" in capsys.readouterr().out
    _write_positions(world.s1, "trb", "long", {"trb:COP_LONG": row, "trb:OLD_LONG": dict(row, symbol="OLD", positionAmt=0.0)}, time.time())
    touch(lf / "mac_state" / "trb" / "long_positions.json", time.time(), json.dumps({"trb:COP_LONG": dict(row, opened_at="2026-10-14T13:00:00Z")}))
    assert sc.gate("trb", time.time()) == 0
    out = capsys.readouterr().out
    assert out.startswith("READY trb") and "opened_at differs from Mac" in out  # warned + alerted, not blocking
    assert any(a["kind"] == "S1_OPENED_AT_DIFFERS_FROM_MAC" for a in alerts(world))
    monkeypatch.setenv("LF_STATE_FAKE_BROKER", str(world.tmp / "missing.json"))
    assert sc.gate("trb", time.time()) == 2  # broker unreachable from S1 -> never READY


def test_state_report_simulation_carries_mac_opened_at(world, monkeypatch):
    t = time.time()
    mac = {"trb:COP_LONG": {"symbol": "COP", "position_side": "LONG", "positionAmt": 14.0, "entry_price": 100.0, "opened_at": "2026-10-14T13:40:00.111111Z"},
           "trb:NKE_LONG": {"symbol": "NKE", "position_side": "LONG", "positionAmt": 3.0, "entry_price": 50.0, "opened_at": "2026-10-13T15:00:00Z"}}
    touch(world.s1 / "data" / "live_failover" / "mac_state" / "trb" / "long_positions.json", t - 5, json.dumps(mac))
    # S1's stale file: COP stamped with today's 14:23:17 takeover time (same entry), a March phantom, no NKE
    s1 = {"trb:COP_LONG": dict(mac["trb:COP_LONG"], opened_at="2026-10-14T14:23:17Z"), "trb:OLD_LONG": {"symbol": "OLD", "positionAmt": 5.0, "entry_price": 9.0, "opened_at": "2026-03-12T06:00:00Z"}}
    _write_positions(world.s1, "trb", "long", s1, t - 12000)
    broker = {"trb": {"trb:COP_LONG": {"positionAmt": 14.0, "entry_price": 100.0}, "trb:NKE_LONG": {"positionAmt": 3.0, "entry_price": 50.0},
                      "trb:NEW_LONG": {"positionAmt": 1.0, "entry_price": 10.0}}}
    sc = _state_check(world, monkeypatch, broker)
    rep = sc.account_report("trb", t)
    assert rep["s1_phantoms"] == ["trb:OLD_LONG"] and rep["s1_missing"] == ["trb:NEW_LONG", "trb:NKE_LONG"]
    assert rep["s1_opened_at_vs_mac"] == [["trb:COP_LONG", "2026-10-14T14:23:17Z", "2026-10-14T13:40:00.111111Z"]]
    sim = rep["simulated_after_takeover"]
    assert sim["opened_at_carried_from_mac"] == 2  # COP (earliest same-entry wins) + NKE (seed)
    assert sim["broker_held_not_on_mac_new_lifecycle"] == ["trb:NEW_LONG"] and sim["opened_at_would_be_takeover_time"] == []
    assert json.loads((world.s1 / "trb" / "long_positions.json").read_text()) == s1  # dry run never writes


# ---------------------------------------------------------------- bash gate per venue + start helper wiring
def test_gate_per_venue_mac_and_s1(world):
    lf_mac = world.mac / "data" / "live_failover"
    lf_mac.mkdir(parents=True, exist_ok=True)
    (lf_mac / "lease_local.json").write_text(json.dumps({"holder": "s1", "gen": 9, "venues": "stocks"}, indent=1, sort_keys=True))
    assert gate(world.mac, "tradier_manage.py") == 0 and gate(world.mac, "tradier_positions.py") == 0 and gate(world.mac, "ez_manage.py") == 1
    (lf_mac / "lease_local.json").write_text(json.dumps({"holder": "s1", "gen": 9}, indent=1, sort_keys=True))
    assert gate(world.mac, "ez_manage.py") == 0 and gate(world.mac, "tradier_manage.py") == 0  # legacy full lease
    lf = world.s1 / "data" / "live_failover"
    lf.mkdir(parents=True, exist_ok=True)
    (lf / "lease.json").write_text(json.dumps({"holder": "s1", "gen": 9, "venues": "crypto,stocks", "run_venues": "crypto"}, indent=1, sort_keys=True))
    assert gate(world.s1, "ez_manage.py", linux=True) == 1 and gate(world.s1, "tradier_manage.py", linux=True) == 0
    (lf / "lease.json").write_text(json.dumps({"holder": "s1", "gen": 9, "venues": "crypto", "run_venues": ""}, indent=1, sort_keys=True))
    assert gate(world.s1, "ez_manage.py", linux=True) == 0  # leased but Mac not yet fenced -> S1 must not run it


def test_start_helper_and_wrapper_wiring():
    start = (TOOLS / "s1_failover_start.sh").read_text()
    assert "positions_ready || exit 4" in start and start.count("positions_ready || exit 4") == 2
    assert "ez_positions_service_main.py ez_positions_realtime.py" in start
    assert "ez_positions_service.py ez_positions_quick.py" not in start
    sup = (TOOLS / "s1_trading_supervisor.sh").read_text()
    assert "lf_lease_fenced ez_manage.py" in sup and "lf_lease_fenced tradier_manage.py" in sup
    wrapper = (REPO / "run_with_watchdog.sh").read_text()
    assert '-d "$WORKDIR/data/live_failover"' in wrapper
    for script in ("s1_failover_start.sh", "s1_trading_supervisor.sh", "live_failover_lease_gate.sh"):
        assert subprocess.run(["bash", "-n", str(TOOLS / script)]).returncode == 0
