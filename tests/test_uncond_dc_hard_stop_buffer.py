"""USER 2026-10-22: UNCOND_DC_HARD_STOP_BUFFER_PCT — the unconditional dc_ hard-stop buffer as a per-sym_side switch.

Default 0.25 = the historical 0.9975 / 1.0025 literals (byte-identical ledger); a non-default value binds; the stocks live
read resolves the per-sym value through tradier_manage._cfg and feeds the shared twin predicate.
"""
import ast
import hashlib
import json
import os
import types
from pathlib import Path

import pytest

import tradier_filter_tf_twins as twins
from vec_decisions import uncond_dc_stop as U

ROOT = Path(__file__).resolve().parents[1]
KEY = "UNCOND_DC_HARD_STOP_BUFFER_PCT"


def _default(path, cls):
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for n in node.body:
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == KEY:
                    return ast.literal_eval(n.value)
    return None


@pytest.mark.parametrize("path,cls", [("config.py", "Config"), ("config_tradier.py", "TradierConfig"), ("v12_quick_engine.py", "QuickConfig")])
def test_field_present_default_025(path, cls):
    assert _default(path, cls) == 0.25


def test_default_returns_historical_literals():
    assert U.buffer_factors(types.SimpleNamespace()) == (0.9975, 1.0025)
    assert U.buffer_factors(types.SimpleNamespace(**{KEY: 0.25})) == (0.9975, 1.0025)


def test_domain_and_values():
    for bad in (9, -0.1, "x", None, True):
        assert U.buffer_pct_value(bad) == 0.25
    lo, hi = U.buffer_factors(types.SimpleNamespace(**{KEY: 0.4}))
    assert abs(lo - 0.996) < 1e-12 and abs(hi - 1.004) < 1e-12
    assert U.buffer_pct_value("0.15") == 0.15


def test_twin_predicate_follows_tolerance():
    assert twins.long_dc_low_stop_fires(99.7, 100.0, 0.25) is True
    assert twins.long_dc_low_stop_fires(99.7, 100.0, 0.40) is False
    assert twins.short_dc_high_stop_fires(100.3, 100.0, 0.25) is True
    assert twins.short_dc_high_stop_fires(100.3, 100.0, 0.40) is False
    assert twins.long_dc_low_stop_fires(99.7, 100.0, U.buffer_pct_value(0.25)) == twins.long_dc_low_stop_fires(99.7, 100.0)


def test_engine_reads_switch_in_uncond_block():
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_uh_lo_f, _uh_hi_f = _udcs.buffer_factors(cfg)" in src
    for lit in ("_uh_llvl4 * 0.9975", "_uh_llvl1 * 0.9975", "_uh_llvl15 * 0.9975", "_uh_lvl4 * 1.0025", "_uh_lvl1 * 1.0025", "_uh_lvl15 * 1.0025"):
        assert lit not in src


def test_tradier_live_reads_per_sym_value(monkeypatch):
    import per_sym_store
    import tradier_manage as tm

    src = (ROOT / "tradier_manage.py").read_text()
    assert f'_uh_tol = _udcs.buffer_pct_value(_cfg("{KEY}", _udcs.DEFAULT_BUFFER_PCT, account_key, symbol, position_side))' in src
    assert src.count("_stop_fires(_uh_px, ") == 6 and src.count(", _uh_tol)") == 6
    monkeypatch.setattr(per_sym_store, "get_overrides", lambda ss: {KEY: 0.4} if ss == "ZZTEST_LONG" else None)
    monkeypatch.delenv("PER_SYM_STORE_SQLITE_DISABLED", raising=False)
    monkeypatch.delenv("V8_DISABLE_PER_SYM", raising=False)
    assert tm._cfg(KEY, 0.25, "trb", "ZZTEST", "LONG") == 0.4
    assert U.buffer_pct_value(tm._cfg(KEY, 0.25, "trb", "ZZTEST", "LONG")) == 0.4


def _published(ss):
    import sqlite3

    db = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("select overrides_json from per_sym_active where sym_side=?", (ss,)).fetchone()
    finally:
        con.close()
    return json.loads(row[0] or "{}") if row else {}


def _run(ss, extra):
    os.environ["V12_ALLOW_STALE_NPZ"] = "1"  # mechanics test on the local NPZ, never a result
    from tools.opt import v12_pilot as P

    prep = P.prepare_batch(ss, 30)
    res = P.evaluate_prepared_sanitized(prep, {**_published(ss), **extra}, 30, include_ledger=True)
    led = res.get("ledger") or []
    n_u = sum(1 for t in led if str(t.get("exit_reason") or "").startswith("UNCOND_DC"))
    return hashlib.md5(json.dumps(led, sort_keys=True, default=str).encode()).hexdigest(), n_u


@pytest.mark.parametrize("ss", ["CLX_LONG", "EGLDUSDT_SHORT"])
def test_engine_default_identical_and_non_default_binds(ss):
    sym = ss.rsplit("_", 1)[0]
    if not (ROOT / "backtest_v8" / "indicators" / f"{sym}.npz").exists():
        pytest.skip(f"no local NPZ for {sym}")
    base, n_base = _run(ss, {})
    if n_base == 0:
        pytest.skip(f"{ss}: no UNCOND_DC exit on this NPZ window")
    assert _run(ss, {KEY: 0.25})[0] == base
    changed = [v for v in (0.10, 0.40, 0.60) if _run(ss, {KEY: v})[0] != base]
    assert changed, f"{ss}: no non-default buffer changed the ledger"
