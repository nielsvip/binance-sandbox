"""Settlement-parity hook (2026-10-14): stock boards are evaluated as cash_settlement.board_account(sym, side, TradierConfig());
SIM_ACCOUNT is GLOBAL_ONLY (never in a set), injected only at evaluation time and only when != the engine default trb."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402
from tools.opt import v12_pilot as VP  # noqa: E402


def test_sim_account_is_global_only():
    s, w = P.sanitize_overrides({"SIM_ACCOUNT": "tra", "WT_EXIT_ENABLED": True}, {})
    assert "SIM_ACCOUNT" not in s and "SIM_ACCOUNT" in w
    assert "SIM_ACCOUNT" not in P._clean_ingested_overrides({"SIM_ACCOUNT": "tra"})


def test_hook_injects_only_at_eval_and_only_when_not_default(monkeypatch):
    from vec_decisions import cash_settlement as CS
    seen = {}
    orig_ps, orig_s = VP.evaluate_prepared_sanitized, VP.evaluate_sanitized
    monkeypatch.setattr(VP, "evaluate_prepared_sanitized", lambda p, o, w=30, i=False: seen.setdefault("ov", dict(o)) or {})
    monkeypatch.setattr(VP, "evaluate_sanitized", orig_s)
    monkeypatch.delattr(VP, "_v15_sim_account_hooked", raising=False)
    monkeypatch.setattr(CS, "board_account", lambda sym, side, cfg=None, live_accounts=None: "trb")
    assert P.install_sim_account_hook("AAPL_LONG") == "trb"
    VP.evaluate_prepared_sanitized(None, {"X": 1})
    assert "SIM_ACCOUNT" not in seen["ov"]  # default account: untouched (results + cache keys identical)
    seen.clear()
    monkeypatch.setattr(CS, "board_account", lambda sym, side, cfg=None, live_accounts=None: "trc")
    assert P.install_sim_account_hook("AAPL_LONG") == "trc"
    src = {"X": 1}
    VP.evaluate_prepared_sanitized(None, src)
    assert seen["ov"]["SIM_ACCOUNT"] == "trc" and "SIM_ACCOUNT" not in src  # caller's set never mutated
    assert P.install_sim_account_hook("BTCUSDC_LONG") is None  # crypto: no hook
    monkeypatch.setattr(VP, "evaluate_prepared_sanitized", orig_ps)
