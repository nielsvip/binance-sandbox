"""USER 2026-10-13 NEW RULE: live applies BEFORE 365D; 2 consecutive losing trades on the NEW settings escalate that
sym_side to prioritized 365D optimization, until new settings replace the current ones."""

import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_loss_streak_watch as W


def _t(ts, pnl, fees=0.04, reason=""):
    return {"ts": ts, "pnl_pct": pnl, "fees_pct": fees, "reason": reason, "symbol": "X", "side": "LONG"}


def test_net_includes_fees():
    assert W.net_of({"pnl_pct": 0.03, "fees_pct": 0.04}) < 0  # a "win" that fees turn into a loss
    assert W.net_of({"pnl_pct": 1.0, "fees_pct": 0.04}) > 0
    assert W.net_of({}) == 0.0


def test_two_consecutive_losers_trigger():
    trades = [_t("2026-10-13T01:00:00Z", 1.0), _t("2026-10-13T02:00:00Z", -0.5), _t("2026-10-13T03:00:00Z", -0.7)]
    streak, losers = W.trailing_loss_streak(trades)
    assert streak == 2
    assert [l["ts"] for l in losers] == ["2026-10-13T02:00:00Z", "2026-10-13T03:00:00Z"]


def test_a_win_after_losses_resets_the_streak():
    trades = [_t("2026-10-13T01:00:00Z", -0.5), _t("2026-10-13T02:00:00Z", -0.7), _t("2026-10-13T03:00:00Z", 2.0)]
    assert W.trailing_loss_streak(trades)[0] == 0


def test_streak_is_time_ordered_not_file_ordered():
    # same three trades shuffled: the trailing run must still be the two most RECENT losers
    trades = [_t("2026-10-13T03:00:00Z", -0.7), _t("2026-10-13T01:00:00Z", 1.0), _t("2026-10-13T02:00:00Z", -0.5)]
    streak, losers = W.trailing_loss_streak(trades)
    assert streak == 2 and losers[0]["ts"] == "2026-10-13T02:00:00Z"


def test_single_loss_does_not_trigger_and_bad_rows_ignored():
    assert W.trailing_loss_streak([_t("2026-10-13T02:00:00Z", -0.5)])[0] == 1
    assert W.trailing_loss_streak([{"ts": None, "pnl_pct": -9}, _t("2026-10-13T02:00:00Z", -0.5)])[0] == 1
    assert W.trailing_loss_streak([])[0] == 0


def test_iso_parsing():
    assert W._iso("2026-10-13T01:00:00Z") == W._iso("2026-10-13T01:00:00+00:00")
    assert W._iso("2026-10-13T01:00:00") is not None  # naive is treated as UTC
    assert W._iso("") is None and W._iso(None) is None and W._iso("junk") is None


def test_applied_at_anchor_from_live_store(tmp_path):
    db = tmp_path / "per_sym_store.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT PRIMARY KEY, updated_at TEXT, winning_tag TEXT)")
    con.execute("INSERT INTO per_sym_active VALUES ('AUSDT_LONG','2026-10-13T02:00:00Z','tag1')")
    con.execute("INSERT INTO per_sym_active VALUES ('BUSDT_SHORT','not-a-date','tag2')")
    con.commit(); con.close()
    m = W.applied_at_map(str(db))
    assert "AUSDT_LONG" in m and m["AUSDT_LONG"][1] == "2026-10-13T02:00:00Z"
    assert "BUSDT_SHORT" not in m  # unparseable anchor is skipped, never assumed
    assert W.applied_at_map(str(tmp_path / "missing.db")) == {}


def test_losses_before_the_new_settings_are_not_counted():
    """"with the new settings" — the anchor is per_sym_active.updated_at, so older losers never trigger."""
    applied = W._iso("2026-10-13T02:00:00Z")
    trades = [_t("2026-10-13T00:30:00Z", -1.0), _t("2026-10-13T01:00:00Z", -1.0), _t("2026-10-13T03:00:00Z", -0.4)]
    fresh = [t for t in trades if (W._iso(t["ts"]) or 0) > applied]
    assert W.trailing_loss_streak(fresh)[0] == 1  # only the post-apply loser counts -> no escalation yet
    assert W.trailing_loss_streak(trades)[0] == 3  # without the anchor it would have falsely escalated
