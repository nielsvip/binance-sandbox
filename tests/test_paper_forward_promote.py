"""Ledger collateral for tools/paper_forward_promote.py + forward_live_settings.py.

USER 2026-10-22: pos-delta paths auto-apply live after 4 days of forward
history (per sym_side, SQL+JSON store), with a clear record of forward vs live
behavior (revoke when deltas fade). These tests pin: the 4-day gate, protective
exclusions, dry-run safety, idempotent apply, revoke, stale-evidence refusal,
and that the live hooks sit above the parity blocks.
"""

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import forward_live_settings as fls  # noqa: E402
import tools.paper_forward_promote as pp  # noqa: E402


def _scores(rows, paths):
    return {"per_sym": rows, "paths": paths}


def _row(path, ss, mean, scored, venue="crypto", kind="entry", events=None):
    return {
        "path": path,
        "venue": venue,
        "kind": kind,
        "sym_side": ss,
        "events": events or scored,
        "scored": scored,
        "pending": 0,
        "mean_delta": mean,
        "pos": mean > 0,
    }


def _path(path, days, venue="crypto", kind="entry"):
    return {
        "path": path,
        "venue": venue,
        "kind": kind,
        "sym_sides": 1,
        "pos_syms": 1,
        "events": 3,
        "scored": 3,
        "pooled_delta": 0.01,
        "days": days,
        "verdict": "WATCH",
    }


def test_gate_map_has_bool_and_inv_kinds():
    gm = pp.gate_map()
    assert gm["WT_3M_FORCE_OPEN"] == ("WT_3M_FORCE_OPEN_ENABLED", "bool")
    assert gm["DC_BREACH_REDUCE"][1] == "inv"


def test_gate_lookup_prefix_fallback():
    gm = pp.gate_map()
    assert pp.gate_lookup(gm, "VEC_DRIVEN_OPEN_RECONCILE") == (
        "VEC_DRIVEN_ENABLED",
        "bool",
    )
    assert pp.gate_lookup(gm, "NO_SUCH_PATH_XYZ", set()) == (None, None)


def test_gate_lookup_knob_fallback_for_blocked_switch():
    gm = pp.gate_map()
    blocked = {"RULE_B_5M_EXIT_ENABLED"}
    assert pp.gate_lookup(gm, "RULE_B_5M_EXIT", blocked) == (
        "RULE_B_5M_EXIT_ENABLED",
        "bool",
    )
    assert pp.gate_lookup(gm, "RULE_B_5M_EXIT", set()) == (None, None)


def test_eligible_gates_on_days_events_and_sign():
    gm_path = "WT_3M_FORCE_OPEN"
    blocked = {"WT_3M_FORCE_OPEN_ENABLED"}
    inv = {gm_path: {"gate_knob": "WT_3M_FORCE_OPEN_ENABLED"}}
    ok = _scores([_row(gm_path, "A_LONG", 0.01, 3)], [_path(gm_path, 4.1)])
    assert len(pp.eligible(ok, inv, 4.0, 3, blocked)) == 1
    young = _scores([_row(gm_path, "A_LONG", 0.01, 3)], [_path(gm_path, 3.9)])
    assert pp.eligible(young, inv, 4.0, 3, blocked) == []
    neg = _scores([_row(gm_path, "A_LONG", -0.01, 3)], [_path(gm_path, 5.0)])
    assert pp.eligible(neg, inv, 4.0, 3, blocked) == []
    thin = _scores([_row(gm_path, "A_LONG", 0.01, 2)], [_path(gm_path, 5.0)])
    assert pp.eligible(thin, inv, 4.0, 3, blocked) == []


def test_eligible_enable_value_bool_true_inv_false():
    blocked = {"WT_3M_FORCE_OPEN_ENABLED", "ABLATION_DISABLE_DC_BREACH_REDUCE"}
    inv = {"WT_3M_FORCE_OPEN": {}, "DC_BREACH_REDUCE": {}}
    s = _scores(
        [
            _row("WT_3M_FORCE_OPEN", "A_LONG", 0.01, 3),
            _row("DC_BREACH_REDUCE", "B_LONG", 0.02, 3),
        ],
        [_path("WT_3M_FORCE_OPEN", 5.0), _path("DC_BREACH_REDUCE", 5.0)],
    )
    out = {c["path"]: c["value"] for c in pp.eligible(s, inv, 4.0, 3, blocked)}
    assert out == {"WT_3M_FORCE_OPEN": True, "DC_BREACH_REDUCE": False}


def test_eligible_excludes_protective_unblocked_and_tf():
    blocked = {"WT_3M_FORCE_OPEN_ENABLED"}
    inv = {"WT_3M_FORCE_OPEN": {}, "STOP_LOSS": {}, "GOLDEN_RULE": {}}
    s = _scores(
        [
            _row("WT_3M_FORCE_OPEN", "A_LONG", 0.01, 3),
            _row("GOLDEN_RULE", "A_LONG", 0.05, 9),
            _row("STOP_LOSS", "A_LONG", 0.05, 9),
        ],
        [
            _path("WT_3M_FORCE_OPEN", 5.0),
            _path("GOLDEN_RULE", 9.0),
            _path("STOP_LOSS", 9.0, kind="exit"),
        ],
    )
    out = pp.eligible(s, inv, 4.0, 3, blocked)
    assert [c["path"] for c in out] == ["WT_3M_FORCE_OPEN"]


def test_apply_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "DIR", tmp_path)
    monkeypatch.setattr(pp, "PDB", tmp_path / "fwd.db")
    monkeypatch.setattr(pp, "PJS", tmp_path / "fwd.json")
    monkeypatch.setattr(pp, "LEDGER", tmp_path / "ledger.jsonl")
    cands = [
        {
            "sym_side": "A_LONG",
            "path": "WT_3M_FORCE_OPEN",
            "venue": "crypto",
            "kind": "entry",
            "knob": "WT_3M_FORCE_OPEN_ENABLED",
            "value": True,
            "gate_kind": "bool",
            "evidence": {"mean_delta": 0.01, "scored": 3, "events": 3, "days": 4.5},
        }
    ]
    applied, _ = pp.apply(cands, live=False)
    assert applied == ["A_LONG:WT_3M_FORCE_OPEN_ENABLED"]
    assert not (tmp_path / "fwd.db").exists()
    assert pp.read_promoted() == {}


def test_apply_live_reapply_revoke_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "DIR", tmp_path)
    monkeypatch.setattr(pp, "PDB", tmp_path / "fwd.db")
    monkeypatch.setattr(pp, "PJS", tmp_path / "fwd.json")
    monkeypatch.setattr(pp, "LEDGER", tmp_path / "ledger.jsonl")
    cands = [
        {
            "sym_side": "A_LONG",
            "path": "WT_3M_FORCE_OPEN",
            "venue": "crypto",
            "kind": "entry",
            "knob": "WT_3M_FORCE_OPEN_ENABLED",
            "value": True,
            "gate_kind": "bool",
            "evidence": {"mean_delta": 0.01, "scored": 3, "events": 3, "days": 4.5},
        }
    ]
    applied, skipped = pp.apply(cands, live=True)
    assert applied == ["A_LONG:WT_3M_FORCE_OPEN_ENABLED"] and skipped == []
    assert (tmp_path / "fwd.json").exists()
    applied2, skipped2 = pp.apply(cands, live=True)
    assert applied2 == [] and skipped2 == ["A_LONG:WT_3M_FORCE_OPEN_ENABLED"]
    assert len(open(tmp_path / "ledger.jsonl").readlines()) == 1
    done = pp.revoke(["A_LONG:WT_3M_FORCE_OPEN_ENABLED"], "ROLLED_BACK", live=True)
    assert done == ["A_LONG:WT_3M_FORCE_OPEN_ENABLED"] and pp.read_promoted() == {}
    acts = [json.loads(ln)["action"] for ln in open(tmp_path / "ledger.jsonl")]
    assert acts == ["PROMOTED", "ROLLED_BACK"]


def test_reader_serves_active_and_honours_kill(tmp_path, monkeypatch):
    monkeypatch.setattr(fls, "DIR", tmp_path)
    monkeypatch.setattr(fls, "DB", tmp_path / "fwd.db")
    monkeypatch.setattr(fls, "JS", tmp_path / "fwd.json")
    monkeypatch.setattr(fls, "CONTROL", tmp_path / "CONTROL.json")
    fls.invalidate()
    import sqlite3

    con = sqlite3.connect(str(tmp_path / "fwd.db"))
    con.execute(pp.SCHEMA)
    con.execute(
        "INSERT INTO promoted VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "A_LONG:K",
            "A_LONG",
            "P",
            "crypto",
            "entry",
            "K",
            json.dumps({"value": True, "promo_id": "A_LONG:K"}),
            "active",
            time.time(),
            "{}",
            None,
        ),
    )
    con.commit()
    con.close()
    assert fls.get("A", "LONG", "K") == (True, "A_LONG:K")
    assert fls.get("A", "LONG", "OTHER") is None
    monkeypatch.setenv("FORWARD_AUTO_PROMOTE", "0")
    fls.invalidate()
    assert fls.get("A", "LONG", "K") is None
    monkeypatch.delenv("FORWARD_AUTO_PROMOTE")
    fls.invalidate()


def test_reader_backtest_modules_stay_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(fls, "DIR", tmp_path)
    monkeypatch.setattr(fls, "DB", tmp_path / "fwd.db")
    monkeypatch.setattr(fls, "JS", tmp_path / "fwd.json")
    monkeypatch.setattr(fls, "CONTROL", tmp_path / "CONTROL.json")
    import sqlite3

    con = sqlite3.connect(str(tmp_path / "fwd.db"))
    con.execute(pp.SCHEMA)
    con.execute(
        "INSERT INTO promoted VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "A_LONG:K",
            "A_LONG",
            "P",
            "crypto",
            "entry",
            "K",
            json.dumps({"value": True, "promo_id": "A_LONG:K"}),
            "active",
            time.time(),
            "{}",
            None,
        ),
    )
    con.commit()
    con.close()
    sys.modules["v12_quick_engine"] = object()
    fls.invalidate()
    try:
        assert fls.get("A", "LONG", "K") is None
    finally:
        del sys.modules["v12_quick_engine"]
        fls.invalidate()


def test_load_scores_refuses_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "DIR", tmp_path)
    (tmp_path / "scores.json").write_text(json.dumps({"per_sym": [], "paths": []}))
    import os

    old = time.time() - 40 * 3600
    os.utime(str(tmp_path / "scores.json"), (old, old))
    with pytest.raises(SystemExit) as e:
        pp.load_scores(30.0, False)
    assert e.value.code == 2


def test_hooks_sit_above_parity_blocks():
    ez = (ROOT / "ez_manage.py").read_text(errors="ignore")
    i_hook = ez.index("import forward_live_settings as _fls")
    i_block = ez.index("if _sub15m_blocked_ez(knob):", i_hook - 2000)
    assert 0 < i_block - i_hook < 1500
    assert "if _fls_hit is not None:" in ez[i_hook : i_hook + 400]
    tm = (ROOT / "tradier_manage.py").read_text(errors="ignore")
    j_hook = tm.index("import forward_live_settings as _fls_tr")
    j_block = tm.index("if _sub15m_blocked(param):", j_hook - 2000)
    assert 0 < j_block - j_hook < 1500
