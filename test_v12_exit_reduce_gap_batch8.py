from types import SimpleNamespace
import numpy as np
from vec_paths.v12_exit_reduce_gap_batch8 import consume_lifecycle_actions,evaluate_gap_batch8

def _data(n=4):
 return {"wt1_15m":np.zeros(n),"wt2_15m":np.ones(n),"wt_cross_bars_ago_15m":np.array([0,5,6,0.]),"wt_cross_15m":np.array(["BEAR","BEAR","BEAR","BULL"]),"close_3m_prev":np.full(n,100.),"low_3m":np.full(n,99.),"low_3m_prev":np.full(n,100.),"stoch_k_3m":np.full(n,50.),"stoch_d_3m":np.full(n,40.)}
def _cfg(**u):
 v=dict(HEDGE_MAX_PCT_OF_LOSER=1.,HEDGE_MAX_ABSOLUTE_USD=100000.,HEDGE_SAME_SYMBOL_PCT=1.,OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED=True,WT_15M_SAME_HEDGE_DAILY_CAP=2,WT_15M_SAME_HEDGE_COOLDOWN_SEC=1800,HEDGE_COMPLETED_LOCKOUT_SECONDS=60,QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED=True,ENABLE_FAST_RISER_REDUCE=False,MOMENTUM_RIDER_HEDGE_RATIO=1.2,OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED=False);v.update(u);return SimpleNamespace(**v)
def _run(cfg=None,data=None,side="LONG",**s):
 d=dict(gain_pct=np.ones(4),position_age_seconds=np.full(4,300.),current_price=np.full(4,100.));d.update(s);return evaluate_gap_batch8(data or _data(),cfg or _cfg(),position_side=side,**d)

def test_hedge_percent_and_absolute_caps_are_separate_exact_payloads():
 r=_run(origin_position_value_usd=np.array([100,100,100,0]),requested_hedge_qty=np.array([2,.5,1,1]),requested_hedge_notional_usd=np.array([200,50,200,20]))
 assert r.values["hedge_size_cap_qty"].tolist()==[1,.5,1,1]
 assert r.values["hedge_hard_cap_notional_usd"].tolist()==[100,50,100,0]

def test_same_symbol_pct_and_capacity_mask_change_values():
 r=_run(_cfg(HEDGE_SAME_SYMBOL_PCT=.5,HEDGE_MAX_PCT_OF_LOSER=.5,HEDGE_MAX_ABSOLUTE_USD=40),position_qty=2,origin_position_value_usd=100,existing_hedge_notional_usd=np.array([0,39,40,41]))
 assert r.values["same_symbol_scan_qty"].tolist()==[1]*4
 assert r.masks["hedge_capacity_available"].tolist()==[True,True,False,False]

def test_opposite_position_binary_filter_and_bypass():
 r=_run(close_or_reduce_requested=True,opposite_position_qty=np.array([0,.1,.1,.1]),close_reason_bypass=np.array([0,0,1,0]))
 assert r.masks["opposite_loser_close_block"].tolist()==[False,True,False,True]

def test_wt15_fresh_cross_daily_cap_and_cooldown():
 r=_run(hedge_daily_count=np.array([0,2,0,0]),elapsed_since_hedge_seconds=np.array([1800,1800,1800,1800]))
 assert r.masks["wt15_same_hedge_open"].tolist()==[True,False,False,False]
 cd=_run(elapsed_since_hedge_seconds=1799)
 assert not cd.masks["wt15_same_hedge_open"].any()

def test_completed_lock_and_last_resort_enable_gate():
 r=_run(_cfg(QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED=False),completed_lock_age_seconds=np.array([59,60,61,100]),is_last_resort=np.array([0,0,1,1]))
 assert r.masks["hedge_completed_lock_block"].tolist()==[True,False,False,False]
 assert r.masks["quick_same_symbol_last_resort_block"].tolist()==[False,False,True,True]

def test_fast_riser_toggle_is_augment_not_reduce():
 cfg=_cfg(ENABLE_FAST_RISER_REDUCE=True,FAST_RISER_DOUBLE_ENABLED=True,ABLATION_DISABLE_FAST_RISER=False,NEW_POSITION_MIN_AGE_SECONDS=180,NEW_POSITION_MAX_LOSS_THRESHOLD=-.7)
 r=_run(cfg,current_price=100.3,position_qty=1,position_min_qty=.1,start_position_size_usd=10,max_position_size_usd=1000,ha_3m="green",gain_pct=1)
 assert r.masks["fast_riser_augment"].all()
 c=consume_lifecycle_actions(r);assert c.masks["AUGMENT"].all() and not c.masks["REDUCE"].any()

def test_fast_riser_newborn_and_missing_native_data_fail_closed():
 cfg=_cfg(ENABLE_FAST_RISER_REDUCE=True,FAST_RISER_DOUBLE_ENABLED=True,ABLATION_DISABLE_FAST_RISER=False,NEW_POSITION_MIN_AGE_SECONDS=180,NEW_POSITION_MAX_LOSS_THRESHOLD=-.7)
 assert not _run(cfg,current_price=100.3,position_qty=1,position_min_qty=.1,start_position_size_usd=10,max_position_size_usd=1000,ha_3m="green",gain_pct=1,position_age_seconds=10).masks["fast_riser_augment"].any()
 d=_data();d.pop("low_3m")
 r=_run(cfg,d,current_price=100.3);assert not r.masks["fast_riser_augment"].any() and "low_3m" in r.missing_arrays["fast_riser_augment"]

def test_momentum_ratio_and_obligatory_master_are_explicit():
 r=_run(_cfg(MOMENTUM_RIDER_HEDGE_RATIO=1.5,OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED=True),momentum_primary_qty=2,origin_position_value_usd=100)
 assert r.values["momentum_rider_hedge_qty"].tolist()==[3]*4
 assert r.masks["obligatory_hedge_loop_enabled"].all()

def test_missing_cross_direction_fails_hedge_path_closed():
 d=_data();d.pop("wt_cross_15m")
 r=_run(data=d);assert not r.masks["wt15_same_hedge_open"].any() and "wt_cross_15m" in r.missing_arrays["wt15_same_hedge"]
