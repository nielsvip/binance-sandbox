import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('reduce_size_retry', Path(__file__).resolve().parents[1] / 'tools/vector_direct_trader.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unfilled_reduce_is_rechecked_against_new_broker_quantity(tmp_path):
    trader = module.Trader.__new__(module.Trader)
    trader.venue, trader.out = 'crypto', tmp_path
    trader.a = SimpleNamespace(dry_run=False)
    trader.done, trader.quiet, trader.done_ts = set(), set(), {}
    trader.filled_open_anchors = set()
    observations = iter([.1, 1.0])
    sent, rows, sized = [], [], []
    trader.annotate_with_mac = lambda account, positions: positions
    trader._ledger = rows.append
    trader.alert = lambda *args, **kwargs: None
    trader.stops = SimpleNamespace(after_fill=lambda *args: 'mocked')
    def size(order, held, price):
        sized.append(held)
        return (0, 'below market minQty') if held == .1 else (.2, '')
    def send(account, order, qty):
        sent.append(qty)
        return {'status': 'FILLED', 'executedQty': str(qty), 'orderId': 123}
    trader.broker = SimpleNamespace(positions=lambda account: {'QNTUSDT_LONG': {'qty': next(observations), 'entry': 100}}, price=lambda symbol: 110, size=size, send=send)
    order = {'acct': 'ang', 'ss': 'QNTUSDT_LONG', 'act': 'REDUCE', 'qty': .025, 'key': 'same-current-bar-intent', 'why': 'CYCLE_TP'}
    trader.send_orders('ang', [order], {}, False)
    assert not sent and order['key'] not in trader.done
    assert rows[-1]['status'] == 'REFUSED_REDUCE_SIZE' and rows[-1]['final'] is False
    trader.send_orders('ang', [order], {}, False)
    assert sized == [.1, 1.0] and sent == [.2]
    assert order['key'] in trader.done


def test_old_size_refusal_is_not_restored_as_completed_execution(tmp_path):
    trader = module.Trader.__new__(module.Trader)
    trader.done_ts = {}
    path = tmp_path / 'ledger.jsonl'
    rows = [{'key': 'unfilled-reduce', 'act': 'REDUCE', 'status': 'SKIP_SIZE', 'final': True}, {'key': 'filled-reduce', 'act': 'REDUCE', 'status': 'SENT', 'final': True}]
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    trader._ledger_path = lambda *args: path
    assert trader._load_done() == {'filled-reduce'}
