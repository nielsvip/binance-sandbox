"""2026-10-24 BABA_SHORT 19:58Z DAYTRADE_TARGET dc_4h_low close + evaluate_stop NameError.

Rev 2 (destroy): the three live-site `_tgt = []` guards are GONE — superseded by a
central destroy in vec_decisions/dc_channel_exits.py, the one function BOTH engines
call (live grey twins + parity loop + v12 resolve/daytrade_dc_exit). Central kill =
no per-site guard can rot or be missed by a future caller.

1. evaluate_stop crashed with NameError: position_key on every cycle (only that
   bare name in the whole 4000-line function) — everything below it, including
   all stop legs, never evaluated. Pin: no unbound position_key loads remain.
2. DAYTRADE_TARGET was never a switch (verified: only DAYTRADE_DC_TARGET_TF /
   _BUFFER_PCT / TRADIER_DC_DAYTRADE_ENABLED exist) so per USER order it is
   DESTROYED at the center: resolve_daytrade_dc returns [] targets no matter which
   TF list, alias, or board value feeds it, and daytrade_dc_exit has no TARGET loop.
   STOPS stay live. Pin the central destroy functionally + in source.
"""
import re
from pathlib import Path

from vec_decisions.dc_channel_exits import daytrade_dc_exit, resolve_daytrade_dc

ROOT = Path(__file__).resolve().parent.parent


def _unbound_loads(path, func, name):
    import ast
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            bound = {a.arg for a in node.args.args} | {"self"}
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    bound.add(n.id)
            return sorted(
                {n.lineno for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == name and name not in bound}
            )
    raise AssertionError(f"{func} not found in {path}")


def test_evaluate_stop_has_no_bare_position_key():
    assert _unbound_loads("tradier_manage.py", "evaluate_stop", "position_key") == []


def _target_on_cfg():
    return {
        # every TARGET source ON at once: TF lists both venues + all 15m aliases
        "DAYTRADE_DC_TARGET_TF": "4h",
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "DC_DAYTRADE_ENABLED": True,
        "DC_DAYTRADE_TARGET_USE_DC4_15M": True,
        "DC_DAYTRADE_TARGET_USE_DC_15M": True,
        "TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M": True,
        "TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M": True,
        "DAYTRADE_DC_STOP_TF": "4h",
    }


def test_resolve_drops_all_target_specs_but_keeps_stops():
    stops, tgts = resolve_daytrade_dc(lambda k, d: _target_on_cfg().get(k, d))
    assert tgts == []
    assert len(stops) > 0


def test_daytrade_exit_never_fires_target_even_when_smuggled():
    smuggled = [{"tf": "4h", "field_long": "dc_high_4h", "field_short": "dc_low_4h", "buf": 0.001, "mode": "list", "tag": "dc_4h"}]
    lvl = lambda f: 100.0  # price sits exactly AT the target level
    for is_long, px in ((True, 100.0), (False, 100.0)):
        fired, reason = daytrade_dc_exit(px, is_long, [], smuggled, lvl)
        assert (fired, reason) == (False, "")


def test_daytrade_stops_still_fire_both_sides():
    stops, _ = resolve_daytrade_dc(lambda k, d: _target_on_cfg().get(k, d))
    lvl = lambda f: 100.0
    fired, reason = daytrade_dc_exit(50.0, True, stops, [], lvl)  # long deep under stop
    assert fired and reason.startswith("DAYTRADE_STOP")
    fired, reason = daytrade_dc_exit(150.0, False, stops, [], lvl)  # short deep over stop
    assert fired and reason.startswith("DAYTRADE_STOP")


def test_no_daytrade_target_reason_producer_in_source():
    src = (ROOT / "vec_decisions" / "dc_channel_exits.py").read_text()
    assert re.findall(r"""["']DAYTRADE_TARGET """, src) == []


def test_daytrade_stop_specs_still_resolved():
    for path in ["tradier_manage.py", "ez_manage.py"]:
        src = (ROOT / path).read_text()
        assert "resolve_daytrade_dc" in src
