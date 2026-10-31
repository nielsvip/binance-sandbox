from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_hlr_top_exit import hlr_top_exit_array,hlr_top_exit_scalar


def _cfg(**kwargs):
    values={"HLR_TOP_EXIT_ENABLED":True,"NOLOSS_MIN_PROFIT_PCT":.3,"HLR_TOP_MIN_GAIN_PCT":1.5,
      "HLR_TOP_MIN_TFS":2,"HLR_TOP_VEL_1H_THRESH":-1.,"HLR_TOP_VEL_4H_THRESH":0.,"HLR_TOP_VEL_D_THRESH":0.,
      "HLR_REENTRY_MULT_1H":1.5,"HLR_REENTRY_MULT_4H":2.,"HLR_REENTRY_MULT_D":2.5,"HLR_REENTRY_MULT_W":3.}
    values.update(kwargs)
    return SimpleNamespace(**values)

def _rows(n,seed):
    rng=np.random.default_rng(seed)
    return {"pnl_pct":rng.uniform(-1,5,n),"wt_velocity_1h":rng.uniform(-8,8,n),
      "wt_velocity_4h":rng.uniform(-8,8,n),"wt_velocity_D":rng.uniform(-8,8,n),"wt_velocity_W":rng.uniform(-8,8,n),
      "wt_momentum_state_4h":rng.choice(["","EXHAUST_UP","EXHAUST_DOWN"],n),
      "wt_momentum_state_D":rng.choice(["","EXHAUST_UP","EXHAUST_DOWN"],n),
      "wt_momentum_state_W":rng.choice(["","EXHAUST_UP","EXHAUST_DOWN"],n),
      "wt_divergence_4h":rng.choice(["","BEAR","BULL"],n),"wt_divergence_D":rng.choice(["","BEAR","BULL"],n),
      "wt_peak_structure_4h":rng.choice(["","LH","HH"],n)}

def test_hlr_top_exit_array_matches_scalar_both_sides_and_switch_thresholds():
    for is_long,seed in ((True,4),(False,5)):
        values=_rows(2000,seed)
        result=hlr_top_exit_array(values,_cfg(),is_long)
        scalar=[hlr_top_exit_scalar({key:values[key][i].item() for key in values},_cfg(),is_long) for i in range(2000)]
        np.testing.assert_array_equal(result["fire"],[row[0] for row in scalar])
        np.testing.assert_allclose(result["reentry_multiplier"],[row[1] for row in scalar])
        assert all(result["masks"][tf].shape==(2000,) for tf in ("1h","4h","D","W"))

def test_hlr_top_exit_requires_a_higher_timeframe_and_both_gain_floors():
    values={"pnl_pct":np.array([1.49,1.5,2.,2.]),"wt_velocity_1h":np.array([-9.,-9.,-9.,0.]),
      "wt_velocity_4h":np.zeros(4),"wt_velocity_D":np.zeros(4),"wt_velocity_W":np.zeros(4),
      "wt_momentum_state_4h":np.array(["","","",""],object),"wt_momentum_state_D":np.array(["","","",""],object),
      "wt_momentum_state_W":np.array(["","","",""],object),"wt_divergence_4h":np.array(["","","",""],object),
      "wt_divergence_D":np.array(["","","",""],object),"wt_peak_structure_4h":np.array(["","","",""],object)}
    values["wt_momentum_state_4h"][2]="EXHAUST_UP"
    result=hlr_top_exit_array(values,_cfg(),True)
    assert result["fire"].tolist()==[False,False,True,False]
    assert result["reentry_multiplier"].tolist()==[1.,1.,2.,1.]
    off=hlr_top_exit_array(values,_cfg(HLR_TOP_EXIT_ENABLED=False),True)
    assert not off["fire"].any()
