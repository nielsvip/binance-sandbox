"""2026-10-17 USER: "make sure nothing can open or close without vec approval for now".

This pins the invariant rather than re-implementing it: every order-emitting path must be reachable ONLY through
execute_now, and execute_now must enforce STRICT_VEC_PARITY on entries AND exits.

It also pins the two DELIBERATE exceptions, because they are the ones that must never be "tightened" by a future
edit. CLAUDE.md is absolute: "no exit may EVER be skipped AT ALL for ANY REASON". A gate that can refuse a
vector-dictated close strands money — that is the 2026-09-28 stranded-losers failure. So:
  * the exit_guarantee FORCE leg places a market close without the entry gates, by design;
  * protective stop placement is a resting CLOSE order of the FIXED_STOP family, by design;
and every vector-emitted exit family is allowlisted in vec_paths/vec_parity_gate.py so GATE_EXITS=True can never
block a real stop. Source-level only: no network, no live config mutation.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _funcs(src):
    tree = ast.parse(src)
    return [(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _enclosing(funcs, ln):
    hits = [f for f in funcs if f[0] <= ln <= f[1]]
    hits.sort(key=lambda f: f[1] - f[0])
    return hits[0][2] if hits else "<module>"


def _callers_of(src, target):
    funcs = _funcs(src)
    out = set()
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(rf"[^_a-zA-Z]{target}\s*\(", line) and "def " not in line:
            out.add(_enclosing(funcs, i))
    return out


def _gate_body(src, inner="_execute_now_inner"):
    """Name of the function holding execute_now's body, asserting the split has not become a bypass.

    2026-10-17: execute_now became a thin wrapper (post-fill "an entry is not done without an exchange stop")
    around its unchanged body, now `_execute_now_inner`. That is still ONE door only while the body has exactly one
    caller — execute_now itself. If anything else ever learns the inner name, the gate has been routed around and
    CLAUDE.md's "ALL orders go through execute_now()" is broken, so this fails loudly rather than adapting.
    """
    if f"def {inner}(" not in src:
        return "execute_now"
    callers = _callers_of(src, inner)
    assert callers == {"execute_now"}, f"{inner} is execute_now's body — it must have exactly one caller, found {sorted(callers)}"
    return inner


def test_crypto_order_paths_are_reachable_only_from_execute_now():
    """CLAUDE.md: "No futures_create_order or REST /fapi/v1/order calls outside execute_now"."""
    src = (ROOT / "ez_manage.py").read_text()
    funcs = _funcs(src)
    emitters = {_enclosing(funcs, i) for i, line in enumerate(src.splitlines(), 1)
                if "futures_create_order" in line and not line.lstrip().startswith("#") and '"""' not in line}
    allowed = {
        "place_maker_order",            # ← execute_now only
        "send_webhook",                 # the DIRECT market wire leg (legacy name, "replaces Finandy webhook")
        "_fixed_stop_taker_backstop",   # FIXED_STOP algo leg, ← execute_now only
        "_exit_force_market",           # exit_guarantee FORCE leg — a close, never blockable
        "initialize",                   # NOT an emitter: wrap_binance_client() names the method while wrapping it
        "execute_now",
        "_execute_now_inner",           # 2026-10-17: execute_now's unchanged BODY (see _gate_body below)
    }
    assert emitters <= allowed, f"a NEW crypto order emitter appeared outside the known set: {sorted(emitters - allowed)}"
    # the three real wire legs must stay downstream of the gate
    assert _callers_of(src, "place_maker_order") == {_gate_body(src)}
    assert _callers_of(src, "_fixed_stop_taker_backstop") == {_gate_body(src)}
    assert _callers_of(src, "send_webhook") <= {_gate_body(src), "place_maker_order"}, \
        "send_webhook is the wire leg; a caller outside the gate body/place_maker_order would bypass the parity gate"


def test_stock_order_paths_are_accounted_for():
    src = (ROOT / "tradier_manage.py").read_text()
    funcs = _funcs(src)
    emitters = {_enclosing(funcs, i) for i, line in enumerate(src.splitlines(), 1)
                if re.search(r"\.place_order\(|_odg_raw_place_order", line) and not line.lstrip().startswith("#")}
    _gate_body(src)  # the stocks gate split the same way on 2026-10-17; prove it is still one door
    allowed = {
        "execute_now",                        # the gate
        "_execute_now_inner",                 # 2026-10-17: execute_now's unchanged BODY
        "place_order",                        # the manager's own wire method, called by execute_now
        "_exit_force_market_stocks",          # exit_guarantee FORCE leg — a close, never blockable
        "_fixed_stop_order_action_tradier",   # protective stop (FIXED_STOP family) — a close, never blockable
    }
    assert emitters <= allowed, f"a NEW stock order emitter appeared outside the known set: {sorted(emitters - allowed)}"


def test_strict_vec_parity_gates_entries_and_exits_and_is_not_shadow():
    from config import Config
    from config_tradier import TradierConfig

    for name, cfg in (("crypto", Config()), ("stocks", TradierConfig())):
        assert getattr(cfg, "STRICT_VEC_PARITY_MODE", False) is True, f"{name}: parity enforcement is OFF"
        assert getattr(cfg, "STRICT_VEC_PARITY_GATE_ENTRIES", False) is True, f"{name}: entries not gated"
        assert getattr(cfg, "STRICT_VEC_PARITY_GATE_EXITS", False) is True, f"{name}: exits not gated"
        assert getattr(cfg, "STRICT_VEC_PARITY_SHADOW", True) is False, f"{name}: still SHADOW (logs, refuses nothing)"


def test_every_nonnegotiable_stop_is_allowlisted_so_exits_are_never_stranded():
    """GATE_EXITS=True is only safe because each vector-emitted stop family is allowlisted. Losing one of these
    re-creates the 2026-09-28 stranded-losers failure (327 crypto FIXED_STOP closes in 3 days would have blocked)."""
    gate = (ROOT / "vec_paths" / "vec_parity_gate.py").read_text()
    for token in ("FIXED_STOP", "DAYTRADE_STOP", "DAYTRADE_TARGET", "TECHNICAL_TARGET",
                  "ULTIMATE_DC_", "VIGILANCE_DC4", "UNCOND_DC_", "GAP_MOC_EXIT"):
        assert f'"{token}"' in gate, f"{token} missing from the vec exit allowlist — GATE_EXITS would block it"


def test_live_only_closes_remain_blocked():
    """The other half of "nothing closes without vec approval": closes the engine never emits must NOT pass."""
    gate = (ROOT / "vec_paths" / "vec_parity_gate.py").read_text()
    blocked_note = gate[gate.index("NOT vector exits"):]
    for token in ("PRE_CLOSE", "E3_STRUCT_EXIT", "Market_Against_Position_Bias"):
        assert token in blocked_note, f"{token} should be documented as a blocked live-only close"
        assert f'"{token}"' not in gate.split("NOT vector exits")[0], f"{token} must NOT be in the allowlist"
