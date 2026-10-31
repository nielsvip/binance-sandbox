import ast
from pathlib import Path
import numpy as np
import pytest
import v12_quick_engine as V
from vec_decisions import live_exit_chain
from tests.test_quick_srs_paired_15m import _cfg, _npz

@pytest.mark.parametrize("full_close", [True, False])
def test_refused_first_exit_does_not_read_or_reuse_a_previous_fill(monkeypatch, full_close):
    data = _npz()
    data["wt1_D"][:] = 20.0
    data["wt2_D"][:] = 10.0
    cfg = _cfg()
    cfg.HTF_TREND_VETO_ON_REDUCE_ENABLED = True
    cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = False
    monkeypatch.setattr(live_exit_chain, "step", lambda *args, **kwargs: ("REFUSED_TEST_EXIT", full_close))
    result = V.simulate_one(data, "REFUSED_EXIT_TEST", True, cfg, force_initial_seed=True)
    assert not any(e.get("type") in ("CLOSE", "REDUCE") and not e.get("unrealised") for e in result["ledger"])
    opened = next(e for e in result["ledger"] if e.get("type") == "OPEN")
    final = next(e for e in result["ledger"] if e.get("unrealised"))
    assert final["qty"] == pytest.approx(opened["qty"])


def test_rejected_append_cannot_commit_or_attach_projection_to_older_event():
    source = ast.parse(Path(V.__file__).read_text())
    cls = next(n for n in ast.walk(source) if isinstance(n, ast.ClassDef) and n.name == "_ExitGateLedger")
    commit = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == "_account_partial_commit")
    allowed = [True]
    ns = {"_exit_event_accept": lambda *args: allowed[0], "_partial_audit": {"accepted_count": 0}}
    exec(compile(ast.Module(body=[cls, commit], type_ignores=[]), "actual_exit_bookkeeping", "exec"), ns)
    ledger = ns["_ExitGateLedger"]("trades")
    prior = {"type": "CLOSE"}
    ledger.append(prior)
    assert ledger.last_append_event is prior
    allowed[0] = False
    ledger.append({"type": "CLOSE"})
    assert ledger.last_append_event is None and ledger == [prior]
    position = {"qty": 10}
    ns["_account_partial_commit"](position, {"account_projected_qty_after": 3}, ledger.last_append_event)
    assert position == {"qty": 10}
    assert ns["_partial_audit"]["accepted_count"] == 0
    assert "account_partial_projection" not in prior
