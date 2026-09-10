from types import SimpleNamespace
import numpy as np
from vec_paths.v12_exit_reduce_gap_batch7 import consume_lifecycle_actions, evaluate_gap_batch7


def _npz(n=5):
    d={"wt1_15m":np.ones(n),"wt2_15m":np.zeros(n),"sma_200_D":np.full(n,90.)}
    for tf in ("D","4h","1h"): d[f"wt1_{tf}"]=np.ones(n); d[f"wt2_{tf}"]=np.zeros(n)
    return d


def _cfg(**u):
    v=dict(STALL_SUB_ENABLED=False,RATIO_PNL_WEIGHT_ENABLED=False,RATIO_MULTIPLIER=1,RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=True,RATIO_CLOSE_LOSING_OVERWEIGHT=False,RATIO_REBALANCE_MAX_CLOSES=3,COMMISSION_BUFFER_PCT=.1)
    v.update(u); return SimpleNamespace(**v)


def _run(cfg=None,npz=None,**s):
    n=5; defaults=dict(position_sides=np.array(["LONG","LONG","LONG","SHORT","SHORT"]),gain_pct=np.array([1,.2,-6,1,-8.]),position_age_minutes=np.array([10,300,400,500,600.]),position_qty=np.ones(n),is_hedge=np.zeros(n,bool),delta_bull_speed=np.zeros(n),delta_bear_speed=np.zeros(n),candidate_score=np.arange(10,5,-1),candidate_price=np.full(n,100.),long_value=70,short_value=30,elapsed_since_rebalance_seconds=99999)
    defaults.update(s); return evaluate_gap_batch7(npz or _npz(),cfg or _cfg(),**defaults)


def test_stall_filters_and_oldest_first_cap():
    r=_run(_cfg(STALL_SUB_ENABLED=True,STALL_AGE_MIN_MIN=180,STALL_GAIN_ABS_MAX=.5,STALL_DELTA_SPEED_MAX=1,STALL_MAX_CLOSES_PER_CYCLE=1),gain_pct=np.array([0,.2,.6,.1,.1]))
    assert r.masks["stall_sub_close"].tolist()==[False,False,False,False,True]


def test_ratio_multiplier_and_pnl_blend_targets():
    base=_run(_cfg(RATIO_MULTIPLIER=2,RATIO_PNL_WEIGHT_ENABLED=False),breadth_bias=.6,average_sentiment=0)
    assert np.isclose(base.values["ratio_target_long_pct"][0],62)
    pnl=_run(_cfg(RATIO_MULTIPLIER=2,RATIO_PNL_WEIGHT_ENABLED=True,RATIO_PNL_DELTA_THRESHOLD=3,RATIO_PNL_ACCELERATION=2.5,RATIO_PNL_WEIGHT=.5,RATIO_PNL_TARGET_LONG_MIN=10,RATIO_PNL_TARGET_LONG_MAX=90),breadth_bias=.6,pnl_long_avg=10,pnl_short_avg=0,pnl_long_count=1,pnl_short_count=1)
    assert pnl.values["ratio_target_long_pct"][0] > 62


def test_cooldown_priority_and_k_cross_override():
    c=_cfg(RATIO_PNL_WEIGHT_ENABLED=True,RATIO_PNL_DELTA_THRESHOLD=3,RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT=1200,RATIO_REBALANCE_COOLDOWN_EXTREME=3600)
    r=_run(c,pnl_long_avg=10,pnl_short_avg=0,pnl_long_count=1,pnl_short_count=1,ratio_extreme=True,elapsed_since_rebalance_seconds=1000)
    assert r.values["ratio_selected_cooldown_seconds"][0]==1200 and not r.masks["ratio_cycle_ready"].any()
    assert _run(c,k1h_crossed=True,elapsed_since_rebalance_seconds=0).masks["ratio_cycle_ready"].all()


def test_close_overweight_commission_and_weakest_wt_first():
    d=_npz(); d["wt1_15m"]=np.array([5,1,.5,9,9.])
    r=_run(_cfg(RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=True,RATIO_REBALANCE_MAX_CLOSES=2),d,gain_pct=np.array([1,.2,.09,2,2]))
    assert r.masks["ratio_close_overweight_reduce"].tolist()==[True,True,False,False,False]


def test_losing_overweight_guards_and_worst_first_cap():
    c=_cfg(RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=False,RATIO_CLOSE_LOSING_OVERWEIGHT=True,RATIO_CLOSE_LOSING_MIN_LOSS_PCT=-5,RATIO_CLOSE_LOSING_MIN_SKEW_PP=20,RATIO_CLOSE_LOSING_MIN_PNL_DELTA_PCT=10,RATIO_CLOSE_LOSING_MAX_PER_CYCLE=1,RATIO_CLOSE_LOSING_COOLDOWN_SECONDS=900,RATIO_REBALANCE_APPLY_HTF_GATE=False)
    r=_run(c,gain_pct=np.array([-6,-9,-7,1,1]),pnl_long_avg=-20,pnl_short_avg=0,pnl_long_count=1,pnl_short_count=1,elapsed_since_loser_close_seconds=901)
    assert r.masks["ratio_close_losing_overweight_reduce"].tolist()==[False,True,False,False,False]


def test_close_only_switch_suppresses_open_path():
    assert not _run(_cfg(RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=True)).masks["ratio_underweight_open_augment"].any()


def test_normal_open_htf_gate_and_size_grid():
    c=_cfg(RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=False,RATIO_REBALANCE_APPLY_HTF_GATE=True,HTF_GATE_SIGNALS_SMA200D=True,HTF_GATE_D_MANDATORY=True,HTF_GATE_MIN_CONFIRMATIONS=3,RATIO_REBALANCE_MAX_OPENS_NORMAL=8,RATIO_REBALANCE_SIZE_MULT=2,RATIO_REBALANCE_SIZE_SKEW_BOOST=.1,RATIO_REBALANCE_SIZE_MAX_MULT=5)
    d=_npz()
    for tf in ("D","4h","1h"): d[f"wt1_{tf}"][:]=-1
    d["sma_200_D"][:]=110
    r=_run(c,d,start_position_size=10)
    assert r.masks["ratio_underweight_open_augment"].tolist()==[False,False,False,True,True]
    assert r.values["ratio_rebalance_size_multiplier"][0]==4
    assert r.values["ratio_rebalance_notional"].tolist()==[0,0,0,40,40]


def test_missing_native_htf_disables_open_and_reports():
    d=_npz(); d.pop("wt1_D")
    r=_run(_cfg(RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=False,RATIO_REBALANCE_APPLY_HTF_GATE=True),d)
    assert not r.masks["ratio_underweight_open_augment"].any()
    assert "wt1_D" in r.missing_arrays["ratio_rebalance_htf_gate"]


def test_lifecycle_mapping_separates_portfolio_actions():
    r=_run(_cfg(STALL_SUB_ENABLED=True,STALL_AGE_MIN_MIN=1,STALL_GAIN_ABS_MAX=20,STALL_DELTA_SPEED_MAX=1,STALL_MAX_CLOSES_PER_CYCLE=1,RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=True))
    c=consume_lifecycle_actions(r)
    assert c.masks["EXIT"].sum()==1
    assert np.array_equal(c.masks["REDUCE"],r.masks["ratio_close_overweight_reduce"]|r.masks["ratio_close_losing_overweight_reduce"])
