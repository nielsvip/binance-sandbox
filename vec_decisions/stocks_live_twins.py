"""STOCKS_LIVE_TWINS (Agent C2, queue 007) — vector twins of two live STOCKS consumers, 15m+ data only (no 1m/3m/5m):
 1. StdevSizer — tradier_manage.py:10273-10345 (_update_stdev_state / detect_stdev_breakout_t) + 13515-13525. In live stocks STDEV_BREAKOUT is NOT an entry source: it is a
    SIZE BOOST (qty * STDEV_BREAKOUT_RETEST_SIZE_MULT, int()) on an entry that is already happening, while a breakout state is active (HTF bb %B beyond PCTB_LONG/SHORT with
    rvol>=RVOL_MIN and 15m volume ok; expires after MAX_AGE_BARS; retests on RETEST_TF_LIST with %B in [RETEST_PCTB_MIN,MAX], k>d (long) k<65, up to MAX_RETESTS).
    First entry of a breakout cycle = BREAKOUT phase (mult 1.0, marks retest_count=1). Approximation (same as N1/004 crypto twin): live advances bars_since once per call, twin once per 15m bar.
 2. dc_floor_confirms — tradier_matrix_gates.delta_dc_floor_confirms (live 19690): DELTA_EXIT is held unless price has broken the 15m Donchian boundary
    (LONG: price <= dc_low_15m, SHORT: price >= dc_high_15m); missing boundary fails open. The completed-bar channel is dc_*_15m_prev (the channel the live price is compared to).
"""
import numpy as np


def _arr(npz, key, n, default):
    if key in npz:
        a = np.asarray(npz[key], dtype=float)
        if len(a) >= n:
            return a[:n]
    return np.full(n, default, dtype=float)


def _lst(v, d):
    if v is None:
        return d
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)


class StdevSizer:
    def __init__(self, npz, n, is_long, cfg):
        htf = _lst(getattr(cfg, 'STDEV_BREAKOUT_HTF_LIST', None), ['D', '4h'])
        self.rtf = _lst(getattr(cfg, 'STDEV_BREAKOUT_RETEST_TF_LIST', None), ['1h', '15m'])
        self.is_long = is_long
        pl, ps = float(getattr(cfg, 'STDEV_BREAKOUT_PCTB_LONG', 1.125)), float(getattr(cfg, 'STDEV_BREAKOUT_PCTB_SHORT', -0.125))
        rmin = float(getattr(cfg, 'STDEV_BREAKOUT_RVOL_MIN', 1.2))
        maxa = int(float(getattr(cfg, 'STDEV_BREAKOUT_MAX_AGE_BARS', 50)))
        self.maxr = int(float(getattr(cfg, 'STDEV_BREAKOUT_MAX_RETESTS', 3)))
        self.rp_min, self.rp_max = float(getattr(cfg, 'STDEV_BREAKOUT_RETEST_PCTB_MIN', 0.85)), float(getattr(cfg, 'STDEV_BREAKOUT_RETEST_PCTB_MAX', 1.05))
        self.size_mult = float(getattr(cfg, 'STDEV_BREAKOUT_RETEST_SIZE_MULT', 1.5))
        vmult = float(getattr(cfg, 'BREAKOUT_15M_VOL_MULT', 1.2))
        rv15 = _arr(npz, 'relative_volume_15m', n, 0.0)
        self.vol_ok = np.where(rv15 > 0, rv15 >= vmult, True)
        self.P = {tf: _arr(npz, f'bb_pct_b_{tf}', n, 0.5) for tf in set(htf + self.rtf)}
        self.K = {tf: (_arr(npz, f'k_{tf}', n, 50.0), _arr(npz, f'd_{tf}', n, 50.0)) for tf in self.rtf}
        rv1 = _arr(npz, 'relative_volume_1h', n, 0.0)
        RV = {tf: _arr(npz, f'relative_volume_{tf}', n, 0.0) for tf in htf}
        # breakout state machine (runs every bar in live: _update_stdev_state)
        self.active = np.zeros(n, dtype=bool)
        self.epoch = np.zeros(n, dtype=np.int32)
        act, since, ep = False, 0, 0
        for i in range(n):
            if act:
                since += 1
                if since > maxa:
                    act = False
            else:
                for tf in htf:
                    rv = float(RV[tf][i])
                    if rv <= 0:
                        rv = float(rv1[i])
                    if rv <= 0:
                        rv = float(rv15[i])
                    p = float(self.P[tf][i])
                    if ((is_long and p > pl) or ((not is_long) and p < ps)) and rv >= rmin and bool(self.vol_ok[i]):
                        act, since, ep = True, 0, ep + 1
                        break
            self.active[i] = act
            self.epoch[i] = ep
        self._ep, self._rc = 0, 0

    def mult(self, i):
        if not self.active[i]:
            return 1.0
        if self.epoch[i] != self._ep:
            self._ep, self._rc = int(self.epoch[i]), 0
        if self._rc == 0:
            if not bool(self.vol_ok[i]):
                return 1.0
            self._rc = 1
            return 1.0
        if self._rc >= self.maxr + 1:
            return 1.0
        for tf in self.rtf:
            p = float(self.P[tf][i]); k, d = float(self.K[tf][0][i]), float(self.K[tf][1][i])
            if self.is_long:
                ok = self.rp_min <= p <= self.rp_max and k > d and k < 65
            else:
                ok = (1.0 - self.rp_max) <= p <= (1.0 - self.rp_min) and k < d and k > 35
            if ok:
                self._rc += 1
                return self.size_mult
        return 1.0


def dc_floor_confirms(npz, n, is_long, close):
    key = 'dc_low_15m_prev' if is_long else 'dc_high_15m_prev'
    b = _arr(npz, key, n, 0.0)
    if not np.any(b > 0):
        b = _arr(npz, 'dc_low_15m' if is_long else 'dc_high_15m', n, 0.0)
    ok = (close <= b) if is_long else (close >= b)
    return np.where(b > 0, ok, True)
