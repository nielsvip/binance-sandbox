import sys
sys.path.insert(0, '/home/niels/binance-sandbox')
from v8_quick_engine import QuickConfig, iter_npz, simulate
cfg = QuickConfig()
cfg.apply_tradier_defaults()
cfg.NOLOSS_ENABLED = False
cfg.PROFIT_TARGET_PCT = 0.5
cfg.MIN_HOLD_BARS = 80
cfg.CT_WT_VELOCITY_GATE_ENABLED = True
cfg.CT_WT_VELOCITY_1H_MIN = 2.0
cfg.WT_EXIT_MIN_TFS = 4
cfg.STRUCTURAL_RANGE_SHIFT_EXIT = True
cfg.EARLY_ABORT_MIN_SYMBOLS = 999999  # no abort
cfg.EARLY_ABORT_SHARPE_FLOOR = 0.0
stores = iter_npz("tradier", None, "2022-01-01")
r = simulate(stores, cfg, 10000.0)
print("CHAMPION FULL VALIDATION:")
print("sharpe="+str(round(r["sharpe"],4))+" pool="+str(round(r.get("pool_sharpe",0),4)))
print("trades="+str(r["trades"])+" wr="+str(round(r["win_rate"],3))+" pnl="+str(round(r["total_pnl"],0)))
print("syms_with_sharpe="+str(r.get("syms_with_sharpe",0))+" syms_excluded="+str(r.get("syms_excluded",0)))
