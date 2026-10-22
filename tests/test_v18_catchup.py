"""V18_CATCHUP (USER LAW 2026-10-20 "publish 12 per hour per server … catch-up mode … when the entire spectrum is up to date we
run the slow version again"): selected per launch by env; forces the slow phases off before any module-level read; naked-only
main pass (cells settled CATCHUP_SKIPPED, counted, noted) except protection rows and MUST cells; MUST capped and linknet-
ordered with the rest carried over (not settled); exit repair only on a large family loss; manifest mode catchup."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "v15_pilot.py").read_text()


def test_profile_forced_at_import_in_a_fresh_process():
    code = "import os; os.environ['V15_ALLOW_MAC']='1'; os.environ['V18_CATCHUP']='1'; os.environ['V15_V17_ROUNDS']='1'; os.environ['V15_FOCUS']='1'; os.environ['V15_HOLDOUT_DAYS']='7'; import sys; sys.path.insert(0, %r); import v15_pilot as P; print(P._V18_CATCHUP, P.V17_ON, os.environ['V15_FOCUS'], os.environ['V15_ENDGAME_S'], os.environ['V15_DIAG_REPAIR_S'], os.environ['V15_SKIP_365D_AT_DONE'], os.environ['V18_ABLATION'], os.environ['V18_WINNERS_K'], P._HOLDOUT['days'])" % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, cwd=str(ROOT)).stdout.strip().splitlines()[-1]
    assert out == "True False 0 0 0 1 0 20 7.0", out  # holdout kept: catch-up cuts search, never measurement


def test_off_by_default_in_a_fresh_process():
    code = "import os; os.environ['V15_ALLOW_MAC']='1'; os.environ.pop('V18_CATCHUP', None); import sys; sys.path.insert(0, %r); import v15_pilot as P; print(P._V18_CATCHUP)" % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, cwd=str(ROOT)).stdout.strip().splitlines()[-1]
    assert out == "False"


def test_wiring_pins():
    assert 'if _V18_CATCHUP and info["kind"] == "eval" and info["hdrs"] and str(switch).strip() not in ESSENTIAL_GATE_SWITCHES:' in SRC
    assert '_cu_keep = [h for h in info["hdrs"] if (_cu_rk, str(h).strip()) in _v18_must_cells]' in SRC
    assert '_v17_note(sname, rr, f"CATCHUP_SKIPPED:{int(st[\'catchup_skipped\'])}")' in SRC
    assert 'if _COARSE_INLINE and st.get("coarse_deferred_filters") and not _V18_CATCHUP:' in SRC
    assert '_cap = int(os.environ.get("V18_MUST_CATCHUP_MAX", "3000"))' in SRC and "items = items[:_cap]" in SRC
    assert 'float(os.environ.get("V18_CATCHUP_EXITREP_MIN_PP", "5"))' in SRC
    assert '_man["mode"] = "catchup" if _V18_CATCHUP else (f"budget:{_v20_budget}" if (_v20_on and _v20_budget > 0) else "slow")' in SRC  # 2026-10-22 V20 budget board (spec §7)
    assert '_ffr_cut = bool(_v17_on and _v17_phase_cut("P4_FFR")) or _V18_CATCHUP' in SRC
    # the catch-up step runs BEFORE the floor (the floor / untried draw still rescues cells)
    assert SRC.index("if _V18_CATCHUP and info[\"kind\"] == \"eval\"") < SRC.index("_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)")


def test_stuck_first_cell_guard_counts_engine_activity():
    # a board evaluating its V18 pre-pass (R0 MUST before row 3; workbook saved only at tab boundaries) is not stuck
    assert '_V18_ACTIVITY: dict = {"evals": 0}' in SRC
    assert '_V18_ACTIVITY["evals"] = _V18_ACTIVITY.get("evals", 0) + 1' in SRC
    assert "_stuck = _stuck and _ev_seen[0] < 0 and _ev_now == 0" in SRC


def test_floor_law_audit_fields_and_floor_not_skipped_in_catchup():
    import importlib
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("V15_ALLOW_MAC", "1")
    P = importlib.import_module("v15_pilot")
    assert P.v18_start_source("template_cat_side_defaults_today") == "cat_side_defaults"
    assert P.v18_start_source("template_defaults_plus_winners_pack") == "defaults+winners"
    assert P.v18_start_source("incumbent") == "per_sym" and P.v18_start_source("history_0") == "per_sym"
    assert '"start_source": v18_start_source((progress.get("best_start") or {}).get("chosen"))' in SRC
    assert '_man["start_source"] = v18_start_source(' in SRC
    # BEST-START-FLOOR is gated only by prepared / fast_switches / the fresh final / V15_BEST_START_FLOOR — no catch-up-forced key
    i = SRC.index('if prepared is not None and fast_switches is None and _fresh_final and os.environ.get("V15_BEST_START_FLOOR", "1") == "1":')
    assert "_V18_CATCHUP" not in SRC[i - 2000:i]
    for k in ("V15_BEST_START_FLOOR", "V15_PREV_BEST_START"):
        assert f'("{k}"' not in SRC[SRC.index("_V18_CATCHUP = os.environ.get"):SRC.index("_V18_CATCHUP = os.environ.get") + 1500]
