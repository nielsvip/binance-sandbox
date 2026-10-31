"""Exactly one trader per venue, never zero for long (USER 2026-10-31).

The two properties under test pull against each other, so each has its own cases:
  NEVER TWO  — a backup must not trade unless a witness confirms the primary is gone.
  NEVER ZERO — a primary must not stop because it lost sight of its peer or the arbiter threw.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import vector_direct_lease as L  # noqa: E402

NOW = 1793400000.0


def beat(host, role, active, age=0.0, now=NOW):
    return {"host": host, "role": role, "active": active, "epoch": now - age, "pid": 1, "venue": "crypto"}


def reader(doc):
    return lambda venue, role: (doc, [])


def never_asked(venue, now):
    raise AssertionError("the witness must not be consulted in this case")


# ── NEVER TWO ──

def test_a_backup_does_not_trade_while_the_primary_beats(tmp_path):
    may, why, _ = L.decide("crypto", "backup", tmp_path, NOW, {}, reader(beat("s1", "primary", True, 10)), never_asked)
    assert may is False and "primary alive" in why


def test_a_backup_does_not_trade_on_its_own_word_when_it_can_see_nothing(tmp_path):
    """The split-brain case: the backup's own link dropped, so silence proves nothing."""
    may, why, _ = L.decide("crypto", "backup", tmp_path, NOW, {}, lambda v, r: (None, ["unreachable"]),
                           lambda venue, now: (False, "no witness could be consulted"))
    assert may is False and ("NOT taking over" in why or "not evidence yet" in why), why


def test_a_backup_takes_over_only_with_a_witness(tmp_path):
    may, why, state = L.decide("crypto", "backup", tmp_path, NOW, {}, reader(beat("s1", "primary", True, 9999)),
                               lambda venue, now: (True, "s6 via s1-int: primary beat 9999s old"))
    assert may is True and why.startswith("TAKEOVER:") and state["active"] is True


def test_a_primary_stands_down_while_the_backup_holds_the_lease(tmp_path):
    may, why, _ = L.decide("crypto", "primary", tmp_path, NOW, {}, reader(beat("s2", "backup", True, 5)), never_asked)
    assert may is False and "standing down" in why


def test_the_handover_is_a_gap_never_an_overlap(tmp_path):
    """Backup active + primary back: the primary stays down until the backup has released."""
    primary_sees = beat("s2", "backup", True, 3)
    may_p, _, _ = L.decide("crypto", "primary", tmp_path, NOW, {}, reader(primary_sees), never_asked)
    state = {"active": True, "primary_healthy_since": NOW - 10}
    may_b, _, _ = L.decide("crypto", "backup", tmp_path, NOW, state, reader(beat("s1", "primary", True, 5)), never_asked)
    assert may_p is False and may_b is True, "exactly one of them trades during the handover"


# ── NEVER ZERO ──

def test_a_primary_keeps_trading_when_the_backup_is_invisible(tmp_path):
    may, why, _ = L.decide("crypto", "primary", tmp_path, NOW, {}, lambda v, r: (None, ["mesh down"]), never_asked)
    assert may is True and "not visible" in why


def test_a_primary_keeps_trading_when_the_backup_is_merely_idle(tmp_path):
    may, why, _ = L.decide("crypto", "primary", tmp_path, NOW, {}, reader(beat("s2", "backup", False, 5)), never_asked)
    assert may is True and "backup idle" in why


def test_a_primary_keeps_trading_when_the_backups_lease_is_stale(tmp_path):
    """A backup that claims the lease but stopped beating does not hold the venue hostage."""
    may, why, _ = L.decide("crypto", "primary", tmp_path, NOW, {}, reader(beat("s2", "backup", True, 9999)), never_asked)
    assert may is True


def test_the_backup_releases_only_after_the_primary_is_durably_healthy(tmp_path):
    state = {"active": True, "primary_healthy_since": NOW - (L.RELEASE_AFTER_S + 1)}
    may, why, out = L.decide("crypto", "backup", tmp_path, NOW, state, reader(beat("s1", "primary", True, 5)), never_asked)
    assert may is False and "releasing the lease back" in why and out["active"] is False


def test_the_beat_is_written_and_readable(tmp_path):
    doc = L.write_beat(tmp_path, "crypto", "primary", True, "trading")
    assert L.beat_path(tmp_path, "crypto").is_file()
    assert doc["role"] == "primary" and doc["active"] is True and doc["venue"] == "crypto"


def test_the_two_roles_of_a_venue_never_share_a_host():
    for venue, roles in L.HOSTS.items():
        assert not (set(roles["primary"]) & set(roles["backup"])), f"{venue} primary and backup overlap"
    assert L.HOSTS["crypto"]["primary"][0].startswith("s1") and L.HOSTS["crypto"]["backup"][0].startswith("s2")
    assert L.HOSTS["stocks"]["primary"][0].startswith("s5") and L.HOSTS["stocks"]["backup"][0].startswith("s6")


def test_a_witness_is_never_one_of_the_two_contenders():
    for venue in ("crypto", "stocks"):
        contenders = set(L.HOSTS[venue]["primary"]) | set(L.HOSTS[venue]["backup"])
        assert not (set(L.WITNESSES[venue]) & contenders), f"{venue} witness is a contender"


def test_an_unknown_role_is_refused(tmp_path):
    try:
        L.decide("crypto", "observer", tmp_path, NOW, {})
        raise AssertionError("an unknown role must be refused")
    except ValueError as exc:
        assert "primary or backup" in str(exc)


# ── the trader's own fail-safe: a fault must not create two traders, nor zero ──

def test_an_arbiter_fault_keeps_the_primary_trading_and_the_backup_down(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("vdt_lease_host", ROOT / "tools" / "vector_direct_trader.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)

    class Boom:
        HOSTS = L.HOSTS
        @staticmethod
        def decide(*a, **k):
            raise RuntimeError("arbiter exploded")
        @staticmethod
        def write_beat(*a, **k):
            return {}

    out = []
    for role, expected in (("primary", True), ("backup", False)):
        t = m.Trader.__new__(m.Trader)
        t.venue, t.out = "crypto", tmp_path
        t.a = __import__("types").SimpleNamespace(role=role)
        t.lease, t.lease_state = Boom, {}
        t._ledger = lambda row: None
        t.alert = lambda kind, **f: None
        out.append((role, t.lease_ok(time.time()), expected))
    assert [(r, got) for r, got, _ in out] == [("primary", True), ("backup", False)], out


def _host(role, ready, venue="crypto", tmp_path=None):
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("vdt_ready_host", ROOT / "tools" / "vector_direct_trader.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    t = m.Trader.__new__(m.Trader)
    t.venue, t.out = venue, tmp_path
    t.a = types.SimpleNamespace(role=role)
    t.accounts = ["ang"]
    rung = []
    t._ledger = lambda row: None
    t.alert = lambda kind, **f: rung.append(kind)

    class Broker:
        def positions(self, acct):
            if not ready:
                raise RuntimeError('HTTP 401 -2015 Invalid API-key, IP, or permissions')
            return {}
    t.broker = Broker()

    class Lease:
        HOSTS = L.HOSTS
        @staticmethod
        def decide(venue, r, out, now=None, state=None):
            return True, "takeover", dict(state or {})
        @staticmethod
        def write_beat(*a, **k):
            return {}
    t.lease, t.lease_state = Lease, {}
    return t, rung


def test_a_backup_that_cannot_reach_the_broker_refuses_the_lease(tmp_path):
    """Otherwise the primary stands down behind a backup that cannot trade — zero traders."""
    t, rung = _host("backup", ready=False, tmp_path=tmp_path)
    assert t.lease_ok(time.time()) is False
    assert "VD_BACKUP_NOT_BROKER_READY" in rung


def test_a_broker_ready_backup_takes_the_lease(tmp_path):
    t, rung = _host("backup", ready=True, tmp_path=tmp_path)
    assert t.lease_ok(time.time()) is True
    assert "VD_BACKUP_NOT_BROKER_READY" not in rung


def test_a_primary_is_never_gated_on_the_readiness_probe(tmp_path):
    """The primary must not be stopped by a probe wobble; losing the primary is the thing to avoid."""
    t, rung = _host("primary", ready=False, tmp_path=tmp_path)
    assert t.lease_ok(time.time()) is True


# ── the false takeover of 2026-10-31 04:59:19Z, and the readiness gaps it exposed ──

def test_an_unreadable_primary_beat_is_not_stale_until_the_grace_has_passed(tmp_path):
    """S1 was mid-restart and slow to answer ssh for under a minute; that must never look like a dead primary."""
    witness_called = []
    witness = lambda venue, now: witness_called.append(1) or (True, "would take over")
    state = {}
    for second in (0, 30, 90, 170):
        may, why, state = L.decide("crypto", "backup", tmp_path, NOW + second, state, lambda v, r: (None, ["timeout"]), witness)
        assert may is False and "not evidence yet" in why, (second, why)
    assert witness_called == [], "the witness must not even be asked inside the grace"


def test_an_unreadable_beat_past_the_grace_still_needs_a_witness_that_read_it_stale(tmp_path):
    state = {"peer_unreadable_since": NOW - (L.TAKEOVER_AFTER_S + 5)}
    may, why, _ = L.decide("crypto", "backup", tmp_path, NOW, state, lambda v, r: (None, ["timeout"]),
                           lambda venue, now: (False, "no witness could read the primary's beat"))
    assert may is False


def test_a_witness_that_cannot_reach_the_primary_is_not_evidence_it_is_dead(monkeypatch):
    calls = []
    def fake_ssh(address, command, timeout=14.0):
        calls.append(command)
        return None, f"{address}: timeout"     # every path to the primary times out from the witness
    monkeypatch.setattr(L, "_ssh_json", fake_ssh)
    verdict, detail = L.witness_says_primary_stale("crypto", NOW)
    assert verdict is False and "unreachable is not dead" in detail


def test_a_readable_beat_resets_the_unreadable_clock(tmp_path):
    state = {"peer_unreadable_since": NOW - 500}
    _, _, state = L.decide("crypto", "backup", tmp_path, NOW, state, reader(beat("s1", "primary", True, 5)), never_asked)
    assert "peer_unreadable_since" not in state


def _ready_host(tmp_path, reachable):
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("vdt_ready_all", ROOT / "tools" / "vector_direct_trader.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    t = m.Trader.__new__(m.Trader)
    t.accounts = ["ang", "fin", "flz", "inf", "men"]
    class Broker:
        def positions(self, acct):
            if acct not in reachable:
                raise RuntimeError("HTTP 401 -2015 Invalid API-key, IP, or permissions, request ip: 2.29.22.116")
            return {}
    t.broker = Broker()
    return t


def test_readiness_requires_every_account_not_just_the_first(tmp_path):
    """S2 reached ang + flz only; probing the first account alone reported it ready for all five."""
    ok, why = _ready_host(tmp_path, {"ang", "flz"}).broker_ready(time.time())
    assert ok is False and "3 of 5 accounts unreachable" in why


def test_readiness_passes_only_when_every_account_answers(tmp_path):
    ok, why = _ready_host(tmp_path, {"ang", "fin", "flz", "inf", "men"}).broker_ready(time.time())
    assert ok is True and "all 5 accounts reachable" in why


def test_an_unready_backup_never_writes_an_active_beat(tmp_path):
    """Defect 4: the beat said active=True before readiness was decided."""
    beats = []
    t, rung = _host("backup", ready=False, tmp_path=tmp_path)
    t.lease.write_beat = staticmethod(lambda out, venue, role, active, why="": beats.append(active) or {})
    assert t.lease_ok(time.time()) is False
    assert True not in beats, "an unready backup must never claim the lease, even for an instant"
