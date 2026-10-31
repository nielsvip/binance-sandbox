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
