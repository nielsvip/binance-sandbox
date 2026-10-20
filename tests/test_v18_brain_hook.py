"""V18 deliverable 2b (docs/v15_pending/19_E8_BRAIN_PILOT_HOOK_20261020.md): V18_BRAIN=1 (default OFF) wires the per-cat_side
PilotBrainV18 allocator into _row_static; an UNTRIED pair (n < 3 across the cat_side) gets V18_UNTRIED_PER_1000/1000 (50),
never the 5/1000 floor nor the brain's p; skipped cells are SETTLE lines with their p; every eval line carries
brain_p / brain_model / phase (+ the V18 phase extras)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_untried_p(monkeypatch):
    monkeypatch.delenv("V18_UNTRIED_PER_1000", raising=False)
    assert V.untried_p() == 0.05
    monkeypatch.setenv("V18_UNTRIED_PER_1000", "200")
    assert V.untried_p() == 0.2


def test_phase_of():
    sw = ("EXIT_VELOCITY",)
    assert V.phase_of("EXIT_VELOCITY", "naked", sw) == "MAIN"
    assert V.phase_of("EXIT_VELOCITY", "JOINT:a+b", sw) == "MAIN"
    assert V.phase_of("V18", "WINNERS:3:A=1", sw) == "WINNERS"
    assert V.phase_of("V18", "EXITREP:EXIT_VELOCITY_WT:A=1", sw) == "EXITREP"
    assert V.phase_of("V18_ABL", "ABL_STRIP:EXIT", sw) == "ABL"
    assert V.phase_of("V18", "XPOLL:CRYPTO_SHORT:A=1", sw) == "XPOLL"


def test_model_contract_cat_side_and_untried_n():
    p = ROOT / "data" / "brain" / "v18_brain_CRYPTO_LONG.json"
    if not p.exists():
        return
    from tools import v18_brain as B
    b = B.PilotBrainV18(p, "DOTUSDT_LONG")
    d = b.decide("EXIT_VELOCITY", "NO_SUCH_SWITCH_V18=1", "NO_SUCH_FILTER=1", "r")
    assert d["n"] < 3 and d["p"] == 1.0  # the model keeps unevidenced keys; the pilot's untried rule replaces that p by 50/1000
    try:
        B.PilotBrainV18(p, "AMD_LONG")
        raise AssertionError("a stocks board loaded a crypto model")
    except ValueError:
        pass


def test_pilot_brain_wiring_pins():
    assert '_v18_brain_on = bool(_V18P is not None and _V18P.flag_on("V18_BRAIN"))' in SRC
    assert "_V18B.PilotBrainV18(_v18_bp, new_symside)" in SRC and "_v18_brain.observe_ctx(tier=_tier_name, bh=bh)" in SRC
    assert 'if float(d.get("n") or 0.0) < 3:' in SRC and "_pe = _v18_untried_p" in SRC
    # the floor's pair evidence: untried -> max(floor, untried p)
    assert "_kp = _u < max(_v18_floor_p, _v18_untried_p)" in SRC
    # brain runs before the floor (the floor is still the last rule) and skips go to the PRIOR-REVIVED list
    i_b = SRC.index("_v18_brain_cells(info, sname, switch, cand)\n")
    i_f = SRC.index("_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)")
    assert i_b < i_f
    assert 'info["prior_skipped"] = list(info.get("prior_skipped") or []) + skip_' in SRC
    assert '"label": "SETTLE", "code": "BRAIN_SKIP"' in SRC
    assert '"phase": _V18P.phase_of(sname, label, SWITCH_SHEETS), "brain_p":' in SRC
    assert '_delta_log({**(_v18_logfields(sname, rr, label) if _v18_logx[0] else {}), "ts": utcnow()' in SRC
    assert 'progress.setdefault("v18", {})["floor"] = {**dict(_v18_floor_tot), **dict(_v18_untried_tot), "untried_p": _v18_untried_p' in SRC
    assert "untried_seen={_v18_untried_tot['untried_seen']} untried_kept={_v18_untried_tot['untried_kept']}" in SRC
    # never-pruned rows (sacred / essential / new / coarse-expand) and WINNER-tier rows keep their naked eval
    assert 'if _v18_brain_on and _v18_brain is not None and not _brain_exempt(sname, switch, cand):' in SRC
    assert 'if info["kind"] == "eval" and _tier_name != "WINNER":' in SRC
