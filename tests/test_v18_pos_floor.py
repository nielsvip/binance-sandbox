"""pin tests for V18_POS_FLOOR_PP (2026-10-27): positivity of gain deltas feeding p_pos / heads / forward / ema / observe beats the
promotion noise floor. Floor 0 must reproduce the historic `> 1e-9` labels exactly; floor 0.48 turns a +0.3 pp delta into a negative."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import v18_brain as B  # noqa: E402
from test_v18_brain import _board_lines  # noqa: E402

OLD = ROOT / "backups" / "before_pos_floor_202610272046_v18_brain.py"


def _set_floor(monkeypatch, floor):
    monkeypatch.setattr(B, "POS_FLOOR", float(floor))
    monkeypatch.setattr(B, "POS_THR", max(B.EPS, float(floor)))


def _spill(tmp_path, mod):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    lines = []
    for day, alt in ((14, False), (15, True), (16, False), (17, False)):
        lines += _board_lines("BTCUSDT_LONG", day, 10.0, alt_pos=alt)
    lines.append({"ts": "2026-10-17T05:00:00Z", "sym_side": "BTCUSDT_LONG", "label": "FORWARD", "key": "EXIT_VELOCITY_WT_MIN_TFS=2", "d_gain": 0.3, "d_trades": 1})
    (logs / "BTCUSDT_LONG_jump.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    spill = tmp_path / "spill"
    assert mod.main(["extract", "--source", "logs", "--from", str(logs / "*.jsonl"), "--host", "t", "--spill-dir", str(spill)]) == 0
    return spill


def _train(tmp_path, mod, tag):
    spill = tmp_path / "spill"
    state = tmp_path / f"state_{tag}.pkl"
    out = tmp_path / f"brain_{tag}"
    assert mod.main(["train", "--spill-dir", str(spill), "--cut", "2026-10-01T00:00:00Z", "--state", str(state), "--out-dir", str(out),
                     "--template-cache", str(tmp_path / "none")]) == 0
    st = mod.load_state(state)
    return st, json.loads((out / "v18_brain_CRYPTO_LONG.json").read_text())


def _labels(st):
    sb = st["brains"]["CRYPTO_LONG"]
    keep = ("M_pos", "M_labels", "B_binds", "B_labels", "fwd_pos", "fwd_labels", "forward_lines", "evidence_naked", "evidence_cells")
    return {k: sb["c"].get(k) for k in keep}, sb["capture"], sb["hn"]


def test_floor_zero_is_default_and_eps():
    assert B.POS_FLOOR == 0.0 and B.POS_THR == B.EPS


@pytest.mark.skipif(not OLD.exists(), reason="pre-floor backup not present")
def test_floor_zero_equals_old_labels(tmp_path, monkeypatch):
    _set_floor(monkeypatch, 0.0)
    spec = importlib.util.spec_from_file_location("v18_brain_prefloor", OLD)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    _spill(tmp_path, B)
    st_new, blob_new = _train(tmp_path, B, "new")
    st_old, blob_old = _train(tmp_path, old, "old")
    assert _labels(st_new) == _labels(st_old)
    assert blob_new["heads"] == blob_old["heads"]
    assert blob_new["cs"] == blob_old["cs"] and blob_new["ema"] == blob_old["ema"]
    assert blob_new["_meta"]["pos_floor_pp"] == 0.0 and st_new["meta"]["pos_floor_pp"] == 0.0


def test_floor_048_flips_03_to_negative(tmp_path, monkeypatch):
    for floor, want in ((0.0, 1), (0.48, 0)):
        _set_floor(monkeypatch, floor)
        g = B.Graph("CRYPTO_LONG")
        i = g.idx(0, "R:X=1")
        g.add_eval(i, 0.3, True, 1.0, [], 1.0, 0)
        assert g.E[i, B.C["pos"]] == want and g.E[i, B.C["neg"]] == 0  # below-floor positives are "not positive", never "negative" sums
        g.add_eval(i, 0.6, True, 1.0, [], 1.0, 0)
        assert g.E[i, B.C["pos"]] == want + 1
        cb = B.CatBrain("CRYPTO_LONG", B.Buckets(), "2026-10-01T00:00:00Z")
        cb._forward_line({"e": "forward", "s": "BTCUSDT_LONG", "key": "X=1", "d_gain": 0.3})
        assert cb.g.E[cb.g.idx(0, "R:X=1"), B.C["fwd_pos"]] == want
        pb = B.PilotBrainV18.__new__(B.PilotBrainV18)
        pb.sym_side, pb.pos_thr = "BTCUSDT_LONG", max(B.EPS, floor)
        pb.ema = __import__("collections").defaultdict(lambda: 0.02)
        pb.T = type("T", (), {"sym_t": {}})()
        pb.observe("EXIT_VELOCITY", "X=1", 0.3)
        assert pb.T.sym_t["R:X=1"] == [float(want), 1.0]


def test_floor_048_lowers_trained_positives(tmp_path, monkeypatch):
    _set_floor(monkeypatch, 0.0)
    _spill(tmp_path, B)
    st0, blob0 = _train(tmp_path, B, "f0")
    _set_floor(monkeypatch, 0.48)
    st1, blob1 = _train(tmp_path, B, "f48")
    c0, c1 = st0["brains"]["CRYPTO_LONG"]["c"], st1["brains"]["CRYPTO_LONG"]["c"]
    assert c1["M_labels"] == c0["M_labels"] and c1["M_pos"] < c0["M_pos"]
    assert c1["B_binds"] == c0["B_binds"]  # binding (noop) semantics stay on EPS
    assert blob1["_meta"]["pos_floor_pp"] == 0.48 and st1["meta"]["pos_floor_pp"] == 0.48
