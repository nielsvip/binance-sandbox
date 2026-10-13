"""tests for tools/v17_combo_engine.py — space validity, library append/query/merge, successive halving with a fake evaluator,
API contract. Run: python -m pytest -q tests/test_v17_combo_engine.py"""
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import v17_combo_engine as CE  # noqa: E402
from tools import switch_types as ST  # noqa: E402


@pytest.fixture(scope="module")
def space():
    return CE.ComboSpace("CRYPTO_LONG")


def test_space_values_typed_and_unblocked(space):
    n = 0
    for k, vals in space.values.items():
        for v in vals:
            if k in getattr(CE._Pilot.m(), "COMPOSITE_SWITCHES", {}):
                continue
            ok, val, why = ST.coerce(k, v, "CRYPTO", strict_unknown=True)
            assert ok, (k, v, why)
            assert CE.blocked_reason({k: v}) == "", (k, v)
            n += 1
    assert n > 1000
    assert space.size()["filter_cells"] > 100


def test_numeric_neighbours_inside_span(space):
    for k, vals in space.values.items():
        nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(nums) >= 3 and min(nums) > 0:
            base = sorted(nums)[len(nums) // 2]
            for nv in space.numeric_neighbours(k, base):
                assert min(nums) <= float(nv) <= max(nums)
                assert not CE.same_val(nv, base)
            return
    pytest.skip("no numeric key")


def test_generate_valid_unique_effective(space):
    base = {}
    cands, st = CE.generate(space, "AVAXUSDC_LONG", base, {"missed_moves": {"n": 3, "size": 5.0}}, {}, None, random.Random(3), 6000)
    assert len(cands) > 3000
    keys = set()
    for c in cands:
        ck = CE.combo_key(c["delta"])
        assert ck not in keys
        keys.add(ck)
        assert CE.effective_delta(base, c["delta"]) == c["delta"]
        assert len([k for k in c["delta"] if k != "FILTER_EXEMPTIONS"]) <= 4
        for k, v in c["delta"].items():
            ok, _v, why = ST.coerce(k, v, "CRYPTO", strict_unknown=True)
            assert ok, (k, v, why)
        assert CE.blocked_reason(c["delta"]) == ""
    assert st["by_origin"].get("explore", 0) > 0
    assert any(len(c["delta"]) >= 2 for c in cands)


def test_schedule_quota_interleaves():
    cands = [{"origin": o, "score": float(i), "delta": {o: i}} for o in ("explore", "pair", "lib_neighbour") for i in range(100)]
    order = CE.schedule(cands, explore_frac=0.15, quota={"explore": 0.15, "pair": 0.35, "lib_neighbour": 0.5})
    first = order[:100]
    share = sum(1 for c in first if c["origin"] == "explore") / 100.0
    assert 0.08 <= share <= 0.22
    assert first[0]["origin"] == "lib_neighbour" and first[0]["score"] == 99.0


def test_coherence_rules(tmp_path, monkeypatch):
    sem = {"keys": {"X_ENTRY_TF": {"role": "trigger", "tf": "var(X_ENTRY_TF)"}, "X_MAX_BARS_AGO": {"role": "window", "tf": "4h"}, "Y_WINDOW_BARS": {"role": "window", "tf": "var"}, "X_CONFIRM_TF": {"role": "confirm"}}, "coherence_rules": []}
    p = tmp_path / "sem.json"
    p.write_text(json.dumps(sem))
    monkeypatch.setattr(CE, "TF_SEM_PATH", p)
    CE._SEM.update(memo=None, doc=None)
    assert CE.coherence_reason({"BB_BOUNCE_ENTRY_TF": "D"}, {}).startswith("C4")
    assert CE.coherence_reason({"BB_BOUNCE_ENTRY_TF": "OFF"}, {}) == ""
    assert CE.coherence_reason({"WT_15M_BOUNCE_MAX_BARS_AGO": 3}, {}).startswith("C9")
    assert CE.coherence_reason({"X_MAX_BARS_AGO": 40}, {}).startswith("C5")  # 4h span 16
    assert CE.coherence_reason({"X_MAX_BARS_AGO": 12}, {}) == ""
    assert CE.coherence_reason({"Y_WINDOW_BARS": 30}, {"X_ENTRY_TF": "D"}, venue="STOCKS").startswith("C5")  # stocks D = 26
    assert CE.coherence_reason({"Y_WINDOW_BARS": 30}, {"X_ENTRY_TF": "D"}, venue="CRYPTO") == ""
    w = CE.coherence_warnings({"BB_BREAKOUT_ENTRY_TF": "D"}, {"BB_BREAKOUT_ENTRY_TF": "D", "COOLDOWN_BARS": 0}, "CRYPTO")
    assert w == ["C1_UNBOUNDED_REOPEN", "C2_NO_LTF_CONFIRM"]
    assert CE.coherence_warnings({}, {"BB_BREAKOUT_ENTRY_TF": "D", "COOLDOWN_BARS": 6, "DC_BREAK_FILTER_TF": "15m"}, "CRYPTO") == []
    f = CE.tf_features({"X_ENTRY_TF": "D", "X_CONFIRM_TF": "1h", "X_MAX_BARS_AGO": 30})
    assert f["htf"] == "D" and f["ltf_confirm"] == "1h" and f["window_bars"] == 30.0
    CE._SEM.update(memo=None, doc=None)


def test_tf_coherent_variants_generated(space):
    base = {"BB_BREAKOUT_ENTRY_TF": "OFF", "COOLDOWN_BARS": 0}
    cands, st = CE.generate(space, "AVAXUSDC_LONG", base, {}, {}, None, random.Random(2), 8000)
    tfc = [c for c in cands if c["origin"] == "tf_coherent"]
    assert tfc, st["by_origin"]
    assert all(any(k in c["delta"] for k in ("COOLDOWN_BARS",) + CE.C2_LTF_CONFIRMS) for c in tfc)
    assert not any("BB_BOUNCE_ENTRY_TF" in c["delta"] and c["delta"]["BB_BOUNCE_ENTRY_TF"] != "OFF" for c in cands)


def test_library_append_query_merge(tmp_path):
    lib = CE.ComboLibrary(tmp_path / "a.sqlite")
    b1 = lib.board("A_LONG|1", sym_side="AAAUSDT_LONG", cat_side="CRYPTO_LONG", side_net_pct=5.0, vol_bar_pct=0.5, chop=10.0, range_pct=12.0, bh=5.0, tim=40.0, trades=60, symptoms=["tim_low"])
    b2 = lib.board("B_LONG|1", sym_side="BBBUSDT_LONG", cat_side="CRYPTO_LONG", side_net_pct=-8.0, vol_bar_pct=1.5, chop=40.0, range_pct=30.0, bh=-8.0, tim=15.0, trades=200, symptoms=[])
    b3 = lib.board("C_LONG|1", sym_side="CCCUSDT_LONG", cat_side="CRYPTO_LONG", side_net_pct=4.5, vol_bar_pct=0.55, chop=11.0, range_pct=11.0, bh=4.0, tim=42.0, trades=55, symptoms=[])
    assert lib.board("A_LONG|1", sym_side="X") == b1
    lib.add_evidence(b1, {"K1": True}, "progress_done", 2.0, symptom="tim_low")
    lib.add_evidence(b1, {"K1": True}, "progress_done", 4.0)
    lib.add_evidence(b2, {"K2": "4h"}, "progress_done", 1.0)
    lib.add_evidence(b3, {"K3": 1.5, "K4": False}, "delta_log", 0.7)
    lib.commit()
    nn = lib.neighbours("CRYPTO_LONG", {"side_net_pct": 5.0, "vol_bar_pct": 0.5, "chop": 10.0, "range_pct": 12.0, "bh": 5.0, "tim": 40.0, "trades": 60}, k=2, exclude_sym="AAAUSDT_LONG")
    assert [b["sym_side"] for _d, b in nn] == ["CCCUSDT_LONG", "BBBUSDT_LONG"]
    pos = lib.positives([b1])
    assert pos[0]["combo"] == {"K1": True} and pos[0]["best_delta"] == 4.0 and abs(pos[0]["delta"] - 3.0) < 1e-9
    fx = lib.symptom_fixes("CRYPTO_LONG", "tim_low")
    assert fx and fx[0]["combo"] == {"K1": True}
    lib.close()
    lib2 = CE.ComboLibrary(tmp_path / "b.sqlite")
    bb = lib2.board("A_LONG|1", sym_side="AAAUSDT_LONG", cat_side="CRYPTO_LONG")
    lib2.add_evidence(bb, {"K9": "OFF"}, "progress_done", 0.3)
    lib2.close()
    info = CE.merge_library(tmp_path / "b.sqlite", [tmp_path / "a.sqlite"], log=lambda *_: None)
    assert info["boards"] == 3 and info["evidence"] == 4


class FakeEval:
    """deterministic stand-in for the engine: gain = 10 + bonuses of hidden winning settings; recent 7D slice penalises W2."""
    workers = 1

    def __init__(self, w1, w2, w3):
        self.w = (w1, w2, w3)
        self.n = 0
        self.cpu_s = 0.0

    def _has(self, ov, w):
        k, v = next(iter(w.items()))
        return k in ov and CE.same_val(ov[k], v)

    def __call__(self, items, deadline=None):
        out = {}
        for key, window, ov, ledger in items:
            self.n += 1
            h = [self._has(ov, w) for w in self.w]
            if window == 30:
                g = 10.0 + 1.0 * h[0] + 2.0 * h[1] + 0.5 * h[2]
                tl = [(i, i + 2, 0.1) for i in range(40)] + ([(100 + i, 103 + i, 0.2) for i in range(6)] if h[0] else []) + ([(200 + i, 202 + i, 0.3) for i in range(6)] if h[1] else []) + ([(300, 301, 0.5)] if h[2] else [])
            else:
                g = 1.0 + 0.2 * h[0] - 1.0 * h[1] + 0.1 * h[2]
                tl = []
            out[key] = ({"gain": g, "trades": 50, "tim": 40.0, "dd": 2.0, "wr": 55.0, "valid": True, "reason": "", "fp": str(h), "bh": 3.0, "pool_sharpe": 0.1, "trades_list": tl if ledger else None}, "")
        return out


def test_search_successive_halving_contract(space, tmp_path):
    base = {}
    cands, _st = CE.generate(space, "AVAXUSDC_LONG", base, {}, {}, None, random.Random(5), 3000)
    singles = [c["delta"] for c in cands if len(c["delta"]) == 1 and c["origin"] in ("symptom_path", "filter_path")]
    w1, w2, w3 = singles[0], singles[1], singles[2]
    assert len({next(iter(w)) for w in (w1, w2, w3)}) == 3
    fake = FakeEval(w1, w2, w3)
    lib = CE.ComboLibrary(tmp_path / "lib.sqlite")
    st = {}
    ranked = CE.search({"npz_prepared": {}}, "AVAXUSDC_LONG", base, symptoms={}, budget_s=120, workers=1, stats_out=st, evaluator=fake, library=lib, seed=7, log=lambda *_: None, max_gen=3000)
    assert ranked and ranked[0]["kind"] == "joint"
    j = ranked[0]
    for k in ("overrides_delta", "gain", "trades", "tim", "dd", "wr", "recent_segment_gain", "n_evals"):
        assert k in j
    assert abs(j["gain"] - 11.5) < 1e-9 and abs(j["delta"] - 1.5) < 1e-9  # w2 wins only on the old regime -> flagged, not adopted
    assert all(CE.same_val(j["overrides_delta"][next(iter(w))], next(iter(w.values()))) for w in (w1, w3))
    assert next(iter(w2)) not in j["overrides_delta"]
    assert st["verify"]["ok"] and st["verify"]["match"]
    has = lambda s, w: CE.same_val(s["overrides_delta"].get(next(iter(w))), next(iter(w.values())))  # noqa: E731
    assert [s for s in j["steps"] if has(s, w1)][0]["changed_trades"] >= 6
    w3_step = [s for s in j["steps"] if has(s, w3)][0]
    if len(w3_step["overrides_delta"]) == 1:
        assert "FEW_TRADES" in w3_step["flags"]  # one changed trade carries the whole delta
    w2_rows = [r for r in ranked if r["kind"] == "combo" and r["overrides_delta"] == w2]
    assert w2_rows and "OLD_REGIME_ONLY" in w2_rows[0]["flags"] and w2_rows[0]["recent_delta"] < 0
    combos = [r for r in ranked if r["kind"] == "combo"]
    assert combos and all(r["delta"] > 0 for r in combos)
    assert st["stage1"]["screened"] >= 100 and st["n_evals"] == fake.n
    info = lib.info()
    assert info["engine_evals"] >= st["stage1"]["screened"] - 5
    lib.close()


def test_search_adopt_old_regime_knob(space, tmp_path, monkeypatch):
    monkeypatch.setenv("V17_COMBO_ADOPT_OLD_REGIME", "1")
    cands, _st = CE.generate(space, "AVAXUSDC_LONG", {}, {}, {}, None, random.Random(5), 3000)
    singles = [c["delta"] for c in cands if len(c["delta"]) == 1 and c["origin"] in ("symptom_path", "filter_path")]
    st = {}
    ranked = CE.search({"npz_prepared": {}}, "AVAXUSDC_LONG", {}, symptoms={}, budget_s=120, workers=1, stats_out=st, evaluator=FakeEval(*singles[:3]), library=CE.ComboLibrary(tmp_path / "l.sqlite"), seed=7, log=lambda *_: None, max_gen=3000)
    assert ranked[0]["kind"] == "joint" and abs(ranked[0]["gain"] - 13.5) < 1e-9
    assert "OLD_REGIME_ONLY" in ranked[0]["flags"]


def test_search_no_lift_returns_no_joint(space, tmp_path):
    class Flat(FakeEval):
        def __call__(self, items, deadline=None):
            return {k: ({"gain": 5.0, "trades": 50, "tim": 40.0, "dd": 1.0, "wr": 50.0, "valid": True, "reason": "", "fp": "x", "bh": 1.0}, "") for k, *_ in items}
    st = {}
    ranked = CE.search({"npz_prepared": {}}, "AVAXUSDC_LONG", {}, symptoms={}, budget_s=20, workers=1, stats_out=st, evaluator=Flat({"a": 1}, {"b": 1}, {"c": 1}), library=CE.ComboLibrary(tmp_path / "l.sqlite"), seed=1, log=lambda *_: None, max_gen=800)
    assert all(r["kind"] != "joint" for r in ranked)
    assert st["lift"] is None


def test_regime_attribution_and_segments():
    ts = [1_700_000_000 + i * 900 for i in range(96 * 56)]  # 56 days of 15m bars
    close = [100 + (i * 0.01 if i < 96 * 28 else (96 * 28) * 0.01 - (i - 96 * 28) * 0.002) for i in range(len(ts))]
    segs = CE.regime_segments({"npz_prepared": {"timestamps": ts, "close": close}}, True, seg_days=14)
    assert len(segs) == 4 and segs[0][0] == 0 and segs[-1][1] == len(ts) - 1
    base = {"gain": 2.0, "trades_list": [(10, 20, 1.0, 1.0), (96 * 30, 96 * 30 + 5, 1.0, 1.0)]}
    cand = {"gain": 3.0, "trades_list": [(10, 20, 1.0, 1.0), (96 * 30, 96 * 30 + 5, 2.0, 2.0)]}
    attr = CE.regime_attribution(base, cand, segs)
    lab_late = segs[2][2]
    assert attr[lab_late]["d_pnl"] > 0
    assert abs(sum(a["cand_pnl"] for a in attr.values()) - 3.0) < 1e-6  # scaled to gain-pp units


class FakeEval365(FakeEval):
    """365D: w1 regresses on the long window (rejected by the default accept rule), w3 holds."""

    def __call__(self, items, deadline=None):
        out = super().__call__([it for it in items if it[1] != 365], deadline)
        for key, window, ov, ledger in items:
            if window != 365:
                continue
            self.n += 1
            h = [self._has(ov, w) for w in self.w]
            g = 20.0 - 3.0 * h[0] + 0.0 * h[1] + 1.0 * h[2]
            out[key] = ({"gain": g, "trades": 300, "tim": 40.0, "dd": 5.0 + 2.0 * h[0], "wr": 50.0, "valid": True, "reason": "", "fp": str(h), "bh": 1.0, "trades_list": [(i * 100, i * 100 + 5, 0.1, 0.1) for i in range(30)] + ([(5000, 5010, 1.0, 1.0)] if h[2] else [])}, "")
        return out


def test_search_dual_window_accept(space, tmp_path):
    cands, _st = CE.generate(space, "AVAXUSDC_LONG", {}, {}, {}, None, random.Random(5), 3000)
    singles = [c["delta"] for c in cands if len(c["delta"]) == 1 and c["origin"] in ("symptom_path", "filter_path")]
    w1, w2, w3 = singles[:3]
    ts = [1_700_000_000 + i * 900 for i in range(96 * 60)]
    p365 = {"npz_prepared": {"timestamps": ts, "close": [100 + (i % 500) * 0.01 for i in range(len(ts))]}}
    st = {}
    lib = CE.ComboLibrary(tmp_path / "l.sqlite")
    ranked = CE.search({"npz_prepared": {}}, "AVAXUSDC_LONG", {}, symptoms={}, budget_s=120, workers=1, stats_out=st, evaluator=FakeEval365(w1, w2, w3), library=lib, seed=7, log=lambda *_: None, max_gen=3000, windows=(30, 365), prepared_365=p365)
    j = ranked[0]
    assert j["kind"] == "joint" and next(iter(w1)) not in j["overrides_delta"]  # long-window regression rejected
    assert CE.same_val(j["overrides_delta"][next(iter(w3))], next(iter(w3.values())))
    assert j["w365"]["gain"] >= st["base365"]["gain"] and st["verify"]["ok"]
    assert j["regime_attr_365"]
    assert lib.db.execute("SELECT COUNT(*) FROM regime_evidence").fetchone()[0] > 0
    assert lib.db.execute("SELECT COUNT(*) FROM evidence WHERE window_days=365").fetchone()[0] > 0
    lib.close()
