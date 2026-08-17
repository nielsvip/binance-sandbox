from tools import rank_classic_formation_holdout as ranker


def row(key, bh, variants):
    return {
        "key": key,
        "symbol": key.rsplit("_", 1)[0],
        "side": key.rsplit("_", 1)[1],
        "bh_return_pct": bh,
        "npz_sha256": "a" * 64,
        "variants": variants,
    }


def variant(gain, trades=12, actions=2, family="wedge"):
    return {
        "total_gain_pct": gain, "trades": trades,
        "formation_action_count": actions, "action_count": trades * 2,
        "family": family, "action": "ENTRY", "timeframe": "1h",
        "max_dd_pct": 2.0,
    }


def test_train_selects_variant_and_holdout_cannot_reselect():
    train = [row("MU_LONG", 10, {
        "baseline": variant(12, actions=0),
        "wedge_entry_1h": variant(30),
        "triangle_entry_1h": variant(20, family="triangle"),
    })]
    holdout = [row("MU_LONG", 5, {
        "baseline": variant(10, actions=0),
        "wedge_entry_1h": variant(8),
        "triangle_entry_1h": variant(40, family="triangle"),
    })]

    result = ranker.rank(train, holdout, 1)

    assert result["all_keys"][0]["variant"] == "wedge_entry_1h"
    assert result["holdout_qualified_count"] == 0
    assert "HOLDOUT_DID_NOT_IMPROVE_BASELINE" in result["all_keys"][0]["failure_reasons"]


def test_same_train_variant_must_beat_bh_and_baseline_on_holdout():
    train = [row("CLF_SHORT", -10, {
        "baseline": variant(2, actions=0),
        "trend_structure_exit_D": variant(25, family="trend_structure"),
    })]
    holdout = [row("CLF_SHORT", 3, {
        "baseline": variant(4, actions=0),
        "trend_structure_exit_D": variant(15, trades=18, actions=5, family="trend_structure"),
    })]

    result = ranker.rank(train, holdout, 1)

    assert result["holdout_qualified_count"] == 1
    assert result["qualified"][0]["holdout"]["alpha_vs_bh_pp"] == 12


def test_rank_refuses_train_holdout_npz_hash_drift():
    train = [row("MU_LONG", 1, {"baseline": variant(1, actions=0)})]
    holdout = [row("MU_LONG", 1, {"baseline": variant(1, actions=0)})]
    holdout[0]["npz_sha256"] = "b" * 64
    try:
        ranker.rank(train, holdout, 1)
    except ValueError as exc:
        assert "train/holdout NPZ hash identity failed" in str(exc)
    else:
        raise AssertionError("hash drift was not rejected")
