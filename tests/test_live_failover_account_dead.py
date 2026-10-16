import sys
sys.path.insert(0, "/Users/niels/Documents/binance/tools")
import live_failover_common as C

T0 = 1_000_000.0
TH = C.TradingHealth("/nonexistent-logdir")

def set_views(t, mgr_age=5.0):
    """Pin every follower to a healthy, fresh view at time t so only `alive` and mgr_age vary."""
    for acct, f in TH.crypto.items():
        for name, fol in f.items():
            fol.poll = lambda now, _f=fol: _f
            age = mgr_age if name == "mgr" else 5.0
            fol.last_any = t - age
            fol.first_seen = t - 3600
            fol.last = {} if name == "mgr" else {"ok": t - 5.0}
            fol.events = []

def run(alive, label, t, mgr_age=5.0, fenced=()):
    set_views(t, mgr_age)
    r = TH.measure(now=t, procs={"crypto": alive, "stocks": {}}, fenced=fenced)["crypto"]
    print(f"{label:44s} kinds={r['kinds']!s:40s} inf_dead_s={r['accounts']['inf']['engine_dead_s']}")
    return r

ALL = {a: 1 for a in C.CRYPTO_ACCOUNTS}
ONE = dict(ALL, inf=0)
TWO = dict(ALL, inf=0, flz=0)

r = run(ALL, "all 5 alive, all logging", T0);                      assert r["kinds"] == [], r["kinds"]
r = run(ONE, "inf engine killed, t+0", T0);                        assert r["kinds"] == [], r["kinds"]
r = run(ONE, "inf dead 239s (under bar)", T0 + 239);               assert r["kinds"] == [], r["kinds"]
r = run(ONE, "inf dead 241s -> MUST FIRE", T0 + 241);              assert r["kinds"] == ["account_dead"], r["kinds"]
assert r["account_dead_accounts"] == ["inf"], r["account_dead_accounts"]
r = run(TWO, "inf+flz dead: flz timer starts now", T0 + 242);      assert r["account_dead_accounts"] == ["inf"], r["account_dead_accounts"]
r = run(TWO, "inf+flz dead 485s -> both named", T0 + 485);         assert r["account_dead_accounts"] == ["flz", "inf"], r["account_dead_accounts"]
r = run(ALL, "all back alive -> MUST CLEAR", T0 + 486);            assert r["kinds"] == [], r["kinds"]
r = run(ONE, "inf dead again, timer restarted", T0 + 487);         assert r["kinds"] == [], r["kinds"]
r = run({a: 0 for a in C.CRYPTO_ACCOUNTS}, "all 5 dead -> traders_dead, not account", T0 + 600)
assert r["kinds"] == ["traders_dead"], r["kinds"]
r = run(ONE, "fenced + inf dead 300s -> silent", T0 + 1200, fenced=("crypto",))
assert r["kinds"] == [], r["kinds"]
# WEDGED engine: process alive, log silent
TH._engine_dead_since.clear()
r = run(ALL, "all alive but ALL logs silent 200s, t+0", T0 + 2000, mgr_age=200.0); assert r["kinds"] == [], r["kinds"]
r = run(ALL, "wedged 241s -> MUST FIRE for all 5", T0 + 2241, mgr_age=200.0)
assert r["kinds"] == ["account_dead"] and len(r["account_dead_accounts"]) == 5, (r["kinds"], r["account_dead_accounts"])
r = run(ALL, "logs fresh again -> MUST CLEAR", T0 + 2242, mgr_age=5.0); assert r["kinds"] == [], r["kinds"]
print("\nALL 13 ASSERTIONS PASSED")
print("S1 NT_NEED['account_dead'] =", __import__("importlib").import_module("live_failover_s1").NT_NEED["account_dead"])
