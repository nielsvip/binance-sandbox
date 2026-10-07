"""tools/v15_trade_autopsy: classification, attribution, scorecards, combo, missing-function rules (synthetic ledgers)."""
from tools import v15_trade_autopsy as TA

CLOSE = [100, 101, 102, 101, 99, 98, 97, 99, 101, 104, 107, 110, 109, 108, 100, 95, 96, 97, 98, 99] + [100] * 20


def row(be, bx, pnl, pct, er="B12", xr="DAYTRADE_TARGET x"):
    return {"type": "CLOSE", "pnl_dollars": pnl, "pnl_pct": pct, "bar_entry": be, "bar_exit": bx, "entry_reason": er, "exit_reason": xr,
            "entry_price": CLOSE[be], "exit_price": CLOSE[bx]}


BASE = [row(0, 2, 2.0, 2.0), row(3, 6, -4.0, -4.0, er="HARDCODED_RALLY_REENTRY"), row(7, 8, 2.0, 2.0), row(13, 15, -12.0, -12.0)]


def test_classify_labels():
    t = TA.classify(TA.realised(BASE), CLOSE, True)
    by = {x["be"]: x["cls"] for x in t}
    assert "LOSER" in by[3] and "LOSER" in by[13]
    assert "PREMATURE_EXIT" in by[7]  # exit at 101, price ran to 110 afterwards
    mm = TA.missed_moves(t, CLOSE, True)
    assert any(m["b0"] >= 9 for m in mm)


def test_attribution_and_scorecard():
    base = TA.classify(TA.realised(BASE), CLOSE, True)
    # candidate A: filters the reentry loser (bar 3); candidate B: holds the bar-7 trade until bar 11 (+9) but hurts bar 0 trade
    ca = TA.realised([row(0, 2, 2.0, 2.0), row(7, 8, 2.0, 2.0), row(13, 15, -12.0, -12.0)])
    cb = TA.realised([row(0, 1, 1.0, 1.0), row(3, 6, -4.0, -4.0), row(7, 11, 9.0, 9.0), row(13, 15, -12.0, -12.0)])
    aa, ab = TA.attribute(base, ca), TA.attribute(base, cb)
    assert aa["eff"][3] == ("FILTERED", 4.0)
    assert ab["eff"][7][0] == "EXIT_LATER" and abs(ab["eff"][7][1] - 7.0) < 1e-9
    sa, sb = TA.scorecard(base, aa), TA.scorecard(base, ab)
    assert sa["losers_fixed"] == 1 and sa["net"] == 4.0
    assert sb["premature_fixed"] == 1 and sb["winners_hurt"] == 1 and abs(sb["net"] - 6.0) < 1e-9
    fx = TA.fixes_per_trade(base, {"A": aa, "B": ab})
    assert fx[3][0][0] == "A" and fx[7][0][0] == "B"
    assert TA.surgical_combo({}, {"A": aa, "B": ab}) == ["B", "A"]


def test_missing_function_rule_found():
    base = TA.classify(TA.realised([row(i * 2, i * 2 + 1, -3.0 if i % 2 else 2.0, -3.0 if i % 2 else 2.0) for i in range(8)]), CLOSE, True)
    feat = [0.0] * 40
    for t in base:
        feat[t["be"]] = 0.95 if t["pnl"] < 0 else 0.3
    unfixed = {t["be"] for t in base if "LOSER" in t["cls"]}
    rules = TA.missing_functions(base, unfixed, {"dc_position_4h": feat}, 40)
    assert rules and rules[0]["feature"] == "dc_position_4h" and rules[0]["op"] == ">" and rules[0]["good_removed"] == 0
