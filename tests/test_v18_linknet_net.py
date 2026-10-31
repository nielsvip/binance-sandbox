import copy
from collections import Counter
import gzip
import json
from unittest.mock import patch

import numpy as np
import pytest

from tools import v18_linknet_net as net
from tests.test_v18_source_noop_gain import source_event

def event():
    value = source_event()
    value.update(archive_member="sources/known.json", market_end=value["npz"], t="2026-10-13T02:30:00Z", b="1000BONKUSDC_SHORT|2026-10-13T02:30:00Z")
    return value

def cell(value, header, marginal, absolute, valid=1, proof=True):
    value["y"].append([header, marginal, valid, 0.0, None, None, None, None, None, 1.0, absolute])
    if proof:
        value["eval_provenance"][header] = {"valid": True, "cached": False, "cum_before": value["cb"]}

def test_positive_marginal_negative_net_and_negative_naked_rescue():
    value = event()
    value["n"][0] = -2.0
    value["nz"] = []
    cell(value, "FILTER_A=True", 1.0, -1.0)
    cell(value, "FILTER_B=True", 3.0, 1.0)
    original = copy.deepcopy(value)
    result = net.net_event(value, Counter())
    assert [candidate[1] for candidate in result["y"]] == [-1.0, 1.0]
    assert result["n"][0] == -2.0
    assert value == original
    with patch.object(net.network.HF, "mode", return_value="context_free"):
        builder = net.NetDataset("CRYPTO_SHORT")
        builder.source_row(value, value["t"])
    assert list(builder.ex.c["kind"]) == [0, 1, 1]
    assert list(builder.ex.c["y"]) == [0, 0, 1]
    assert builder.c["rescued_rows"] == 1

def test_missing_invalid_and_unproven_cells_never_get_negative_labels():
    value = event()
    value["nz"] = []
    cell(value, "INVALID=True", 0.0, value["n"][0], valid=0)
    cell(value, "UNPROVEN=True", 0.0, value["n"][0], proof=False)
    cell(value, "MISMATCH=True", 0.0, value["n"][0])
    value["eval_provenance"]["MISMATCH=True"]["cum_before"] += 1.0
    cell(value, "NAN=True", float("nan"), float("nan"))
    result = net.net_event(value, Counter())
    assert result["y"] == []
    assert "ABSENT=True" not in str(result["y"])

def test_explicit_noop_preserves_actual_nonzero_net_and_deduplicates_y_nz():
    value = event()
    header = value["nz"][0][0]
    value["nz"] *= 2
    counters = Counter()
    result = net.net_event(value, counters)
    assert len(result["y"]) == 1
    assert result["y"][0][1] == value["n"][0] != 0.0
    cell(value, header, 0.0, value["n"][0])
    result = net.net_event(value, Counter())
    assert len(result["y"]) == 1

def test_conflicting_duplicate_measurements_are_unknown():
    value = event()
    value["nz"] = []
    cell(value, "FILTER_A=True", 0.0, value["n"][0])
    cell(value, "FILTER_A=True", 1.0, value["n"][0] + 1.0)
    assert net.net_event(value, Counter())["y"] == []

@pytest.mark.parametrize("missing", [True, False])
def test_independent_fresh_cell_net_survives_unknown_naked_without_fabricated_R(missing):
    value = event()
    value["nz"] = []
    value["n"] = None if missing else [-4.0, 0, 0.0, None, None, None, None, None, 1.0, 1]
    value["eval_provenance"]["naked"]["valid"] = False
    cell(value, "VALID_NET=True", None, 0.75)
    cell(value, "NEGATIVE_NET=True", None, -0.25)
    cell(value, "UNPROVEN=True", None, 0.9, proof=False)
    result = net.net_event(value, Counter())
    assert result["n"] is None
    assert [candidate[1] for candidate in result["y"]] == [0.75, -0.25]
    with patch.object(net.network.HF, "mode", return_value="context_free"):
        builder = net.NetDataset("CRYPTO_SHORT")
        builder.source_row(value, value["t"])
    assert list(builder.ex.c["kind"]) == [1, 1]
    assert list(builder.ex.c["y"]) == [1, 0]

def test_source_cached_running_naked_does_not_hide_independently_measured_cells():
    value = event()
    value["raw_reason"] = "RUNNING_IDENTITY"
    value["n"][0], value["n"][-1] = 0.0, 0
    value["eval_provenance"]["naked"]["cached"] = True
    value["nz"][0][1] = 0.0
    cell(value, "COUNTER_TREND_ADD_BLOCK_ENABLED=True", -0.00397, -0.00397)
    cell(value, "HTF_DIRECTION_GATE_ENABLED=False", -0.03838, -0.03838)
    result = net.net_event(value, Counter())
    assert result["n"] is None
    assert len(result["y"]) == 2
    assert [candidate[1] for candidate in result["y"]] == [-0.00397, -0.03838]

@pytest.mark.parametrize("case", ["baseline", "cached", "invalid", "identity", "dead", "npz", "source", "market"])
def test_unproven_rows_are_excluded(case):
    value = event()
    if case == "baseline":
        value["eval_provenance"]["naked"]["cum_before"] += 1
    elif case == "cached":
        value["eval_provenance"]["naked"]["cached"] = True
    elif case == "invalid":
        value["n"][1] = 0
    elif case == "identity":
        value["is_running"] = True
    elif case == "dead":
        value["raw_reason"] = "DEAD_VEC_PATH: never applied"
    elif case == "npz":
        value["npz"] += 900
    elif case == "source":
        value["source_record_identity"] = "unknown"
    elif case == "market":
        value["market_end"] += 900
    assert net.net_event(value, Counter()) is None

def test_dataset_retains_source_identity_and_original_board_split_without_full_row_negatives(tmp_path):
    value = event()
    with patch.object(net.network.HF, "mode", return_value="context_free"):
        builder = net.NetDataset("CRYPTO_SHORT")
        builder.source_row(value, value["t"])
        builder.source_row(value, value["t"])
        builder.save(tmp_path)
    data, keys = net.network.load_ds("CRYPTO_SHORT", tmp_path)
    assert len(data["kind"]) == 2
    assert data["source_i"].tolist() == [0, 0]
    assert keys["schema"] == net.SCHEMA
    assert builder.c["duplicate_source_record"] == 1
    with gzip.open(tmp_path / "ds_CRYPTO_SHORT_sources.jsonl.gz", "rt") as source:
        provenance = json.loads(source.readline())
        assert source.readline() == ""
    assert provenance["source_record_identity"] == value["source_record_identity"]
    assert provenance["row_completeness"] == "unknown"
    assert "eval_provenance" not in provenance
    assert len(provenance["eval_provenance_sha256"]) == 64
    trainer = net.NetTrainer.__new__(net.NetTrainer)
    trainer.D = data
    trainer.source_cohort = {"chronological_cut_validation": "2026-10-12T00:00:00Z", "chronological_cut_test": "2026-10-14T00:00:00Z"}
    splits, _ = trainer.splits()
    assert splits["val"].tolist() == [True, True]
    assert not splits["fit"].any() and not splits["test"].any()

def test_masked_metrics_and_net_positive_amount_targets(tmp_path):
    value = event()
    value["nz"] = []
    cell(value, "FILTER_A=True", 0.4, value["n"][0] + 0.4)
    with patch.object(net.network.HF, "mode", return_value="context_free"):
        builder = net.NetDataset("CRYPTO_SHORT")
        builder.source_row(value, value["t"])
        builder.save(tmp_path)
    trainer = net.NetTrainer.__new__(net.NetTrainer)
    trainer.D, trainer.K = net.network.load_ds("CRYPTO_SHORT", tmp_path)
    trainer._targets()
    assert trainer.M[:, 0].tolist() == [1.0, 1.0]
    assert trainer.M[:, 1].tolist() == [0.0, 1.0]
    assert not trainer.M[:, 2:].any()
    assert np.isclose(trainer.Y[1, 1], np.log1p(value["n"][0] + 0.4))
