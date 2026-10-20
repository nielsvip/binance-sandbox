"""V18_PROFILE_YELLOWS (USER 2026-10-20 structural speed-up): with the switch ON a row's yellow set = needed U suffocating U
evidence_pos (+ linknet proposals) of THIS cat_side's data/learn/needed_filters_<CS>.json, plus a floor / untried draw over
every other column (never zero); INERT / DAMAGING switches with n_syms >= threshold keep naked + floor only; absent file =
today's behaviour."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("V18_PROFILE_YELLOWS", raising=False)
    assert V.profile_on() is False


def test_select_keeps_evidence_and_draws_the_rest():
    pool = ["A=1", "B=1", "C=1", "D=1"]
    keep, rest, why = V.profile_select(pool, {"needed": ["B=1"], "suffocating": [], "evidence_pos": ["Z=9"]}, {"verdict": "SOMETIMES", "n_syms": 40}, ["D=1"], lambda h: h == "C=1", 10)
    assert why == "PROFILE" and keep == ["B=1", "D=1", "C=1"] and rest == ["A=1"]


def test_only_default_relative_state_reduces_a_row():
    pool = ["A=1", "B=1", "C=1"]
    # the coarse verdict alone never reduces a row (USER LAW: most switches are negative because their default already won)
    keep, rest, why = V.profile_select(pool, {"needed": ["A=1", "B=1"]}, {"verdict": "INERT", "n_syms": 50}, [], lambda h: False, 10)
    assert keep == ["A=1", "B=1"] and why == "PROFILE"
    # a settled-off / damaging state drops only the UNDOCUMENTED cells; evidence-backed cells always stay (CDE_SHORT forensics)
    keep, rest, why = V.profile_select(pool, {"needed": ["A=1"], "evidence_pos": ["B=1"]}, {"state": "SETTLED_OFF", "n_syms": 12}, [], lambda h: False, 10)
    assert keep == ["A=1", "B=1"] and rest == ["C=1"] and why == "PROFILE_SETTLED_OFF_NAKED_FLOOR"
    keep, _, why = V.profile_select(pool, {}, {"state": "DAMAGING", "n_syms": 30, "interval": [0.0046, 0.0974]}, [], lambda h: h == "C=1", 10, sibling_pos=["B=1"])
    assert keep == ["B=1", "C=1"] and why == "PROFILE_DAMAGING_NAKED_FLOOR"  # sibling-positive pair kept + the floor draw stays
    keep, _, why = V.profile_select(pool, {"needed": ["A=1"]}, {"state": "DAMAGING", "n_syms": 40}, [], lambda h: False, 10)
    assert keep == ["A=1"] and why == "PROFILE"  # DAMAGING without an interval is not trusted


def test_cde_short_forensics_case():
    # EXIT_VELOCITY!18 LH_LL_TOP_EXIT_STRUCT_TF=4h,D: state DAMAGING (n_syms 30, interval) — its evidence_pos cell
    # LH_LL_TOP_EXIT_STRUCT_MODE=LL (+1.0343 joint in the champion) must be evaluated
    pool = ["LH_LL_TOP_EXIT_STRUCT_MODE=LL", "OTHER_F=1"]
    entry = {"needed": ["LH_LL_TOP_EXIT_STRUCT_MODE=LH_AND_LL"], "evidence_pos": ["LH_LL_TOP_EXIT_STRUCT_MODE=LL"]}
    keep, rest, _ = V.profile_select(pool, entry, {"state": "DAMAGING", "n_syms": 30, "interval": [0.0046, 0.0974]}, [], lambda h: False, 10)
    assert keep == ["LH_LL_TOP_EXIT_STRUCT_MODE=LL"] and rest == ["OTHER_F=1"]


def test_floor_never_zero_over_the_rest():
    pool = [f"F{i}=1" for i in range(4000)]
    keep, rest, _ = V.profile_select(pool, {}, {}, [], lambda h: V.floor_draw("CRYPTO_LONG", "X_LONG", "T", "S=1@" + h, "r") < 0.05, 10)
    assert 120 < len(keep) < 290 and len(keep) + len(rest) == 4000


def test_load_profile_fail_open_and_cat_side(tmp_path, monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    a, b, n, why = V.load_profile("CRYPTO_LONG")
    assert a is None and "absent" in why
    (tmp_path / "needed_filters_CRYPTO_LONG.json").write_text(json.dumps({"S=1": {"needed": ["F=1"]}}))
    (tmp_path / "switch_profile_CRYPTO_LONG.json").write_text(json.dumps({"_meta": {"cat_side": "CRYPTO_SHORT"}, "rows": {}}))
    a, b, n, why = V.load_profile("CRYPTO_LONG")
    assert a is None and "MISMATCH" in why
    (tmp_path / "switch_profile_CRYPTO_LONG.json").write_text(json.dumps({"_meta": {"cat_side": "CRYPTO_LONG", "thresholds": {"min_sym": 3}}, "rows": {"S=1": {"verdict": "WORKS"}}}))
    a, b, n, why = V.load_profile("CRYPTO_LONG")
    assert a["needed"]["S=1"]["needed"] == ["F=1"] and b["S=1"]["verdict"] == "WORKS" and n == 10


def test_pilot_wiring_pins():
    assert "_v18_prof_on = bool(_V18P is not None and _V18P.profile_on())" in SRC
    assert "_V18P.profile_select(pool, _ent, _prow," in SRC and "_v18_prof_sib.get(_rk))" in SRC
    assert '_pe = _v18_untried_p if (_untried_sw or (_n is not None and _n < 3)) else max(_V18P.floor_p(), 0.001)' in SRC
    i_p = SRC.index("_v18_profile_cells(info, sname, switch, cand)\n")
    i_f = SRC.index("_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)")
    assert i_p < i_f
    assert "[V18-PROFILE]" in SRC and 'progress["done"][key]["v18_profile"] = dict(st["v18_profile"])' in SRC


def test_prev_xls_single_value_c_is_a_baseline_override_when_switched_on():
    # CLAUDE.md regression #3: "XLS prev parser ... missed single-value C (False) baseline overrides" — V18_C_BARE_VALUES=1
    # reads a bare C value as the row switch's override (type-checked); default OFF until the gate decides
    i = SRC.index("def _do_xls_prev():")
    body = SRC[i:SRC.index("_ex_xls = _cf_xls.ThreadPoolExecutor(max_workers=1)")]
    assert '_V18P.flag_on("V18_C_BARE_VALUES")' in body
    assert "_parts.append((str(_a).strip(), _bv))" in body
    assert "_cand_compatible(str(_a).strip(), _bv, {})[0]" in body
