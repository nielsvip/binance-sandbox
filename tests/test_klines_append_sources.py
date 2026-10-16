"""klines_append source-merge contract (USER 2026-10-15).

s1 holds the COMPLETE kline files and may only be appended to; the Mac and gateway hold SHORTER files that carry the
LATEST bars (built from second-by-second ticks, 1m -> 3/5m -> 15m -> 1h -> 4h -> D). Three faults were found and are
pinned here:

  1. `if canon: return canon` let a LAGGING canonical lane freeze the store — measured 2026-10-15: canonical at
     12:45Z (s1) / 13:45Z (Mac) while the tick feeds were at 18:00-18:30Z, so every crypto symbol stopped at 12:45Z,
     npz_bar_refresh reported EXCHANGE_BAR_NOT_READY for 218-281 of 369 symbols, and every board fitted 6 h-old bars.
  2. stock bars live in `klines_cache_macbook/tradier/`; the flat glob never looked there, so s1's long stock files
     sat at the previous session's close while today's 18:15Z bars were already on the box.
  3. the Mac publishes a `provisional` temp-OHLC bar for the forming candle. merge_klines only corrects canonical
     rows, so a provisional bar appended to s1 would never be replaced by the official OHLCV.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(tmp, monkeypatch):
    """Import klines_append with every source root pointed at a temp tree."""
    spec = importlib.util.spec_from_file_location("klines_append_t", str(ROOT / "klines_append.py"))
    m = importlib.util.module_from_spec(spec)
    sys.argv = ["klines_append.py"]
    spec.loader.exec_module(m)
    m.CANONICAL = tmp / "klines_canonical"
    m.PRIMARY = tmp / "klines_cache" / "gateway"
    m.BACKUP = tmp / "klines_cache_gateway"
    m.MACBOOK = tmp / "klines_cache_macbook"
    m.TARGET = tmp / "klines_cache_backtest"
    for d in (m.CANONICAL, m.PRIMARY, m.BACKUP, m.MACBOOK, m.TARGET):
        d.mkdir(parents=True, exist_ok=True)
    return m


def bar(ts, close=1.0, **extra):
    b = {"timestamp": f"2026-10-15T{ts}:00.000000Z", "open": close, "high": close, "low": close, "close": close, "volume": 10.0}
    b.update(extra)
    return b


def write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows))


@pytest.fixture
def ka(tmp_path, monkeypatch):
    return load(tmp_path, monkeypatch)


def test_lagging_canonical_no_longer_freezes_the_store(ka):
    """The exact 2026-10-15 shape: canonical ends 12:45Z, the tick feed is at 18:30Z."""
    write(ka.CANONICAL / "CHRUSDT_15m.json", [bar("12:30"), bar("12:45")])
    write(ka.PRIMARY / "CHRUSDT_15m.json", [bar("12:45"), bar("18:15"), bar("18:30")])
    fresh = ka.gather_fresh("CHRUSDT_15m.json")
    last = max(b["timestamp"] for b in fresh)
    assert last.endswith("18:30:00.000000Z"), f"newer-than-canonical bars must flow through, got {last}"


def test_canonical_keeps_authority_inside_its_own_range(ka):
    """Canonical exists to CORRECT mislabelled bars — a live row must never displace a canonical row it overlaps."""
    write(ka.CANONICAL / "CHRUSDT_15m.json", [bar("12:45", close=99.0)])
    write(ka.PRIMARY / "CHRUSDT_15m.json", [bar("12:45", close=1.0), bar("13:00", close=2.0)])
    fresh = ka.gather_fresh("CHRUSDT_15m.json")
    at_1245 = [b for b in fresh if b["timestamp"].endswith("12:45:00.000000Z")]
    assert len(at_1245) == 1 and at_1245[0]["close"] == 99.0, "canonical value must win inside its range"
    assert any(b["timestamp"].endswith("13:00:00.000000Z") for b in fresh)


def test_no_canonical_means_live_feeds_merge_as_before(ka):
    write(ka.PRIMARY / "XUSDT_15m.json", [bar("18:00")])
    write(ka.MACBOOK / "XUSDT_15m.json", [bar("18:15")])
    ts = sorted(b["timestamp"] for b in ka.gather_fresh("XUSDT_15m.json"))
    assert len(ts) == 2 and ts[-1].endswith("18:15:00.000000Z")


def test_provisional_bars_are_never_appended(ka):
    """A temp OHLC row must not enter s1's long store: merge_klines would never correct it later."""
    write(ka.MACBOOK / "tradier/GOOGL_15m.json", [bar("18:00"), bar("18:15", provisional=True)])
    fresh = ka.gather_fresh("tradier/GOOGL_15m.json")
    assert [b["timestamp"] for b in fresh] == [bar("18:00")["timestamp"]], "the provisional 18:15 bar must be dropped"


def test_official_bar_is_appended_once_provisional_is_replaced(ka):
    write(ka.MACBOOK / "tradier/GOOGL_15m.json", [bar("18:00"), bar("18:15")])
    assert len(ka.gather_fresh("tradier/GOOGL_15m.json")) == 2


def test_tradier_subdir_is_discovered(ka):
    write(ka.MACBOOK / "tradier/GOOGL_15m.json", [bar("18:15")])
    write(ka.PRIMARY / "CHRUSDT_15m.json", [bar("18:15")])
    names = ka.filenames_to_process(None)
    assert "tradier/GOOGL_15m.json" in names, f"stock files must be discovered, got {names}"
    assert "CHRUSDT_15m.json" in names, "crypto discovery must not regress"


def test_stock_append_creates_the_subdir_and_grows_only(ka):
    write(ka.MACBOOK / "tradier/GOOGL_15m.json", [bar("18:00"), bar("18:15")])
    write(ka.TARGET / "tradier/GOOGL_15m.json", [bar("17:45")])
    r = ka.append_one("tradier/GOOGL_15m.json", dry_run=True)
    assert r["status"] == "dry:appended" and r["n_added"] == 2, r
    assert r["total"] == 3, "existing rows are kept — s1's store only ever grows"


def test_canonical_kill_switch_still_works(ka, monkeypatch):
    write(ka.CANONICAL / "CHRUSDT_15m.json", [bar("12:45", close=99.0)])
    write(ka.PRIMARY / "CHRUSDT_15m.json", [bar("12:45", close=1.0)])
    monkeypatch.setenv("KLINES_APPEND_CANONICAL", "0")
    fresh = ka.gather_fresh("CHRUSDT_15m.json")
    assert fresh[0]["close"] == 1.0, "KLINES_APPEND_CANONICAL=0 must bypass canonical entirely"


def test_symbol_is_parsed_from_the_basename_not_the_relative_path(ka):
    """`name` is relative ("tradier/GOOGL_15m.json") so joins follow, but the SYMBOL must come from the basename —
    otherwise sym becomes "tradier/GOOGL" and both --symbols filtering and the tokenized-stock denial break."""
    write(ka.MACBOOK / "tradier/GOOGL_15m.json", [bar("18:00")])
    write(ka.MACBOOK / "CHRUSDT_15m.json", [bar("18:00")])
    assert ka.filenames_to_process(["GOOGL"]) == ["tradier/GOOGL_15m.json"], "a --symbols filter must match stock files"
    assert ka.filenames_to_process(["CHRUSDT"]) == ["CHRUSDT_15m.json"]


def test_tokenized_stock_denial_still_applies_under_tradier(ka, monkeypatch):
    write(ka.MACBOOK / "tradier/AAPLUSDT_15m.json", [bar("18:00")])
    monkeypatch.setattr(ka, "_excl_denied", lambda sym: sym == "AAPLUSDT")
    assert ka.filenames_to_process(None) == [], "denial is keyed on the bare symbol, so it must still fire in tradier/"
