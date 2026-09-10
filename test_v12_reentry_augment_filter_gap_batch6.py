import csv,json
from pathlib import Path
import numpy as np

from vec_paths.v12_reentry_augment_filter_gap_batch6 import FIELD_CONTRACTS,LIFECYCLE_FILTER_APIS,golden_rule_decision

ROOT=Path(__file__).resolve().parent

def _default(raw,typ):
    if typ=='bool':return raw.lower()=='true'
    if typ=='int':return int(raw)
    if typ=='float':return float(raw)
    if typ=='List[str]':return [] if not raw else json.loads(raw)
    return raw

def test_20_field_contract_matches_authority_and_entry_augment_consumers():
    rows={r['field']:r for r in csv.DictReader((ROOT/'data/reports/V12_REENTRY_AUGMENT_FILTER_GAP_BATCH6_CONTRACT.csv').open())};auth={r['field']:r for r in csv.DictReader((ROOT/'data/reports/V12_MISSING_FIELD_DEFINITION_MAP.csv').open())}
    assert len(rows)==len(FIELD_CONTRACTS)==20
    for name,c in FIELD_CONTRACTS.items():
        assert rows[name]['authoritative_type']==auth[name]['authoritative_type']==c.value_type
        assert json.loads(rows[name]['authoritative_default_json'])==_default(auth[name]['authoritative_default'],c.value_type)==c.default
        assert tuple(json.loads(rows[name]['authoritative_grid_json']))==tuple(json.loads(auth[name]['authoritative_grid']))==c.grid
        assert set(c.lifecycle_filter_consumers)==set(LIFECYCLE_FILTER_APIS[name])=={'ENTRY','AUGMENT'}
        assert set(LIFECYCLE_FILTER_APIS[name].values())=={'golden_rule_decision'}

def _fixture(mode='crypto'):
    n=5;native='3m' if mode=='crypto' else '5m';a={'close':np.full(n,101.),f'wt1_{native}':np.ones(n),f'wt2_{native}':np.zeros(n)}
    highs={'15m':np.full(n,100.),'1h':np.array([200,100,200,200,200.]),'4h':np.array([200,200,100,200,200.]),'D':np.array([200,200,200,100,200.]),'W':np.array([200,200,200,200,100.])}
    for tf in ('15m','1h','4h','D','W'):
        a[f'dc_high_{tf}']=highs[tf];a[f'dc_low_{tf}']=np.full(n,50.);a[f'bb_upper_{tf}']=np.full(n,200.);a[f'bb_lower_{tf}']=np.full(n,40.)
    state={'candidate_mask':np.ones(n,bool),'cooldown_ready':np.ones(n,bool),'current_notional':np.zeros(n)}
    return a,state

def _vote_arrays(mode='crypto',bull=True,n=5):
    tfs=('3m','15m','1h','4h','D') if mode=='crypto' else ('5m','15m','1h','4h','D','W');a={}
    for tf in tfs:
        a[f'wt1_{tf}']=np.full(n,1. if bull else -1.);a[f'wt2_{tf}']=np.zeros(n);a[f'rsi_{tf}']=np.full(n,60 if bull else 40.);a[f'mfi_{tf}']=np.full(n,60 if bull else 40.);a[f'dc_position_{tf}']=np.full(n,.8 if bull else .2);a[f'bb_pct_b_{tf}']=np.full(n,.8 if bull else .2);a[f'relative_volume_{tf}']=np.full(n,2.);a[f'stoch_k_{tf}']=np.full(n,60 if bull else 40.);a[f'dc_high_{tf}']=np.full(n,100.);a[f'dc_low_{tf}']=np.full(n,50.);a[f'bb_upper_{tf}']=np.full(n,100.);a[f'bb_lower_{tf}']=np.full(n,50.);a[f'close_{tf}']=np.full(n,101.)
    return a

def test_master_disabled_is_available_but_enabled_missing_native_data_is_na():
    state={'candidate_mask':np.ones(2,bool),'cooldown_ready':np.ones(2,bool),'current_notional':np.zeros(2)}
    assert golden_rule_decision({},state,True,'crypto',{'GOLDEN_RULE_ENABLED':False}).available
    result=golden_rule_decision({},state,True,'crypto',{})
    assert not result.available and not result.mask.any() and 'wt1_3m' in result.reason

def test_breakout_cascade_uses_highest_real_tf_multiplier_and_target():
    arrays,state=_fixture();cfg={'GOLDEN_RULE_HTF_MIN_TFS':0}
    result=golden_rule_decision(arrays,state,True,'crypto',cfg)
    assert result.available and result.mask.all()
    assert result.multiplier.tolist()==[1,1.5,2,3,4]
    assert result.target_usd.tolist()==[5,7.5,10,15,20]

def test_dc_and_bb_switches_change_same_tf_breakout_mask_without_reason_proxy():
    arrays,state=_fixture();arrays['dc_high_15m'][:]=100;arrays['bb_upper_15m'][:]=100
    base={'GOLDEN_RULE_HTF_MIN_TFS':0,'GOLDEN_RULE_DC_1H_ENABLED':False,'GOLDEN_RULE_DC_4H_ENABLED':False,'GOLDEN_RULE_DC_D_ENABLED':False,'GOLDEN_RULE_DC_W_ENABLED':False,'GOLDEN_RULE_BB_1H_ENABLED':False,'GOLDEN_RULE_BB_4H_ENABLED':False,'GOLDEN_RULE_BB_D_ENABLED':False,'GOLDEN_RULE_BB_W_ENABLED':False}
    assert golden_rule_decision(arrays,state,True,'crypto',{**base,'GOLDEN_RULE_DC_15M_ENABLED':True,'GOLDEN_RULE_BB_15M_ENABLED':False}).mask.all()
    assert golden_rule_decision(arrays,state,True,'crypto',{**base,'GOLDEN_RULE_DC_15M_ENABLED':False,'GOLDEN_RULE_BB_15M_ENABLED':True}).mask.all()
    assert not golden_rule_decision(arrays,state,True,'crypto',{**base,'GOLDEN_RULE_DC_15M_ENABLED':False,'GOLDEN_RULE_BB_15M_ENABLED':False}).mask.any()

def test_activation_list_and_cooldown_notional_are_exact_lifecycle_gates():
    arrays,state=_fixture();cfg={'GOLDEN_RULE_HTF_MIN_TFS':0,'GOLDEN_RULE_REQUIRE_ACTIVATION':True,'GOLDEN_RULE_ACTIVATION_TF_LIST':['4h']}
    result=golden_rule_decision(arrays,state,True,'crypto',cfg)
    assert result.mask.tolist()==[False,False,True,False,False]
    state['cooldown_ready'][2]=False
    assert not golden_rule_decision(arrays,state,True,'crypto',cfg).mask.any()
    state['cooldown_ready'][2]=True;state['current_notional'][2]=8.1
    assert not golden_rule_decision(arrays,state,True,'crypto',cfg).mask.any()

def test_vote_path_uses_exact_htf_arrays_when_breakout_path_is_disabled():
    arrays,state=_fixture();arrays.update(_vote_arrays())
    cfg={'GR_TOTAL_VOTE_SCORE_MIN':20,'GOLDEN_RULE_HTF_MIN_TFS':0}
    for tf in ('15M','1H','4H','D','W'):
        cfg[f'GOLDEN_RULE_DC_{tf}_ENABLED']=False;cfg[f'GOLDEN_RULE_BB_{tf}_ENABLED']=False
    result=golden_rule_decision(arrays,state,True,'crypto',cfg)
    assert result.available and result.mask.all()
    assert not golden_rule_decision(arrays,state,True,'crypto',{**cfg,'GR_TOTAL_VOTE_SCORE_MIN':36}).mask.any()

def test_short_and_tradier_native_5m_route_do_not_use_crypto_3m_proxy():
    arrays,state=_fixture('tradier');arrays.update(_vote_arrays('tradier',False));arrays['close'][:]=49;arrays['wt1_5m'][:]=-1
    for tf in ('15m','1h','4h','D','W'):
        arrays[f'dc_low_{tf}'][:]=50
    result=golden_rule_decision(arrays,state,False,'tradier',{'GOLDEN_RULE_HTF_MIN_TFS':0})
    assert result.available and result.mask.all()
    del arrays['wt1_5m'];arrays['wt1_3m']=np.full(5,-1.)
    assert not golden_rule_decision(arrays,state,False,'tradier',{'GOLDEN_RULE_HTF_MIN_TFS':0}).available

