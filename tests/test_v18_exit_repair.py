"""V18 R2 EXIT REPAIR (doc 17 §3.3): this board's exit-family loss table (+ drift-into-loss) merged with the cat_side digest's
exit_loss_rank (board weight 1, digest 0.5; stop/execution families reported, never targets) -> top families -> repair paths
-> real evals -> attribution (losers fixed / winners hurt / Δ metrics) -> greedy adopt; re-diagnose while lifting."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402
from tools import v15_trade_autopsy as TA  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()
FIX = ROOT / "tests" / "fixtures" / "v18_digest"


def _fam(r):
    return str(r).split("|")[0]


def _t(be, bx, pnl, pnl_pct, mfe, post=0.0, cls=(), reason="EXIT_VELOCITY_WT"):
    return {"be": be, "bx": bx, "pnl": pnl, "pnl_pct": pnl_pct, "mfe_pct": mfe, "post_pct": post, "cls": list(cls), "exit_reason": reason}


def test_board_family_table_metrics():
    trades = [_t(1, 2, -0.5, -0.4, 1.2, cls=("LOSER",)), _t(5, 15, 1.0, 2.0, 4.0, cls=("GIVEBACK",)), _t(20, 21, -0.2, -0.1, 0.1, post=1.5, cls=("LOSER", "PREMATURE_EXIT")), _t(30, 40, 0.3, 0.5, 0.6, reason="DAYTRADE_TARGET")]
    tb = V.board_family_table(trades, _fam, 1.0)
    e = tb["EXIT_VELOCITY_WT"]
    assert e["n"] == 3 and e["losers"] == 2 and e["loss_sum_pct"] == 0.7
    assert e["one_bar_share"] == round(2 / 3, 4) and e["mean_bars_held"] == round(12 / 3, 3)
    assert e["giveback_sum"] == 2.0 and e["premature_sum"] == 1.5
    assert e["drift_into_loss_n"] == 1 and e["drift_into_loss_sum"] == 0.5  # MFE 1.2 >= 1.0 then closed at a loss
    assert tb["DAYTRADE_TARGET"]["losers"] == 0


def test_merge_rank_weights_shares_and_excludes_stop_execution(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    d, _ = V.load_digest("CRYPTO_LONG")
    board = {"DAYTRADE_TARGET": {"loss_sum_pct": 1.0, "drift_into_loss_sum": 0.0}, "FIXED_STOP": {"loss_sum_pct": 9.0}}
    r = V.merge_family_rank(board, d["exit_loss_rank"], 1.0, 0.5)
    fams = [f for f, _, _ in r]
    assert "FIXED_STOP" not in fams  # stop/execution: reported by the ranker, never a board repair target
    # DAYTRADE_TARGET: board share 1.0 + 0.5 * digest share 20/140; EXIT_VELOCITY_WT: 0.5 * 120/140
    sc = {f: s for f, s, _ in r}
    assert abs(sc["DAYTRADE_TARGET"] - (1.0 + 0.5 * 20 / 140)) < 1e-6
    assert abs(sc["EXIT_VELOCITY_WT"] - 0.5 * 120 / 140) < 1e-6
    assert fams[0] == "DAYTRADE_TARGET"
    assert V.merge_family_rank({}, None) == []


def test_curated_and_digest_menu(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    d, _ = V.load_digest("CRYPTO_LONG")
    assert "EXIT_VELOCITY_WT_MIN_TFS" in V.curated_switches_for("EXIT_VELOCITY_WT")
    s = V.curated_switches_for("ZZZ", {"board": {"drift_into_loss_n": 1, "giveback_sum": 0, "losers": 0}})
    assert "HARD_BREAKEVEN_FLOOR" in s and "BB_FROZEN_STOP_FIELD" not in s
    m = V.repair_entries(d, "EXIT_VELOCITY_WT")
    assert m[0]["switch"] == "EXIT_VELOCITY_WT_MIN_TFS" and V.companions_of(m[0]) == [("WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", "True")]
    assert [e["switch"] for e in V.fixed_stop_entries(d)] == ["FIXED_STOP_REF_FIELD"]


def test_attribution_card_on_real_autopsy_logic():
    base = [{"be": 1, "bx": 3, "pnl": -1.0, "cls": ["LOSER"]}, {"be": 10, "bx": 12, "pnl": 2.0, "cls": []}]
    cand = [{"be": 1, "bx": 2, "pnl": -0.2}, {"be": 10, "bx": 11, "pnl": 1.5}, {"be": 20, "bx": 22, "pnl": 0.4}]
    card = V.attribution_card(base, cand, {"gain_pct": 1.0, "max_dd_pct": 5.0, "wr_pct": 50.0, "tim_pct": 30.0, "trades": 2}, {"gain_pct": 1.7, "max_dd_pct": 4.0, "wr_pct": 66.6, "tim_pct": 31.0, "trades": 3}, TA.attribute, TA.scorecard)
    assert card["losers_fixed"] == 1 and card["winners_hurt"] == 1 and card["saved"] == 0.8 and card["hurt"] == 0.5
    assert card["d_gain"] == 0.7 and card["d_dd"] == -1.0 and card["d_trades"] == 1.0


def test_extend_deadline_rule(monkeypatch):
    monkeypatch.setenv("V15_V17_MIN_LIFT_PM", "0.3")
    assert V.extend_deadline(0.1, 100.0, 120.0, 50.0, 1000.0) == 120.0  # not lifting enough
    assert V.extend_deadline(1.0, 100.0, 200.0, 50.0, 1000.0) == 200.0  # deadline not near
    assert V.extend_deadline(1.0, 100.0, 140.0, 50.0, 1000.0) == 220.0  # +max(120, window)
    assert V.extend_deadline(1.0, 100.0, 140.0, 50.0, 150.0) == 140.0  # hard cap reached
    assert V.lift_pm(0.6, 120.0) == 0.3


def test_pilot_r2_wiring_pins():
    assert '_v18_r2_on = bool(_V18P is not None and _V18P.flag_on("V18_EXIT_REPAIR"))' in SRC
    assert 'res = _eps_l(prepared, sanitize_overrides(dict(ov), defaults)[0], args.window_days, include_ledger=True)' in SRC
    assert 'ranked = _V18P.merge_family_rank(table, (_v18_dg or {}).get("exit_loss_rank"), 1.0, 0.5)' in SRC
    assert 'drift = float(os.environ.get("V18_DRIFT_MFE_PCT", "1.0"))' in SRC and 'nfam = int(os.environ.get("V18_EXIT_FAMILIES", "4"))' in SRC
    assert '"EXITREP", deadline, ph, accept=_v18_accept' in SRC
    assert "_V18P.attribution_card(base, ctr, base_res, cres, _TA.attribute, _TA.scorecard, moves)" in SRC
    assert '_V18P.fixed_stop_entries(_v18_dg)' in SRC
    assert 'ws_e = wb.create_sheet("EXIT_REPAIR")' in SRC
    # runs unconditionally when ON — not inside the V17 flag
    i = SRC.index('if _v18_r2_on:\n                _v18_exit_repair(')
    assert "if _v17_on" not in SRC[i - 400:i]
