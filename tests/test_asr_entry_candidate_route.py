import numpy as np
import pytest

import vec_decisions.asr_ordered_composer as composer


def test_entry_candidate_route_passes_prefix_score_and_masks_upstream_veto(monkeypatch):
    seen = {}

    def fake_late(inputs, is_long, is_exit=False):
        seen["score"] = inputs["score_before_dc"].copy()
        seen["is_long"] = is_long
        seen["is_exit"] = is_exit
        return {"final_score": inputs["score_before_dc"] + 8,
                "recommendation": np.full(3, "OPEN", dtype=object),
                "masks": {}, "complete_rate": False, "remaining_stages": ()}

    monkeypatch.setattr(composer, "compose_asr_late_score", fake_late)
    prefix = {"score": np.array([11.0, 14.0, 20.0]),
              "entry_allowed": np.array([True, False, True])}
    late_inputs = {"n": 3, "score_before_dc": np.array([-99.0, -99.0, -99.0]), "stages": {}}
    result = composer.compose_asr_entry_candidate_array(
        prefix, late_inputs, True, np.array([True, True, False])
    )
    assert seen["score"].tolist() == [11.0, 0.0, 0.0]
    assert seen["is_long"] is True and seen["is_exit"] is False
    assert result["entry_allowed"].tolist() == [True, False, False]
    assert result["final_score"].tolist() == [19.0, 0.0, 0.0]
    assert result["recommendation"].tolist() == ["OPEN", "WAIT", "WAIT"]
    assert result["masks"]["upstream_entry_veto"].tolist() == [False, False, True]
    assert result["masks"]["entry_prefix_veto"].tolist() == [False, True, False]
    assert result["complete_rate"] is False
    assert result["remaining_stages"][-1] == "native_entry_family_sources_and_action_dispatch"
    assert late_inputs["score_before_dc"].tolist() == [-99.0, -99.0, -99.0]


def test_entry_candidate_route_requires_explicit_upstream_lifecycle_mask():
    with pytest.raises(ValueError, match="upstream_entry_eligible"):
        composer.compose_asr_entry_candidate_array(
            {"score": [1.0], "entry_allowed": [True]},
            {"n": 1, "stages": {}}, True, None
        )
