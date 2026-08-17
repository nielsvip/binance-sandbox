from tools.run_amat_doubletop_reclaim_probe import reclaim_short
def test_resting_short_no_touch_later_gap_and_cost_helper():
 assert reclaim_short(100,105,106,102,0)==None
 assert reclaim_short(100,95,97,94,0)==95
 assert reclaim_short(100,101,102,99,10)<100
