import numpy as np
import pytest
from vec_paths.live_entry_engine import compute_engine_score_arrays, compute_engine_scores_vec

class Config:
    LIVE_ENTRY_ENGINE_ENABLED=True
    LIVE_ENTRY_ENGINE_WT_ENABLED=True
    LIVE_ENTRY_ENGINE_STOCH_ENABLED=True
    LIVE_ENTRY_ENGINE_DC_ENABLED=True
    LIVE_ENTRY_ENGINE_HTF_ENABLED=True
    LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED=True
    LIVE_ENTRY_ENGINE_MIN_SCORE=.5
    USE_1M_3M_SIGNALS_ENABLED=True
    LIVE_ENTRY_ENGINE_BOOST_SCORE=8.

def test_array_engine_scores_match_scalar_provider_on_both_sides_and_modes():
    rng=np.random.default_rng(7712); n=96
    npz={"close":rng.uniform(80,120,n),"wt1_3m":rng.normal(size=n),"wt2_3m":rng.normal(size=n),"wt1_15m":rng.normal(size=n),"wt2_15m":rng.normal(size=n),"wt1_1h":rng.normal(size=n),"wt2_1h":rng.normal(size=n),"wt1_4h":rng.normal(size=n),"wt2_4h":rng.normal(size=n),"wt1_D":rng.normal(size=n),"wt2_D":rng.normal(size=n),"wt_velocity_3m":rng.normal(size=n),"wt_velocity_1h":rng.normal(size=n),"wt_velocity_4h":rng.normal(size=n),"stoch_k_5m":rng.uniform(0,100,n),"stoch_d_5m":rng.uniform(0,100,n),"k_5m_prev":rng.uniform(0,100,n),"d_5m_prev":rng.uniform(0,100,n),"stoch_k_15m":rng.uniform(0,100,n),"stoch_d_15m":rng.uniform(0,100,n),"stoch_k_1h":rng.uniform(0,100,n),"stoch_d_1h":rng.uniform(0,100,n),"stoch_k_4h":rng.uniform(0,100,n),"stoch_d_4h":rng.uniform(0,100,n),"dc_high_3m":rng.uniform(90,110,n),"dc_high_15m":rng.uniform(90,110,n),"dc_high_1h":rng.uniform(90,110,n),"dc_low_3m":rng.uniform(90,110,n),"dc_low_15m":rng.uniform(90,110,n),"dc_low_1h":rng.uniform(90,110,n),"dc_low_4h":rng.uniform(90,110,n),"dc_high_4h":rng.uniform(90,110,n),"dc_basis_15m":rng.uniform(90,110,n),"ha_5m":rng.choice(np.array([-1,0,1],dtype=np.int8),n),"sma_200_D":rng.uniform(90,110,n),"dc_basis_D":rng.uniform(90,110,n),"dc_basis_D_ant":rng.uniform(90,110,n),"ha_4h":rng.choice(np.array([-1,0,1],dtype=np.int8),n),"ha_D":rng.choice(np.array([-1,0,1],dtype=np.int8),n),"stoch_k_4h":rng.uniform(1,100,n),"bb_pct_b_D":rng.uniform(0,1,n),"bb_pct_b_4h":rng.uniform(0,1,n),"bb_pct_b_1h":rng.uniform(0,1,n)}
    for side in ("LONG","SHORT"):
        vector=compute_engine_score_arrays(npz,n,side,Config(),mode="tradier")
        for i in range(n):
            scalar=compute_engine_scores_vec(npz,i,side,Config(),mode="tradier")
            for name,(fire,score) in vector.items():
                want=scalar.get(name,(False,"",0.))
                assert bool(fire[i])==bool(want[0]),(side,i,name,fire[i],want)
                assert float(score[i])==pytest.approx(float(want[2]),abs=1e-12),(side,i,name,score[i],want)


@pytest.mark.parametrize("mode", ["crypto", "tradier"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ltf_off_arrays_match_shared_native_15m_input_adapter(mode, side):
    rng=np.random.default_rng(9127); n=12
    npz={"close":rng.uniform(80,120,n),"close_15m":rng.uniform(80,120,n)}
    for tf in ("3m","15m","1h","4h","D"):
        npz[f"wt1_{tf}"]=rng.normal(size=n); npz[f"wt2_{tf}"]=rng.normal(size=n)
    npz["wt_velocity_3m"]=rng.normal(size=n); npz["wt_velocity_15m"]=rng.normal(size=n)
    for tf in ("3m","15m","1h","4h"):
        npz[f"stoch_k_{tf}"]=rng.uniform(0,100,n); npz[f"stoch_d_{tf}"]=rng.uniform(0,100,n)
    npz["k_15m_prev"]=rng.uniform(0,100,n); npz["d_15m_prev"]=rng.uniform(0,100,n)
    npz["stoch_k_15m_prev"]=npz["k_15m_prev"]; npz["stoch_d_15m_prev"]=npz["d_15m_prev"]
    for tf in ("3m","15m","1h","4h"):
        npz[f"dc_high_{tf}"]=rng.uniform(90,110,n); npz[f"dc_low_{tf}"]=rng.uniform(90,110,n)
    npz["dc_basis_15m"]=rng.uniform(90,110,n); npz["ha_3m"]=rng.choice(np.array([-1,0,1],dtype=np.int8),n); npz["ha_15m"]=rng.choice(np.array([-1,0,1],dtype=np.int8),n)
    npz.update({"dc_high_4h":rng.uniform(90,110,n),"dc_low_4h":rng.uniform(90,110,n),"sma_200_D":rng.uniform(90,110,n),"dc_basis_D":rng.uniform(90,110,n),"dc_basis_D_ant":rng.uniform(90,110,n),"ha_4h":rng.choice(np.array([-1,0,1],dtype=np.int8),n),"ha_D":rng.choice(np.array([-1,0,1],dtype=np.int8),n),"stoch_k_4h":rng.uniform(1,100,n),"bb_pct_b_D":rng.uniform(0,1,n),"bb_pct_b_4h":rng.uniform(0,1,n)})
    cfg=Config(); cfg.USE_1M_3M_SIGNALS_ENABLED=False
    arrays=compute_engine_score_arrays(npz,n,side,cfg,mode=mode)
    for idx in range(n):
        scalar=compute_engine_scores_vec(npz,idx,side,cfg,mode=mode)
        for name,(fire,score) in arrays.items():
            expected=scalar.get(name,(False,"",0.))
            assert bool(fire[idx])==bool(expected[0]),(mode,side,idx,name,fire[idx],expected)
            assert float(score[idx])==pytest.approx(float(expected[2]),abs=1e-12),(mode,side,idx,name,score[idx],expected)


def test_paired_15m_adapter_uses_previous_15m_values_and_never_stale_ltf():
    from vec_paths.live_entry_engine import pair_15m_engine_indicators
    indicators={"wt1_3m":999.,"wt1_15m":4.,"wt_velocity_3m":-9.,"wt_velocity_15m":2.,"stoch_k_3m":1.,"stoch_k_15m":65.,"k_3m_prev":0.,"k_15m_prev":55.,"d_3m_prev":99.,"d_15m_prev":60.,"dc_high_3m":100.,"dc_high_15m":110.,"ha_3m":"red","ha_15m":"green"}
    paired=pair_15m_engine_indicators(indicators,False)
    assert paired["wt1_3m"]==4. and paired["wt_velocity_3m"]==2.
    assert paired["stoch_k_3m"]==65. and paired["k_3m_prev"]==55. and paired["d_3m_prev"]==60.
    assert paired["dc_high_3m"]==110. and paired["ha_3m"]=="green"
    assert indicators["wt1_3m"]==999. and indicators["dc_high_3m"]==100.
