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
    assert may is False and "NOT taking over" in why


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
