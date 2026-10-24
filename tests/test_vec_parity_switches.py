"""USER 2026-10-24: every VEC_LAST_* intervention names a path to fix — 10 parity
switches pin the vec backstop to native producers. Source-level pins, helpers
tested directly (mirrors test_ratio_rebalance_master.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vec_decisions import vec_parity_gates as G

SWITCHES = {
    "VEC_OVERRIDE_ENABLED": True,
    "VEC_OVERRIDE_BYPASS_CIRCUIT_BREAKER": True,
    "VEC_REFUSE_NON_VECTOR_EXIT_ENABLED": True,
    "VEC_REFUSE_QUICK_REDUCE_ENABLED": True,
    "VEC_DISAGREE_KILL_ENABLED": False,
    "VEC_DISAGREE_FLAT_OPEN_BLOCK": True,
    "VEC_PENDING_TTL_S": 300.0,
    "VEC_PENDING_DEFER_NATIVE_ENABLED": True,
    "VEC_HOLD_EXPIRY_FLATTEN_ENABLED": True,
    "VEC_CONFIRM_REQUIRE_BOARD_ENABLED": True,
}

TABS = {
    "VEC_OVERRIDE_ENABLED": "GLOBAL_RISK_GATES",
    "VEC_OVERRIDE_BYPASS_CIRCUIT_BREAKER": "GLOBAL_RISK_GATES",
    "VEC_REFUSE_NON_VECTOR_EXIT_ENABLED": "EXIT_STRUCTURAL",
    "VEC_REFUSE_QUICK_REDUCE_ENABLED": "EXIT_VELOCITY",
    "VEC_DISAGREE_KILL_ENABLED": "GLOBAL_RISK_GATES",
    "VEC_DISAGREE_FLAT_OPEN_BLOCK": "ENTRY_CONFIRMATION_GATES",
    "VEC_PENDING_TTL_S": "GLOBAL_RISK_GATES",
    "VEC_PENDING_DEFER_NATIVE_ENABLED": "ENTRY_CONFIRMATION_GATES",
    "VEC_HOLD_EXPIRY_FLATTEN_ENABLED": "EXIT_STRUCTURAL",
    "VEC_CONFIRM_REQUIRE_BOARD_ENABLED": "GLOBAL_RISK_GATES",
}


def test_config_and_quickconfig_defaults():
    import config as C
    from v12_quick_engine import QuickConfig

    for sw, default in SWITCHES.items():
        assert getattr(C.Config, sw) == default, sw
        assert getattr(QuickConfig, sw) == default, sw


def test_override_verdict():
    assert (
        G.override_verdict("VEC_LAST_CLEAN", "", "", True, True)[0] == "VEC_LAST_CLEAN"
    )
    assert (
        G.override_verdict(
            "VEC_LAST_OVERRIDE", "CIRCUIT_BREAKER", "NATIVE_X", True, True
        )[0]
        == "VEC_LAST_OVERRIDE"
    )
    tag, detail, _ = G.override_verdict(
        "VEC_LAST_OVERRIDE", "CIRCUIT_BREAKER", "NATIVE_X", False, True
    )
    assert tag == "VEC_LAST_REFUSE" and detail == "NATIVE_X"
    tag, detail, _ = G.override_verdict(
        "VEC_LAST_OVERRIDE", "CIRCUIT_BREAKER", "", False, True
    )
    assert tag == "VEC_LAST_CLEAN" and detail == "VEC_OVERRIDE_DISABLED"
    tag, detail, _ = G.override_verdict(
        "VEC_LAST_OVERRIDE", "CIRCUIT_BREAKER", "NATIVE_X", True, False
    )
    assert tag == "VEC_LAST_REFUSE" and detail == "NATIVE_X"
    tag, detail, _ = G.override_verdict(
        "VEC_LAST_OVERRIDE", "OTHER_GATE", "", True, False
    )
    assert tag == "VEC_LAST_OVERRIDE"


def test_non_vector_exit_refused():
    assert G.non_vector_exit_refused("REDUCE X", False, True) is False
    assert G.non_vector_exit_refused("QUICK_REDUCE_REDUCE_k", True, False) is False
    assert G.non_vector_exit_refused("QUICK_BB_RECOVERY_EXIT_T", True, False) is False
    assert G.non_vector_exit_refused("QUICK_REDUCE_REDUCE_k", True, True) is True
    assert G.non_vector_exit_refused("RATIO_CLOSE_X", True, True) is True


def test_flat_open_block_never_closes():
    assert G.flat_open_blocked("OPEN", "B12_ENTRY", True, True) is True
    assert G.flat_open_blocked("AUGMENT", "B12_ENTRY", True, True) is True
    assert G.flat_open_blocked("CLOSE", "B12_ENTRY", True, True) is False
    assert G.flat_open_blocked("REDUCE", "B12_ENTRY", True, True) is False
    assert G.flat_open_blocked("OPEN", "FIXED_STOP_LONG_px", True, True) is False
    assert G.flat_open_blocked("OPEN", "HEDGE_OPEN_X", True, True) is False
    assert G.flat_open_blocked("OPEN", "B12_ENTRY", True, False) is False
    assert G.flat_open_blocked("OPEN", "B12_ENTRY", False, True) is False


def test_pending_confirm_and_ttl():
    assert G.disagree_refuse(True, True) is True
    assert G.disagree_refuse(True, False) is False
    assert G.pending_defer_on(True) is True
    assert G.pending_ttl_s(300.0) == 300.0
    assert G.pending_ttl_s(99999) == 3600.0
    assert G.pending_ttl_s(0) == 60.0
    assert G.pending_ttl_s(None) == 300.0
    assert G.hold_expiry_flatten_on(True) is True
    assert G.confirm_release_ok(True, False, True) is True
    assert G.confirm_release_ok(False, True, True) is False
    assert G.confirm_release_ok(False, True, False) is True
    assert G.confirm_release_ok(False, False, False) is False


def test_live_gates_wired():
    src = (ROOT / "ez_manage.py").read_text()
    assert "vec_parity_gates" in src
    for sw in SWITCHES:
        assert f'"{sw}"' in src, sw


def test_template_rows_crypto():
    for fn in ("TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx"):
        wb = openpyxl.load_workbook(
            ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / fn, read_only=True
        )
        try:
            for sw, tab in TABS.items():
                ws = wb[tab]
                hits = [
                    r[0].value
                    for r in ws.iter_rows(min_row=3, max_col=1)
                    if r[0].value == sw
                ]
                assert len(hits) >= 2, f"{fn}:{tab}:{sw}"
        finally:
            wb.close()
