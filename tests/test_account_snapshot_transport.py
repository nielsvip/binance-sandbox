import json
import time

from tools.account_execution_state import build_snapshot, persist_snapshot
from tools.vector_direct_fill_export import account_snapshot_for_export
from tools.vector_direct_history_import import build_row, sync_account_execution_snapshots


def test_account_snapshot_body_is_verified_and_retained_in_fill_history(tmp_path):
    now = time.time()
    snapshot = build_snapshot('ang', 'QNTUSDT_LONG', {'qty': .1, 'entry': 194.59}, broker_confirmed=True, broker_source='Binance.positionRisk', observed_at=now, bar_ts=now - 900, settings_sha256='a' * 64, filter_sha256='b' * 64, state_sha256='c' * 64, now=now)
    base = tmp_path / 'data/vector_direct/account_execution'
    reference = persist_snapshot(base, snapshot)
    context = account_snapshot_for_export(tmp_path, 'ang', 'QNTUSDT_LONG', reference)
    assert context['snapshot_verification'] == 'VERIFIED'
    assert context['snapshot']['qty'] == .1
    rec = {'acct': 'ang', 'ss': 'QNTUSDT_LONG', 'act': 'REDUCE', 'account_execution_context': context, 'fill': {'found': True, 'order_id': '123', 'exec_qty': .1, 'avg_price': 200, 'quote': 20, 'fill_ms': int(now * 1000)}}
    row = build_row(rec, 'S1_VECTOR_DIRECT')
    assert row['account_execution_context'] == context
    stored = base / reference['snapshot_path']
    broken = json.loads(stored.read_text())
    broken['qty'] = 100
    stored.write_text(json.dumps(broken))
    rejected = account_snapshot_for_export(tmp_path, 'ang', 'QNTUSDT_LONG', reference)
    assert rejected['snapshot_verification'] == 'UNVERIFIED'
    assert 'snapshot' not in rejected


def test_account_snapshot_reference_cannot_cross_accounts_or_escape_root(tmp_path):
    reference = {'snapshot_sha256': 'd' * 64, 'snapshot_path': '../../outside.json'}
    result = account_snapshot_for_export(tmp_path, 'ang', 'QNTUSDT_LONG', reference)
    assert result['snapshot_verification'] == 'UNVERIFIED'
    reference['snapshot_path'] = 'snapshots/fin/QNTUSDT_LONG/' + 'd' * 64 + '.json'
    assert account_snapshot_for_export(tmp_path, 'ang', 'QNTUSDT_LONG', reference)['snapshot_verification'] == 'UNVERIFIED'


def test_importer_syncs_verified_snapshots_even_when_fill_is_already_imported(tmp_path):
    now = time.time()
    snapshot = build_snapshot('ang', 'QNTUSDT_LONG', {'qty': .1, 'entry': 194.59}, broker_confirmed=True, broker_source='Binance.positionRisk', observed_at=now, bar_ts=now - 900, settings_sha256='a' * 64, filter_sha256='b' * 64, state_sha256='c' * 64, now=now)
    source_root = tmp_path / 'source' / 'data' / 'vector_direct' / 'account_execution'
    reference = persist_snapshot(source_root, snapshot)
    context = account_snapshot_for_export(tmp_path / 'source', 'ang', 'QNTUSDT_LONG', reference)
    rec = {'acct': 'ang', 'ss': 'QNTUSDT_LONG', 'account_execution_context': context}
    destination = tmp_path / 'mac' / 'data' / 'vector_direct' / 'account_execution'
    result = sync_account_execution_snapshots([rec], destination)
    assert result == {'requested': 1, 'verified': 1, 'already': 0, 'rejected': 0, 'invalid_references': 0}
    saved = destination / reference['snapshot_path']
    assert json.loads(saved.read_text()) == snapshot
    second = sync_account_execution_snapshots([rec], destination)
    assert second['verified'] == 0
    assert second['already'] == 1

    tampered = {**context, 'snapshot': {**snapshot, 'qty': 999}}
    rejected = sync_account_execution_snapshots([{'acct': 'ang', 'ss': 'QNTUSDT_LONG', 'account_execution_context': tampered}], tmp_path / 'bad')
    assert rejected['rejected'] == 1
    assert not (tmp_path / 'bad' / reference['snapshot_path']).exists()
