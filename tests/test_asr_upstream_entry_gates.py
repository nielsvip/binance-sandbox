import numpy as np
import pytest

from vec_decisions.asr_upstream_entry_gates import asr_upstream_entry_gates


def _state():
    return {"current_price":np.array([100.,100.,100.,0.,100.]),
      "tradeable_key_set_nonempty":np.array([True,True,False,False,True]),
      "tradeable_position_member":np.array([True,False,False,False,False]),
      "temp_key_member":np.array([False,False,False,False,False]),
      "position_exists":np.array([False,False,False,False,True]),
      "position_amt":np.array([0.,0.,0.,0.,-1.]),
      "btc_dedicated_account_blocked":np.array([False,False,True,False,True]),
      "is_allowed":np.array([True,True,True,True,True])}

def test_upstream_gates_preserve_tradeable_position_btc_permission_price_order():
    result=asr_upstream_entry_gates(_state())
    assert result["reason"].tolist()==["","NOT A TRADEABLE KEY","BTC_HARD_BLOCKED","No current_price","BTC_HARD_BLOCKED"]
    assert result["eligible"].tolist()==[True,False,False,False,True]

def test_temp_membership_allows_nontradeable_candidate_and_unallowed_is_later():
    state=_state()
    state["temp_key_member"][1]=True
    state["is_allowed"][1]=False
    result=asr_upstream_entry_gates(state)
    assert result["reason"][1]=="Not Allowed"

def test_upstream_gates_require_exact_account_and_position_facts():
    state=_state()
    del state["tradeable_position_member"]
    with pytest.raises(ValueError,match="tradeable_position_member"):
        asr_upstream_entry_gates(state)
