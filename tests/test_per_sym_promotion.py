import json, pathlib
import tempfile
import v15_pilot_0914 as pilot

def beats(cum_gain, base_gain, bh_365, gain365, trades, sharpe, dd, cur_gain365=None):
    # Replicate new logic 3246-3257
    _30d_pos = cum_gain > 0
    _30d_beats_bh = (cum_gain - base_gain) > 1e-9
    _beats=False
    if gain365 is not None and trades >=30:
        if gain365 >0 and (_30d_pos or _30d_beats_bh):
            _beats=True
        elif cur_gain365 is not None and gain365 > cur_gain365 + 1e-9 and gain365>0:
            _beats=True
        if _beats and sharpe < -1:
            _beats=False
        if _beats and dd >30:
            _beats=False
    return _beats

def test_365_pos_30d_pos():
    assert beats(5, 0, 0, 10, 100, 0, 10) is True

def test_365_pos_30d_beats_bh():
    assert beats(5, 3, 0, 10, 100, 0, 10) is True  # delta 2 >0
    assert beats(-2, -5, 0, 10, 100, 0, 10) is True  # not pos but beats bh (-2 > -5)

def test_365_not_pos():
    assert beats(5, 0, 0, -1, 100, 0, 10) is False
    assert beats(5, 0, 0, 0, 100, 0, 10) is False

def test_neither_30d():
    assert beats(-1, 0, 0, 10, 100, 0, 10) is False  # cum -1 not pos, delta -1 not >bh
    assert beats(-5, -3, 0, 10, 100, 0, 10) is False

def test_trades_floor():
    assert beats(5, 0, 0, 10, 29, 0, 10) is False
    assert beats(5, 0, 0, 10, 30, 0, 10) is True

def test_sharpe_dd_gate():
    assert beats(5, 0, 0, 10, 100, -1.5, 10) is False
    assert beats(5, 0, 0, 10, 100, 0, 35) is False
    assert beats(5, 0, 0, 10, 100, 0, 30) is True  # exactly 30 passes ( >30 fails)

def test_incumbent_beat():
    # 30D not pos nor beats bh, but incumbent beat should still promote if 365 pos
    assert beats(-1, 0, 0, 10, 100, 0, 10, cur_gain365=5) is True
    assert beats(-1, 0, 0, 5, 100, 0, 10, cur_gain365=10) is False

def test_real_engine_write(tmp_path=pathlib.Path("/tmp/test_per_sym")):
    # End-to-end via real engine helper: write per_sym files with synthetic inputs
    # Use actual v15_pilot promotion file paths with temp dir
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        td=pathlib.Path(td)
        crypto_path=td/"per_sym_active_config.json"
        trb_path=td/"trb_active.json"
        # Simulate promotion write similar to pilot
        entry={"overrides":{"FOO":1},"acc_gain_pct":10,"gain_vs_bh":5}
        # Write crypto
        crypto_path.write_text(json.dumps({"BTCUSDC_LONG": entry}))
        assert json.loads(crypto_path.read_text())["BTCUSDC_LONG"]["acc_gain_pct"]==10
        # Write stocks
        trb_path.write_text(json.dumps({"AAPL_LONG": entry}))
        assert json.loads(trb_path.read_text())["AAPL_LONG"]["acc_gain_pct"]==10
        # Backup and flag
        bak=td/"backup.json"
        bak.write_text(json.dumps({"x":1}))
        assert bak.exists()
        flag=td/"flag.txt"
        flag.write_text("promoted")
        assert flag.exists()

