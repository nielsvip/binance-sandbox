import ast
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from golden_rule_htf import _ind_score
from vec_decisions.tradeable_mandatory_entry import entry_mask, trigger
from vec_decisions.asr_input_adapter import paired_indicator_view

@pytest.mark.parametrize('is_long', [True, False])
@pytest.mark.parametrize('use_ltf', [True, False])
@pytest.mark.parametrize('gate', [True, False])
def test_numpy_matches_original_native_predicate_and_gr(is_long, use_ltf, gate):
    backup = Path('backups/before_mandatory_entry_shared_predicate_ez_manage_20261031T070641Z_1793430401633008000.py')
    tree = ast.parse(backup.read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_process_single_override_check')
    expr = next(node.value for node in ast.walk(fn) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_trigger_z' for t in node.targets))
    compiled = compile(ast.Expression(expr), '<original native trigger>', 'eval')
    n=240; rng=np.random.default_rng(792)
    data={}
    for tf in ('3m','15m','1h','4h','D'):
        for field in ('k','k_prev','wt1','wt2','rsi','mfi','stoch_k','stoch_d','adx'):
            data[f'k_{tf}_prev' if field == 'k_prev' else f'{field}_{tf}']=rng.uniform(-10,100,n)
        data[f'dc_high_{tf}']=rng.uniform(95,110,n)
        data[f'dc_low_{tf}']=rng.uniform(80,94,n)
        data[f'bb_upper_{tf}']=np.full(n,110.)
        data[f'bb_lower_{tf}']=np.full(n,80.)
        data[f'relative_volume_{tf}']=rng.uniform(0,3,n)
        data[f'macd_hist_{tf}']=rng.uniform(-1,1,n)
        data[f'ha_color_{tf}']=rng.choice([-1,0,1],n)
        data[f'ha_{tf}']=rng.choice([-1,0,1],n)
    prices=rng.uniform(75,115,n)
    cfg=SimpleNamespace(MODE='crypto',TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED=True,USE_1M_3M_SIGNALS_ENABLED=use_ltf,WT_3M_FORCE_OPEN_GR_GATE_ENABLED=gate,WT_3M_FORCE_OPEN_GR_VOTE_MIN=20,WT_3M_FORCE_OPEN_GR_MIN_TFS=2,WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF=5)
    expected=[]
    for i in range(n):
        ind={key:val[i] for key,val in data.items()}
        ind,_,_=paired_indicator_view(ind,{},use_ltf)
        ha={-1:'red',0:'neutral',1:'green'}[ind['ha_3m']]
        scope=dict(_is_long_z=is_long,_is_short_z=not is_long,_px_z=prices[i],_dch3m_z=ind['dc_high_3m'],_dcl3m_z=ind['dc_low_3m'],_rising_z=ind['k_3m']>ind['k_3m_prev'] or ha=='green',_falling_z=ind['k_3m']<ind['k_3m_prev'] or ha=='red')
        fire=eval(compiled,{},scope)
        assert bool(trigger(prices[i],ind['dc_high_3m'],ind['dc_low_3m'],ind['k_3m'],ind['k_3m_prev'],ha,is_long)) == fire
        scores=[_ind_score(ind,tf,is_long,prices[i])[0] for tf in ('3m','15m','1h','4h','D')]
        expected.append(fire and (not gate or (sum(scores)>=20 and sum(s>=5 for s in scores)>=2)))
    np.testing.assert_array_equal(entry_mask(data,n,is_long,cfg,prices),expected)

def test_actual_engine_switch_changes_open_bar():
    import v12_quick_engine as engine
    from tests.test_quick_srs_paired_15m import _cfg, _npz
    data=_npz(); n=len(data['timestamps'])
    data['dc_high_15m']=np.full(n,101.)
    data['dc_high_15m'][2]=99.
    data['k_15m']=np.full(n,60.)
    data['k_15m_prev']=np.full(n,50.)
    cfg=_cfg()
    for key,value in {'TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED':True,'WT_3M_FORCE_OPEN_GR_GATE_ENABLED':False,'ABLATION_DISABLE_QUICK_ENTRY':True,'QUICK_ENTRY_ABLATION_SUPPRESS_VEC':True,'STRUCTURAL_RANGE_SHIFT_EXIT':False,'OPTIMAL_HOLD_BARS_EXIT_ENABLED':False}.items():
        setattr(cfg,key,value)
    from vec_decisions.generic_filter_tf import FILTER_TF_MAP
    for name, (target, _) in FILTER_TF_MAP.items():
        if target == "entry":
            setattr(cfg,name,"OFF")
    result=engine.simulate_one(data,'MANDATORY_TEST',True,cfg)
    opens=[row for row in result['ledger'] if row.get('type')=='OPEN']
    assert opens and opens[0]['bar']==2
    assert 'TRADEABLE_KEYS_MANDATORY' in opens[0].get('reason','')
    cfg.TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED=False
    disabled=engine.simulate_one(data,'MANDATORY_TEST',True,cfg)
    assert not [row for row in disabled['ledger'] if row.get('type')=='OPEN']
