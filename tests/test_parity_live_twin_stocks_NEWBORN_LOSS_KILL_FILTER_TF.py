"""PARITY LANE C 2026-10-06 — NEWBORN_LOSS_KILL velocity-TF selection identical to vec simulate_one.

Vec: _nlk_tf = twin_yellow_filters.resolve_filter_tf(FILTER_TF, family_raw=(VEL_TF or '3m'), filter_default='15m') or (VEL_TF or '3m');
key = wt_velocity_{tf} unless OFF -> wt_velocity_15m (block extracted from live v12 source and executed).
Live: tradier_filter_tf_twins.newborn_vel_key in the process_position TWIN_SIZING_REDUCE chain.
"""
import itertools

import numpy as np
import pytest

from tests._laneC_parity_helpers import L, Cfg, tm_source, v12_block

BLOCK = v12_block("import vec_decisions.twin_yellow_filters as _tyf_nlk", "_nlk_key = f'wt_velocity_{_nlk_tf}'")


def _vec_key(filter_tf, vel_tf):
    ns = {"cfg": Cfg(NEWBORN_LOSS_KILL_FILTER_TF=filter_tf, NEWBORN_LOSS_KILL_VEL_TF=vel_tf)}
    exec(compile(BLOCK, "v12_nlk_block", "exec"), ns)
    return ns["_nlk_key"]


@pytest.mark.parametrize("filter_tf,vel_tf", list(itertools.product(["15m", "1h", "4h", "D", "OFF", "", "3m"], ["", "3m", "5m", "15m", "1h", "OFF"])))
def test_key_identical(filter_tf, vel_tf):
    assert L.newborn_vel_key(filter_tf, vel_tf) == _vec_key(filter_tf, vel_tf)


def test_hook_replaces_old_extra_and():
    src = tm_source()
    assert "_ftf_twins.newborn_vel_key(_cfg_ps('NEWBORN_LOSS_KILL_FILTER_TF', '15m', account_key, symbol, position_side), _cfg_ps('NEWBORN_LOSS_KILL_VEL_TF', '', account_key, symbol, position_side))" in src
    assert "_nlk_tf not in ('15m', 'OFF', 'off', '')" not in src
