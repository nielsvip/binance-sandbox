import json
import os

import verified_gain_boost as vgb


def _write_tiers(base, tiers):
    d = os.path.join(str(base), "data")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "persym_size_tiers.json"), "w") as fh:
        json.dump({"tiers": tiers}, fh)
    vgb._fresh_tiers()


def test_cap_and_gain_scaling(tmp_path):
    _write_tiers(tmp_path, {
        "AAA_LONG": {"tier": "RANKED", "g30": 11.34, "trades_30d": 223},
        "BBB_SHORT": {"tier": "RANKED", "g30": 100.0, "trades_30d": 50},
    })
    assert vgb.boost_for("AAA", "LONG", tmp_path) == 11.34
    assert vgb.boost_for("BBB", "SHORT", tmp_path) == vgb.BOOST_CAP


def test_zero_cases(tmp_path):
    _write_tiers(tmp_path, {
        "CCC_LONG": {"tier": "BOOK_POS_HOLD", "g30": 5.0, "trades_30d": 40},
        "DDD_LONG": {"tier": "UNLISTED_MIN", "g30": 9.0, "trades_30d": 40},
        "EEE_LONG": {"tier": "RANKED", "g30": -3.0, "trades_30d": 40},
        "FFF_LONG": {"tier": "RANKED", "g30": 8.0, "trades_30d": 9},
        "GGG_LONG": {"tier": "RANKED", "g30": 0.0, "trades_30d": 40},
    })
    assert vgb.boost_for("CCC", "LONG", tmp_path) == 5.0
    assert vgb.boost_for("DDD", "LONG", tmp_path) == 0.0
    assert vgb.boost_for("EEE", "LONG", tmp_path) == 0.0
    assert vgb.boost_for("FFF", "LONG", tmp_path) == 0.0
    assert vgb.boost_for("GGG", "LONG", tmp_path) == 0.0
    assert vgb.boost_for("ZZZ", "LONG", tmp_path) == 0.0


def test_fail_open(tmp_path):
    vgb._fresh_tiers()
    assert vgb.boost_for("AAA", "LONG", tmp_path) == 0.0
    d = os.path.join(str(tmp_path), "data")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "persym_size_tiers.json"), "w") as fh:
        fh.write("{not json")
    vgb._fresh_tiers()
    assert vgb.boost_for("AAA", "LONG", tmp_path) == 0.0


def test_blended_delta_ordering(tmp_path):
    _write_tiers(tmp_path, {
        "WIN_LONG": {"tier": "RANKED", "g30": 20.0, "trades_30d": 100},
        "LOSS_SHORT": {"tier": "RANKED", "g30": 20.0, "trades_30d": 100},
    })
    rows = [
        {"symbol": "WIN", "final_score_norm": 10.0},
        {"symbol": "MID", "final_score_norm": 15.0},
        {"symbol": "LOSS", "final_score_norm": -10.0},
    ]
    for r in rows:
        bl = vgb.boost_for(r["symbol"], "LONG", tmp_path)
        bs = vgb.boost_for(r["symbol"], "SHORT", tmp_path)
        r["final_score_norm"] += bl - bs
    winners = sorted(rows, key=lambda x: x["final_score_norm"], reverse=True)
    losers = sorted(rows, key=lambda x: x["final_score_norm"])
    assert winners[0]["symbol"] == "WIN"
    assert losers[0]["symbol"] == "LOSS"
