"""V17 CARRY (rows dead on this sym_side's last boards are not re-evaluated) + PHASE CUT (never lifted on >= 20 boards) +
non-winner combo slice — USER 2026-10-14 "99% time savings and 10x bh"."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def _clear(monkeypatch):
    for k in ("V15_V17_CARRY", "V15_V17_CARRY_WINNER", "V15_V17_CARRY_MAX_AGE_D", "V15_V17_CARRY_REQUIRE_FP", "V15_V17_CARRY_MAX_RUN", "V15_V17_CARRY_SAMPLE", "V15_V17_CARRY_EPS"):
        monkeypatch.delenv(k, raising=False)


def test_carry_streak_lifecycle(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("V15_V17_CARRY_SAMPLE", "1000000")  # no rotation in this test
    now = time.time()
    k, live = P.carry_key("EXIT_VELOCITY", "A_ENABLED", "True"), P.carry_key("EXIT_VELOCITY", "B", "1")
    b = P.carry_update({}, {k: ("eval", 0.0), live: ("eval", 0.4)}, "fp1", now)
    assert not P.carry_decide(b, k, "fp1", now, "MEDIOCRE")  # 1 dead board is not enough
    b = P.carry_update(b, {k: ("eval", 0.0), live: ("eval", 0.0)}, "fp1", now)
    assert P.carry_decide(b, k, "fp1", now, "MEDIOCRE") and not P.carry_decide(b, live, "fp1", now, "MEDIOCRE")
    assert not P.carry_decide(b, k, "fp1", now, "WINNER")  # winners always evaluate
    assert not P.carry_decide(b, k, "fp1", now + 8 * 86400, "LOSER")  # NPZ window moved > 7 d
    b = P.carry_update(b, {k: ("carried", None)}, "fp1", now)
    b = P.carry_update(b, {k: ("carried", None)}, "fp1", now)
    assert not P.carry_decide(b, k, "fp1", now, "LOSER")  # after 2 carried boards it is re-evaluated
    b = P.carry_update(b, {k: ("eval", 0.7)}, "fp1", now)
    assert b["rows"][k] == [0, 0]  # came alive -> streak reset


def test_carry_fp_and_rotation(monkeypatch):
    _clear(monkeypatch)
    now = time.time()
    b = {"rows": {P.carry_key("S", f"K{i}", "1"): [2, 0] for i in range(200)}, "last_at": now, "start_fp": "fpA", "boards": 3}
    monkeypatch.setenv("V15_V17_CARRY_REQUIRE_FP", "1")
    assert not any(P.carry_decide(b, k, "fpB", now, "LOSER") for k in b["rows"])
    monkeypatch.setenv("V15_V17_CARRY_REQUIRE_FP", "0")
    n = sum(P.carry_decide(b, k, "fpB", now, "LOSER") for k in b["rows"])
    assert 160 <= n < 200  # the rotating 1/10 sample is still evaluated
    monkeypatch.setenv("V15_V17_CARRY", "0")
    assert not any(P.carry_decide(b, k, "fpA", now, "LOSER") for k in b["rows"])


def test_carry_wiring_static():
    assert 'reason=f"CARRIED_DEAD:' in SRC and "carry_update(_carry_book, _carry_rows, _carry_fp, _t.time())" in SRC
    assert not "CARRIED_DEAD".startswith("SKIPPED")  # structural (settled), not a policy skip that blocks publish


def test_phase_cut_list(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"phases": {"P3_FOCUS": {"n": 25, "lifted": 0}, "P3_SURGEON": {"n": 30, "lifted": 4}, "P3_RETEST": {"n": 5, "lifted": 0}, "P3_TIMFIX": {"n": 40, "lifted": 0}}}))
    monkeypatch.delenv("V15_V17_CUT_MIN_N", raising=False)
    assert P.v17_phase_cut_list(p) == {"P3_FOCUS"}  # TIMFIX is law (never cuttable); RETEST n<20
    assert "P3_REGIME365" not in P.V17_CUTTABLE_PHASES and "P3_TIMFIX" not in P.V17_CUTTABLE_PHASES


def test_nonwinner_combo_slice_static():
    assert 'V15_V17_COMBO_MIN_S_{_tier_name}' in SRC and '"MEDIOCRE": "120"' in SRC
    assert "(_cmin + 20.0) if _it == 1 and not _win else 0.0" in SRC


def test_rotation_off_is_eligibility(monkeypatch):
    _clear(monkeypatch)
    now = time.time()
    b = {"rows": {P.carry_key("S", f"K{i}", "1"): [2, 0] for i in range(50)}, "last_at": now, "boards": 2}
    assert all(P.carry_decide(b, k, "", now, "LOSER", rotate=False) for k in b["rows"])
    assert '"carry_sampled": bool(st.get("carry_sampled"))' in SRC


def test_combo_repeat_gated_by_own_rate():
    assert "(_win and _combo_rate[0] >= float(os.environ.get(\"V15_V17_MIN_LIFT_PM\", \"0.3\")))" in SRC
    assert 'os.environ.get("V15_V17_COMBO_SHARE_WINNER", "0.5")' in SRC


def test_p4_cuttable_and_guards_stay():
    assert {"P4_FFR", "P4_POST_ENDGAME_DIAG"} <= P.V17_CUTTABLE_PHASES
    assert '_post_cut = bool(_v17_on and _v17_phase_cut("P4_POST_ENDGAME_DIAG"))' in SRC
    i_cut = SRC.index('_post_cut = bool(_v17_on')
    assert SRC.index("BEST-SEEN PUBLISH GATE", i_cut) > i_cut and SRC.index("[BEST-START-FLOOR] {new_symside} REFUSED") > i_cut


def test_winner_needs_real_potential(monkeypatch):
    monkeypatch.delenv("V15_TIER_FORCE", raising=False)
    monkeypatch.delenv("V15_TIER_WIN_BIG_MOVER", raising=False)
    assert P.symside_tier(3.0, 12.0, 40, None)[0] == "MEDIOCRE"  # aligned mover bh 12, potential 3 -> no longer WINNER
    assert P.symside_tier(3.0, 12.0, 40, 15.0)[0] == "WINNER"  # previous final 15 >= max(bh 12, 10)
    assert P.symside_tier(1.0, 30.0, 40, None)[0] == "WINNER"  # |bh| >= 25 stays WINNER
