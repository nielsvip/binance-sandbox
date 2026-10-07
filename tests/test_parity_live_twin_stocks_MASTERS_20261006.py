"""PARITY LANE C 2026-10-06 — new stocks live masters (director): EXIT_VELOCITY_WT_ENABLED (True), STOCKS_WTDC_SCORER_EXIT_ENABLED
(True), MULTI_TF_EXIT_ENABLED_TRADIER (False), KG_STOCKS_HARD_VETO_ENABLED (False) — defaults == today's live, same names
as the lane-D vec twins; KG twin == vec_decisions.live_kindergarten_stocks.pass_mask on identical inputs."""
import numpy as np
import pytest

import vec_decisions.live_kindergarten_stocks as K
from tests._laneC_parity_helpers import L, Cfg, vsafe, tm_source, tradier_default


def test_defaults_today():
    assert tradier_default("EXIT_VELOCITY_WT_ENABLED") is False  # director 2026-10-06: config_tradier default False — the live tradier parser drops wt_velocity_* keys so live cannot run it (fix = parser carries wt_velocity_*; vec_only_resolution_20261006.md)
    assert tradier_default("STOCKS_WTDC_SCORER_EXIT_ENABLED") is True
    assert tradier_default("MULTI_TF_EXIT_ENABLED_TRADIER") is False
    assert tradier_default("KG_STOCKS_HARD_VETO_ENABLED") is False


def test_hooks_present():
    src = tm_source()
    assert "if not _gx_fire and bool(_cfg_ps('EXIT_VELOCITY_WT_ENABLED', True, account_key, symbol, position_side)):" in src
    assert 'path_switch(config, "WT_DC_EXIT_ENABLED", True) and bool(_cfg_ps("STOCKS_WTDC_SCORER_EXIT_ENABLED", True' in src
    assert "bool(_cfg_ps('MULTI_TF_EXIT_ENABLED_TRADIER', False, _vf_acct, symbol, _vf_exit_side))" in src
    assert "_ftf_twins.kg_stocks_block(_ftf_ps, _ftf_get, _px_ftf, is_long)" in src


CFGS = [
    dict(EMA_9_21_FILTER_ENABLED=True, KINDERGARTEN_CUMULATIVE_MODE=True, EMA_9_21_FILTER_TFS="15m,1h,4h", KINDERGARTEN_CUMULATIVE_MIN_TFS=2),
    dict(KINDERGARTEN_EMA_GATE_ENABLED=True, KINDERGARTEN_CUMULATIVE_MODE=True, EMA_9_21_FILTER_TFS="1h,4h,D", KINDERGARTEN_STRICT_TFS="D", EMA_50_200_FILTER_ENABLED=True, EMA_50_200_TFS="D", SMA_200_FILTER_ENABLED=True, SMA_200_TIMEFRAME="D"),
    dict(EMA_9_21_FILTER_ENABLED=True, KINDERGARTEN_CUMULATIVE_MODE=False, EMA_9_21_TIMEFRAME="1h"),
]


@pytest.mark.parametrize("ci", range(len(CFGS)))
@pytest.mark.parametrize("is_long", [True, False])
def test_kg_live_equals_vec(ci, is_long):
    n = 300
    r = np.random.default_rng(70 + ci)
    close = 100 + np.cumsum(r.normal(0, 1, n))
    npz = {"close": close, "sma_200_D": close * (1 + r.normal(0, 0.02, n))}
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"ema_9_above_21_{tf}"] = (r.random(n) < 0.5).astype(np.float64)
    npz["ema_50_above_200_D"] = (r.random(n) < 0.5).astype(np.float64)
    cfgd = dict(CFGS[ci], KG_STOCKS_LIVE_GATE=True, KG_STOCKS_HARD_VETO_ENABLED=True)
    vm = K.pass_mask(npz, n, is_long, Cfg(**cfgd), close, vsafe)
    blocks = 0
    for i in range(n):
        d = {k: float(v[i]) for k, v in npz.items()}
        veto = L.kg_stocks_block(lambda k, dd=None: cfgd.get(k, dd), d.get, float(close[i]), is_long)
        assert (veto is None) == bool(vm[i]), (ci, i, veto)
        blocks += int(veto is not None)
    assert 0 < blocks < n


def test_kg_default_inert():
    assert L.kg_stocks_block(lambda k, d=None: {"EMA_9_21_FILTER_ENABLED": True}.get(k, d), {"ema_9_above_21_1h": 0.0}.get, 1.0, True) is None
