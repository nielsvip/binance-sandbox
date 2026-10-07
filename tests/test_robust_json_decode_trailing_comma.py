"""Regression: trailing comma in symbols JSON must not produce empty set.

Root cause 2026-09-15: symbols_flz.json had a trailing comma before the closing
bracket (',\\n]'). _robust_json_decode used orjson.loads directly which rejects
trailing commas, returned None, and _get_symbols() converted that to set() —
so the entire flz trading universe became empty. FLZ then stopped trading all
sym_sides except positions that already existed (BTC appeared to trade via the
has_existing_position bypass).

Fix: _robust_json_decode now strips ',]' / ',}' before retrying, so a single
trailing comma is tolerated at any level. This test covers the fix and the
original valid-file path.
"""
import importlib.util
import pathlib
import re
import sys

import orjson
import json as stdlib_json

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_robust():
    # Load the actual function from ez_manage without importing the whole module
    src = (ROOT / "ez_manage.py").read_text()
    # Extract _robust_json_decode source by exec into a clean dict
    ns = {"orjson": orjson, "json": stdlib_json, "re": re}
    # Find the method and dedent it into a plain function for testing
    import ast
    import textwrap
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_robust_json_decode":
            fn_src = ast.get_source_segment(src, node)
            # Method has (self, content_bytes) — rewrite to function(content_bytes)
            fn_src = fn_src.replace("def _robust_json_decode(self, content_bytes):", "def _robust_json_decode(content_bytes):")
            fn_src = textwrap.dedent(fn_src)
            exec(fn_src, ns)
            return ns["_robust_json_decode"]
    raise AssertionError("_robust_json_decode not found in ez_manage.py")


def test_symbols_flz_parses_as_valid_json():
    data = orjson.loads((ROOT / "symbols_flz.json").read_bytes())
    assert isinstance(data, list)
    assert len(data) == 13
    assert "BTCUSDC" in data
    # Must not have trailing comma artefact
    raw = (ROOT / "symbols_flz.json").read_text()
    assert not raw.strip().endswith(",]")


def test_robust_decode_tolerates_trailing_comma_list():
    robust = _load_robust()
    assert robust(b'["BTCUSDC", "ETHUSDC",]') == ["BTCUSDC", "ETHUSDC"]
    assert robust(b'["BTCUSDC",]') == ["BTCUSDC"]
    assert robust(b'["a", "b", ]') == ["a", "b"]


def test_robust_decode_tolerates_trailing_comma_object():
    robust = _load_robust()
    assert robust(b'{"a": 1,}') == {"a": 1}
    assert robust(b'{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_robust_decode_preserves_valid_json():
    robust = _load_robust()
    assert robust(b'["BTCUSDC"]') == ["BTCUSDC"]
    assert robust(b'{"x": 1}') == {"x": 1}


def test_robust_decode_old_corrupted_symbols_flz():
    robust = _load_robust()
    old = b'[\n  "BTCUSDC",\n  "BTCDOMUSDT",\n  "ETHUSDC",\n  "BNBUSDC",\n  "SOLUSDC",\n  "XRPUSDC",\n  "DOGEUSDC",\n  "ZECUSDC",\n  "GRAMUSDT",\n  "WLDUSDC",\n  "DASHUSDT",\n  "HYPEUSDT",\n  "100PEPEUSDC",\n]\n'
    result = robust(old)
    assert result == [
        "BTCUSDC",
        "BTCDOMUSDT",
        "ETHUSDC",
        "BNBUSDC",
        "SOLUSDC",
        "XRPUSDC",
        "DOGEUSDC",
        "ZECUSDC",
        "GRAMUSDT",
        "WLDUSDC",
        "DASHUSDT",
        "HYPEUSDT",
        "100PEPEUSDC",
    ]


def test_robust_decode_current_file_via_robust():
    robust = _load_robust()
    raw = (ROOT / "symbols_flz.json").read_bytes()
    result = robust(raw)
    assert result is not None
    assert len(result) == 13
