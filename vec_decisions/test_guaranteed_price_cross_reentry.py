"""Random-input parity test: scalar core == vec mask for GUARANTEED_PRICE_CROSS_REENTRY.

Mirrors strategy_enhancements pyramid parity discipline. Asserts the scalar per-bar core
(_guaranteed_price_cross_reentry_fires, exercised via check_guaranteed_price_cross_reentry)
agrees bit-for-bit with the vectorized mask (check_guaranteed_price_cross_reentry_vec) over
>=10000 random samples, for both LONG and SHORT, across several config threshold sets.

Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_guaranteed_price_cross_reentry.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from vec_decisions.guaranteed_price_cross_reentry import (
    check_guaranteed_price_cross_reentry,
    check_guaranteed_price_cross_reentry_vec,
)


class _Cfg:
    def __init__(self, **kw):
        self.EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED = True
        self.EZ_REENTRY_PRICE_CROSS_PCT = 0.0
        self.REENTRY_MAX_PRICE_DIVERGENCE_PCT = 20.0
        self.REENTRY_CONFIRMATION_GATES_ENABLED = True
        self.REENTRY_STOCH_K_MAX_LONG = 85.0
        self.REENTRY_STOCH_K_MIN_SHORT = 15.0
        self.REENTRY_CHURN_GUARD_ENABLED = True
        self.REENTRY_CHURN_GUARD_WINDOW_S = 3600.0
        self.REENTRY_CHURN_GUARD_USE_4BAR = True
        self.REENTRY_SMA200_BACKUP_ENABLED = False
        for k, v in kw.items():
            setattr(self, k, v)


def _run(seed, n, is_long, cfg, is_leash=False):
    rng = np.random.default_rng(seed)
    exit_px = rng.uniform(0.5, 200.0, n)
    # mix favorable + adverse + huge-divergence crosses
    cur_px = exit_px * rng.uniform(0.4, 1.6, n)
    # churn/sma/DC-breakout inputs (2026-06-02 new branches): mix elapsed in/out of window;
    # DC levels near price w/ some zeros. 4-bar = churn levels; 1-bar + 1h/15m = DC-breakout.
    elapsed = rng.uniform(0.0, 7200.0, n)
    sma200 = np.where(rng.random(n) < 0.6, cur_px * rng.uniform(0.9, 1.1, n), 0.0)
    def _lvl():
        return np.where(rng.random(n) < 0.6, cur_px * rng.uniform(0.95, 1.05, n), 0.0)
    dc_h4_3 = _lvl(); dc_l4_3 = _lvl()
    dc_h3 = _lvl(); dc_l3 = _lvl()
    dc_h1h = _lvl(); dc_l1h = _lvl()
    dc_h15 = _lvl(); dc_l15 = _lvl()
    fk = rng.uniform(0, 100, n); fd = rng.uniform(0, 100, n)
    _use4 = bool(getattr(cfg, "REENTRY_CHURN_GUARD_USE_4BAR", True))
    dc_churn = (dc_h4_3 if _use4 else dc_h3) if is_long else (dc_l4_3 if _use4 else dc_l3)
    w1_3 = rng.uniform(-80, 80, n)
    w2_3 = rng.uniform(-80, 80, n)
    w1_15 = rng.uniform(-80, 80, n)
    w2_15 = rng.uniform(-80, 80, n)
    w1_1h = rng.uniform(-80, 80, n)
    w2_1h = rng.uniform(-80, 80, n)
    k15 = rng.uniform(0, 100, n)
    kp15 = rng.uniform(0, 100, n)
    k1h = rng.uniform(0, 100, n)
    ha_choice = rng.integers(0, 3, n)
    ha_g = ha_choice == 0
    ha_r = ha_choice == 1
    basis = np.where(rng.random(n) < 0.5, rng.uniform(0.5, 200.0, n), 0.0)
    _dc_en = bool(getattr(cfg, "REENTRY2_DC_BREAK_ENABLED", True))
    vec = check_guaranteed_price_cross_reentry_vec(
        cfg, cur_px, exit_px, is_long,
        w1_3, w2_3, w1_15, w2_15, w1_1h, w2_1h,
        k15, kp15, k1h, ha_g, ha_r, basis,
        elapsed_s_arr=elapsed, dc_churn_lvl_arr=dc_churn,
        sma_200_15m_arr=sma200, is_leash_re=is_leash,
        dc_high_3m_arr=dc_h3, dc_low_3m_arr=dc_l3, dc_high_1h_arr=dc_h1h, dc_low_1h_arr=dc_l1h,
        dc_high_15m_arr=dc_h15, dc_low_15m_arr=dc_l15, dcf_k_arr=fk, dcf_d_arr=fd,
        dc_break_enabled=_dc_en,
        dc_allow_15m=bool(getattr(cfg, "REENTRY2_DC_BREAK_ALLOW_15M", True)),
        dc_req_k=bool(getattr(cfg, "REENTRY2_DC_BREAK_REQUIRE_K_FILTER", True)),
        dc_req_wt=bool(getattr(cfg, "REENTRY2_DC_BREAK_REQUIRE_WT_FILTER", False)),
    )
    mismatches = 0
    first = []
    for i in range(n):
        ha_str = "green" if ha_g[i] else ("red" if ha_r[i] else "neutral")
        ind = {
            "wt1_3m": w1_3[i], "wt2_3m": w2_3[i],
            "wt1_15m": w1_15[i], "wt2_15m": w2_15[i],
            "wt1_1h": w1_1h[i], "wt2_1h": w2_1h[i],
            "stoch_k_15m": k15[i], "stoch_k_15m_prev": kp15[i],
            "stoch_k_1h": k1h[i], "ha_4h": ha_str,
            "stoch_k_3m": fk[i], "stoch_d_3m": fd[i],
            "dc_basis_4h": basis[i], "basis_4h": 0.0,
            "dc_high4_3m": dc_churn[i], "dc_low4_3m": dc_churn[i],
            "dc_high_3m": dc_h3[i], "dc_low_3m": dc_l3[i],
            "dc_high_1h": dc_h1h[i], "dc_low_1h": dc_l1h[i],
            "dc_high_15m": dc_h15[i], "dc_low_15m": dc_l15[i],
            "sma_200_15m": sma200[i],
        }
        scal, _ = check_guaranteed_price_cross_reentry(cfg, ind, cur_px[i], exit_px[i], is_long, elapsed_s=elapsed[i], is_leash_re=is_leash)
        if bool(scal) != bool(vec[i]):
            mismatches += 1
            if len(first) < 5:
                first.append((i, bool(scal), bool(vec[i]), cur_px[i], exit_px[i]))
    return mismatches, first


def main():
    total = 0
    configs = [
        ("default", _Cfg(), False),
        ("cross_pct_0.002", _Cfg(EZ_REENTRY_PRICE_CROSS_PCT=0.002), False),
        ("conf_off", _Cfg(REENTRY_CONFIRMATION_GATES_ENABLED=False), False),
        ("tight_k", _Cfg(REENTRY_STOCH_K_MAX_LONG=70.0, REENTRY_STOCH_K_MIN_SHORT=30.0), False),
        ("small_div", _Cfg(REENTRY_MAX_PRICE_DIVERGENCE_PCT=5.0), False),
        ("disabled", _Cfg(EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED=False), False),
        ("churn_off", _Cfg(REENTRY_CHURN_GUARD_ENABLED=False), False),
        ("churn_1bar", _Cfg(REENTRY_CHURN_GUARD_USE_4BAR=False), False),
        ("sma_backup_on", _Cfg(REENTRY_SMA200_BACKUP_ENABLED=True), False),
        ("leash", _Cfg(), True),
        ("leash+sma+churn", _Cfg(REENTRY_SMA200_BACKUP_ENABLED=True), True),
    ]
    seed = 0
    for name, cfg, is_leash in configs:
        for is_long in (True, False):
            seed += 1
            mm, first = _run(seed, 10000, is_long, cfg, is_leash=is_leash)
            total += mm
            side = "LONG" if is_long else "SHORT"
            print(f"[{name:18s} {side}] samples=10000 mismatches={mm}")
            for f in first:
                print(f"    MISMATCH idx={f[0]} scalar={f[1]} vec={f[2]} cur={f[3]:.4f} exit={f[4]:.4f}")
    print(f"\nTOTAL mismatches across all configs: {total}")
    if total == 0:
        print("PARITY OK: scalar core == vec mask")
        sys.exit(0)
    print("PARITY FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
