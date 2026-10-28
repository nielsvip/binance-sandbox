"""D (2026-10-27): promotion must hold on the unseen confirm window. Pure module tests + source contracts on v15_pilot.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import v15_holdout_promotion as HP  # noqa: E402


def _scorer(gains, calls=None):
    def score(sets):
        if calls is not None:
            calls.append(sorted(sets))
        return {k: ({"gain_pct": gains[k], "valid": True} if gains.get(k) is not None else {"valid": False}) for k in sets}
    return score


def test_changed_and_revert():
    final = {"A": 1, "B": 2, "C": 3}
    start = {"A": 1, "B": 5, "D": 9}
    assert HP.changed_switches(final, start) == ["B", "C", "D"]
    r = HP.revert(final, start, ["B", "C", "D"])
    assert r == {"A": 1, "B": 5, "D": 9}


def test_holds_only_above_floor_and_prunes():
    final = {"A": 1, "B": 2, "C": 3}
    start = {"A": 0}
    gains = {"final": 5.0, "start": 4.0, "loo:A": 4.0, "loo:B": 4.7, "loo:C": 5.2, "pruned": 4.9}
    calls = []
    r = HP.confirm_promotions(final, start, _scorer(gains, calls), floor=0.48)
    assert r["kept"] == ["A"]  # 5.0 - 4.0 = 1.0 > 0.48
    assert sorted(r["dropped"]) == ["B", "C"]  # 0.3 inside the floor; -0.2 hurts on the confirm window
    assert r["pruned"] == {"A": 1}
    assert r["g_pruned"] == 4.9 and r["g_final"] == 5.0
    assert calls[-1] == ["pruned"]


def test_nothing_dropped_keeps_final_and_no_extra_eval():
    final, start = {"A": 1}, {}
    calls = []
    r = HP.confirm_promotions(final, start, _scorer({"final": 3.0, "start": 1.0, "loo:A": 1.0}, calls), floor=0.48)
    assert r["dropped"] == [] and r["pruned"] == final and r["g_pruned"] == 3.0 and len(calls) == 1


def test_unscored_is_kept_never_guessed():
    final, start = {"A": 1, "B": 2}, {}
    r = HP.confirm_promotions(final, start, _scorer({"final": 3.0, "start": 1.0, "loo:A": None, "loo:B": 1.0}), floor=0.48)
    assert "A" in r["kept"] and r["unscored"] == ["A"] and r["dropped"] == []
    r2 = HP.confirm_promotions(final, start, _scorer({"final": None, "start": 1.0, "loo:A": 1.0, "loo:B": 1.0}), floor=0.48)
    assert r2["dropped"] == [] and sorted(r2["unscored"]) == ["A", "B"]


def test_cap_books_unscored():
    final = {f"S{i}": i for i in range(5)}
    g = {"final": 2.0, "start": 0.0, **{f"loo:S{i}": 0.0 for i in range(5)}}
    r = HP.confirm_promotions(final, {}, _scorer(g), floor=0.48, max_switches=3)
    assert r["capped"] and len(r["kept"]) == 5 and len(r["unscored"]) == 2


def test_expectation_order():
    assert HP.expectation({"forward": {"gain_pct": 1.5}, "holdout_confirm": {"g_pruned": 9.0}}) == (1.5, "forward_out_of_sample")
    assert HP.expectation({"holdout_confirm": {"g_pruned": 2.0, "confirm_days": 7.0}}) == (2.0, "holdout_confirm_7.0d")
    assert HP.expectation({"final_gain": 40.0}) == (None, "none")


def test_pilot_wiring_contract():
    src = (ROOT / "v15_pilot.py").read_text()
    assert '_HOLDOUT_PROMOTE = os.environ.get("V15_HOLDOUT_PROMOTE", "1") == "1"' in src  # operator order 2026-10-28: default ON
    assert 'if (_HOLDOUT["days"] <= 0 and not _HOLDOUT.get("confirm_days")) or _HOLDOUT["installed"]:' in src
    assert 'if _HOLDOUT.get("installed") and _HOLDOUT["days"] > 0:' in src  # gate OOS scoring only with the gate holdout
    assert 'if int(getattr(args, "window_days", 30) or 30) != 30:' in src
    assert '_HOLDOUT["cut"] = holdout_cut(last, _HOLDOUT["days"] + _HOLDOUT.get("confirm_days", 0.0))' in src
    assert "def holdout_confirm_score(" in src
    # the confirm step runs BEFORE the fresh-final / gain-truth / publish block, so the published set and name are the pruned set's
    i_hc, i_ff, i_pub = src.index("_HP.confirm_promotions("), src.index("_fresh_final = _eps_ff(prepared"), src.index("_final_name = _final_matrix_name(")
    assert i_hc < i_ff < i_pub
    assert '"promotable_estimate_basis": "forward_out_of_sample" if fwd_gain is not None else ("holdout_confirm"' in src


def test_honest_headline_reads_holdout_confirm(monkeypatch):
    monkeypatch.setenv("V15_PROMOTION_FLOOR_PP", "0.48")
    import importlib
    try:
        P = importlib.import_module("v15_pilot")
    except Exception as e:  # the pilot needs the full tree; the source contract above still pins the wiring
        import pytest
        pytest.skip(f"v15_pilot import unavailable here: {e}")
    h = P.honest_headline({"baseline_gain": 2.0, "final_gain": 14.0, "done": {}, "holdout_confirm": {"g_pruned": 1.25}})
    assert h["promotable_estimate_pct"] == 1.25 and h["promotable_estimate_basis"] == "holdout_confirm"
    h2 = P.honest_headline({"baseline_gain": 2.0, "final_gain": 14.0, "done": {}})
    assert h2["promotable_estimate_pct"] == 2.0 and h2["promotable_estimate_basis"] == "baseline_only"


def test_tier_and_metrics_contract():
    src = (ROOT / "v15_pilot.py").read_text()
    assert 'if _HOLDOUT_PROMOTE and _hl and _hl.get("promotable_estimate_pct") is not None:' in src
    assert "_wt = _wbt(new_symside, _g_tier," in src
    assert '_set_metric("HOLDOUT_DROPPED", ", ".join(progress["holdout_confirm"].get("dropped") or []) or "none")' in src


def test_floor_on_confirm_contract():
    src = (ROOT / "v15_pilot.py").read_text()
    i_hc = src.index("_HP.confirm_promotions(")
    i_fl = src.index('_fl_hc = progress.get("holdout_confirm") if (_HOLDOUT_PROMOTE')
    i_pub = src.index("_final_name = _final_matrix_name(")
    assert i_hc < i_fl < i_pub  # the floor runs after the confirm step and before the publish
    assert '_fl_conf = holdout_confirm_score(new_symside, _fl_alt, defaults)' in src  # judged on the unseen confirm window
    assert 'progress["headline"] = honest_headline(progress, new_symside)' in src[i_fl:i_pub]  # spec path writes the headline


def test_invalid_but_traded_counts_zero_trades_unconfirmable():
    final, start = {"A": 1}, {}
    def score(sets):
        out = {}
        for k in sets:
            out[k] = {"gain_pct": {"final": 1.0, "start": 0.2, "loo:A": 0.1}.get(k, 0.5), "valid": False, "trades": 3}
        return out
    r = HP.confirm_promotions(final, start, score, floor=0.48)
    assert r["kept"] == ["A"] and r["unscored"] == [] and not r["unconfirmable"]
    def score0(sets):
        return {k: {"gain_pct": 0.0, "valid": False, "trades": 0} for k in sets}
    r0 = HP.confirm_promotions(final, start, score0, floor=0.48)
    assert r0["unconfirmable"] and r0["unscored"] == ["A"] and r0["dropped"] == []
