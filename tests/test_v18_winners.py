"""V18 R1 WINNERS (doc 17 §3.5, §6): the cat_side digest's top-K winners (switch+cand WITH companions as one candidate, plus
naked) in one batch vs the start set, greedy joint on positives <= V18_WINNERS_MAX_ADOPT, usual gates + DD + WR measured;
"template defaults + full winners pack" is the third BEST-START candidate. Refused paths never become candidates."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402
from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()
FIX = ROOT / "tests" / "fixtures" / "v18_digest"


def _dg(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    return V.load_digest("CRYPTO_LONG")[0]


def test_winners_take_sorted_by_score(monkeypatch):
    w = V.winners_take(_dg(monkeypatch), 2)
    assert [x["switch"] for x in w] == ["ABLATION_DISABLE_FILTER_ENTRY", "WT_DIV_ENTRY_GATE_ENABLED"]
    assert V.winners_take(None, 5) == []


def test_companions_and_pack(monkeypatch):
    w = V.winners_take(_dg(monkeypatch), 10)
    assert V.companions_of(w[1]) == [("MOM3_FILTER_TF", "OFF")]
    pack = V.winners_pack(w)
    keys = [k for k, _ in pack]
    assert keys[:3] == ["ABLATION_DISABLE_FILTER_ENTRY", "WT_DIV_ENTRY_GATE_ENABLED", "MOM3_FILTER_TF"]
    assert len(keys) == len(set(keys))


def test_parts_refusals():
    d = {}
    assert P.v18_parts_to_overrides([("ABLATION_DISABLE_FILTER_ENTRY", "True")], d)[1].startswith("ABLATION_IS_A_LEVER")
    assert P.v18_parts_to_overrides([("MAX_AUGMENTS_PER_POSITION", "0")], d)[1] == "MAXAUG_NEVER_ZERO"
    g = next(iter(P.GLOBAL_ONLY_KEYS))
    assert P.v18_parts_to_overrides([(g, "True")], d)[1].startswith("GLOBAL_ONLY")
    assert P.v18_parts_to_overrides([("NO_SUCH_FIELD_XYZ_V18", "1")], d)[1].startswith("NOT_IN_CONFIG")
    assert P.v18_parts_to_overrides([], d) == (None, "EMPTY")


def test_parts_accepts_a_real_field_and_hard_set_is_refused():
    f = "EXIT_VELOCITY_WT_MIN_TFS"
    if f not in P.known_config_fields():
        return
    dov, why = P.v18_parts_to_overrides([(f, "2")], {})
    assert why == "" and dov and float(dov[f]) == 2.0
    if "DC_HARD_STOP_15M_ENABLED" in P.known_config_fields():
        assert P.v18_parts_to_overrides([("DC_HARD_STOP_15M_ENABLED", "False")], {})[0] is None


def test_deny_reason():
    assert V.deny_reason({"MODE": "crypto"}).startswith("DENY_ARTEFACT")
    assert V.deny_reason({"ABLATION_DISABLE_FILTER_EXIT": True}).startswith("ABLATION_IS_A_LEVER")
    assert V.deny_reason({"GAP_MOC_EXIT_ENABLED": True}).startswith("HARD_SET")
    assert V.deny_reason({"MTF_ATR_TRAIL_FILTER_TF": "OFF"}).startswith("PROTECTED_STOP_OFF")
    assert V.deny_reason({"MTF_ATR_TRAIL_FILTER_TF": "4h"}) == ""
    assert V.deny_reason({"BB_FROZEN_STOP_ENABLED": False}).startswith("PROTECTED_STOP_OFF")
    assert V.deny_reason({"X": 1}) == ""


def test_extra_gates_dd_and_wr():
    assert V.extra_gate_reason({"wr_pct": None, "max_dd_pct": 5}) == "WR_NOT_MEASURED"
    assert V.extra_gate_reason({"wr_pct": 50, "max_dd_pct": None}) == "DD_UNKNOWN"
    assert V.extra_gate_reason({"wr_pct": 50, "max_dd_pct": 31.0}).startswith("DD 31.00 > 30")
    assert V.extra_gate_reason({"wr_pct": 50, "max_dd_pct": 31.0}, chain_dd=40.0) == ""  # an over-DD chain may only improve
    assert V.extra_gate_reason({"wr_pct": 0.0, "max_dd_pct": 12.0}) == ""


def test_pilot_r1_wiring_pins():
    assert '_v18_r1_on = bool(_V18P is not None and _V18P.flag_on("V18_WINNERS"))' in SRC
    assert 'max_prom=int(os.environ.get("V18_WINNERS_MAX_ADOPT", "8")), book=_v18' in SRC
    assert 'k = int(os.environ.get("V18_WINNERS_K", "60"))' in SRC
    assert 'f"WINNERS:{_wr}:{sw}={c}+"' in SRC and 'f"WINNERS:{_wr}:{sw}={c}"' in SRC
    # R1/R2/R3 run before the main pass, after BEST-START (which runs in _main_single before _spec_fill_workbook)
    i_pre = SRC.index('_v18_winners(_t.time() + float(os.environ.get("V18_WINNERS_S", "240")))')
    i_loop = SRC.index("    for qi, (sname, rr, switch, cand) in enumerate(pending_queue):")
    assert i_pre < i_loop
    assert SRC.index('_v18_exit_repair(_t.time() + float(os.environ.get("V18_EXIT_REPAIR_S", "300")))') < i_loop
    assert SRC.index('"R3_ABLATION_PRE")') < i_loop
    # third BEST-START candidate
    assert '_pb_list.append(("template_defaults_plus_winners_pack", {**_pb_tpl, **_v18_pack}))' in SRC
    # phases book in progress["v18"], promotions keep the E-chain fields (cumulative_before/after via _v17_promote)
    assert "def _v17_promote(it: dict, how: str, prefix: str, ph: dict, book=None):" in SRC
    assert '_pbook = _v17 if book is None else book' in SRC
    assert 'progress["done"][dkey]["v18_phase"] = prefix' in SRC
    # V18 gates on top of _v17_ok
    assert "return not _V18P.extra_gate_reason(res, _v18_chain_dd(), QUAL_DD_MAX)" in SRC
    # legacy resumed boards keep their semantics (no mixed chain), like V17
    assert '(_v18.get("on") or not _v17_main_done)' in SRC
    # new xlsx tab
    assert 'ws_w = wb.create_sheet("WINNERS")' in SRC


def test_candidates_must_be_template_keys_of_this_board():
    assert 'refused[label] = f"NOT_A_TEMPLATE_KEY:{\',\'.join(_nt)}"' in SRC
    assert "_v18_tpl_keys = {str(_w).strip() for (_s, _r, _w, _c) in queue}" in SRC
