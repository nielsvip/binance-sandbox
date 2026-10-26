from tools import npz_tail_compactor as C


def test_rollup_never_starts_near_publication_boundary(monkeypatch):
    monkeypatch.setattr(C, 'load_universe', lambda base: (_ for _ in ()).throw(AssertionError('must not scan history')))
    for phase in (0, 1, 30, 89.999, 720, 899.999):
        assert C.compact_once('.', 'crypto', clock=lambda: 9000 + phase)['symbols'] == 0


def test_rollup_yields_before_next_boundary(monkeypatch):
    monkeypatch.setattr(C, 'load_universe', lambda base: ['A_LONG', 'B_LONG'])
    monkeypatch.setattr(C, 'owned_symbols', lambda universe, side: ['A', 'B'])
    monkeypatch.setattr(C, 'npz_for', lambda base, symbol: symbol)
    monkeypatch.setattr(C, 'tail_len', lambda path: 4)
    times = iter([9100, 9100, 9100, 9720])
    compacted = []
    monkeypatch.setattr(C, 'compact_tail', lambda path: compacted.append(path) or 4)
    result = C.compact_once('.', 'stocks', clock=lambda: next(times))
    assert compacted == ['A'] and result['symbols'] == 1
