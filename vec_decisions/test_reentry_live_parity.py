"""Permanent regression: the SHARED reentry predicate reproduces the LIVE fire decision
EXACTLY for both live paths, and the LIVE confirmation gate delegates to the shared one.

Locks the 2026-06-25 single-source wiring (ez_reentry + ez_reentry_daemon + batch5 vec all
route through vec_decisions.guaranteed_price_cross_reentry):

  • INLINE (ez_reentry.enforce_price_cross_reentry): cross + divergence + confirmation,
    NO DC-breakout, NO in-function churn  → shared(dc_break=False, churn=False, divergence=True, ha_force=True)
  • DAEMON (ez_reentry_daemon._evaluate_and_queue): cross + REENTRY2 DC + churn + confirmation
    (DC bypasses confirm), NO divergence    → shared(dc_break=True, churn=True, divergence=False, ha_force=False)

Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_reentry_live_parity.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as CFG
from ez_reentry import check_reentry_confirmation
from vec_decisions.guaranteed_price_cross_reentry import (
    check_guaranteed_price_cross_reentry as SHARED,
    reentry_confirmation_gate,
)

random.seed(99)
WT = lambda: round(random.choice([0.0, 0.0] + [random.uniform(-90, 90)]), 3)
K = lambda: round(random.choice([0.0, 0.0] + [random.uniform(0, 100)]), 2)
MAXDIV = float(getattr(CFG, "REENTRY_MAX_PRICE_DIVERGENCE_PCT", 20.0)) / 100.0


def _sf(v, d=0.0):
    try: return float(v) if v is not None else d
    except Exception: return d


def rand_ind(cur):
    near = lambda: round(cur * random.uniform(0.97, 1.03), 4)
    z = lambda p=0.3: 0.0 if random.random() < p else near()
    return {
        "wt1_3m": WT(), "wt2_3m": WT(), "wt1_5m": WT(), "wt2_5m": WT(),
        "wt1_15m": WT(), "wt2_15m": WT(), "wt1_1h": WT(), "wt2_1h": WT(),
        "stoch_k_3m": K(), "stoch_d_3m": K(), "stoch_k_3m_prev": K(),
        "stoch_k_15m": K(), "stoch_k_15m_prev": K(), "stoch_k_1h": K(),
        "ha_4h": random.choice(["green", "red", "neutral"]),
        "dc_basis_4h": z(), "basis_4h": z(),
        "dc_high_3m": z(), "dc_low_3m": z(), "dc_high_1h": z(), "dc_low_1h": z(),
        "dc_high_15m": z(), "dc_low_15m": z(), "dc_high4_3m": z(0.4), "dc_low4_3m": z(0.4),
        "sma_200_15m": z(),
    }


def live_inline(ind, is_long, cur, ex, is_leash):
    div = abs(cur - ex) / ex
    fav = (is_long and cur > ex) or ((not is_long) and cur < ex)
    if div > MAXDIV and not fav:
        return False
    crossed = (is_long and cur > ex) or ((not is_long) and cur < ex)
    if not crossed:
        return False
    return bool(check_reentry_confirmation(ind, is_long, CFG, cur, is_leash_re=is_leash)[0])


def live_daemon(ind, is_long, cur, ex, exit_ts, now):
    crossed = (is_long and cur > ex) or ((not is_long) and cur < ex)
    is_dc = False
    if not crossed:
        buf = 0.001
        fk = _sf(ind.get("stoch_k_3m", 0)); fd = _sf(ind.get("stoch_d_3m", 0))
        kdata = abs(fk) > 1e-9 or abs(fd) > 1e-9
        dh3 = _sf(ind.get("dc_high_3m", 0)); dl3 = _sf(ind.get("dc_low_3m", 0))
        dh1h = _sf(ind.get("dc_high_1h", 0)); dl1h = _sf(ind.get("dc_low_1h", 0))
        dh15 = _sf(ind.get("dc_high_15m", 0)); dl15 = _sf(ind.get("dc_low_15m", 0))
        if is_long:
            d3 = dh3 > 0 and cur > dh3 * (1 + buf)
            d1h15 = (dh1h > 0 and cur > dh1h * (1 + buf)) or (dh15 > 0 and cur > dh15 * (1 + buf))
            if d3 or (d1h15 and ((fk > fd) if kdata else True)):
                is_dc = True
        else:
            d3 = dl3 > 0 and cur < dl3 * (1 - buf)
            d1h15 = (dl1h > 0 and cur < dl1h * (1 - buf)) or (dl15 > 0 and cur < dl15 * (1 - buf))
            if d3 or (d1h15 and ((fk < fd) if kdata else True)):
                is_dc = True
    if not crossed and not is_dc:
        return False
    if exit_ts > 0 and (now - exit_ts) < 3600.0:
        key = "dc_high4_3m" if is_long else "dc_low4_3m"
        try: lvl = float(ind.get(key, 0) or 0)
        except Exception: lvl = 0.0
        if lvl > 0:
            cg_ok = (cur > lvl * 1.001) if is_long else (cur < lvl * 0.999)
            if not cg_ok:
                return False
    if not is_dc:
        if not bool(check_reentry_confirmation(ind, is_long)[0]):
            return False
    return True


def main():
    N = 60000
    mb = mc = mg = 0
    for _ in range(N):
        is_long = random.random() < 0.5
        ex = round(random.uniform(10, 200), 4)
        m = random.random()
        if m < 0.4:
            cur = ex * (1 + random.uniform(0.0001, 0.03)) if is_long else ex * (1 - random.uniform(0.0001, 0.03))
        elif m < 0.6:
            cur = ex * (1 - random.uniform(0.0001, 0.05)) if is_long else ex * (1 + random.uniform(0.0001, 0.05))
        elif m < 0.8:
            cur = ex * (1 + random.uniform(0.05, 0.40)) if is_long else ex * (1 - random.uniform(0.05, 0.40))
        else:
            cur = ex * (1 - random.uniform(0.05, 0.40)) if is_long else ex * (1 + random.uniform(0.05, 0.40))
        cur = round(cur, 4)
        ind = rand_ind(cur)
        is_leash = random.random() < 0.3
        # gate delegation: live gate == shared gate (identical by construction)
        if check_reentry_confirmation(ind, is_long, CFG, cur, is_leash_re=is_leash) != reentry_confirmation_gate(ind, is_long, CFG, cur, is_leash):
            mg += 1
        # inline
        sb, _ = SHARED(CFG, ind, cur, ex, is_long, elapsed_s=1e18, is_leash_re=is_leash,
                       dc_break_override=False, churn_override=False, divergence_guard=True, ha_force_enabled=True)
        if live_inline(ind, is_long, cur, ex, is_leash) != bool(sb):
            mb += 1
        # daemon
        now = 1_000_000.0
        exit_ts = now - random.choice([0, 100, 1000, 5000, 99999])
        sc, _ = SHARED(CFG, ind, cur, ex, is_long, elapsed_s=(now - exit_ts), is_leash_re=False,
                       dc_break_override=True, churn_override=True, divergence_guard=False, ha_force_enabled=False)
        if live_daemon(ind, is_long, cur, ex, exit_ts, now) != bool(sc):
            mc += 1
    print(f"gate-delegation mismatches: {mg}")
    print(f"INLINE  shared-vs-live mismatches over {N}: {mb}")
    print(f"DAEMON  shared-vs-live mismatches over {N}: {mc}")
    if mg == mb == mc == 0:
        print("REENTRY LIVE PARITY OK")
        sys.exit(0)
    print("REENTRY LIVE PARITY FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
