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
from vec_paths import vec_parity_gate as P

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
    "VEC_DICTATE_ONLY_ENABLED": True,
    "VEC_EXEC_BOARD_OPEN_ENABLED": True,
    "VEC_EXEC_BOARD_CLOSE_ENABLED": True,
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
    "VEC_DICTATE_ONLY_ENABLED": "GLOBAL_RISK_GATES",
    "VEC_EXEC_BOARD_OPEN_ENABLED": "ENTRY_CONFIRMATION_GATES",
    "VEC_EXEC_BOARD_CLOSE_ENABLED": "EXIT_STRUCTURAL",
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


def test_stock_shadow_reasons_with_quick_engine_twins_are_vec_achievable():
    # These reasons appeared in rotated Tradier STRICT_VEC_PARITY_SHADOW logs.
    # Each is backed by an existing v12 QuickEngine mask/stateful path.
    for reason in (
        "HARDCODED_RALLY_REENTRY_LONG_close1679>exit1658",
        "HTF_WT_CHURN_REENTRY_SHORT_age96m",
        "REENTRY_PRICE_CROSS_BACK_exit982.63_cur998.89_dist1.66%_age8680m",
        "MANDATORY_REENTRY_PRICE_CROSS_exit17.75_cur17.74_dist0.03%_age2m_CROSSED_BACK",
        "WT_SIMPLE_GUARANTEE_L_wt1=89.9_wt2=83.1",
        "TRADIER_DC_BREAKOUT_S_adx35",
        "WT_15M_BOUNCE_OPEN_S_wt1=-61.2_wt2=-60.8",
        "ALL_TF_AGAINST_CLOSE_1of5_g-0.56%",
        "GAP_RISK_EXIT_A_OPEN_RECLAIM+B_STRUCT_BREAK_g0.65%",
        "WICK_REJECT_EXIT_15m_upper-wick1.00_g-0.23",
        "E_1_WT_DELTA_EXIT_delta=-60_thr=50_g-0.04",
        "LR_BAND_LADDER_PARITY_TARGET_x5.260_usd10521_4h",
        "WT_SIMPLE_GUARANTEE_15m_wt10vs12",
    ):
        assert P.is_vec_achievable(reason), reason
    # The gate must continue to distinguish live-only exits with no vector emitter.
    assert not P.is_vec_achievable("E3_STRUCT_EXIT_2of3_g0.00%")


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


def test_dictate_only_exemptions():
    # USER 2026-10-25 vec-parity-only: RATIO trims lost their exemption — only
    # board-confirmed speech and per_sym dictate survive.
    assert G.dictate_only_exempt("REDUCE", "RATIO_CLOSE_SHORT_X") is False
    assert G.dictate_only_exempt("AUGMENT", "RATIO_AUGMENT_X") is False
    assert G.dictate_only_exempt("OPEN", "RATIO_OPEN_X") is False
    assert G.dictate_only_exempt("CLOSE", "RATIO_CLOSE_X") is False
    assert G.dictate_only_exempt("CLOSE", "FIXED_STOP_LONG_px") is False
    assert G.dictate_only_exempt("CLOSE", "LIQUIDATION_PROTECT_X") is False
    assert G.dictate_only_exempt("OPEN", "HEDGE_OPEN_X") is False
    assert G.dictate_only_exempt("REDUCE", "OVERSIZE_TRIM_X") is False
    assert G.dictate_only_exempt("OPEN", "VEC_DRIVEN_OPEN_PURE_BOARD_X") is True
    assert G.dictate_only_exempt("CLOSE", "QUICK_REDUCE_X") is False


def test_dictate_only_persym_overrides_global():
    from unittest.mock import patch

    with patch(
        "per_sym_store.get_overrides", return_value={"STOCH_XTREME_EXIT_ENABLED": True}
    ):
        assert (
            G.dictate_only_exempt(
                "QUICK_CLOSE", "BT_STOCH_XTREME_EXIT_g=0.06%", "BTCDOMUSDT_SHORT"
            )
            is True
        )
    with patch(
        "per_sym_store.get_overrides", return_value={"STOCH_XTREME_EXIT_ENABLED": False}
    ):
        assert (
            G.dictate_only_exempt(
                "QUICK_CLOSE", "BT_STOCH_XTREME_EXIT_g=0.06%", "BTCDOMUSDT_SHORT"
            )
            is False
        )
    with patch("per_sym_store.get_overrides", return_value={}):
        assert (
            G.dictate_only_exempt(
                "QUICK_CLOSE", "BT_STOCH_XTREME_EXIT_g=0.06%", "BTCDOMUSDT_SHORT"
            )
            is False
        )
    with patch(
        "per_sym_store.get_overrides", return_value={"STOCH_XTREME_EXIT_ENABLED": True}
    ):
        assert (
            G.dictate_only_exempt("CLOSE", "FIXED_STOP_LONG_px", "BTCDOMUSDT_SHORT")
            is False
        )
        assert (
            G.dictate_only_exempt("CLOSE", "QUICK_PEAK_GIVEBACK_X", "BTCDOMUSDT_SHORT")
            is False
        )


def test_exec_board_ok():
    assert G.exec_board_ok("OPEN", True, True) is True
    assert G.exec_board_ok("OPEN", False, True) is False
    assert G.exec_board_ok("AUGMENT", False, True) is False
    assert G.exec_board_ok("CLOSE", True, False) is False
    assert G.exec_board_ok("REDUCE", True, True) is True
    assert G.exec_board_ok("CLOSE", True, True) is True


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


STOCKS_SWITCHES = {
    "VEC_OVERRIDE_ENABLED": True,
    "VEC_OVERRIDE_BYPASS_CIRCUIT_BREAKER": True,
    "VEC_DISAGREE_KILL_ENABLED": False,
    "VEC_DISAGREE_FLAT_OPEN_BLOCK": True,
    "VEC_PENDING_TTL_S": 300.0,
    "VEC_PENDING_DEFER_NATIVE_ENABLED": True,
    "VEC_CONFIRM_REQUIRE_BOARD_ENABLED": True,
    "VEC_DICTATE_ONLY_ENABLED": True,
    "VEC_EXEC_BOARD_OPEN_ENABLED": True,
    "VEC_EXEC_BOARD_CLOSE_ENABLED": True,
}

CRYPTO_ONLY = (
    "VEC_REFUSE_NON_VECTOR_EXIT_ENABLED",
    "VEC_REFUSE_QUICK_REDUCE_ENABLED",
    "VEC_HOLD_EXPIRY_FLATTEN_ENABLED",
)


def test_tradier_config_defaults_and_venue_scope():
    import config_tradier as CT

    for sw, default in STOCKS_SWITCHES.items():
        assert getattr(CT.TradierConfig, sw) == default, sw
    for sw in CRYPTO_ONLY:
        assert not hasattr(CT.TradierConfig, sw), sw


def test_tradier_live_gates_wired():
    src = (ROOT / "tradier_manage.py").read_text()
    assert "vec_parity_gates" in src
    for sw in STOCKS_SWITCHES:
        assert f'"{sw}"' in src, sw


def test_template_rows_stocks():
    for fn in ("TEMPLATE_STOCKS_LONG.xlsx", "TEMPLATE_STOCKS_SHORT.xlsx"):
        wb = openpyxl.load_workbook(
            ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / fn, read_only=True
        )
        try:
            for sw in STOCKS_SWITCHES:
                ws = wb[TABS[sw]]
                hits = [
                    r[0].value
                    for r in ws.iter_rows(min_row=3, max_col=1)
                    if r[0].value == sw
                ]
                assert len(hits) >= 2, f"{fn}:{TABS[sw]}:{sw}"
        finally:
            wb.close()
