"""V18 principle 12 (USER 2026-10-20 "give every indicator/filter combination a chance"): V18_FLOOR=1 gives every naked row
and every (switch=cand, filter=opt) cell of a cat_side a floor evaluation probability >= V18_FLOOR_PER_1000/1000 that
overrides every POLICY skip (prior verdicts, zero-formula, autopsy-inert, low-yield, brain, carry, possym, V17 sampling);
structural skips never change; cells the floor leaves skipped stay settled with their p (never hollow)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_flag_and_p(monkeypatch):
    monkeypatch.delenv("V18_FLOOR", raising=False)
    monkeypatch.delenv("V18_FLOOR_PER_1000", raising=False)
    assert V.floor_on() is False and V.floor_p() == 0.005
    monkeypatch.setenv("V18_FLOOR", "1")
    monkeypatch.setenv("V18_FLOOR_PER_1000", "50")
    assert V.floor_on() is True and V.floor_p() == 0.05
    monkeypatch.setenv("V18_FLOOR_PER_1000", "5000")
    assert V.floor_p() == 1.0


def test_draw_is_deterministic_uniform_and_rotates():
    a = V.floor_draw("CRYPTO_LONG", "X_LONG", "T", "A=1@F=2", "r1")
    assert a == V.floor_draw("CRYPTO_LONG", "X_LONG", "T", "A=1@F=2", "r1")
    assert 0.0 <= a < 1.0
    assert a != V.floor_draw("CRYPTO_LONG", "X_LONG", "T", "A=1@F=2", "r2")
    assert a != V.floor_draw("CRYPTO_LONG", "Y_LONG", "T", "A=1@F=2", "r1")
    n = 20000
    hits = sum(1 for i in range(n) if V.floor_draw("CRYPTO_LONG", "X_LONG", "T", f"k{i}", "r") < 0.005)
    assert 60 <= hits <= 145  # ~100 expected at 5/1000


def test_every_cell_rotates_in_over_rounds():
    # across rounds every cell gets its chance (coverage law): 2000 rounds at p=0.005 -> P(never) ~ 4.5e-5 per cell
    cells = [f"S=1@F={i}" for i in range(40)]
    seen = set()
    for r in range(2000):
        seen.update(c for c in cells if V.floor_draw("CRYPTO_LONG", "X_LONG", "T", c, f"r{r}") < 0.005)
    assert seen == set(cells)


def _keep(set_rows=False, cells=()):
    return lambda kind, h: set_rows if kind == "row" else (h in cells)


def test_structural_skip_never_changes():
    for reason in ("NO_CANDIDATE: x", "NOT_WIRED_VEC: x", "ABLATION_NEVER_ON: x", "NOT_IN_CONFIG: GLOBAL_ONLY", "MAXAUG_NEVER_ZERO", "TYPE: bad"):
        info = {"kind": "skip", "reason": reason, "hdrs": ["F=1"], "g": None}
        st = V.floor_apply(info, _keep(True, ("F=1",)), 1.0)
        assert info["kind"] == "skip" and info["reason"] == reason and st["rows_rescued"] == 0


def test_policy_skipped_row_rescued_by_row_draw():
    info = {"kind": "skip", "reason": "ZERO_FORMULA_ROW: pos_sym=0 in 30 syms", "hdrs": ["F=1", "G=2"], "g": None}
    st = V.floor_apply(info, _keep(True, ()), 0.005)
    assert info["kind"] == "eval" and info["reason"] == "" and "g" not in info
    assert info["hdrs"] == [] and info["coarse_deferred_filters"] == ["F=1", "G=2"]
    assert info["v18_floor"]["rescued_row"] and info["v18_floor"]["overrode"].startswith("ZERO_FORMULA_ROW")
    assert st == {"rows_seen": 1, "rows_policy_skipped": 1, "rows_rescued": 1, "cells_policy_skipped": 2, "cells_rescued": 0, "cells_settled": 2}


def test_policy_skipped_row_rescued_by_a_cell_draw_keeps_possym_pending_semantics():
    info = {"kind": "skip", "reason": "SKIPPED_SAMPLING(pos_sym=0)", "hdrs": ["F=1", "G=2"], "g": None}
    V.floor_apply(info, _keep(False, ("G=2",)), 0.005)
    assert info["kind"] == "eval" and info["hdrs"] == ["G=2"] and info["sampled_filters"] == ["F=1"]


def test_policy_skipped_row_kept_skipped_is_settled_with_p():
    info = {"kind": "skip", "reason": "CARRIED_DEAD: |naked|<eps", "hdrs": ["F=1"], "g": None}
    st = V.floor_apply(info, _keep(False, ()), 0.005)
    assert info["kind"] == "skip" and info["v18_floor"] == {"p": 0.005, "rescued_row": False, "rescued_cells": [], "settled_n": 1, "overrode": None}
    assert st["rows_policy_skipped"] == 1 and st["cells_settled"] == 1


def test_eval_row_cells_rescued_from_every_policy_list():
    info = {"kind": "eval", "reason": "", "hdrs": ["K=1"], "inert_filters": ["I=1"], "prior_skipped": ["P=1", "B=1"], "brain_skipped": ["B=1"], "coarse_deferred_filters": ["C=1"], "sampled_skip_p": {"C=1": 0.01}, "sampled_filters": ["S=1"], "zero_skipped": ["Z=1"]}
    st = V.floor_apply(info, _keep(False, ("I=1", "B=1", "C=1", "Z=1")), 0.005)
    assert info["hdrs"] == ["K=1", "I=1", "B=1", "C=1", "Z=1"]
    assert info["inert_filters"] == [] and info["prior_skipped"] == ["P=1"] and info["brain_skipped"] == []
    assert info["coarse_deferred_filters"] == [] and info["sampled_skip_p"] == {} and info["sampled_filters"] == ["S=1"] and info["zero_skipped"] == []
    assert st["cells_policy_skipped"] == 6 and st["cells_rescued"] == 4 and st["cells_settled"] == 2
    assert info["v18_floor"]["rescued_cells"] == ["I=1", "B=1", "C=1", "Z=1"] and info["v18_floor"]["settled_n"] == 2


def test_floor_add_totals():
    t = {}
    V.floor_add(t, {"rows_seen": 1, "cells_rescued": 2})
    V.floor_add(t, {"rows_seen": 1, "cells_rescued": 0})
    assert t == {"rows_seen": 2, "cells_rescued": 2}


def test_pilot_floor_wiring_pins():
    assert "_v18_floor_on = bool(_V18P is not None and _V18P.floor_on())" in SRC
    # discovery runs for a policy-skipped row so its cells can get their own draws
    assert "def _row_disc(info: dict, sname: str, switch, cand):" in SRC
    assert "_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)" in SRC
    # the floor is the LAST rule of _row_static: it overrides every earlier skip decision
    i_apply = SRC.index("_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)")
    i_v17 = SRC.index("elif _v17_on and info[\"kind\"] == \"eval\" and os.environ.get(\"V15_V17_P2_NAKED_ONLY\", \"0\") == \"1\":")
    i_cache = SRC.index("        _static_info[k] = info\n        return info")
    assert i_v17 < i_apply < i_cache
    assert "[V18-FLOOR]" in SRC and 'progress.setdefault("v18", {})["floor"]' in SRC
    # autopsy-inert revive keeps the floor-rescued cells (hdrs + inert, identical when hdrs is empty)
    assert '_rv_new = [h for h in st["inert_filters"] if h not in st["hdrs"]]' in SRC and 'st["hdrs"] = list(st["hdrs"]) + _rv_new' in SRC
