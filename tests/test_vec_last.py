"""VEC-last ordering (USER 2026-10-24): VEC override paths act last in execute_now.

A vec-dictated order traverses every native gate first; each native gate it
would fail is recorded, and the final VEC_LAST backstop allows it with a
VEC_LAST_OVERRIDE line naming the refusing gates. A non-vec order refused by
the vec law keeps its original refusal string, issued late as VEC_LAST_REFUSE.
When VEC_LAST_OVERRIDE vanishes from the logs, native paths have caught up.
"""
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EARLY_VEC_RETURNS = [
    'return "POLICY_NON_VECTOR_EXIT_DISABLED"',
    'return "BLOCKED_HERMETIC_NOT_VEC"',
    "BLOCKED_PARITY_NATIVE_",
    "BLOCKED_VECTOR_EXEC_",
    'return "BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED"',
    'return "BLOCKED_NATIVE_ENTRY_VEC_DRIVEN"',
]


def _inner_body(src):
    return src.split("async def _execute_now_inner", 1)[1]


def test_vec_last_decide_matrix():
    from live_twins.vec_last import decide
    ok, tag, detail = decide(True, ["TIMING_HOLD", "LOSING_POSITION"], "")
    assert ok is True
    assert tag == "VEC_LAST_OVERRIDE"
    assert "TIMING_HOLD" in detail and "LOSING_POSITION" in detail
    ok, tag, detail = decide(True, [], "")
    assert ok is True
    assert tag == "VEC_LAST_CLEAN"
    ok, tag, detail = decide(False, [], "POLICY_NON_VECTOR_EXIT_DISABLED")
    assert ok is False
    assert tag == "VEC_LAST_REFUSE"
    assert detail == "POLICY_NON_VECTOR_EXIT_DISABLED"
    ok, tag, detail = decide(False, [], "")
    assert ok is True
    assert tag == "VEC_LAST_CLEAN"


def test_vec_refusals_deferred_past_native_gates():
    for fname in ("ez_manage.py", "tradier_manage.py"):
        body = _inner_body(pathlib.Path(fname).read_text())
        late = body.index("─── VEC_LAST_BACKSTOP")
        early_returns = [ln for ln in body[:late].splitlines() if ln.strip().startswith("return")]
        for pat in EARLY_VEC_RETURNS:
            assert not any(pat in ln for ln in early_returns), f"{fname}: early VEC refusal still present: {pat}"
        native_marker = "365D" if fname.startswith("ez") else "SYM_HALT"
        assert native_marker in body[:late], f"{fname}: native gates missing before backstop"


def test_vec_dictated_predicate_matches_parity_order():
    from live_twins.vec_last import vec_dictated as _vd
    import ez_manage as _ez
    corpus = [
        "VEC_DRIVEN_OPEN_PURE_BOARD_BOARD_HOLDS_LIVE_FLAT_BOARD_QTY_1_LIVE_Q",
        "twin act |VEC_EXACT live",
        "BOARD_CONVERGE_CLOSE vec=WT_EXIT",
        "CLOSE VEC_EXIT_OWED paper-flat-live-holds-retry",
        "RECONCILE_XRPUSDC_LONG",
        "WT_CROSS_EXIT_1h_bull",
        "B_KZONE",
    ]
    for reason in corpus:
        assert _vd(reason) == bool(_ez._parity_vec_order(reason)), reason


def test_vec_maker_lock_busy_is_deferred_without_market_fallback():
    import ez_manage as _ez

    assert _ez._vec_maker_lock_busy_is_defer(-2.0, "VEC_EXIT_OWED |VEC_EXACT")
    assert _ez._vec_maker_lock_busy_is_defer(
        -2.0, "VEC_DRIVEN_CLOSE_RECONCILE_"
    )
    assert not _ez._vec_maker_lock_busy_is_defer(-1.0, "VEC_EXIT_OWED |VEC_EXACT")
    assert not _ez._vec_maker_lock_busy_is_defer(-2.0, "NATIVE_EXIT")
    src = pathlib.Path("ez_manage.py").read_text()
    assert 'return "DEFERRED_MAKER_LOCK_BUSY_RETRY"' in src
    assert "VEC_MAKER_LOCK_BUSY_DEFER" in src
    assert "BLOCKED_MAKER_SUPPRESS_WEBHOOK" in src  # ambiguous sentinels remain fail-closed
