import numpy as np
import pytest

from vec_decisions.asr_noloss_exit_gate import noloss_exit_gate_array,noloss_exit_gate_scalar


def test_noloss_exit_gate_matches_native_order_ratio_and_hedge_exemptions():
    state={"gain":np.array([.29,.3,.3001,.2,.2,.2]),"effective_noloss_floor":np.array([.5,.5,.5,.5,.5,0.]),
      "ratio_available":np.array([False,True,True,False,False,False]),"ratio":np.array([1.,3.,3.,1.,1.,1.]),
      "is_hedge_pos":np.array([False,False,False,True,False,False]),
      "early_exit_fired":np.array([False,False,False,False,True,False])}
    result=noloss_exit_gate_array(state,True,ratio_max=2.5,ratio_min=.4)
    assert result["hold"].tolist()==[True,True,False,False,False,False]
    assert result["effective_floor"].tolist()==[.5,.5,.3,.5,.5,0.]
    assert result["overweight_trim"].tolist()==[False,False,True,False,False,False]

def test_noloss_short_ratio_threshold_and_floor_equality():
    state={"gain":np.array([.3,.30001,.29]),"effective_noloss_floor":np.full(3,.5),
      "ratio_available":np.ones(3,dtype=bool),"ratio":np.array([.4,.399,.2]),
      "is_hedge_pos":np.zeros(3,dtype=bool),"early_exit_fired":np.zeros(3,dtype=bool)}
    result=noloss_exit_gate_array(state,False,ratio_max=2.5,ratio_min=.4)
    assert result["overweight_trim"].tolist()==[False,True,False]
    assert result["hold"].tolist()==[True,False,True]

def test_scalar_array_parity_and_missing_floor_fails_closed():
    state={"gain":np.array([.2,.4,.2]),"effective_noloss_floor":np.array([.5,.5,.5]),
      "ratio_available":np.array([False,True,False]),"ratio":np.array([1.,3.,1.]),
      "is_hedge_pos":np.array([False,False,True]),"early_exit_fired":np.array([False,False,False])}
    result=noloss_exit_gate_array(state,True)
    expected=[noloss_exit_gate_scalar(state["gain"][i],state["effective_noloss_floor"][i],True,bool(state["ratio_available"][i]),float(state["ratio"][i]),is_hedge_pos=bool(state["is_hedge_pos"][i])) for i in range(3)]
    assert result["hold"].tolist()==[x[0] for x in expected]
    assert result["effective_floor"].tolist()==[x[1] for x in expected]
    with pytest.raises(ValueError,match="effective_noloss_floor"):
        noloss_exit_gate_array({"gain":[.2]},True)
