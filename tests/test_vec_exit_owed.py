"""Exit-owed registry: a vec CLOSE handed to live execution stays owed until the
broker is observed flat. A consumed-but-unfilled close (cancelled dispatch,
blocked result, process restart) must be re-dispatched every sweep — never
stranded the way ALGOUSDT_LONG was on 2026-10-12/13 (close cancelled at
-0.65%, position flushed 17h later at -8.72%)."""
import pytest

from live_twins import vec_exact as vx

PK = "ang:ALGOUSDT_LONG"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(vx, "_exit_owed_path", lambda: tmp_path / "vec_exact_exit_owed.json")
    vx._EXIT_OWED = None
    yield
    vx._EXIT_OWED = None


def test_not_owed_by_default():
    assert vx.exit_owed_has(PK) is False
    assert vx.exit_owed_due(PK, 682.7) is False


def test_add_makes_due_immediately_no_bar_clock():
    vx.exit_owed_add(PK, "CLOSE EXIT_VELOCITY_WT against-long g-0.65%")
    assert vx.exit_owed_has(PK) is True
    # due on the very next observation — no 8-bar stale-flatten wait
    assert vx.exit_owed_due(PK, 682.7) is True
    assert vx.exit_owed_due(PK, 682.7) is True


def test_flat_observation_clears():
    vx.exit_owed_add(PK, "CLOSE X")
    assert vx.exit_owed_due(PK, 0.0) is False
    assert vx.exit_owed_has(PK) is False


def test_survives_restart_via_persistence():
    vx.exit_owed_add(PK, "CLOSE X")
    vx._EXIT_OWED = None  # simulate process restart: in-memory state gone
    assert vx.exit_owed_has(PK) is True
    assert vx.exit_owed_due(PK, 10.0) is True


def test_drop_clears_and_persists():
    vx.exit_owed_add(PK, "CLOSE X")
    vx.exit_owed_drop(PK)
    assert vx.exit_owed_has(PK) is False
    vx._EXIT_OWED = None
    assert vx.exit_owed_has(PK) is False


def test_add_idempotent_keeps_first_reason():
    vx.exit_owed_add(PK, "CLOSE FIRST")
    vx.exit_owed_add(PK, "CLOSE SECOND")
    assert vx._exit_owed_load()[PK]["reason"] == "CLOSE FIRST"


def test_per_key_isolation():
    vx.exit_owed_add(PK, "CLOSE X")
    assert vx.exit_owed_has("ang:OTHERUSDT_LONG") is False
    assert vx.exit_owed_due("ang:OTHERUSDT_LONG", 5.0) is False
