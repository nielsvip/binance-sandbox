from types import SimpleNamespace
import pytest
from tools import vector_direct_stops as stops_module

class Exchange:
    def __init__(self, side, previous, failure=None):
        self.side, self.failure, self.calls = side, failure, []
        self.rows = [self.row(1, previous), self.row(2, 999.0, 'SHORT' if side == 'LONG' else 'LONG')]
        self.posts = 0
    def row(self, identifier, level, side=None):
        return dict(algoId=identifier, positionSide=side or self.side, orderType='STOP_MARKET', workingType='MARK_PRICE', closePosition=True, triggerPrice=level, algoStatus='NEW')
    def is_dual(self, account):
        return True
    def _req(self, method, path, account=None, params=None):
        self.calls.append((method, path, dict(params or {})))
        if method == 'GET':
            return list(self.rows)
        if method == 'DELETE':
            if self.failure == 'cancel':
                raise RuntimeError('cancel unavailable')
            self.rows = [row for row in self.rows if row['algoId'] != params['algoId']]
            if self.failure == 'cancel_ack':
                raise RuntimeError('lost cancel acknowledgment')
            return {'status': 'CANCELED'}
        if method == 'POST':
            self.posts += 1
            if any(row['positionSide'] == params['positionSide'] for row in self.rows):
                raise RuntimeError('-4130 existing closePosition stop')
            if self.failure == 'place' and self.posts == 1:
                raise RuntimeError('replacement rejected')
            row = self.row(100 + self.posts, float(params['triggerPrice']))
            self.rows.append(row)
            if self.failure == 'ack' and self.posts == 1:
                raise RuntimeError('lost acknowledgment')
            return dict(algoId=row['algoId'], algoStatus='NEW')
        raise AssertionError('unexpected request')

@pytest.mark.parametrize('side,previous,desired', [('LONG', .4438, .4553), ('SHORT', .51, .49)])
@pytest.mark.parametrize('failure', [None, 'place', 'ack', 'cancel', 'cancel_ack'])
def test_prearm_replaces_only_old_side_and_recovers_protection(monkeypatch, tmp_path, side, previous, desired, failure):
    broker = Exchange(side, previous, failure)
    rows, alerts = [], []
    trader = SimpleNamespace(venue='crypto', broker=broker, a=SimpleNamespace(npz_dir=tmp_path), _ledger=rows.append, alert=lambda *args, **kwargs: alerts.append((args, kwargs)))
    helper = stops_module.Stops(trader)
    monkeypatch.setattr(helper, 'entry_level', lambda order, mark: desired)
    monkeypatch.setattr(helper, '_tick', lambda symbol: .0001)
    monkeypatch.setattr(stops_module, 'dc15', lambda *args: {})
    order = dict(ss=f'TIAUSDC_{side}', act='AUGMENT', key='same-bar-augment')
    if failure in ('place', 'cancel', 'cancel_ack'):
        with pytest.raises(RuntimeError):
            helper.prearm_entry('ang', order, 11, 12, .476)
        actual = next(row for row in broker.rows if row['positionSide'] == side)
        assert float(actual['triggerPrice']) == previous
    else:
        assert helper.prearm_entry('ang', order, 11, 12, .476) == desired
        helper.verify_entry('ang', order, 11, 12, desired)
        assert next(row for row in broker.rows if row['positionSide'] == side)['triggerPrice'] == desired
    assert any(row['algoId'] == 2 for row in broker.rows)
    assert not alerts
    assert all(call[2]['algoId'] == 1 for call in broker.calls if call[0] == 'DELETE')

@pytest.mark.parametrize('side,previous,desired', [('LONG', .46, .4553), ('SHORT', .48, .49)])
def test_already_tighter_closeposition_stop_is_reused(monkeypatch, tmp_path, side, previous, desired):
    broker = Exchange(side, previous)
    trader = SimpleNamespace(venue='crypto', broker=broker, a=SimpleNamespace(npz_dir=tmp_path), _ledger=lambda row: None)
    helper = stops_module.Stops(trader)
    monkeypatch.setattr(helper, 'entry_level', lambda order, mark: desired)
    monkeypatch.setattr(stops_module, 'dc15', lambda *args: {})
    helper.prearm_entry('ang', dict(ss=f'TIAUSDC_{side}', act='AUGMENT', key='same-bar'), 11, 12, .476)
    assert all(call[0] == 'GET' for call in broker.calls)
