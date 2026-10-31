import ast
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from vec_decisions.tradeable_mandatory_entry import entry_masks, force_trigger

@pytest.mark.parametrize('is_long',[True,False])
@pytest.mark.parametrize('require_hh',[True,False])
def test_force_predicate_matches_unchanged_native_source(is_long,require_hh):
    path=Path('backups/before_mandatory_wt_force_native_shared_ez_manage_20261031T071547Z_1793430947180439000.py')
    tree=ast.parse(path.read_text())
    fn=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='_process_single_override_check')
    names=('_dist_ok_z','_wt_dir_ok_z','_htf_ok_z','_hh_ok_z','_wt_trigger_z')
    nodes=[next(node for node in ast.walk(fn) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)) for name in names]
    code=compile(ast.Module(body=nodes,type_ignores=[]),'<native WT force>','exec')
    rng=np.random.default_rng(297);n=400
    fields=['_px_z','_sma15_z','_wt1_3m_z','_wt2_3m_z','_wt1_15m_z','_wt2_15m_z','_wt1_1h_z','_wt2_1h_z','_xv_z','_xvp_z']
    values=[rng.uniform(-5.,110.,n) for _ in fields]
    expected=[]
    for index in range(n):
        scope={key:val[index] for key,val in zip(fields,values)}
        scope.update(_is_long_z=is_long,_is_short_z=not is_long,_fo_pct_z=.01,_req_hh_z=require_hh)
        exec(code,{},scope);expected.append(scope['_wt_trigger_z'] and scope['_px_z']>0)
    np.testing.assert_array_equal(force_trigger(*values,is_long,.01,require_hh),expected)

def test_parent_switch_and_raw_dc_elif_precedence():
    data={'close':np.array([105.,105.]),'dc_high_15m':np.array([100.,110.]),'k_15m':np.array([60.,60.]),'k_15m_prev':np.array([50.,50.]),'sma_200_15m':np.array([90.,90.]),'wt1_15m':np.array([2.,2.]),'wt2_15m':np.array([0.,0.]),'wt1_1h':np.array([2.,2.]),'wt2_1h':np.array([0.,0.])}
    cfg=SimpleNamespace(TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED=True,WT_3M_FORCE_OPEN_ENABLED=True,WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS=False,WT_3M_FORCE_OPEN_GR_GATE_ENABLED=False)
    dc,force=entry_masks(data,2,True,cfg,data['close']);np.testing.assert_array_equal(dc,[True,False]);np.testing.assert_array_equal(force,[False,True])
    cfg.WT_3M_FORCE_OPEN_GR_GATE_ENABLED=True;cfg.WT_3M_FORCE_OPEN_GR_VOTE_MIN=999
    dc,force=entry_masks(data,2,True,cfg,data['close']);assert not dc.any() and not force.any()
    cfg.WT_3M_FORCE_OPEN_GR_GATE_ENABLED=False;cfg.TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED=False
    dc,force=entry_masks(data,2,True,cfg,data['close']);assert not dc.any() and not force.any()

def test_force_switch_changes_actual_engine_ledger():
    import v12_quick_engine as engine
    from tests.test_quick_srs_paired_15m import _cfg,_npz
    from vec_decisions.generic_filter_tf import FILTER_TF_MAP
    data=_npz();n=len(data['timestamps']);cfg=_cfg()
    data['sma_200_15m']=np.full(n,200.);data['sma_200_15m'][2]=90.
    data['wt1_15m']=np.full(n,2.);data['wt2_15m']=np.zeros(n);data['wt1_1h']=np.full(n,2.);data['wt2_1h']=np.zeros(n)
    for key,value in {'TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED':True,'WT_3M_FORCE_OPEN_ENABLED':True,'WT_3M_FORCE_OPEN_GR_GATE_ENABLED':False,'WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS':False,'ABLATION_DISABLE_QUICK_ENTRY':True,'STRUCTURAL_RANGE_SHIFT_EXIT':False,'OPTIMAL_HOLD_BARS_EXIT_ENABLED':False}.items():setattr(cfg,key,value)
    for name,(target,_) in FILTER_TF_MAP.items():
        if target=='entry':setattr(cfg,name,'OFF')
    result=engine.simulate_one(data,'WT_FORCE_TEST',True,cfg)
    opens=[r for r in result['ledger'] if r.get('type')=='OPEN']
    assert opens and opens[0]['bar']==2 and 'WT_3M_FORCE_OPEN' in opens[0]['reason']
    cfg.WT_3M_FORCE_OPEN_ENABLED=False
    result=engine.simulate_one(data,'WT_FORCE_TEST',True,cfg)
    assert not [r for r in result['ledger'] if r.get('type')=='OPEN']
