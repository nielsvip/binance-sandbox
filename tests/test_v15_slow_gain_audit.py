"""Slow-vs-quick audit pairs honestly: no cherry-picking, no survivorship filtering."""
import tools.v15_slow_gain_audit as A

SS = "2026-10-21T18:00:00Z"


def _m(ss, gain, mode, utc, host="s6", trades=100, tim=30.0):
    return {"symside": ss, "file": "x", "gain": gain, "trades": trades,
            "tim": tim, "dd": 5.0, "bh": -100.0, "mode": mode,
            "published_utc": utc, "host": host, "pilot_md5": "p",
            "npz_last_bar_ts": 1, "publish_class": "QUALIFIED"}


def test_pairs_every_symside_including_regressions():
    rows = A.pair_manifests([
        _m("AUSDT_LONG", 5.0, "catchup", "2026-10-21T10:00:00Z"),
        _m("AUSDT_LONG", 8.0, "slow", "2026-10-21T20:00:00Z"),
        _m("BUSDT_LONG", 9.0, "catchup", "2026-10-21T10:00:00Z"),
        _m("BUSDT_LONG", 4.0, "slow", "2026-10-21T20:00:00Z"),
    ], SS)
    by = {r["symside"]: r for r in rows}
    assert by["AUSDT_LONG"]["status"] == "IMPROVED" and by["AUSDT_LONG"]["d_gain"] == 3.0
    assert by["BUSDT_LONG"]["status"] == "REGRESSED" and by["BUSDT_LONG"]["d_gain"] == -5.0


def test_pending_and_no_quick_stay_visible():
    rows = A.pair_manifests([
        _m("CUSDT_LONG", 5.0, "catchup", "2026-10-21T10:00:00Z"),
        _m("DUSDT_LONG", 7.0, "slow", "2026-10-21T20:00:00Z"),
    ], SS)
    by = {r["symside"]: r for r in rows}
    assert by["CUSDT_LONG"]["status"] == "PENDING_SLOW"
    assert by["DUSDT_LONG"]["status"] == "NO_QUICK_SLOW_ONLY"


def test_newest_manifest_wins_each_leg():
    rows = A.pair_manifests([
        _m("EUSDT_LONG", 1.0, "catchup", "2026-10-20T10:00:00Z"),
        _m("EUSDT_LONG", 2.0, "catchup", "2026-10-21T10:00:00Z"),
        _m("EUSDT_LONG", 3.0, "slow", "2026-10-21T20:00:00Z"),
    ], SS)
    assert rows[0]["d_gain"] == 1.0


def test_skew_is_flagged_not_hidden():
    rows = A.pair_manifests([
        _m("FUSDT_LONG", 5.0, "catchup", "2026-10-21T10:00:00Z", host="s6"),
        dict(_m("FUSDT_LONG", 6.0, "slow", "2026-10-21T20:00:00Z", host="s5"),
             npz_last_bar_ts=2, pilot_md5="q", trades=12),
    ], SS)
    notes = " ".join(rows[0]["notes"])
    assert "host skew" in notes and "NPZ window moved" in notes
    assert "pilot version changed" in notes and "thin sample" in notes
