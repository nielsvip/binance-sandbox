import copy
from collections import Counter
from unittest.mock import patch

import pytest

from tools import v18_brain as brain


def source_event():
    return {"e": "row", "s": "1000BONKUSDC_SHORT", "b": "source-board", "sh": "ENTRY_REVERSAL_BOUNCE", "k": "BB_BOUNCE_ENTRY_TF=4h", "src": "progress", "ts_source": "explicit_progress_row_npz", "source_file": "source_progress.json", "source_sha256": "a" * 64, "progress_key": "ENTRY_REVERSAL_BOUNCE!5:BB_BOUNCE_ENTRY_TF=4h", "source_record_identity": "b" * 64, "npz": 1791858600.0, "explicit_row_npz": "ts1791858600.0_n39744", "engine_hash_per_row": None, "cb": 1.41493, "nzk": 1, "n": [-0.00416, 1, 0.0, None, None, None, None, None, 1.0, 1], "y": [], "nz": [["WT_15M_BOUNCE_BB_MIN=0.1", -0.00416, 1]], "eval_provenance": {"naked": {"valid": True, "cached": False, "cum_before": 1.414931271556552}}}


def model():
    return brain.CatBrain("CRYPTO_SHORT", brain.Buckets(), "2026-10-01T00:00:00Z")


def test_source_noop_teaches_gain_heads_and_graph_before_counting_evidence():
    learner = model()
    event = source_event()
    original = copy.deepcopy(event)
    with patch.object(learner, "_record", wraps=learner._record) as capture:
        learner.handle(event, "2026-10-13T10:30:00Z")
    assert learner.h.n["F"] == learner.h.n["F0"] == 1
    assert learner.h.n["M"] == learner.h.n["B"] == 1
    gain_records = [call.args for call in capture.call_args_list if call.args[0] in ("F", "F0R")]
    assert len(gain_records) == 2
    assert all(record[1] == brain.sig(-4.0) and record[3] == 0.0 and record[5] == 0.0 for record in gain_records)
    assert learner.g.pw(0, "F:WT_15M_BOUNCE_BB_MIN=0.1") == (0.0, 1.0)
    assert learner.g.pw(0, "F:WT_15M_BOUNCE_BB_MIN=0.1", "R:BB_BOUNCE_ENTRY_TF=4h") == (0.0, 1.0)
    assert learner.c["gain_noop_historical_source_only"] == 1
    assert event == original


def test_actual_small_marginal_is_preserved_instead_of_forcing_zero():
    event = source_event()
    event["nz"][0][1] += brain.EPS / 2
    cells = brain.source_noop_gain_cells(event, Counter())
    assert len(cells) == 1
    assert cells[0][1] == event["nz"][0][1] - event["n"][0]
    assert cells[0][1] != 0.0
    assert cells[0][3:9] == [0.0, None, None, None, None, None]


def test_existing_positive_negative_and_noop_cells_are_not_counted_twice():
    event = source_event()
    event["y"] = [["POS=True", 0.4, 1, 0.2, None, None, None, None, None, 1.0, 0.39584], ["NEG=True", -0.3, 1, 0.2, None, None, None, None, None, 1.0, -0.30416], ["WT_15M_BOUNCE_BB_MIN=0.1", 0.0, 1, 0.2, None, None, None, None, None, 1.0, -0.00416]]
    event["nz"] *= 2
    with_noops, original = model(), model()
    without_noops = copy.deepcopy(event)
    without_noops.pop("nz")
    with_noops.handle(event, "2026-10-13T10:30:00Z")
    original.handle(without_noops, "2026-10-13T10:30:00Z")
    assert with_noops.h.w == original.h.w
    assert with_noops.h.n["F0"] == original.h.n["F0"] == 3
    assert with_noops.g.pw(0, "F:POS=True") == (1.0, 1.0)
    assert with_noops.g.pw(0, "F:NEG=True") == (0.0, 1.0)
    assert with_noops.c["gain_noop_duplicate"] == 2


@pytest.mark.parametrize("case", ["missing_naked", "invalid_naked", "cached", "identity", "unwired", "invalid_cell", "nonfinite", "different_chain", "different_npz", "missing_source_hash", "missing_source_identity", "sampled_cell", "unwired_cell", "failed_cell", "not_noop"])
def test_unverified_records_do_not_become_gain_evidence(case):
    event = source_event()
    header = event["nz"][0][0]
    if case == "missing_naked":
        event["n"] = None
    elif case == "invalid_naked":
        event["n"][1] = 0
    elif case == "cached":
        event["eval_provenance"]["naked"]["cached"] = True
    elif case == "identity":
        event["reason"] = "RUNNING_IDENTITY: no evaluation"
    elif case == "unwired":
        event["reason"] = "DEAD_VEC_PATH: not applied"
    elif case == "invalid_cell":
        event["nz"][0][2] = 0
    elif case == "nonfinite":
        event["nz"][0][1] = float("nan")
    elif case == "different_chain":
        event["eval_provenance"]["naked"]["cum_before"] += 1
    elif case == "different_npz":
        event["explicit_row_npz"] = "other"
    elif case == "missing_source_hash":
        event.pop("source_sha256")
    elif case == "missing_source_identity":
        event.pop("source_record_identity")
    elif case == "sampled_cell":
        event["sampled_out_filters"] = [header]
    elif case == "unwired_cell":
        event["excluded_unwired"] = [header]
    elif case == "failed_cell":
        event["yellow_reasons"] = {header: "TIM out of range"}
    elif case == "not_noop":
        event["nz"][0][1] += 0.1
    assert brain.source_noop_gain_cells(event, Counter()) == []


def test_export_marks_historical_noops_without_current_engine_authority():
    learner = model()
    learner.handle(source_event(), "2026-10-13T10:30:00Z")
    state = brain._state_dump({"meta": {}, "tidx": {}, "brains": {"CRYPTO_SHORT": learner}})
    blob, _ = brain.export_one(state["brains"]["CRYPTO_SHORT"], {}, 5, 0.001, 0.002)
    assert blob["_meta"]["source_noop_gain"] == {"policy": brain.SOURCE_NOOP_GAIN_POLICY, "restored": 1, "current_engine_cell_authority": False, "timing_measured": False, "propensity_known": False}
    assert blob["_meta"]["p_ref"] == {"R": 0.001, "F": 0.002}
