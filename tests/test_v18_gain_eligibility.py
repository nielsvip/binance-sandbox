import numpy as np
from collections import Counter
from tools import v18_brain as brain
from tools import v18_linknet as linknet


def dataset():
    return {"kind": np.array([0, 0, 0, 1, 1]), "y": np.array([1, 0, 1, 0, 1]), "d": np.array([1.0, 0.0, np.nan, 0.0, 9.0]), "valid": np.array([1, 1, 1, 1, 0]), "w": np.ones(5), "m_tim": np.zeros(5), "m_dd": np.zeros(5), "m_wr": np.zeros(5), "set_i": np.zeros(5, dtype=int), "filt_i": np.zeros(5, dtype=int), "ts": np.arange(5.0), "b_t0": np.arange(5.0)}


def test_targets_preserve_diagnostics_but_mask_ambiguous_and_unknown():
    trainer = linknet.Trainer.__new__(linknet.Trainer)
    trainer.D = dataset()
    trainer._targets()
    assert trainer.M[:, 0].tolist() == [1, 1, 0, 1, 0]
    assert np.isfinite(trainer.Y).all()
    assert trainer.D["d"][-1] == 9.0
    assert trainer.D["valid"][-1] == 0
    assert trainer.gain_eligibility == {"eligible": 3, "excluded_ambiguous_invalid": 1, "excluded_missing_nonfinite": 1}
    splits, _ = trainer.splits()
    assert splits["all"].tolist() == [True, True, False, True, False]
    for name in ("fit", "val", "pre_test", "test", "test_ph"):
        assert not (splits[name] & ~splits["all"]).any()


def test_count_priors_and_runtime_counts_ignore_unknown_and_invalid():
    trainer = linknet.Trainer.__new__(linknet.Trainer)
    trainer.D = dataset()
    trainer.K = {"sets": ["row"], "filts": ["filter"]}
    all_rows = np.arange(5)
    clean_rows = np.array([0, 1, 3])
    full, _ = trainer.count_baseline(all_rows, all_rows)
    clean, _ = trainer.count_baseline(clean_rows, all_rows)
    np.testing.assert_array_equal(full, clean)
    empty, _ = trainer.count_baseline(np.array([2, 4]), all_rows)
    np.testing.assert_array_equal(empty, np.full(5, 0.5))
    scorer = linknet.Scorer.__new__(linknet.Scorer)
    scorer.D, scorer.K = trainer.D, trainer.K
    scorer.evidence()
    assert scorer.ev["n_set"].tolist() == [2]
    assert scorer.ev["nfo"].tolist() == [1]


def test_graph_diagnostic_visits_do_not_become_negative_evidence():
    graph = brain.Graph("CRYPTO_LONG")
    index = graph.idx(0, "R:SW=True")
    graph.add_eval(index, 9.0, False, 0.1, [None] * 5, 1.0, 1)
    graph.add_eval(index, float("nan"), True, 0.1, [None] * 5, 1.0, 1)
    assert graph.pw(0, "R:SW=True") == (0.0, 0.0)
    graph.add_eval(index, 0.0, True, 0.1, [None] * 5, 1.0, 1)
    assert graph.pw(0, "R:SW=True") == (0.0, 1.0)
    assert not brain.gain_eligible(False, 9.0)
    assert not brain.gain_eligible(True, float("inf"))
    assert brain.gain_eligible(True, 0.0)


def test_graph_heads_never_learn_from_ambiguous_naked_or_cells():
    model = brain.CatBrain("CRYPTO_LONG", brain.Buckets(), "2026-10-01T00:00:00Z")
    event = {"e": "row", "s": "BTCUSDT_LONG", "b": "BTCUSDT_LONG|2026-10-20T00:00:00Z", "sh": "EXIT_STRUCTURAL", "k": "SW=True", "n": [9.0, 0, 0.1, None, None, None, None, None, 1.0, 1], "y": [["FILTER=True", 7.0, 0, 0.1, None, None, None, None, None, 1.0, 16.0]]}
    model.handle(event, "2026-10-20T00:00:00Z")
    assert model.h.n["R"] == model.h.n["F"] == model.h.n["F0"] == model.h.n["M"] == model.h.n["B"] == 0
    assert model.c["gain_naked_excluded_ambiguous_invalid"] == 1
    event["n"][0:2] = [0.0, 1]
    event["y"][0][1:3] = [0.0, 1]
    model.handle(event, "2026-10-20T00:01:00Z")
    assert model.h.n["R"] == model.h.n["F"] == 1


def test_raw_failure_reason_retained_without_inventing_execution_success():
    events = []
    assembler = brain.Assembler("BTCUSDT_LONG", "frozen_raw.jsonl", events.append, Counter())
    record = {"ts": "2026-10-20T00:00:00Z", "sheet": "EXIT_STRUCTURAL", "row": 3, "switch": "SW", "cand": True, "label": "naked", "delta": 0.0, "gain_pct": 0.0, "cum_before": 0.0, "valid": False, "invalid_reason": "prepare failed (no npz)", "err": "missing data"}
    assembler.feed(record, 1)
    assembler.close_row(False)
    row = next(event for event in events if event["e"] == "row")
    assert row["eval_provenance"]["naked"]["invalid_reason"] == "prepare failed (no npz)"
    assert row["eval_provenance"]["naked"]["err"] == "missing data"
    assert "evaluation_success" not in row["eval_provenance"]["naked"]


def test_dataset_to_trainer_preserves_missing_vs_invalid_and_real_delta(tmp_path):
    builder = linknet.CatDS("CRYPTO_LONG")
    for index, (delta, valid) in enumerate([(9.0, 0), (None, 1), (0.0, 1)]):
        timestamp = f"2026-10-20T00:0{index}:00Z"
        event = {"s": "BTCUSDT_LONG", "b": "BTCUSDT_LONG|2026-10-20T00:00:00Z", "sh": "EXIT_STRUCTURAL", "k": f"SW{index}=True", "n": [delta, valid, 0.1, None, None, None, None, None, 1.0, 1], "y": []}
        builder.row(event, timestamp)
    builder.save(tmp_path)
    trainer = linknet.Trainer.__new__(linknet.Trainer)
    trainer.D, _ = linknet.load_ds("CRYPTO_LONG", tmp_path)
    trainer._targets()
    assert trainer.D["valid"].tolist() == [0, 1, 1]
    assert trainer.D["d"][0] == 9.0
    assert np.isnan(trainer.D["d"][1])
    assert trainer.M[:, 0].tolist() == [0, 0, 1]
    assert trainer.gain_eligibility == {"eligible": 1, "excluded_ambiguous_invalid": 1, "excluded_missing_nonfinite": 1}


def test_chain_join_requires_unique_context_at_evaluation_time():
    builder = linknet.CatDS("CRYPTO_LONG")
    builder.ctxj = {"cbmap": {("BTCUSDT_LONG", 1.0): [(2, 99, 100.0, 110.0), (3, 1, 200.0, 210.0)]}}
    event = {"s": "BTCUSDT_LONG", "b": "board", "cb": 1.0}
    assert builder._base_of(event, 105.0) == 2
    assert builder._base_of(event, 205.0) == 3
    assert builder._base_of(event, 150.0) is None
    builder.ctxj["cbmap"][("BTCUSDT_LONG", 1.0)].append((4, 1, 100.0, 110.0))
    assert builder._base_of(event, 105.0) is None
    assert builder.c["ctx_log_outside_recorded_time"] == 1
    assert builder.c["ctx_log_ambiguous_time_match"] == 1
