"""Per-cat_side shard filter (USER 2026-10-25): S1 crypto_short, S2 crypto_long,
S5 stock_long, S6 stock_short. Default (env unset) serves ALL — today's behavior.
"""
import importlib
import os

import tools.vec_shard as vs

CASES = [
    ("BTCUSDC_LONG", "crypto_long"),
    ("ETHUSDT_SHORT", "crypto_short"),
    ("SOLUSDC_LONG", "crypto_long"),
    ("AAPL_LONG", "stock_long"),
    ("BABA_SHORT", "stock_short"),
    ("AG_SHORT", "stock_short"),
]


def _with_shard(val, fn):
    old = os.environ.get("VEC_SHARD")
    try:
        if val is None:
            os.environ.pop("VEC_SHARD", None)
        else:
            os.environ["VEC_SHARD"] = val
        importlib.reload(vs)
        return fn()
    finally:
        if old is None:
            os.environ.pop("VEC_SHARD", None)
        else:
            os.environ["VEC_SHARD"] = old
        importlib.reload(vs)


def test_default_serves_all():
    assert _with_shard(None, lambda: [vs.serves(ss) for ss, _ in CASES]) == [True] * len(CASES)
    assert _with_shard("all", lambda: vs.active_shard()) == "all"


def test_each_shard_serves_only_its_cat_side():
    for shard in ("crypto_long", "crypto_short", "stock_long", "stock_short"):
        got = _with_shard(shard, lambda: {ss: vs.serves(ss) for ss, _ in CASES})
        for ss, cat in CASES:
            assert got[ss] == (cat == shard), (shard, ss)


def test_unknown_shard_fails_open():
    assert _with_shard("crypto_mid", lambda: vs.active_shard()) == "all"
    assert _with_shard("crypto_mid", lambda: vs.serves("BTCUSDC_LONG")) is True


def test_load_sets_unions_sets_file_live_keys():
    import json
    import tools.vec_live_executor as le
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        vd = {"BTCUSDC_LONG": {"mode": "live", "account": ["men"]}}
        st = {"BTCUSDC_LONG": {"mode": "live", "account": ["men"], "overrides": {"A": 1}},
              "AAPL_LONG": {"mode": "live", "account": ["trb"], "overrides": {"B": 2}},
              "XXX_LONG": {"mode": "shadow", "account": ["trb"], "overrides": {"C": 3}}}
        open(os.path.join(td, "vec_driven.json"), "w").write(json.dumps(vd))
        open(os.path.join(td, "vec_driven_sets.json"), "w").write(json.dumps(st))
        old_vd, old_sets, old_cands = le.VEC_DRIVEN, le.SETS, le.CANDIDATES
        from pathlib import Path
        try:
            le.VEC_DRIVEN, le.SETS, le.CANDIDATES = Path(td) / "vec_driven.json", Path(td) / "vec_driven_sets.json", Path(td) / "candidates.json"
            out = le.load_sets()
        finally:
            le.VEC_DRIVEN, le.SETS, le.CANDIDATES = old_vd, old_sets, old_cands
    assert out["BTCUSDC_LONG"][1] == {"A": 1}
    assert out["AAPL_LONG"] == ({"mode": "live", "account": ["trb"]}, {"B": 2})
    assert "XXX_LONG" not in out


def test_venue_matches_lane_authority():
    from tools.opt import evaluate_v12 as e
    for ss, cat in CASES:
        sym = ss.rsplit("_", 1)[0]
        want_crypto = cat.startswith("crypto")
        assert vs.is_crypto_sym(sym) == e.is_crypto(sym) == want_crypto, ss
    assert vs.cat_side_of("MALFORMED") is None
    assert vs.cat_side_of("BTCUSDC_LONG") == "crypto_long"
