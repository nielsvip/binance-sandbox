import importlib.util
import json
import time
from pathlib import Path

from tools.account_execution_state import load_latest_snapshot

spec = importlib.util.spec_from_file_location('account_snapshot_capture', Path(__file__).resolve().parents[1] / 'tools/vector_direct_trader.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_capture_preserves_fresh_broker_quantity_and_config_binding(tmp_path):
    trader = module.Trader.__new__(module.Trader)
    trader.venue, trader.out = 'crypto', tmp_path
    now = time.time()
    trader.position_observed_at = {'ang': now}
    (tmp_path / 'exchange_info_crypto.json').write_text(json.dumps({'_fetched': now, 'symbols': {'QNTUSDT': {'step': .1, 'min_qty': .1, 'max_qty': 100}}}))
    provenance = {'settings_sha256': 'a' * 64, 'state_sha256': 'b' * 64}
    position = {'qty': .1, 'entry': 194.59, 'break_even': 194.68}
    result = trader.capture_account_position('ang', 'QNTUSDT_LONG', position, provenance, (int(now) // 900 - 1) * 900)
    assert result['status'] == 'BROKER_OBSERVED'
    snapshot = load_latest_snapshot(tmp_path / 'account_execution', 'ang', 'QNTUSDT_LONG')
    assert snapshot['qty'] == .1 and snapshot['entry_price'] == 194.59
    assert snapshot['lifecycle_id'] is None
    assert snapshot['settings_sha256'] == provenance['settings_sha256']
    assert snapshot['observed_at'] == now


def test_stale_observation_is_not_restamped_as_current(tmp_path):
    trader = module.Trader.__new__(module.Trader)
    trader.venue, trader.out = 'stocks', tmp_path
    trader.position_observed_at = {'trb': time.time() - 60}
    result = trader.capture_account_position('trb', 'LMT_LONG', {'qty': 1, 'entry': 600}, {'settings_sha256': 'a' * 64, 'state_sha256': 'b' * 64}, 1793412000)
    assert result['status'] == 'UNVERIFIED' and 'stale' in result['error']
    assert not (tmp_path / 'account_execution').exists()
