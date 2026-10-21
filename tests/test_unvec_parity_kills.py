"""USER 2026-10-21: 42 unvectorized live paths switched OFF until parity + forward test.

Contracts: (1) every kill switch in KILL_SWITCHES defaults False in its config
class (path inert at defaults); (2) each switch is read at the head of its
listed function(s) via a global-only getattr (no board can re-enable it);
(3) ez_manage.check_entry_trigger (sub-15m leg) refuses at defaults with the
SUB15M_TRIGGER_OFF_PARITY reason (behavioral, imports the real module).
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
import config_tradier as CT  # noqa: E402

CRYPTO_KILLS = {
    "ENABLE_EPQ_ENTRY_STREAMS": ("ez_positions_quick.py", ("quick_entry_monitor_loop", "bulk_entry_scan_loop")),
    "ENABLE_EPQ_EXIT_PRIORITY": ("ez_positions_quick.py", ("priority_exit_scan_loop",)),
    "ENABLE_EPQ_SENT_SCANNER": ("ez_positions_quick.py", ("_scan_for_entries",)),
    "ENABLE_EPQ_EXEC_ENTRY": ("ez_positions_quick.py", ("_execute_entry",)),
    "ENABLE_EPQ_TRANSITION_ENTRY": ("ez_positions_quick.py", ("transition_to_entry",)),
    "ENABLE_EPQ_FORCE_ENTRY": ("ez_positions_quick.py", ("_force_fresh_entry",)),
    "ENABLE_EPQ_REENTRY_DISPATCH": ("ez_positions_quick.py", ("_dispatch_reentry_guaranteed",)),
    "ENABLE_EZ_REENTRY_PERIODIC": ("ez_manage.py", ("periodic_evaluate_reentry_loop",)),
    "ENABLE_BTC_DEDICATED_ENTRY": ("ez_positions_quick.py", ("_btc_dedicated_entry_decision",)),
    "ENABLE_SCALP_V3_OPEN": ("ez_positions_quick.py", ("_scalp_v3_attempt_open",)),
    "ENABLE_SUB15M_ENTRY_TRIGGER": ("ez_manage.py", ("check_entry_trigger",)),
}
TRADIER_KILLS = {
    "ENABLE_TR_SCALP_OPEN": ("tradier_manage.py", ("_open_scalp",)),
    "ENABLE_TR_DAYTRADE_WING": ("tradier_manage.py", ("_scan_dc_entries",)),
    "ENABLE_TR_REENTRY_MONITOR": ("tradier_manage.py", ("reentry_monitor_loop",)),
    "ENABLE_TR_REENTRY_DISPATCH": ("tradier_manage.py", ("_dispatch_reentry_guaranteed_trd",)),
    "TR_HIGH_GAIN_REOPEN_ENABLED": ("tradier_manage.py", ("periodic_direct_high_gain_reopen",)),
    "ENABLE_TR_SWING_ENTRIES": ("tradier_manage.py", ("is_swing_entry_allowed",)),
    "ENABLE_TR_SYMGATE_REENTRY": ("tradier_manage.py", ("would_exit_trigger_now",)),
    "ENABLE_TR_REENTRY_PERIODIC": ("tradier_manage.py", ("periodic_evaluate_reentry_loop",)),
}


def _fn_head_source(path, name):
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment((ROOT / path).read_text(), node) or ""
    raise AssertionError(f"{name} not found in {path}")


def test_crypto_kills_default_false():
    cfg = C.Config()
    for switch in CRYPTO_KILLS:
        assert getattr(cfg, switch, None) is False, switch


def test_tradier_kills_default_false():
    cfg = CT.TradierConfig()
    for switch in list(TRADIER_KILLS) + ["UVE_LIVE_ENABLED"]:
        assert getattr(cfg, switch, None) is False, switch


def test_kill_sites_at_function_heads():
    for switch, (path, names) in {**CRYPTO_KILLS, **TRADIER_KILLS}.items():
        for name in names:
            head = _fn_head_source(path, name)[:1200]
            assert switch in head, f"{switch} not at head of {path}:{name}"


def test_sub15m_trigger_refuses_at_defaults():
    import ez_manage as E

    ok, reason = E.check_entry_trigger({"k_1m": 1.0}, True)
    assert ok is False
    assert reason == "SUB15M_TRIGGER_OFF_PARITY"


def test_backstop_switches_default_on():
    assert C.Config().VEC_BACKSTOP_SYNC_ENABLED is True
    assert CT.TradierConfig().VECTOR_ENFORCE_ENABLED is True


def test_enforce_venue_override_and_pass_reasons():
    from types import SimpleNamespace

    from live_twins import vector_enforce as V

    assert V.enabled() is True
    assert V.enabled(CT.TradierConfig()) is True
    assert V.enabled(SimpleNamespace(VECTOR_ENFORCE_ENABLED=False)) is False
    assert V.check("OPEN", "x:Y_LONG", "whatever", SimpleNamespace(VECTOR_ENFORCE_ENABLED=False)) == (True, "ENFORCE_OFF")
    assert V.check("CLOSE", "trb:AAPL_LONG", "BOARD_CONVERGE_CLOSE vec=abc")[0] is True
    assert V.check("CLOSE", "x:Y_LONG", "FIXED_STOP_BREACH exit")[0] is True
    assert V.check("OPEN", "x:Y_LONG", "B12 |VEC_EXACT")[0] is True


def test_backstop_wiring_present():
    src_ez = (ROOT / "ez_manage.py").read_text()
    assert "def _vec_exact_process_position(account_key, position_key, trade_manager, _backstop_only=False)" in src_ez
    assert "_backstop_only=True" in src_ez
    assert "VEC_BACKSTOP_SYNC_ENABLED" in src_ez
    src_tm = (ROOT / "tradier_manage.py").read_text()
    assert src_tm.count("vector_enforce as _ve_") >= 2
