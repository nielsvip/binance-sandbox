"""USER 2026-10-22 ERASE: WIN_TRAIL_EROSION_PCT (fixed-% trailing exit) and DELTA_MAX_HOLD_BARS (time exit) are gone.

Fields gone from every config surface, ladder legs gone from simulate_one, twins gone, and a set that still carries the
erased keys produces the SAME ledger as a set without them (engine ignores them = byte-identical to the 0/OFF default).
"""
import ast
import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ERASED = ("WIN_TRAIL_EROSION_PCT", "DELTA_MAX_HOLD_BARS")


def _class_fields(path, cls):
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    raise AssertionError(f"{cls} not found in {path}")


@pytest.mark.parametrize("path,cls", [("config.py", "Config"), ("config_tradier.py", "TradierConfig"), ("v12_quick_engine.py", "QuickConfig")])
def test_fields_gone(path, cls):
    fields = _class_fields(path, cls)
    for k in ERASED:
        assert k not in fields, f"{k} still a {cls} field"


def test_quickconfig_runtime_has_no_erased_field():
    import v12_quick_engine as V

    names = {f.name for f in dataclasses.fields(V.QuickConfig)}
    assert not names & set(ERASED)
    assert not set(ERASED) & set(V.AUTO_WIRED_PARAMS)


@pytest.mark.parametrize("path", ["v12_quick_engine.py", "ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py", "vec_decisions/twin_p0_crypto_a.py", "vec_decisions/twin_p0_crypto_b.py"])
def test_no_reads_left(path):
    src = (ROOT / path).read_text()
    for k in ERASED:
        assert f'"{k}"' not in src and f"'{k}'" not in src, f"{path} still reads {k}"
    assert '"DELTA_MAX_HOLD"' not in src and '"WIN_TRAIL_EROSION"' not in src


def test_twin_registries_dropped():
    from vec_decisions import twin_orange_ports_b as T

    assert "WIN_TRAIL_EROSION_PCT" not in T.FILTERS
    assert "WIN_TRAIL_EROSION_PCT" not in T.DEFAULTS and "WIN_TRAIL_EROSION_PCT" not in T.STATUS


def _run(ss, extra):
    os.environ["V12_ALLOW_STALE_NPZ"] = "1"  # mechanics test on the local NPZ, never a result
    from tools.opt import v12_pilot as P

    prep = P.prepare_batch(ss, 30)
    res = P.evaluate_prepared_sanitized(prep, dict(extra), 30, include_ledger=True)
    led = res.get("ledger") or []
    return hashlib.md5(json.dumps(led, sort_keys=True, default=str).encode()).hexdigest(), led


@pytest.mark.parametrize("ss", ["CLX_LONG", "SNDK_SHORT"])
def test_erased_keys_are_ignored_by_engine(ss):
    sym = ss.rsplit("_", 1)[0]
    if not (ROOT / "backtest_v8" / "indicators" / f"{sym}.npz").exists():
        pytest.skip(f"no local NPZ for {sym}")
    base, led = _run(ss, {})
    if not led:
        pytest.skip(f"{ss}: empty ledger on this NPZ")
    for extra in ({"WIN_TRAIL_EROSION_PCT": 0.5}, {"DELTA_MAX_HOLD_BARS": 20}):
        md5, led2 = _run(ss, extra)
        assert md5 == base, f"{ss} {extra} changed the ledger"
        assert not any(str(t.get("exit_reason") or t.get("reason")) in ("WIN_TRAIL_EROSION", "DELTA_MAX_HOLD") for t in led2)
