"""Parity regression: ez_manage _psym_get is crypto-only (stocks via USDT
not yet traded — inf dedicated to best crypto performers), tradier_manage
global merge must include stocks, and live gate must honor TEMPLATE for tradeable.

Covers 2026-09-24 fixes:
- _psym_get crypto still works (BTCUSDC_LONG) — stocks via USDT intentionally NOT merged in ez_manage yet
- _load_global_per_sym_cfgs merges stocks file (ABT_SHORT) for tradier
- _ezm_is_live_side_enabled TEMPLATE fallback for tradeable without per_sym (AXTIUSDT_LONG)
- live gate still blocks unprofitable (AXSUSDT_LONG) but allows profitable (BTCUSDC)
"""
import json
from pathlib import Path

import ez_manage as em
import tradier_manage as tm


def test_psym_get_crypto_still_flows():
    em._ezm_per_sym_cfgs_mtime = 0
    em._ezm_per_sym_stocks_mtime = 0
    v = em._psym_get("BTCUSDC", "LONG", "OI_CONFIRM_MIN_CHANGE_PCT", None)
    assert v is not None and v != "DEFAULT"
    raw = json.loads((Path("data/hourly_reconfig/per_sym_active_config.json")).read_text())
    exp = raw.get("BTCUSDC_LONG", {}).get("overrides", {}).get("OI_CONFIRM_MIN_CHANGE_PCT")
    if exp is not None:
        assert float(v) == float(exp)


def test_psym_get_stocks_via_usdt_suffix():
    """Stocks via USDT not yet traded in ez_manage — should NOT flow (crypto-only).
    AAPL_SHORT via AAPLUSDT_SHORT must return DEFAULT until enabled."""
    em._ezm_per_sym_cfgs_mtime = 0
    raw_stocks = json.loads((Path("data/hourly_reconfig/per_sym_active_config_stocks.json")).read_text())
    sample = "AAPL_SHORT"
    if sample not in raw_stocks:
        sample = next(iter(k for k in raw_stocks if k != "_meta"), None)
    assert sample is not None
    ov = raw_stocks[sample].get("overrides", {})
    if not ov:
        return
    knob = next(iter(ov.keys()))
    sym_base = sample.rsplit("_", 1)[0]
    side = sample.rsplit("_", 1)[1]
    v_crypto_suffix = em._psym_get(f"{sym_base}USDT", side, knob, "DEFAULT")
    # crypto-only: USDT suffix should NOT resolve stocks overrides yet
    assert v_crypto_suffix == "DEFAULT", f"USDT suffix {sym_base}USDT_{side} unexpectedly found stocks override (should be crypto-only until enabled)"


def test_tradier_global_merges_stocks():
    tm._global_per_sym_cfgs_mtime = 0
    g = tm._load_global_per_sym_cfgs()
    assert "ABT_SHORT" in g or "AAPL_SHORT" in g, "global merged should contain stocks keys like ABT_SHORT (was crypto-only before fix)"
    raw_stocks = json.loads((Path("data/hourly_reconfig/per_sym_active_config_stocks.json")).read_text())
    # pick a stocks-only key not in trb
    stocks_only = None
    raw_trb = json.loads((Path("data/hourly_reconfig/trb/active_config.json")).read_text()) if Path("data/hourly_reconfig/trb/active_config.json").exists() else {}
    for k in raw_stocks:
        if k != "_meta" and k not in raw_trb:
            stocks_only = k
            break
    if stocks_only:
        assert stocks_only in g, f"stocks-only {stocks_only} missing from global merged (stocks file not merged)"
        assert "overrides" not in g[stocks_only] or isinstance(g[stocks_only], dict)


def test_live_gate_template_fallback_for_tradeable():
    # AXTIUSDT_LONG is in tradeable_keys but has no crypto per_sym; should be TEMPLATE allowed now (was BEST-blocked before)
    tk = set(json.loads(Path("tradeable_keys.json").read_text()))
    tk_syms = set(k.split(":", 1)[1] if ":" in k else k for k in tk)
    candidate = None
    raw_crypto = json.loads((Path("data/hourly_reconfig/per_sym_active_config.json")).read_text())
    for sym_side in sorted(tk_syms):
        if sym_side not in raw_crypto:
            # also not in stocks
            raw_stocks = json.loads((Path("data/hourly_reconfig/per_sym_active_config_stocks.json")).read_text())
            base = sym_side.rsplit("_", 1)[0].replace("USDT", "").replace("USDC", "")
            side = sym_side.rsplit("_", 1)[1]
            if sym_side not in raw_stocks and f"{base}_{side}" not in raw_stocks:
                candidate = sym_side
                break
    if candidate:
        sym, side = candidate.rsplit("_", 1)
        ok, reason = em._ezm_is_live_side_enabled(sym, side)
        assert ok is True, f"tradeable {candidate} without per_sym should be TEMPLATE allowed, got {ok} {reason}"
        assert "TEMPLATE" in reason


def test_live_gate_still_blocks_unprofitable():
    # AXSUSDT_LONG known losing vs per_sym (from existing test)
    ok, _ = em._ezm_is_live_side_enabled("AXSUSDT", "LONG")
    assert ok is False
    ok2, _ = em._ezm_is_live_side_enabled("AXSUSDT", "SHORT")
    assert ok2 is True
    # BTCUSDC both profitable
    assert em._ezm_is_live_side_enabled("BTCUSDC", "LONG")[0] is True


def test_tradier_cfg_fallback_via_stocks():
    # ABT_SHORT is stocks-only; _cfg should find it via global merged fallback
    # trb file does not have ABT_SHORT as primary for this test? Check: if trb missing, _cfg should still return stocks global.
    # Use a knob known in stocks overrides like WT_DC_LONG_ENABLED
    raw_stocks = json.loads((Path("data/hourly_reconfig/per_sym_active_config_stocks.json")).read_text())
    if "ABT_SHORT" not in raw_stocks:
        return
    ov = raw_stocks["ABT_SHORT"].get("overrides", {})
    knob = "WT_DC_LONG_ENABLED"
    if knob not in ov:
        return
    v = tm._cfg(knob, "DEFAULT", "trb", "ABT", "SHORT")
    assert v != "DEFAULT", f"ABT_SHORT {knob} not found via _cfg global fallback (stocks merge broken)"
