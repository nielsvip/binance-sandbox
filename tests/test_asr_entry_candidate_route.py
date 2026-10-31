import numpy as np
import pytest
from types import SimpleNamespace

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


def test_entry_candidate_route_computes_account_membership_vetoes_from_explicit_state(monkeypatch):
    def fake_late(inputs, is_long, is_exit=False):
        return {"final_score": np.ones(2, dtype=int) * 22,
                "recommendation": np.full(2, "OPEN", dtype=object),
                "masks": {}, "complete_rate": False, "remaining_stages": ()}
    monkeypatch.setattr(composer, "compose_asr_late_score", fake_late)
    prefix={"score":np.array([5.,6.]),"entry_allowed":np.array([True,True])}
    state={"current_price":np.array([100.,100.]),"tradeable_key_set_nonempty":np.array([True,True]),
      "tradeable_position_member":np.array([False,True]),"temp_key_member":np.array([False,False]),
      "position_exists":np.array([False,False]),"position_amt":np.zeros(2),
      "btc_dedicated_account_blocked":np.zeros(2,dtype=bool),"is_allowed":np.ones(2,dtype=bool)}
    result=composer.compose_asr_entry_candidate_array(prefix,{"n":2,"stages":{}},True,state)
    assert result["entry_allowed"].tolist()==[False,True]
    assert result["recommendation"].tolist()==["WAIT","OPEN"]
    assert result["upstream_reason"].tolist()==["NOT A TRADEABLE KEY",""]


def test_raw_npz_candidate_api_keeps_prefix_late_price_and_stage_order_bound(monkeypatch):
    calls=[]
    prefix={"score":np.array([6.]),"entry_allowed":np.array([True]),"masks":{"source_gate":np.array([False])},"reentry_state_out":{"bounce_reentry_k_reset":True}}
    def fake_prefix(npz,n,is_long,cfg,context,score=None):
        calls.append(("prefix",npz,context["current_price"].copy()))
        return prefix
    def fake_assemble(npz,cfg,state,n,is_long):
        calls.append(("assemble",npz,state["current_price"].copy()))
        return {"n":n,"cfg":cfg,"score_before_dc":np.array([0.]),"stages":{}}
    def fake_compose(prefix_result,late_inputs,is_long,upstream):
        assert prefix_result is prefix
        assert late_inputs["score_before_dc"].tolist()==[0.]
        return {"final_score":np.array([22]),"recommendation":np.array(["OPEN"],object),"masks":{},"complete_rate":False}
    monkeypatch.setattr("vec_decisions.asr_entry_prefix.score_asr_entry_prefix_array",fake_prefix)
    monkeypatch.setattr(composer,"assemble_asr_composer_inputs",fake_assemble)
    monkeypatch.setattr(composer,"compose_asr_entry_candidate_array",fake_compose)
    npz={"timestamps":np.array([15])}; cfg=object(); prices=np.array([100.])
    state={"current_price":prices.copy()}
    result=composer.evaluate_asr_entry_candidate_array(npz,1,True,cfg,{"current_price":prices.copy()},state,{"membership":True})
    assert [row[0] for row in calls]==["prefix","assemble"]
    assert calls[0][1] is npz and calls[1][1] is npz
    assert result["reentry_state_out"]=={"bounce_reentry_k_reset":True}
    assert result["masks"]["prefix:source_gate"].tolist()==[False]
    assert result["complete_rate"] is False

def test_raw_npz_candidate_api_rejects_mismatched_late_price():
    with pytest.raises(ValueError,match="current_price must match"):
        composer.evaluate_asr_entry_candidate_array(
            {"timestamps":np.array([1])},1,True,object(),{"current_price":[100.]},
            {"current_price":[101.]},{"membership":True})


def test_candidate_route_applies_native_no_double_augment_before_late_score(monkeypatch):
    seen = {}
    def fake_late(inputs, is_long, is_exit=False):
        seen["score"] = inputs["score_before_dc"].copy()
        score = inputs["score_before_dc"] + 20
        return {"final_score": score, "recommendation": np.where(score > 0, "GOOD_BUY", "WAIT"),
                "masks": {}, "complete_rate": False, "remaining_stages": ()}
    monkeypatch.setattr(composer, "compose_asr_late_score", fake_late)
    prefix = {"score": np.array([10., 11.]), "entry_allowed": np.array([True, True])}
    late = {"n": 2, "cfg": SimpleNamespace(MIN_GAIN=.5), "stages": {}}
    lifecycle = {"position_exists": [True, True], "last_augmentation_present": [True, True],
                 "minutes_since_augmentation": [23.0, 24.0], "gain": [.49, .49]}
    result = composer.compose_asr_entry_candidate_array(
        prefix, late, True, np.array([True, True]), lifecycle_state=lifecycle
    )
    assert seen["score"].tolist() == [0.0, 11.0]
    assert result["entry_allowed"].tolist() == [False, True]
    assert result["recommendation"].tolist() == ["WAIT", "GOOD_BUY"]
    assert result["masks"]["no_double_augment_veto"].tolist() == [True, False]
