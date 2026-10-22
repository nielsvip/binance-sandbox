"""binding_map_v1 (spec 22 §2.3, family map §c): contract keys, own-cat_side refusal, the §c rules on fixture rows,
fail-open UNKNOWN, and the must-index hygiene / pair-class split (spec 22 §4.1-4.2)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v18_binding_map as B  # noqa: E402
from tools import v15_v18_must_index as M  # noqa: E402

FAMILIES = {
    "ENTRY": ["BB_BOUNCE_ENTRY_TF", "WT_15M_BOUNCE_OPEN_ENABLED", "MOM3_FILTER_TF"],
    "REENTRY": ["REENTRY_WINDOW_BARS"],
    "AUGMENT": ["AUGMENT_LADDER_ENABLED"],
    "EXIT_VEL": ["WT_ACCEL_EXIT_ENABLED"],
    "EXIT_STOP": ["STOP_LOSS_ENABLED"],
    "EXIT_TARGET": ["DC_DAYTRADE_ENABLED", "DC_DAYTRADE_STOP_PCT"],
    "REDUCE": ["QUICK_REDUCE_STRONG"],
    "SIZING": ["START_POSITION_SIZE"],
}
FILTERS = {
    "MOM3_FILTER_TF": {"class": "ENTRY_TF_MASK", "stages": ["ENTRY"], "master": None, "anchor": "a"},
    "HTF_DIRECTION_GATE_ENABLED": {"class": "ENTRY_BOOL_GATE", "stages": ["ENTRY", "AUGMENT"], "master": None, "anchor": "a"},
    "WT_15M_BOUNCE_BB_MAX": {"class": "ENTRY_PARAM", "stages": ["ENTRY"], "master": "WT_15M_BOUNCE_OPEN_ENABLED", "anchor": "a"},
    "EXIT_TOP_FADE_FILTER_TF": {"class": "EXIT_CONFIRM", "stages": ["EXIT"], "master": None, "anchor": "a"},
    "PEAK_GIVEBACK_BE_EROSION_FILTER_TF": {"class": "EROSION_CONFIRM", "stages": ["EXIT"], "master": "PEAK_GIVEBACK_PROTECTION_ENABLED", "anchor": "a"},
    "DC_BREACH_REDUCE_FILTER_TF": {"class": "REDUCE_CONFIRM", "stages": ["REDUCE"], "master": "EXIT_DC_BREACH_REDUCE_ENABLED", "anchor": "a"},
    "FROZEN_STOP_FILTER_TF": {"class": "EXIT_TF_SELECTOR", "stages": ["EXIT"], "master": "BB_FROZEN_STOP_ENABLED", "anchor": "a"},
    "HARDCODED_RALLY_MIN_MOVE_PCT": {"class": "REENTRY_PARAM", "stages": ["REENTRY"], "master": "HARDCODED_RALLY_REENTRY_ENABLED", "anchor": "a"},
    "HAIKU_WINNER_FILTER_TF": {"class": "NO_ENGINE_READ", "stages": [], "master": None, "anchor": "a"},
}


def _bib(v, reach=1):
    return {"defaults": {"QuickConfig": v}, "vec_read_count": {"reachable": reach}, "vec_reads": ["v12_quick_engine.py:1(x)"] if reach else []}


BIBLE = {
    "BB_BOUNCE_ENTRY_TF": _bib("OFF"), "WT_15M_BOUNCE_OPEN_ENABLED": _bib(False), "MOM3_FILTER_TF": _bib("OFF"), "REENTRY_WINDOW_BARS": _bib(4),
    "AUGMENT_LADDER_ENABLED": _bib(True), "WT_ACCEL_EXIT_ENABLED": _bib(True), "STOP_LOSS_ENABLED": _bib(False), "DC_DAYTRADE_ENABLED": _bib(True),
    "DC_DAYTRADE_STOP_PCT": _bib(0.015), "QUICK_REDUCE_STRONG": _bib(True), "START_POSITION_SIZE": _bib(1.0), "DAYTRADE_DC_TARGET_TF": _bib("15m,1h,4h"),
    "DAYTRADE_DC_STOP_TF": _bib("1h"), "BB_FROZEN_STOP_ENABLED": _bib(False), "EXIT_DC_BREACH_REDUCE_ENABLED": _bib(True), "HARDCODED_RALLY_REENTRY_ENABLED": _bib(True),
    "REENTRY_ENTRY_FILTER_ENABLED": _bib(False), "REENTRY_APPLY_ENTRY_GATES_ENABLED": _bib(False), "PEAK_GIVEBACK_PROTECTION_ENABLED": _bib(True), "WIN_TRAIL_EROSION_PCT": _bib(0),
    "DEAD_SWITCH_X": _bib(1, reach=0),
}
FIX_ROWS = {
    "ENTRY_REVERSAL_BOUNCE": [("BB_BOUNCE_ENTRY_TF", "4h", ["MOM3_FILTER_TF=1h", "WT_15M_BOUNCE_BB_MAX=0.9", "HAIKU_WINNER_FILTER_TF=1h", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h"]), ("WT_15M_BOUNCE_OPEN_ENABLED", True, ["WT_15M_BOUNCE_BB_MAX=0.9"]), ("MOM3_FILTER_TF", "D", ["MOM3_FILTER_TF=1h"]), ("DEAD_SWITCH_X", 2, ["MOM3_FILTER_TF=1h"]), ("NOT_IN_FAMILY_MAP", 1, ["MOM3_FILTER_TF=1h"]), ("BB_BOUNCE_ENTRY_TF", "1h", ["BRAND_NEW_FILTER_TF=4h"])],
    "REENTRY_WINDOWED": [("REENTRY_WINDOW_BARS", 8, ["MOM3_FILTER_TF=1h", "HARDCODED_RALLY_MIN_MOVE_PCT=1.0"])],
    "AUGMENT_TREND": [("AUGMENT_LADDER_ENABLED", False, ["HTF_DIRECTION_GATE_ENABLED=True", "MOM3_FILTER_TF=1h"])],
    "EXIT_VELOCITY": [("WT_ACCEL_EXIT_ENABLED", False, ["EXIT_TOP_FADE_FILTER_TF=1h", "FROZEN_STOP_FILTER_TF=1h", "HARDCODED_RALLY_MIN_MOVE_PCT=1.0"])],
    "EXIT_STRUCTURAL": [("STOP_LOSS_ENABLED", True, ["EXIT_TOP_FADE_FILTER_TF=1h"]), ("DC_DAYTRADE_ENABLED", False, ["PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h"]), ("DC_DAYTRADE_STOP_PCT", 0.02, ["MOM3_FILTER_TF=1h"])],
    "REDUCE_PROFIT_LOCK": [("QUICK_REDUCE_STRONG", False, ["DC_BREACH_REDUCE_FILTER_TF=1h"])],
    "STDEV_SLOPE_SIZING": [("START_POSITION_SIZE", 2.0, ["MOM3_FILTER_TF=1h"])],
}


def _tpl():
    tabs = {t: [{"row": i + 3, "switch": s, "cand": c, "bold": False, "yellows": ys} for i, (s, c, ys) in enumerate(rows)] for t, rows in FIX_ROWS.items()}
    hb = {h: False for rows in FIX_ROWS.values() for _, _, ys in rows for h in ys}
    return {"tabs": tabs, "header_bold": hb}


def _bm(cs="CRYPTO_LONG"):
    fm = {"families": {f: {"stage": f.lower(), "switches": s} for f, s in FAMILIES.items()}, "filters": FILTERS}
    return B.build_one(cs, fm, BIBLE, {"ABLATION_DISABLE_FILTER_ENTRY": ("MOM3_FILTER_TF",)}, ("HTF_DIRECTION_GATE_ENABLED",), _tpl(), {"fixture": "0"})


def _cell(bm, tab, sw, c, hdr):
    return bm["rows"][f"{tab}!{sw}={c}"]["cells"][hdr]


def test_contract_keys():
    bm = _bm()
    assert set(bm) >= {"meta", "switches", "filters", "rules", "rows", "summary"}
    assert bm["meta"]["cat_side"] == "CRYPTO_LONG" and bm["meta"]["contract"] == "binding_map_v1" and {"built_at", "sources"} <= set(bm["meta"])
    s = bm["switches"]["BB_BOUNCE_ENTRY_TF"]
    assert set(s) >= {"family", "stage", "masters", "vec_reads", "dead", "anchor"} and s["family"] == "ENTRY"
    assert set(bm["filters"]["MOM3_FILTER_TF"]) >= {"class", "stages", "master", "vec_reads", "anchor"}
    assert all(set(r) >= {"family", "class", "verdict", "condition_keys", "anchor"} for r in bm["rules"])
    row = bm["rows"]["ENTRY_REVERSAL_BOUNCE!BB_BOUNCE_ENTRY_TF=4h"]
    assert set(row) >= {"family", "dead_row", "cells"} and set(row["cells"]["MOM3_FILTER_TF=1h"]) >= {"verdict", "condition_keys", "reason"}
    tot = bm["summary"]["_total"]
    assert sum(tot.values()) == sum(len(ys) for rows in FIX_ROWS.values() for _, _, ys in rows)
    assert sum(bm["summary"]["ENTRY_REVERSAL_BOUNCE"].values()) == 9


def test_load_refuses_other_cat_side(tmp_path):
    p = B.write_map(_bm("STOCKS_LONG"), tmp_path)
    assert B.load_binding_map(p, "STOCKS_LONG")["meta"]["cat_side"] == "STOCKS_LONG"
    with pytest.raises(B.BindingMapError, match="CAT_SIDE_MISMATCH"):
        B.load_binding_map(p, "CRYPTO_LONG")


def test_family_map_rules_on_fixture_rows():
    bm = _bm()
    E, R = "ENTRY_REVERSAL_BOUNCE", "REENTRY_WINDOWED"
    assert _cell(bm, E, "BB_BOUNCE_ENTRY_TF", "4h", "MOM3_FILTER_TF=1h")["verdict"] == "BINDS"  # 1 entry mask on ENTRY row
    c = _cell(bm, E, "BB_BOUNCE_ENTRY_TF", "4h", "WT_15M_BOUNCE_BB_MAX=0.9")  # 2 param with master False
    assert c["verdict"] == "CONDITIONAL" and c["condition_keys"] == ["WT_15M_BOUNCE_OPEN_ENABLED=True"]
    assert _cell(bm, E, "WT_15M_BOUNCE_OPEN_ENABLED", "True", "WT_15M_BOUNCE_BB_MAX=0.9")["verdict"] == "BINDS"  # 2 row arms the master
    c = _cell(bm, R, "REENTRY_WINDOW_BARS", "8", "MOM3_FILTER_TF=1h")  # 3 entry mask on REENTRY row
    assert c["verdict"] == "CONDITIONAL" and c.get("condition_mode") == "any" and "REENTRY_ENTRY_FILTER_ENABLED=True" in c["condition_keys"]
    assert _cell(bm, R, "REENTRY_WINDOW_BARS", "8", "HARDCODED_RALLY_MIN_MOVE_PCT=1.0")["verdict"] == "BINDS"  # 4
    assert _cell(bm, "AUGMENT_TREND", "AUGMENT_LADDER_ENABLED", "False", "HTF_DIRECTION_GATE_ENABLED=True")["verdict"] == "BINDS"  # 5
    assert _cell(bm, "AUGMENT_TREND", "AUGMENT_LADDER_ENABLED", "False", "MOM3_FILTER_TF=1h")["verdict"] == "INDIRECT"  # 6
    assert _cell(bm, "EXIT_VELOCITY", "WT_ACCEL_EXIT_ENABLED", "False", "EXIT_TOP_FADE_FILTER_TF=1h")["verdict"] == "BINDS"  # 7
    c = _cell(bm, "EXIT_STRUCTURAL", "STOP_LOSS_ENABLED", "True", "EXIT_TOP_FADE_FILTER_TF=1h")  # 8 TOP_FADE on STOP row
    assert c["verdict"] == "NEVER" and c["reason"] == "EXIT_CONFIRM_BYPASSED"
    c = _cell(bm, E, "BB_BOUNCE_ENTRY_TF", "4h", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h")  # 9 erosion under DC lists
    assert c["verdict"] == "NEVER" and c["reason"] == "DC_DAYTRADE_BRANCH"
    c = _cell(bm, "EXIT_STRUCTURAL", "DC_DAYTRADE_ENABLED", "False", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h")  # 9 DC branch off on its own row
    assert c["verdict"] == "CONDITIONAL" and c["condition_keys"] == ["WIN_TRAIL_EROSION_PCT>0"]
    assert _cell(bm, "REDUCE_PROFIT_LOCK", "QUICK_REDUCE_STRONG", "False", "DC_BREACH_REDUCE_FILTER_TF=1h")["verdict"] == "BINDS"  # 10
    c = _cell(bm, "EXIT_VELOCITY", "WT_ACCEL_EXIT_ENABLED", "False", "FROZEN_STOP_FILTER_TF=1h")  # 11 master off
    assert c["verdict"] == "CONDITIONAL" and c["condition_keys"] == ["BB_FROZEN_STOP_ENABLED=True"]
    for tab, sw, cnd in (("ENTRY_REVERSAL_BOUNCE", "DEAD_SWITCH_X", "2"), ("EXIT_STRUCTURAL", "DC_DAYTRADE_STOP_PCT", "0.02")):  # 12 dead rows
        row = bm["rows"][f"{tab}!{sw}={cnd}"]
        assert row["dead_row"] and all(v["verdict"] == "NEVER" and v["reason"] == "DEAD_ROW" for v in row["cells"].values())
    c = _cell(bm, E, "BB_BOUNCE_ENTRY_TF", "4h", "HAIKU_WINNER_FILTER_TF=1h")  # 13 NO_ENGINE_READ
    assert c["verdict"] == "NEVER" and c["reason"] == "NO_ENGINE_READ"
    assert _cell(bm, "EXIT_VELOCITY", "WT_ACCEL_EXIT_ENABLED", "False", "HARDCODED_RALLY_MIN_MOVE_PCT=1.0")["verdict"] == "CONDITIONAL"  # 14
    assert _cell(bm, "STDEV_SLOPE_SIZING", "START_POSITION_SIZE", "2.0", "MOM3_FILTER_TF=1h")["verdict"] == "INDIRECT"  # 15


def test_self_pair_and_unknown_fail_open():
    bm = _bm()
    c = _cell(bm, "ENTRY_REVERSAL_BOUNCE", "MOM3_FILTER_TF", "D", "MOM3_FILTER_TF=1h")
    assert c["verdict"] == "NEVER" and c["reason"] == "SELF_PAIR"
    for sw, cnd, hdr in (("NOT_IN_FAMILY_MAP", "1", "MOM3_FILTER_TF=1h"), ("BB_BOUNCE_ENTRY_TF", "1h", "BRAND_NEW_FILTER_TF=4h")):
        c = _cell(bm, "ENTRY_REVERSAL_BOUNCE", sw, cnd, hdr)
        assert c["verdict"] == "CONDITIONAL" and c["reason"] == "UNKNOWN_FAMILY"


def test_pair_verdict_outside_shaded_cells(tmp_path):
    bm = B.load_binding_map(B.write_map(_bm(), tmp_path), "CRYPTO_LONG")
    assert B.pair_verdict(bm, "WT_ACCEL_EXIT_ENABLED", False, "MOM3_FILTER_TF=4h")["verdict"] == "INDIRECT"
    assert B.pair_verdict(bm, "AUGMENT_LADDER_ENABLED", False, "HTF_DIRECTION_GATE_ENABLED=False")["verdict"] == "BINDS"
    assert B.pair_verdict(bm, "BB_BOUNCE_ENTRY_TF", "D", "WT_15M_BOUNCE_BB_MAX=0.8")["condition_keys"] == ["WT_15M_BOUNCE_OPEN_ENABLED=True"]


def _rec(ss, sheet, row, sw, c, label, delta, trades=20):
    return {"sym_side": ss, "sheet": sheet, "row": row, "switch": sw, "cand": c, "label": label, "delta": delta, "valid": True, "trades": trades, "err": ""}


def test_must_hygiene_drops_and_pair_classes(tmp_path):
    out = tmp_path / "learn"
    B.write_map(_bm(), out)
    E = "ENTRY_REVERSAL_BOUNCE"
    lines = [
        _rec("ADAUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "naked", 1.0),
        _rec("ADAUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "MOM3_FILTER_TF=1h", 1.5),  # beats naked -> pair
        _rec("ADAUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "WT_15M_BOUNCE_BB_MAX=0.9", 1.0),  # == naked -> demoted
        _rec("ADAUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "HAIKU_WINNER_FILTER_TF=1h", 1.0),  # NO_ENGINE_READ -> dropped
        _rec("ADAUSDT_LONG", E, 5, "MOM3_FILTER_TF", "D", "naked", -0.5),
        _rec("ADAUSDT_LONG", E, 5, "MOM3_FILTER_TF", "D", "MOM3_FILTER_TF=1h", 0.4),  # self-pair -> dropped
        _rec("ADAUSDT_LONG", E, 6, "DEAD_SWITCH_X", "2", "naked", 0.3),  # dead row -> dropped
        _rec("DOTUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "naked", -2.0),
        _rec("DOTUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h", 0.2),  # DC branch -> dropped
    ]
    log = tmp_path / "a.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in lines))
    st = M.build(str(log), out)
    d = json.loads((out / "must_pos_CRYPTO_LONG.json").read_text())
    it = d["items"]
    assert set(it) == {"BB_BOUNCE_ENTRY_TF=4h", "BB_BOUNCE_ENTRY_TF=4h@MOM3_FILTER_TF=1h"}
    assert it["BB_BOUNCE_ENTRY_TF=4h"]["positive_class"] == "row"
    assert it["BB_BOUNCE_ENTRY_TF=4h@MOM3_FILTER_TF=1h"]["positive_class"] == "pair" and it["BB_BOUNCE_ENTRY_TF=4h@MOM3_FILTER_TF=1h"]["beats_naked"] is True
    assert d["_dropped"] == {"NO_ENGINE_READ": 1, "SELF_PAIR": 1, "DEAD_ROW": 1, "DC_DAYTRADE_BRANCH": 1}
    assert len(d["_dropped_sample"]) == 4 and d["_demoted_to_row"] == 1
    assert st["hygiene"]["CRYPTO_LONG"]["published"] == 2
    state = json.loads((out / "must_pos_state_CRYPTO_LONG.json").read_text())["items"]
    assert "BB_BOUNCE_ENTRY_TF=4h@WT_15M_BOUNCE_BB_MAX=0.9" in state  # kept in state, only demoted in the published index
    with open(log, "a") as fh:  # a later sibling where the same cell beats its naked promotes it (incremental, naked carried)
        fh.write(json.dumps(_rec("XRPUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "naked", 0.1)) + "\n")
    M.build(str(log), out)
    with open(log, "a") as fh:
        fh.write(json.dumps(_rec("XRPUSDT_LONG", E, 3, "BB_BOUNCE_ENTRY_TF", "4h", "WT_15M_BOUNCE_BB_MAX=0.9", 0.9)) + "\n")
    M.build(str(log), out)
    d2 = json.loads((out / "must_pos_CRYPTO_LONG.json").read_text())
    e = d2["items"]["BB_BOUNCE_ENTRY_TF=4h@WT_15M_BOUNCE_BB_MAX=0.9"]
    assert e["beats_naked"] is True and e["beats_syms"] == ["XRPUSDT_LONG"] and sorted(e["syms"]) == ["ADAUSDT_LONG", "XRPUSDT_LONG"]
    assert d2["_demoted_to_row"] == 0


def test_must_no_binding_map_fail_open(tmp_path):
    out = tmp_path / "learn"
    log = tmp_path / "a.jsonl"
    log.write_text(json.dumps(_rec("ADAUSDT_LONG", "ENTRY_REVERSAL_BOUNCE", 5, "MOM3_FILTER_TF", "D", "MOM3_FILTER_TF=1h", 0.4)) + "\n")
    st = M.build(str(log), out)
    d = json.loads((out / "must_pos_CRYPTO_LONG.json").read_text())
    assert d["_dropped"] == {} and "absent" in st["hygiene"]["CRYPTO_LONG"]["binding_map"]
    assert d["items"]["MOM3_FILTER_TF=D@MOM3_FILTER_TF=1h"]["beats_naked"] is None and d["_beats_unknown"] == 1
