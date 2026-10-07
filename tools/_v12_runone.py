import sys, os, json, logging
logging.disable(logging.WARNING)          # silence per-bar INFO/WARNING (huge speedup)
os.environ["PYTHONWARNINGS"] = "ignore"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _n in list(logging.root.manager.loggerDict):
    try: logging.getLogger(_n).setLevel(logging.ERROR)
    except Exception: pass
import backtest_v12_engine as B
ss = sys.argv[1]; ov = json.load(open(sys.argv[2]))
try:
    r = B.run_one(ss, ov, window_days=int(sys.argv[3]) if len(sys.argv) > 3 else 30)
    print("V12RESULT " + json.dumps({k: r.get(k) for k in ("valid", "gain_pct", "trades", "pool_sharpe", "invalid_reason")}))
except Exception as e:
    print("V12RESULT " + json.dumps({"valid": False, "invalid_reason": f"exc {type(e).__name__} {str(e)[:150]}"}))
