"""V18 R3 ABLATION (doc 17 §5, USER 2026-10-20): ABLATION_DISABLE_* is a lever, never a setting; strip per GROUP (ENTRY split
by tab family), discover the paths the group gates, attribute by restoring one filter at a time, remedy each killer in order
(i) scoped FILTER_EXEMPTIONS, (ii) filter RE-SET (GLOBAL_RESET), (iii) GLOBAL_OFF as a labelled measured candidate (never a
silent proxy); restore all, fresh eval on the full chain, adopt above best. Guaranteed budget V18_ABL_SHARE (floor 240 s),
before the main pass and again after it for the exit groups; digest filter_veto entries first."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()
FIX = ROOT / "tests" / "fixtures" / "v18_digest"


def test_groups_split_entry_by_tab_family_and_keep_the_rest():
    import vec_decisions.filter_ablation_groups as G
    ents = G.GROUPS["ABLATION_DISABLE_FILTER_ENTRY"]
    rt = {ents[0]: "ENTRY_BREAKOUT_CHANNEL", ents[1]: "ENTRY_CONFIRMATION_GATES", ents[2]: "EXIT_STRUCTURAL"}
    g = V.ablation_groups(G.GROUPS, rt)
    assert g["ENTRY:ENTRY_BREAKOUT_CHANNEL"] == [ents[0]] and g["ENTRY:ENTRY_CONFIRMATION_GATES"] == [ents[1]]
    assert ents[2] in g["ENTRY:OTHER"]
    assert sum(len(v) for k, v in g.items() if k.startswith("ENTRY")) == len(ents)
    for name, n in (("MTF_HTF", 36), ("REENTRY", 23), ("EXIT", 27), ("AUGMENT", 13), ("REDUCE", 3)):
        assert len(g[name]) == len(G.GROUPS["ABLATION_DISABLE_FILTER_" + name]) == n
    assert list(g)[-5:] == ["MTF_HTF", "REENTRY", "EXIT", "AUGMENT", "REDUCE"]
    assert not any(k.startswith("ABLATION_") for k in g)


def test_group_tabs_category_and_exit_groups():
    assert V.group_tabs("ENTRY:ENTRY_BREAKOUT_CHANNEL") == V.GROUP_TABS["ENTRY"]
    assert V.group_category("REENTRY") == "reentry" and V.group_category("REDUCE") == "exit" and V.group_category("MTF_HTF") == "entry"
    assert V.is_exit_group("EXIT") and V.is_exit_group("REDUCE") and not V.is_exit_group("ENTRY:OTHER")


def test_scoped_exemption_order():
    exm = {"SW_A=True": {"F1": {"FILTER_EXEMPTIONS": {"MOM3": ["F1"]}}}}
    assert V.scoped_exemption("SW_A", "True", "F1", "entry", exm) == ({"FILTER_EXEMPTIONS": {"MOM3": ["F1"]}}, "EXEMPT:map")
    assert V.scoped_exemption("SW_R", "True", "F2", "reentry", {}, filter_keys={"F2"}) == ({"FILTER_EXEMPTIONS": {"REENTRY": ["F2"]}}, "EXEMPT:REENTRY")
    assert V.scoped_exemption("SW_G", "True", "F3", "augment", {}, augment_keys={"F3"}) == ({"FILTER_EXEMPTIONS": {"AUGMENT": ["F3"]}}, "EXEMPT:AUGMENT")
    assert V.scoped_exemption("SW_E", "True", "F4", "entry", {}, filter_keys={"F4"}) == (None, "")  # no engine scope -> next remedy
    assert V.scoped_exemption("SW_R", "True", "NOT_A_MEMBER", "reentry", {}, filter_keys={"F2"}) == (None, "")


def test_veto_candidates_from_digest(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    d, _ = V.load_digest("CRYPTO_LONG")
    vc = V.veto_candidates(d, 10)
    assert [v["switch"] for v in vc] == ["WT_15M_BOUNCE_OPEN_ENABLED", "REENTRY_WT15M_ENABLED"]  # bogus remedy kind dropped
    assert V.REMEDY_LABEL == {"exemption": "EXEMPT", "reset": "GLOBAL_RESET", "global_off": "GLOBAL_OFF"}


def test_budget_guaranteed_floor(monkeypatch):
    monkeypatch.delenv("V18_ABL_SHARE", raising=False)
    monkeypatch.delenv("V18_ABL_FLOOR_S", raising=False)
    assert V.abl_budget(1500.0) == 450.0  # WINNER tier: 30% of 1500
    assert V.abl_budget(300.0) == 240.0  # floor
    monkeypatch.setenv("V18_ABL_SHARE", "0.5")
    assert V.abl_budget(1500.0) == 750.0


def test_pilot_r3_wiring_pins():
    assert '_v18_r3_on = bool(_V18P is not None and _V18P.flag_on("V18_ABLATION"))' in SRC
    assert '_v18_ablation(_t.time() + _V18P.abl_budget(v17_p3_cap(_tier_name)), "R3_ABLATION_PRE")' in SRC
    assert '"R3_ABLATION_POST", exit_only=True)' in SRC
    i_post = SRC.index('"R3_ABLATION_POST", exit_only=True)')
    i_loop = SRC.index("    for qi, (sname, rr, switch, cand) in enumerate(pending_queue):")
    i_focus = SRC.index("    # USER 2026-10-12 FOCUS ROUND: tier budget on what moved this sym_side")
    assert i_loop < i_post < i_focus
    # never adopt a stripped base: the strip eval goes to _v17_batch only (booked, never promoted)
    assert 'bi = _v17_batch([{"label": f"ABL_STRIP:{g}", "ov": base_ov, "sheet": "V18_ABL"}]' in SRC
    # remedies in order: RESTORE + scoped EXEMPT, then GLOBAL_RESET, then GLOBAL_OFF — each labelled, never a silent proxy
    assert "for tier in (tier_a, tier_b, tier_c):" in SRC
    assert '"remedy": "GLOBAL_RESET"' in SRC and '"remedy": "GLOBAL_OFF"' in SRC and '"remedy": "EXEMPT"' in SRC
    assert "ABL_PROXY" not in SRC[SRC.index("def _v18_ablation("):SRC.index("def _v18_sheet():")]
    # done keys + digest veto first
    assert 'done_key": f"ABL!{g}:{x[\'sw\']}={x[\'cand\']}' in SRC
    assert 'f"ABL!VETO:{v[\'switch\']}={v[\'cand\']}+{lab}"' in SRC
    assert 'ws_a = wb.create_sheet("ABLATION")' in SRC
    # ABLATION_* never a candidate anywhere in V18
    assert 'return None, f"ABLATION_IS_A_LEVER:{k}"' in SRC
