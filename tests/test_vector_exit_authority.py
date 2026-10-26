import ast
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest

from live_twins import vec_provenance
from live_twins.vector_exit_authority import current_vector_exit


def gate_prefix():
    tree = ast.parse(Path('ez_manage.py').read_text())
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == 'execute_now')
    method.decorator_list = []
    cutoff = next(index for index, node in enumerate(method.body) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '_res' for target in node.targets))
    method.body = method.body[:cutoff + 1] + [ast.Return(value=ast.Name(id='_res', ctx=ast.Load()))]
    ast.fix_missing_locations(method)
    namespace = {'Optional': Optional, 'config': SimpleNamespace(COMMISSION_BUFFER_PCT=0.08), 'logger': logging.getLogger('exit_authority_test'), 'safe_fetch_float': lambda value, default: float(value) if value is not None else default, '_psym_get': lambda symbol, side, key, default: default}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'ez_manage.py', 'exec'), namespace)
    return namespace['execute_now']


@pytest.mark.asyncio
async def test_real_execute_now_net_gate_follows_structural_vector_exit():
    method = gate_prefix()
    inner = AsyncMock(return_value='SUCCESS_PROVEN')
    manager = SimpleNamespace(positions_by_account={'flz': {'flz:TESTUSDT_LONG': SimpleNamespace(gain=-2)}}, _execute_now_inner=inner)
    args = dict(position_key='flz:TESTUSDT_LONG', account_key='flz', symbol='TESTUSDT', side='SELL', position_side='LONG', action='CLOSE', quantity=1, old_price=10, is_full_close=True, reason='VEC_DRIVEN_CLOSE_SIGNAL')
    assert await method(manager, **args) == 'BLOCKED_NET_GAIN'
    inner.assert_not_awaited()
    token = vec_provenance.mark(args['position_key'], time.time() - 900, 'CLOSE', 'VEC_DRIVEN_STATE')
    try:
        assert await method(manager, **args) == 'SUCCESS_PROVEN'
        inner.assert_awaited_once()
    finally:
        vec_provenance.reset(token)
    assert not current_vector_exit(args['position_key'])


def test_authority_is_key_action_and_lifetime_scoped():
    token = vec_provenance.mark('flz:TESTUSDT_LONG', time.time() - 900, 'CLOSE', 'VEC_DRIVEN_STATE')
    try:
        assert current_vector_exit('flz:TESTUSDT_LONG')
        assert not current_vector_exit('flz:OTHERUSDT_LONG')
        assert not current_vector_exit('flz:TESTUSDT_LONG', now=time.time() + 301)
    finally:
        vec_provenance.reset(token)
    token = vec_provenance.mark('flz:TESTUSDT_LONG', time.time() - 900, 'OPEN', 'VEC_DRIVEN_STATE')
    try:
        assert not current_vector_exit('flz:TESTUSDT_LONG')
    finally:
        vec_provenance.reset(token)
