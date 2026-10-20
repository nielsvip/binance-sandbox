"""V18 memory candidate (2026-10-20 board memory profile): V18_COMPACT_KEYS (16-byte blake2b eval-cache keys instead of the
~82 KB 851-pair tuple), V18_MEM_FREE (7D/365D slices dropped after the pinpoint round; a later caller re-prepares), and
V18_MUST_ORDER (linknet pair_with_context ordering of the MUST set; ordering only). All default OFF."""
import gzip
import json
import os
import sys
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def _tuple(d):
    return tuple(sorted((k, str(v)) for k, v in d.items()))


def test_compact_digest_is_injective_on_distinct_sets_and_stable():
    base = {f"K{i:04d}": (i % 3 == 0) if i % 2 else i / 7 for i in range(851)}
    seen = {}
    for i in range(851):
        d = dict(base)
        d[f"K{i:04d}"] = "X"
        seen[V.compact_digest(_tuple(d))] = i
    assert len(seen) == 851
    assert V.compact_digest(_tuple(base)) == V.compact_digest(_tuple(dict(reversed(list(base.items())))))
    assert len(V.compact_digest(_tuple(base))) == 16


def test_compact_key_memory():
    d = {f"KEY_NUMBER_{i:04d}_SOME_SWITCH_NAME": (i % 3 == 0) if i % 2 else float(i) / 7 for i in range(851)}
    tracemalloc.start()
    full = [(_tuple(d), 30, 1) for _ in range(200)]
    a = tracemalloc.get_traced_memory()[0]
    del full
    tracemalloc.stop()
    tracemalloc.start()
    small = [(V.compact_digest(_tuple(d)), 30, 1) for _ in range(200)]
    b = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    assert a / 200 > 50_000 and b / 200 < 1_000, (a / 200, b / 200)
    assert small


def test_order_must_keeps_the_set():
    items = ["A=1", "B=2@F=1", "C=3", "D=4"]
    out = V.order_must(items, {"C=3": 0, "B=2@F=1": 1})
    assert out == ["C=3", "B=2@F=1", "A=1", "D=4"] and sorted(out) == sorted(items)
    assert V.order_must(items, {}) == items


def test_load_must_order(tmp_path, monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    d = {"meta": {"cat_side": "CRYPTO_LONG"}, "items": [{"key": "A=1", "filter": None}, {"key": "B=2", "filter": "F=1"}], "per_sym": {"ME_LONG": {"ranked": [[1, 0.9, 0.1, 0.1], [0, 0.5, 0.1, 0.1]]}}}
    with gzip.open(tmp_path / "linknet_pair_with_context_CRYPTO_LONG.json.gz", "wt") as fh:
        json.dump(d, fh)
    m, why = V.load_must_order("CRYPTO_LONG", "ME_LONG")
    assert m == {"B=2@F=1": 0, "A=1": 1} and why.startswith("ok")
    assert V.load_must_order("CRYPTO_LONG", "OTHER_LONG")[0] == {}
    d["meta"]["cat_side"] = "CRYPTO_SHORT"
    with gzip.open(tmp_path / "linknet_pair_with_context_CRYPTO_LONG.json.gz", "wt") as fh:
        json.dump(d, fh)
    assert "MISMATCH" in V.load_must_order("CRYPTO_LONG", "ME_LONG")[1]


def test_pilot_wiring_pins():
    assert '_v18_compact_ck = bool(_V18P is not None and _V18P.flag_on("V18_COMPACT_KEYS"))' in SRC
    assert "return (_V18P.compact_digest(_t), args.window_days, id(prepared))" in SRC
    assert "return (_t, args.window_days, id(prepared))" in SRC  # OFF = the original key exactly
    assert '_V18P.flag_on("V18_MEM_FREE")' in SRC and '_v17_rg["prep365"] = None' in SRC
    assert "items = _V18P.order_must(items, _mo)" in SRC
