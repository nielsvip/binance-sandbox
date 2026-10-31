import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from tools import v18_brain as brain
from tools import v18_linknet as linknet
from tests.test_v18_source_noop_gain import source_event


def build(event, tmp_path):
    with patch.object(linknet.HF, "mode", return_value="context_free"):
        builder = linknet.CatDS("CRYPTO_SHORT")
        builder.row(event, "2026-10-13T02:30:00Z")
        builder.save(tmp_path)
    trainer = linknet.Trainer.__new__(linknet.Trainer)
    trainer.D, trainer.K = linknet.load_ds("CRYPTO_SHORT", tmp_path)
    trainer._targets()
    return builder, trainer


def test_eligible_historical_noop_enters_bce_and_counts_but_not_positive_amount_or_unknown_metrics(tmp_path):
    event = source_event()
    original = copy.deepcopy(event)
    builder, trainer = build(event, tmp_path)
    rows = np.flatnonzero(trainer.D["kind"] == 1)
    assert len(rows) == 1
    i = rows[0]
    assert trainer.D["d"][i] == trainer.D["y"][i] == 0.0
    assert trainer.M[i, 0] == 1.0
    assert trainer.M[i, 1:5].tolist() == [0.0, 0.0, 0.0, 0.0]
    scorer = linknet.Scorer.__new__(linknet.Scorer)
    scorer.D, scorer.K = trainer.D, trainer.K
    scorer.evidence()
    assert scorer.pair_ev(event["sh"] + "|" + event["k"], event["nz"][0][0]) == (1, 0, 0.0)
    assert builder.c["gain_noop_historical_source_only"] == 1
    assert event == original


def test_duplicate_y_nz_does_not_duplicate_a_label_and_actual_small_marginal_is_retained(tmp_path):
    event = source_event()
    event["nz"][0][1] += brain.EPS / 2
    expected = event["nz"][0][1] - event["n"][0]
    event["nz"] *= 2
    event["y"] = [["POS=True", 0.4, 1, 0.1, None, None, None, None, None, 2.0, 0.39584], ["NEG=True", -0.3, 1, 0.1, None, None, None, None, None, 1.0, -0.30416]]
    builder, trainer = build(event, tmp_path)
    rows = np.flatnonzero(trainer.D["kind"] == 1)
    assert len(rows) == 3
    assert trainer.D["d"][rows[-1]] == pytest.approx(expected, rel=1e-6, abs=1e-20)
    assert trainer.D["d"][rows[-1]] != 0.0
    assert trainer.D["y"][rows].tolist() == [1, 0, 0]
    assert trainer.M[rows, 0].tolist() == [2.0, 1.0, 1.0]
    event["y"].append(brain.source_noop_gain_cells(event, {})[0] if False else [event["nz"][0][0], expected, 1, 0.0, None, None, None, None, None, 1.0, event["nz"][0][1]])
    assert len(linknet.gain_cells(event)) == 3


@pytest.mark.parametrize("mutation", ["invalid_noop", "invalid_naked", "identity", "dead", "cached", "different_npz", "different_chain"])
def test_invalid_or_unverified_source_noops_are_not_training_examples(tmp_path, mutation):
    event = source_event()
    if mutation == "invalid_noop":
        event["nz"][0][2] = 0
    elif mutation == "invalid_naked":
        event["n"][1] = 0
    elif mutation == "identity":
        event["reason"] = "RUNNING_IDENTITY"
    elif mutation == "dead":
        event["reason"] = "DEAD_VEC_PATH"
    elif mutation == "cached":
        event["eval_provenance"]["naked"]["cached"] = True
    elif mutation == "different_npz":
        event["explicit_row_npz"] = "ts1791859500.0_n39744"
    else:
        event["eval_provenance"]["naked"]["cum_before"] += 1.0
    _, trainer = build(event, tmp_path)
    assert not np.any(trainer.D["kind"] == 1)


def test_existing_no_nz_inputs_preserve_original_labels_and_weights(tmp_path):
    event = source_event()
    event.pop("nz")
    event["y"] = [["POS=True", 0.4, 1, 0.2, None, None, None, None, None, 3.0, 0.39584], ["NEG=True", -0.3, 1, 0.2, None, None, None, None, None, 1.0, -0.30416]]
    with patch.object(brain, "source_noop_gain_cells", side_effect=AssertionError("no nz path must remain untouched")):
        _, trainer = build(event, tmp_path)
    rows = np.flatnonzero(trainer.D["kind"] == 1)
    assert trainer.D["y"][rows].tolist() == [1, 0]
    assert trainer.M[rows, 0].tolist() == [3.0, 1.0]
    assert trainer.M[rows, 1].tolist() == [3.0, 0.0]


def test_calibration_reader_includes_same_source_backed_zero(tmp_path):
    event = source_event()
    timestamp = "2026-10-13T02:30:00Z"
    category = "CRYPTO_SHORT"
    np.savez(tmp_path / ("linknet_scores_" + category + ".npz"), meta=np.frombuffer(json.dumps({"scored_at": "2026-10-01T00:00:00Z"}).encode(), np.uint8), row_keys=np.array([event["sh"] + "|" + event["k"]]), yellows=np.array([event["nz"][0][0]]), cell_row=np.array([0]), cell_yellow=np.array([0]), row_p_pos=np.array([[0.1]]), cell_p_pos=np.array([[0.9]]), cell_n_measured=np.array([0]))
    def sorted_source(directories, temporary, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(timestamp + "\t" + json.dumps(event, separators=(",", ":")) + "\n")
    args = SimpleNamespace(out_dir=tmp_path, tmp_dir=tmp_path / "tmp", spill_dir=[tmp_path], cat_side=[category], scores="auto", since=None, note="source-noop reader regression")
    with patch.object(brain, "sorted_events", side_effect=sorted_source):
        linknet.cmd_calibrate(args)
    report = json.loads((tmp_path / "linknet_calibration.jsonl").read_text())
    assert report["cells_all"]["n"] == 1
    assert report["cells_all"]["pos"] == report["cells_all"]["realised"] == 0
    assert report["cells_all"]["mean_pred"] == 0.9
