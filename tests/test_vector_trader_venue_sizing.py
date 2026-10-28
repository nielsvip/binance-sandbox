"""The tiny S1/S5 vector traders must size from THEIR OWN venue's config file. Nothing else.

OPERATOR 2026-10-28, verbatim: "stock and crypto are not the same pls DONT FUCK THIS UP stocks 900
crypto 15 NO EXCEPTION" and "a stock can NEVER see config.py and a crypto can never see
config_tradier.py JUST MAKE SURE THE NEW TINY SCIPTS THAT ACUALLLY TRADE on s1 crypto and s5 stock DO
ACUTLALY READ HTESE VALUES FORM THE RIGHT CONFIG FILES".

The bug this pins: the three sizing knobs were ONE venue-blind triple of env reads defaulting to the
CRYPTO value (15), so the stocks trader on S5 ran on a $15 slice and kept falling through to the
one-whole-share path.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _fresh(env=None):
    for k in [k for k in os.environ if k.startswith("VD_")]:
        del os.environ[k]
    if env:
        os.environ.update(env)
    sys.modules.pop("vector_direct_trader", None)
    return importlib.import_module("vector_direct_trader")


def _stub_cfg(venue, start, maxpos):
    """Swap in a stub config module for one venue.

    Mutating the real class attribute does NOT work: both `config.Config.__init__` (via `_apply_mode`,
    the MARKET MODE overlay) and `config_tradier.TradierConfig.__init__` re-set these fields, which is
    exactly why the resolver instantiates instead of reading the class. So to exercise the warning and
    the refusal paths, the module itself is stubbed and restored.
    """
    mod_name, cls_name = ("config", "Config") if venue == "crypto" else ("config_tradier", "TradierConfig")
    import types

    saved = sys.modules.get(mod_name)
    stub = types.ModuleType(mod_name)

    class _C:
        START_POSITION_SIZE = start
        MAX_POSITION_SIZE = maxpos
        MARKET_MODE = "STUB_MODE"

    _C.__module__ = mod_name
    setattr(stub, cls_name, _C)
    sys.modules[mod_name] = stub
    vd = _fresh()

    def restore():
        if saved is not None:
            sys.modules[mod_name] = saved
        else:
            sys.modules.pop(mod_name, None)

    return vd, restore


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: v for k, v in os.environ.items() if k.startswith("VD_")}
    yield
    for k in [k for k in os.environ if k.startswith("VD_")]:
        del os.environ[k]
    os.environ.update(saved)


def test_crypto_sizes_from_config_py_only():
    import config as C

    vd = _fresh()
    rep = vd.apply_venue_sizing("crypto")
    assert rep["config"].startswith("config.py:Config")
    assert vd.START_POSITION_SIZE == float(C.Config().START_POSITION_SIZE)
    assert vd.MAX_OPEN_NOTIONAL == float(C.Config().MAX_POSITION_SIZE)
    assert vd.STOCK_ONE_SHARE_MAX == 0.0, "the one-share rule is stocks-only"


def test_stocks_sizes_from_config_tradier_only():
    import config_tradier as CT

    vd = _fresh()
    rep = vd.apply_venue_sizing("stocks")
    assert rep["config"].startswith("config_tradier.py:TradierConfig")
    assert vd.START_POSITION_SIZE == float(CT.TradierConfig().START_POSITION_SIZE)
    assert vd.MAX_OPEN_NOTIONAL == float(CT.TradierConfig().MAX_POSITION_SIZE)
    assert vd.STOCK_ONE_SHARE_MAX == float(CT.TradierConfig().MAX_POSITION_SIZE)


def test_the_two_venues_never_share_a_number():
    """A crypto start equal to the stocks start would mean one config is feeding both."""
    import config as C
    import config_tradier as CT

    vd = _fresh()
    vd.apply_venue_sizing("crypto")
    crypto = (vd.START_POSITION_SIZE, vd.MAX_OPEN_NOTIONAL)
    vd = _fresh()
    vd.apply_venue_sizing("stocks")
    stocks = (vd.START_POSITION_SIZE, vd.MAX_OPEN_NOTIONAL)
    assert crypto != stocks
    assert crypto == (float(C.Config().START_POSITION_SIZE), float(C.Config().MAX_POSITION_SIZE))
    assert stocks == (float(CT.TradierConfig().START_POSITION_SIZE), float(CT.TradierConfig().MAX_POSITION_SIZE))


def test_mandated_values_are_in_the_right_config_files():
    """The operator's numbers, each in its OWN file: crypto 15 in config.py, stocks 900 in config_tradier.py."""
    import config as C
    import config_tradier as CT

    # the EFFECTIVE value is what trades: config.Config.__init__ overlays the active MARKET MODE, and
    # LIGHT_MODE used to hold crypto START at 12 while the class said 15.
    assert float(C.Config().START_POSITION_SIZE) == 15.0, "crypto effective START_POSITION_SIZE must be 15 (class AND market mode)"
    assert float(C.Config.START_POSITION_SIZE) == 15.0, "crypto class START_POSITION_SIZE must be 15 in config.py"
    assert float(CT.TradierConfig().START_POSITION_SIZE) == 900.0, "stocks effective START_POSITION_SIZE must be 900"


def test_env_is_not_an_authority_for_sizing():
    """A second source is exactly how stocks ended up on the crypto slice — env must be inert and say so."""
    import config_tradier as CT

    vd = _fresh({"VD_START_POSITION_SIZE": "999", "VD_MAX_OPEN_NOTIONAL": "999"})
    rep = vd.apply_venue_sizing("stocks")
    assert vd.START_POSITION_SIZE == float(CT.TradierConfig().START_POSITION_SIZE)
    assert vd.MAX_OPEN_NOTIONAL == float(CT.TradierConfig().MAX_POSITION_SIZE)
    assert any("IGNORED" in p for p in rep["pinned"]), "an ignored env override must be reported, not silent"


def test_unresolved_sizing_is_zero_not_the_other_venues_slice():
    """Before apply_venue_sizing runs, sizing must be 0 (no order), never a silent default."""
    vd = _fresh()
    assert vd.START_POSITION_SIZE == 0.0
    assert vd.MAX_OPEN_NOTIONAL == 0.0
    assert vd.STOCK_ONE_SHARE_MAX == 0.0


def test_a_mismatch_against_the_mandate_is_reported_loudly():
    vd, restore = _stub_cfg("stocks", start=500.0, maxpos=4000.0)
    try:
        rep = vd.apply_venue_sizing("stocks")
        assert any("mandate is 900" in p for p in rep["pinned"]), rep["pinned"]
        assert vd.START_POSITION_SIZE == 500.0, "the config file stays the authority; the warning is the alarm"
    finally:
        restore()


def test_non_positive_size_refuses_to_trade():
    vd, restore = _stub_cfg("crypto", start=0.0, maxpos=80.0)
    try:
        with pytest.raises(SystemExit):
            vd.apply_venue_sizing("crypto")
    finally:
        restore()


def test_sizing_is_resolved_before_the_trader_exists():
    """apply_venue_sizing must be called before Trader(a), so no order can be planned unsized."""
    src = (ROOT / "tools" / "vector_direct_trader.py").read_text()
    i_size = src.index("apply_venue_sizing(a.venue)")
    i_trader = src.index("t = Trader(a)")
    assert i_size < i_trader, "venue sizing must be resolved before the Trader is constructed"


def test_only_the_owning_config_is_named_at_each_call_site():
    """Guard against someone reintroducing a shared/venue-blind sizing read."""
    src = (ROOT / "tools" / "vector_direct_trader.py").read_text()
    fn = src[src.index("def apply_venue_sizing"):]
    fn = fn[: fn.index("\ndef ", 1)] if "\ndef " in fn[1:] else fn
    assert "import config as _m" in fn and "import config_tradier as _m" in fn
    # the crypto branch must demand module "config", the stocks branch module "config_tradier"
    assert '"config"' in fn and '"config_tradier"' in fn
    assert "got_mod != want_mod" in fn, "the exact-module guard must stay"
