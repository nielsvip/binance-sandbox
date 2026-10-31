from collections import Counter

import numpy as np
import pytest

from tools import v18_linknet as network
from tools.v18_linknet_numpy_inference import production_probability_grid
from tests.test_v18_linknet import fctx


def scorer_fixture(production):
    scorer = object.__new__(network.Scorer2)
    scorer.cs, scorer.prod = "CRYPTO_LONG", production
    scorer.fz = network.Featurizer(scorer.cs, fctx=fctx(), feature_mode="context_free")
    index = scorer.fz.fctx.scoring_index
    keys = [f"{tab}|{switch}={value}" for tab, block in index["tabs"].items() for _, switch, value, _ in block["rows"]]
    headers = sorted({header for block in index["tabs"].values() for header in block["yellows"]})
    scorer.fz.table([scorer.fz.set_feats(key) for key in keys], network.LS)
    scorer.fz.table([scorer.fz.filt_feats(header) for header in headers], network.LF)
    scorer.fz.table([network.Featurizer.ctx_feats((kind, "UNK", "BTCUSDT_LONG", "U", "", "log")) for kind in ("R", "F")], network.LC)
    scorer.net1 = network.LinkNet(len(scorer.fz.names), seed=7) if production != "counts" else None
    scorer.net2 = network.LinkNet2(len(scorer.fz.names), 1, seed=7)
    scorer.net = scorer.net1 if scorer.net1 is not None else scorer.net2
    scorer.secondary = scorer.net1 is not None
    scorer.platt = {"R": [0.8, -0.1], "F": [0.7, -0.3]}
    scorer.platt_all = {"hybrid_v2": scorer.platt}
    scorer.unknown_feature_hits = Counter()
    scorer.unknown_context_features = Counter()
    scorer.xvocab = {}
    scorer.K = {"sets": keys[:2], "filts": headers[:2], "xctxs": [], "boards": []}
    scorer.D = {"kind": np.array([0, 0, 1, 1, 1]), "set_i": np.array([0, 1, 0, 0, 1]), "filt_i": np.array([-1, -1, 0, 1, 0]), "y": np.array([1, 0, 0, 1, 0]), "d": np.array([0.1, -0.2, -0.1, 0.4, 0.0]), "valid": np.ones(5), "x_i": np.full(5, -1)}
    scorer.evidence()
    scorer.ctx_table = lambda kind: scorer.table([network.Featurizer.ctx_feats((kind, "UNK", "BTCUSDT_LONG", "U", "", "log"))], network.LC)
    return scorer


@pytest.mark.parametrize("production", ["v1", "hybrid_v1", "counts"])
def test_production_probabilities_match_original_grid_without_sparse_or_v2_work(monkeypatch, production):
    scorer = scorer_fixture(production)
    original = scorer.grid([("ACTUAL_SYMBOL_UNK", [])], regimes_for_first=False)
    layout = {key: np.array(original[source]) for key, source in (("row_keys", "row_keys"), ("yellows", "yellows"), ("cell_row", "cell_row"), ("cell_yellow", "cell_yellow"), ("cell_n_measured", "n_meas"), ("cell_pos_measured", "pos_meas"), ("row_n_measured", "row_n"), ("row_pos_measured", "row_pos"))}
    def forbidden(*args, **kwargs):
        pytest.fail("unused v2/sparse forward path invoked")
    monkeypatch.setattr(scorer, "xpool", forbidden)
    monkeypatch.setattr(scorer.net2, "heads2", forbidden)
    monkeypatch.setattr(scorer.net2, "pool", forbidden)
    actual = production_probability_grid(scorer, layout, "BTCUSDT_LONG", chunk_size=7)
    assert np.array_equal(actual["cell_p_pos"], original["ctx"][0]["cells"]["p_pos"])
    assert np.array_equal(actual["row_p_pos"], original["ctx"][0]["rows"]["p_pos"])
    for key, value in layout.items():
        assert np.array_equal(actual[key], value)
    changed = dict(layout, cell_n_measured=layout["cell_n_measured"] + 1)
    with pytest.raises(ValueError, match="counts differ"):
        production_probability_grid(scorer, changed, "BTCUSDT_LONG")
    with pytest.raises(ValueError, match="category"):
        production_probability_grid(scorer, layout, "AAPL_LONG")
