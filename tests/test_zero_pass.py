"""zero_pass (USER 2026-10-21): every calculated sym_side trades; negatives at floor, never refused."""

import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import zero_pass as zp


def _prog(d, name, gain=10.0, verdict=None, age_s=3600):
    p = d / name
    doc = {"cumulative_gain": gain, "final_gain_fresh_vec": gain}
    if verdict:
        doc["verdict"] = verdict
    p.write_text(json.dumps(doc))
    old = time.time() - age_s
    import os

    os.utime(p, (old, old))
    return p


def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(zp, "ROOT", tmp_path)
    monkeypatch.setattr(zp, "PROGRESS_DIR", tmp_path / "prog")
    monkeypatch.setattr(zp, "SELECTION", tmp_path / "sel.json")
    (tmp_path / "prog").mkdir()
    zp._SCAN.update(
        checked=0.0, files={}, gains={}, sel_mtime=None, sel_syms=frozenset()
    )
    return tmp_path


def _stub_switches(monkeypatch, trade_all=True, neg_floor=True):
    mod = types.ModuleType("config")
    mod.Config = type(
        "Config",
        (),
        {"ZERO_PASS_TRADE_ALL": trade_all, "ZERO_PASS_NEG_FLOOR": neg_floor},
    )
    monkeypatch.setitem(sys.modules, "config", mod)
    tmod = types.ModuleType("config_tradier")
    tmod.TradierConfig = type("TradierConfig", (), {})
    monkeypatch.setitem(sys.modules, "config_tradier", tmod)


def test_calculated_via_board_and_fresh_backtest(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    _stub_switches(monkeypatch)
    (tmp_path / "sel.json").write_text(json.dumps({"sym_sides": {"ARUSDT_LONG": {}}}))
    _prog(tmp_path / "prog", "KSMUSDT_SHORT_v14_progress.json", gain=-4.0)
    assert zp.calculated("ARUSDT", "LONG") is True
    assert zp.calculated("KSMUSDT", "SHORT") is True
    assert zp.calculated("NOPEUSDT", "LONG") is False


def test_stale_backtest_and_bad_verdict_not_calculated(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    _stub_switches(monkeypatch)
    _prog(tmp_path / "prog", "OLDUSDT_LONG_v14_progress.json", age_s=10 * 86400)
    _prog(tmp_path / "prog", "DEADUSDT_SHORT_v14_progress.json", verdict="NO_TRADES")
    assert zp.calculated("OLDUSDT", "LONG") is False
    assert zp.calculated("DEADUSDT", "SHORT") is False


def test_gain_prefers_cumulative_then_fresh_vec(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    _stub_switches(monkeypatch)
    _prog(tmp_path / "prog", "AUSDT_LONG_v14_progress.json", gain=12.0)
    assert zp.gain("AUSDT", "LONG") == 12.0
    assert zp.gain("MISSING", "LONG") is None


def test_neg_only_when_floor_on_and_gain_negative(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    _stub_switches(monkeypatch, neg_floor=True)
    _prog(tmp_path / "prog", "NEGUSDT_LONG_v14_progress.json", gain=-3.0)
    _prog(tmp_path / "prog", "POSUSDT_LONG_v14_progress.json", gain=5.0)
    assert zp.neg("NEGUSDT", "LONG") is True
    assert zp.neg("POSUSDT", "LONG") is False
    assert zp.neg("MISSING", "LONG") is False
    _stub_switches(monkeypatch, neg_floor=False)
    assert zp.neg("NEGUSDT", "LONG") is False


def test_switches_default_true_without_config(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    monkeypatch.delitem(sys.modules, "config", raising=False)
    monkeypatch.delitem(sys.modules, "config_tradier", raising=False)
    assert zp.trade_all_on() is True and zp.neg_floor_on() is True


def test_gates_reference_zero_pass_bypass():
    ez = (ROOT / "ez_manage.py").read_text()
    assert "zero_pass" in ez and "ZERO_PASS_ALLOW" in ez
    for needle in (
        "BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED",
        "BLOCKED_NO_365D_CONFIRM",
        "BLOCKED_GATES_A_",
        "BLOCKED_PARITY_FILTER_TF_",
        "BLOCKED_PARITY_STRICT_OPEN_",
        "BLOCKED_BOARD_ENTRY_",
    ):
        assert needle in ez, needle
    assert ez.count("ZERO_PASS_ALLOW") >= 6, "every disarmed veto logs its allow"
    assert "ZERO_PASS_NO_SUPPRESS" in ez
    q = (ROOT / "ez_positions_quick.py").read_text()
    assert "zero_pass" in q
    tm = (ROOT / "tradier_manage.py").read_text()
    assert "zero_pass" in tm and "ZERO_PASS_ALLOW" in tm


def test_no_suppress_keeps_correctness_and_infra():
    ez = (ROOT / "ez_manage.py").read_text()
    for needle in (
        "BLOCKED_INVALID_POSITION_KEY",
        "BLOCKED_POSITIONS_BROKER_UNREACHABLE",
        "BLOCKED_PARITY_DEDUP_",
        "BLOCKED_NO_DOUBLE_OPEN_",
        "BLOCKED_EMERGENCY_BRAKE_10_PER_MIN_CHURN",
    ):
        assert needle in ez, needle
    assert "ZERO_PASS" not in ez[ez.index("BLOCKED_INVALID_POSITION_KEY") - 200 : ez.index("BLOCKED_INVALID_POSITION_KEY")], "correctness refusals stay unconditional"
