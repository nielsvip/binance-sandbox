"""Forward-parity enforcer: finds the no-trade fault classes, fixes only what is safe.

Covers USER 2026-10-21 barbaric rule tooling: gate incoherence (natives in
charge + hermetic enforce), corrupt/empty universes, timeframe refusal, and
the never-orders contract.
"""

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import forward_parity_enforcer as fpe


def _stub_config(monkeypatch, vec_exact_mode):
    mod = types.ModuleType("config")
    mod.Config = type("Config", (), {"PARITY_VEC_EXACT_MODE": vec_exact_mode})
    monkeypatch.setitem(sys.modules, "config", mod)


def test_gate_incoherence_detected_and_fixed(tmp_path, monkeypatch):
    monkeypatch.setattr(fpe, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "hermetic_mode.json").write_text(
        json.dumps({"mode": "enforce"})
    )
    (tmp_path / "backups").mkdir()
    _stub_config(monkeypatch, False)
    gate = fpe.check_gate_coherence()
    assert gate["fault"] is True and gate["hermetic_mode"] == "enforce"
    res = fpe.fix_gate_coherence()
    assert res["fixed"] is True
    assert (
        json.loads((tmp_path / "data" / "hermetic_mode.json").read_text())["mode"]
        == "log"
    )


def test_gate_coherent_when_twin_in_charge(tmp_path, monkeypatch):
    monkeypatch.setattr(fpe, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "hermetic_mode.json").write_text(
        json.dumps({"mode": "enforce"})
    )
    _stub_config(monkeypatch, True)
    assert fpe.check_gate_coherence()["fault"] is False


def test_corrupt_universe_repaired_with_identical_membership(tmp_path, monkeypatch):
    monkeypatch.setattr(fpe, "ROOT", tmp_path)
    (tmp_path / "backups").mkdir()
    p = tmp_path / "symbols_men_long.json"
    p.write_text('[\n  "AAAUSDT",\n  "BBBUSDC",\n]')
    rows = fpe.check_universes()
    men = [r for r in rows if r["acct"] == "men" and r["side"] == "long"][0]
    assert men["status"] == "CORRUPT_JSON" and men["recoverable"] is True
    res = fpe.fix_universe_syntax("men", "long")
    assert res["fixed"] is True and res["n"] == 2
    assert sorted(json.loads(p.read_text())) == ["AAAUSDT", "BBBUSDC"]


def test_empty_universe_reported_never_refilled(tmp_path, monkeypatch):
    monkeypatch.setattr(fpe, "ROOT", tmp_path)
    (tmp_path / "symbols_inf_long.json").write_text("[]")
    rows = fpe.check_universes()
    inf = [r for r in rows if r["acct"] == "inf" and r["side"] == "long"][0]
    assert inf["status"] == "EMPTY"
    assert fpe.fix_universe_syntax("inf", "long")["fixed"] is False
    assert (tmp_path / "symbols_inf_long.json").read_text() == "[]"


def test_non_15m_timeframe_refused(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["forward_parity_enforcer.py", "--timeframe", "5m"]
    )
    assert fpe.main() == 2, "must refuse 1/3/5m"
    assert "REFUSED" in capsys.readouterr().out


def test_enforcer_never_touches_orders():
    src = Path(fpe.__file__).read_text()
    assert "futures_create_order" not in src
    assert "fapi/v1/order" not in src
    assert "execute_now(" not in src
