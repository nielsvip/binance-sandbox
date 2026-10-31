from tools import v15_nextgen_neutral_context as neutral
from tools import v18_linknet as network


def test_future_document_context_cannot_change_historical_context_features_or_labels():
    first = {"e": "row", "t": "2026-10-01T00:00:00Z", "g": "DOWN", "ti": "LOSER", "bh": -50.0, "rd": "old", "n": [2.0, 1], "y": [["FILTER_TF=1h", 5.0, 1]]}
    second = dict(first, g="STRONG_UP", ti="WINNER", bh=900.0, rd="post-cut")
    left, right = neutral.neutralize_event(first), neutral.neutralize_event(second)
    left_features = network.Featurizer.ctx_feats(("R", left["g"], "BTCUSDC_LONG", left["ti"], ""))
    right_features = network.Featurizer.ctx_feats(("R", right["g"], "BTCUSDC_LONG", right["ti"], ""))
    assert left_features == right_features
    assert left["n"] == right["n"] == first["n"] and left["y"] == right["y"] == first["y"]
    assert left["g"] == "UNK" and left["ti"] is None and left["bh"] is None and left["rd"] is None
    assert first["g"] == "DOWN" and second["g"] == "STRONG_UP"
    assert not left["runtime_candidate"] and not right["runtime_candidate"]
