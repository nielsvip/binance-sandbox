import json

from tools.vec_state_merge import merge


def test_only_owner_can_advance_state_and_old_bar_never_replaces_new(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    (source / 'state').mkdir(parents=True)
    (destination / 'state').mkdir(parents=True)
    path = source / 'state' / 'BTCUSDC_SHORT.json'
    target = destination / 'state' / path.name
    target.write_text(json.dumps({'ss': 'BTCUSDC_SHORT', 'bar_ts': 200, 'written_at': 220}))
    path.write_text(json.dumps({'ss': 'BTCUSDC_SHORT', 'bar_ts': 100, 'written_at': 300}))
    assert merge(source, destination, 'crypto_short')['older'] == 1
    assert json.loads(target.read_text())['bar_ts'] == 200
    path.write_text(json.dumps({'ss': 'BTCUSDC_SHORT', 'bar_ts': 300, 'written_at': 320}))
    assert merge(source, destination, 'crypto_long')['off_shard'] == 1
    assert merge(source, destination, 'crypto_short')['updated'] == 1
    assert json.loads(target.read_text())['bar_ts'] == 300


def test_newer_settings_recalculation_on_same_bar_advances(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    (source / 'state').mkdir(parents=True)
    path = source / 'state' / 'AAPL_LONG.json'
    state = {'ss': 'AAPL_LONG', 'bar_ts': 200, 'written_at': 220, 'set_md5': 'first'}
    path.write_text(json.dumps(state))
    assert merge(source, destination, 'stock_long')['updated'] == 1
    path.write_text(json.dumps(dict(state, written_at=230, set_md5='new')))
    assert merge(source, destination, 'stock_long')['updated'] == 1
    assert json.loads((destination / 'state' / path.name).read_text())['set_md5'] == 'new'
