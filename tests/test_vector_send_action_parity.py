from types import SimpleNamespace
import pytest
from tools import vector_direct_trader as trader_module


def send_exit(tmp_path, account, action, is_long, quantity, entry, mark):
    symbol_side = 'TESTUSDC_LONG' if is_long else 'TESTUSDC_SHORT'
    position = {symbol_side: {'qty': quantity, 'entry': entry, 'break_even': entry, 'price': mark}}
    sent, ledger = [], []
    obj = trader_module.Trader.__new__(trader_module.Trader)
    obj.venue = 'crypto'
    obj.root = obj.out = tmp_path
    obj.a = SimpleNamespace(dry_run=False)
    obj.quiet, obj.done, obj.filled_open_anchors = set(), set(), set()
    obj.done_ts, obj.attempts = {}, {}
    obj._ledger = ledger.append
    obj.alert = lambda *args, **kwargs: None
    obj.annotate_with_mac = lambda account, positions: None
    obj.capture_account_position = lambda *args: {}
    obj.stops = SimpleNamespace(after_fill=lambda *args: 'simulated broker stop lifecycle')
    class Broker:
        def positions(self, account):
            return position
        def price(self, symbol):
            return mark
        def size(self, order, held, price):
            return held if action == 'CLOSE' else held / 2, ''
        def send(self, account, order, qty):
            sent.append({'account': account, 'action': action, 'bar_ts': 1793412000, 'qty': qty})
            return {'status': 'FILLED', 'executedQty': str(qty)}
    obj.broker = Broker()
    order = {'ss': symbol_side, 'act': action, 'key': f'{account}|{symbol_side}|1793412000|{action}|0', 'why': 'fixture forward action'}
    obj.send_orders(account, [order], position, False)
    return sent, ledger

@pytest.mark.parametrize('action', ['REDUCE', 'CLOSE'])
@pytest.mark.parametrize('is_long', [True, False])
def test_actual_send_gate_allows_quantity_difference_on_same_action_bar(tmp_path, action, is_long):
    mark = 101 if is_long else 99
    ang, _ = send_exit(tmp_path, 'ang', action, is_long, 100, 100, mark)
    inf, _ = send_exit(tmp_path, 'inf', action, is_long, 10, 100, mark)
    assert [(row['bar_ts'], row['action']) for row in ang] == [(row['bar_ts'], row['action']) for row in inf] == [(1793412000, action)]
    assert ang[0]['qty'] == 10 * inf[0]['qty']

@pytest.mark.parametrize('action', ['REDUCE', 'CLOSE'])
@pytest.mark.parametrize('is_long', [True, False])
def test_account_entry_difference_can_change_actual_action_acceptance(tmp_path, action, is_long):
    mark = 101 if is_long else 99
    losing_entry = 102 if is_long else 98
    ang, rows = send_exit(tmp_path, 'ang', action, is_long, 100, losing_entry, mark)
    inf, _ = send_exit(tmp_path, 'inf', action, is_long, 10, 100, mark)
    assert not ang
    assert len(inf) == 1
    assert rows[-1]['status'] == ('REFUSED_NONPROFIT_REDUCE' if action == 'REDUCE' else 'REFUSED_NONSTOP_LOSS_CLOSE')
    assert rows[-1]['actual_profit_check']['gain_per_unit_before_exit_fee'] < 0
