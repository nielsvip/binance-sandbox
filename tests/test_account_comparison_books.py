import datetime as dt
import json

from tools.account_comparison_books import (FIN_MIRROR_END, FIN_MIRROR_START,
                                            side_allowed, side_symbols,
                                            source_account)


def test_fin_mirrors_men_for_two_weeks_then_restores_own_dynamic_books(tmp_path):
    (tmp_path / "symbols_men_long.json").write_text(json.dumps(["AAAUSDT"]))
    (tmp_path / "symbols_men_short.json").write_text(json.dumps(["BBBUSDT"]))
    (tmp_path / "symbols_fin_long.json").write_text(json.dumps(["CCCUSDT"]))
    (tmp_path / "symbols_fin_short.json").write_text(json.dumps(["DDDUSDT"]))
    during = FIN_MIRROR_START + dt.timedelta(days=7)
    assert source_account("fin", during) == "men"
    assert side_symbols(tmp_path, "fin", "LONG", during) == {"AAAUSDT"}
    assert side_symbols(tmp_path, "fin", "SHORT", during) == {"BBBUSDT"}
    assert not side_allowed(tmp_path, "fin", "CCCUSDT", "LONG", during)
    assert source_account("fin", FIN_MIRROR_END) == "fin"
    assert side_symbols(tmp_path, "fin", "LONG", FIN_MIRROR_END) == {"CCCUSDT"}
    assert side_symbols(tmp_path, "fin", "SHORT", FIN_MIRROR_END) == {"DDDUSDT"}


def test_trc_mirrors_trb_exactly_and_missing_book_fails_closed(tmp_path):
    (tmp_path / "symbols_trb_long.json").write_text(json.dumps(["AAPL"]))
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    (tmp_path / "symbols_trc_long.json").write_text(json.dumps(["MSFT"]))
    assert side_symbols(tmp_path, "trc", "LONG") == {"AAPL"}
    assert not side_allowed(tmp_path, "trc", "MSFT", "LONG")
    assert not side_allowed(tmp_path, "trc", "AAPL", "SHORT")
    (tmp_path / "symbols_trb_long.json").unlink()
    assert not side_allowed(tmp_path, "trc", "AAPL", "LONG")
