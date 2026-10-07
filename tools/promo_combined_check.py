import sys, json, os
os.chdir(os.environ.get("PROMO_OVERLAY", "/tmp/p3_sb")); sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), "tools", "opt"))
import evaluate_v12 as E
cases = json.load(open(os.environ.get("PROMO_CASES", "/tmp/p3_cases_by_cs.json")))
out = {}
for ss in sys.argv[1].split(","):
    sym, side = ss.rsplit("_", 1)
    cs = ("CRYPTO_" if sym.endswith(("USDT", "USDC")) else "STOCKS_") + side
    p = E.prepare(ss, 30)
    if p is None:
        out[ss] = "NO_PREPARE"; continue
    res = {}
    for name, ov in (("base", {}), ("promoted", cases[cs])):
        r = E.evaluate_prepared(p, dict(ov))
        res[name] = [r.get("gain_pct"), r.get("trades") or r.get("n_trades"), r.get("valid")]
    out[ss] = res
print("RES" + json.dumps(out))
