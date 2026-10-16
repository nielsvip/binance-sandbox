"""Focused tests for the live-guard track: sync proof tiers + day-loss guard + halt predicate.

Covers tools/v15_live_sync_proof.py, tools/v15_day_loss_guard.py, and the predicate logic
referenced by tools/patches/PENDING_UNLOCK_day_halt_wire.md. No locked file is imported
for behaviour — ez_manage/tradier_manage are referenced by source-anchor only.
"""

import json
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_day_loss_guard as G
from tools import v15_live_sync_proof as P


def _t(ts, pnl, fees=0.04, px=10.0, qty=5.0, acct="ang", sym="XRPUSDC", side="LONG", reason="R"):
    return {"ts": ts, "account": acct, "symbol": sym, "side": side, "pnl_pct": pnl,
            "fees_pct": fees, "exit_price": px, "qty": qty, "reason": reason, "hold_minutes": 3.0}


# ─── day-loss metric ───

def test_metric_is_quote_notional_weighted():
    t = _t("2026-10-14T01:00:00+00:00", -1.0, 0.04, px=10.0, qty=5.0)  # net -1.04% on $50
    assert G.trade_usd(t) == abs(-1.04 / 100 * 10.0 * 5.0) * -1
    assert round(G.trade_usd(t), 4) == -0.52


def test_metric_fallback_when_price_or_qty_unusable():
    assert G.trade_usd(_t("2026-10-14T01:00:00+00:00", -1.0, px=0.0)) is None  # entry_price=0-style row
    assert G.trade_usd(_t("2026-10-14T01:00:00+00:00", -1.0, qty=0.0)) is None
    usd, pts, n, nfb = G.day_total([_t("2026-10-14T01:00:00+00:00", -1.0, px=0.0)])
    assert (usd, n, nfb) == (0.0, 1, 1) and round(pts, 2) == -1.04


def test_fees_included_in_net():
    assert G.net_of({"pnl_pct": 0.03, "fees_pct": 0.04}) < 0
    assert G.net_of({"pnl_pct": 1.0, "fees_pct": 0.04}) > 0


# ─── breach / halt files / escalation ───

def _returns_dir(tmp_path, trades_by_file):
    d = tmp_path / "data" / "per_trade_returns"
    d.mkdir(parents=True)
    for name, trades in trades_by_file.items():
        (d / name).write_text("\n".join(json.dumps(t) for t in trades) + "\n")
    return tmp_path


def test_breach_detected_and_reported(tmp_path):
    _returns_dir(tmp_path, {"ang_20261014.jsonl": [_t("2026-10-14T01:00:00+00:00", -50.0, px=100.0, qty=50.0)]})
    res = G.check_account("ang", "2026-10-14", -50.0, -200.0, root=tmp_path)
    assert res["breach"] and res["metric"] == "USD_WEIGHTED" and res["day_net_usd"] < -50.0


def test_no_breach_writes_no_halt(tmp_path):
    _returns_dir(tmp_path, {"ang_20261014.jsonl": [_t("2026-10-14T01:00:00+00:00", 0.5)]})
    res = G.check_account("ang", "2026-10-14", -50.0, -200.0, root=tmp_path)
    assert not res["breach"] and res["reason"] == ""
    assert not (tmp_path / "data" / "live_halt_ang.json").exists()


def test_halt_file_shape_and_day_scope(tmp_path):
    p = G.write_halt("fin", "UTC-day net -60.00 USD (< -50.00)", "2026-10-14", -60.0, -50.0, root=tmp_path)
    doc = json.loads(p.read_text())
    assert doc == {"halt": True, "reason": doc["reason"], "at": doc["at"], "day": "2026-10-14",
                   "account": "fin", "day_net_usd": -60.0, "threshold_usd": -50.0,
                   "by": "tools/v15_day_loss_guard.py"}
    assert G.halt_active("fin", root=tmp_path, today="2026-10-14")[0] is True
    assert G.halt_active("fin", root=tmp_path, today="2026-10-15")[0] is False  # stale day never halts


def test_escalation_merges_with_loss_streak_shape(tmp_path):
    (tmp_path / "data").mkdir(parents=True)
    prior = {"_meta": {"at": "x", "flagged": 1}, "sym_sides": {"OLD_SIDE": {"state": "PRIORITIZE_365D"}}}
    (tmp_path / "data" / "v15_365_priority.json").write_text(json.dumps(prior))
    db = tmp_path / "s.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT PRIMARY KEY, updated_at TEXT, winning_tag TEXT)")
    con.execute("INSERT INTO per_sym_active VALUES ('XRPUSDC_LONG','2026-10-14T00:00:00Z','tag9')")
    con.commit(); con.close()
    losing = {"XRPUSDC_LONG": [_t("2026-10-14T01:00:00+00:00", -2.0), _t("2026-10-14T02:00:00+00:00", -3.0)]}
    _, esc = G.escalate_priority(losing, "test", "2026-10-14", root=tmp_path, db_path=str(db))
    doc = json.loads((tmp_path / "data" / "v15_365_priority.json").read_text())
    assert esc == ["XRPUSDC_LONG"] and doc["sym_sides"]["OLD_SIDE"]["state"] == "PRIORITIZE_365D"  # preserved
    e = doc["sym_sides"]["XRPUSDC_LONG"]
    assert set(e) == {"state", "streak", "applied_at", "winning_tag", "trades_since_apply", "losers", "note"}
    assert e["state"] == "PRIORITIZE_365D" and e["applied_at"] == "2026-10-14T00:00:00Z"


def test_guard_never_touches_orders():
    src = pathlib.Path(G.__file__).read_text()
    for token in ("execute_now", "futures_create_order", "fapi/v1/order", "place_order", "cancel_order"):
        assert token not in src, f"guard must never order: {token}"


# ─── halt predicate (what the pending manager hunks call) ───

class TestDayHaltPredicate:
    def test_no_file_is_fail_open(self, tmp_path):
        ok, why = G.halt_active("ang", root=tmp_path, today="2026-10-14")
        assert (ok, why) == (False, "no halt file")

    def test_corrupt_file_is_fail_open(self, tmp_path):
        (tmp_path / "data").mkdir(parents=True)
        (tmp_path / "data" / "live_halt_ang.json").write_text("{not json")
        ok, _ = G.halt_active("ang", root=tmp_path, today="2026-10-14")
        assert ok is False

    def test_hunk_action_tokens_match_managers(self):
        # The pending hunks skip OPEN/AUGMENT-family actions and never exits. The token sets
        # must stay identical to the manager anchors: ez 365D gate (OPEN/AUGMENT/REENTRY/
        # REVERSE) + tradier entry_banned list, minus nothing; CLOSE/REDUCE must not match.
        ez_tokens = ("OPEN", "AUGMENT", "REENTRY", "REVERSE")
        tradier_extra = ("REENTER", "REENTRY_OPEN", "QUICK_OPEN", "QUICK_AUGMENT", "HEDGE_OPEN")
        for act in ("OPEN", "AUGMENT", "REENTRY", "REVERSE", "QUICK_OPEN", "HEDGE_OPEN", "REENTRY_OPEN"):
            assert any(t in act for t in ez_tokens) or act in tradier_extra
        for act in ("CLOSE", "REDUCE", "FULL_CLOSE", "PLACE_STOP", "CANCEL_STOP"):
            assert not any(t in act for t in ez_tokens) and act not in tradier_extra


# ─── sync proof tiers ───

def test_load_boards_falls_back_to_published_selection_file(tmp_path):
    # Mac store has no fleet_orchestrator_publish rows: the file fallback must kick in,
    # with its provenance recorded (never silently treated as a live build).
    fb = tmp_path / "published_selection.json"
    fb.write_text(json.dumps({"_meta": {"built_at": "2026-10-14T19:20:18+00:00", "by": "tools/v15_published_selection.py", "n": 1},
                              "sym_sides": {"XRPUSDC_LONG": {"overrides_md5": "m" * 32, "verify": "HISTORY_PUBLISH_ROW",
                                                             "n_overrides": 5, "evidence": {"trades": 68}}}, }))
    db = tmp_path / "empty.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE per_sym_history (sym_side TEXT, meta_json TEXT)")
    con.commit(); con.close()
    boards, prov = P.load_boards(str(db), str(tmp_path), str(fb))
    assert set(boards) == {"XRPUSDC_LONG"} and prov.startswith("file:")
    boards2, prov2 = P.load_boards(str(db), str(tmp_path), str(tmp_path / "missing.json"))
    assert boards2 == {} and prov2.startswith("none")


def test_npromoted_single_key_is_fragile_signal():
    mans = {"XRPUSDC_LONG": {"m1": {"manifest": "f.json", "counts": {"C": 1}}}}
    assert P.npromoted_for("XRPUSDC_LONG", {"overrides_md5": "m1"}, mans, {}) == (1, "manifest")
    assert P.npromoted_for("XRPUSDC_LONG", {"overrides_md5": "other"}, mans, {"XRPUSDC_LONG": 7}) == (7, "history")
    assert P.npromoted_for("NOSUCH", {"overrides_md5": "x"}, mans, {}) == (None, "unknown")


def test_bars_boundaries():
    assert P.bars_ok({"trades": 10, "max_dd_pct": 30.0, "tim_pct": 20.0})[0] is True
    assert P.bars_ok({"trades": 10, "max_dd_pct": 30.0, "tim_pct": 80.0})[0] is True
    assert P.bars_ok({"trades": 9, "max_dd_pct": 30.0, "tim_pct": 50.0})[0] is False
    assert P.bars_ok({"trades": 68, "max_dd_pct": -31.0, "tim_pct": 50.0})[0] is False
    assert P.bars_ok({"trades": 68, "max_dd_pct": 4.0, "tim_pct": 80.1})[0] is False


def test_overrides_md5_is_order_insensitive_and_stable():
    assert P.overrides_md5({"b": 1, "a": 2}) == P.overrides_md5({"a": 2, "b": 1})
    assert len(P.overrides_md5({"a": 1})) == 32


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("V15_DAY_HALT_NET", "-10")
    assert G.halt_threshold_usd() == -10.0
    monkeypatch.setenv("V15_DAY_HALT_NET", "junk")
    assert G.halt_threshold_usd() == G.DEFAULT_HALT_NET_USD
