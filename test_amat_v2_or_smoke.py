import pytest
from tools import run_amat_v2_or_smoke as smoke
from tools import run_shared_route_formation_vector_lane as lane

def test_smoke_masks_are_exact_distinct_15m_families():
    arms=[{'family':'trend_structure','timeframe':'15m'}, {'family':'wedge','timeframe':'15m'}, {'family':'cup_handle','timeframe':'15m'}]
    cfg=smoke.cfg(lane.v8.SweepConfig(),arms,'ENTRY')
    assert cfg.FORMATION_TFS == '15m'
    assert cfg.FORMATION_TREND_STRUCTURE_ENTRY_ENABLED
    assert cfg.FORMATION_WEDGE_ENTRY_ENABLED
    assert cfg.FORMATION_CUP_HANDLE_ENTRY_ENABLED
    assert not cfg.FORMATION_TRIANGLE_ENTRY_ENABLED

def test_smoke_refuses_tf_collapse():
    with pytest.raises(ValueError, match='EXACT_15M'):
        smoke.cfg(lane.v8.SweepConfig(),[{'family':'trend_structure','timeframe':'1h'}],'ENTRY')
