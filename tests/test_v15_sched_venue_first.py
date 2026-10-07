"""venue_order: market-clock default, crypto-first gate Oct 7-8 UTC, env override."""
import datetime
import os
import sys

sys.path.insert(0, "tools")
import v15_fleet_scheduler as S


def test_clock_default_after_gate():
    for iso, want in (("2026-10-09T14:00:00+00:00", ["crypto", "stocks"]),  # Fri 10:00 ET open
                      ("2026-10-09T21:00:00+00:00", ["stocks", "crypto"]),  # Fri 17:00 ET closed
                      ("2026-10-11T14:00:00+00:00", ["stocks", "crypto"])):  # Sun closed
        os.environ.pop("V15_SCHED_VENUE_FIRST", None)
        now = datetime.datetime.fromisoformat(iso)
        assert S.venue_order(now) == want, iso


def test_crypto_first_gate_overrides_closed_market():
    os.environ.pop("V15_SCHED_VENUE_FIRST", None)
    for iso in ("2026-10-07T21:00:00+00:00",  # Wed 17:00 ET closed, in gate
                "2026-10-08T02:00:00+00:00"):  # Thu night, in gate
        now = datetime.datetime.fromisoformat(iso)
        assert S.venue_order(now) == ["crypto", "stocks"], iso


def test_priority_syms_parsing():
    os.environ["V15_SCHED_PRIORITY_SYMS"] = "EDUUSDT, nmrusdt ,,MELANIAUSDT"
    assert S.priority_syms() == {"EDUUSDT", "NMRUSDT", "MELANIAUSDT"}
    os.environ.pop("V15_SCHED_PRIORITY_SYMS", None)
    assert S.priority_syms() == set()


def test_priority_sorts_first():
    os.environ["V15_SCHED_PRIORITY_SYMS"] = "NMRUSDT"
    pri = S.priority_syms()
    vrank = {"crypto": 0, "stocks": 1}
    all_syms = ["AAPL", "EDUUSDT", "NMRUSDT"]
    ranked = sorted(all_syms, key=lambda s: S.sym_rank_key(s, vrank, all_syms, pri))
    assert ranked[0] == "NMRUSDT"
    assert ranked[1:] == ["EDUUSDT", "AAPL"]
    os.environ.pop("V15_SCHED_PRIORITY_SYMS", None)


def test_env_override_wins():
    now = datetime.datetime.fromisoformat("2026-10-09T21:00:00+00:00")
    os.environ["V15_SCHED_VENUE_FIRST"] = "crypto"
    assert S.venue_order(now) == ["crypto", "stocks"]
    os.environ["V15_SCHED_VENUE_FIRST"] = "stocks"
    now2 = datetime.datetime.fromisoformat("2026-10-07T21:00:00+00:00")
    assert S.venue_order(now2) == ["stocks", "crypto"]
    os.environ.pop("V15_SCHED_VENUE_FIRST", None)
