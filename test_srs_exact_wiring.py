from pathlib import Path


def _source_between(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_stock_live_srs_short_is_the_true_side_mirror() -> None:
    source = Path("tradier_manage.py").read_text()
    evaluator = _source_between(
        source,
        "    async def evaluate_stop(",
        "    async def evaluate_open(",
    )
    srs = _source_between(
        evaluator,
        "# ═══ RANGE TOP/BOTTOM EXIT",
        "# === PARTIAL_PROFIT_LOCK",
    )
    assert "if is_long and _srs_entry > _srs_high:" in srs
    assert "elif not is_long and _srs_entry < _srs_low:" in srs
    assert "_srs_k5m < _srs_k5m_prev" in srs
    assert "_srs_k5m > _srs_k5m_prev" in srs


def test_stock_exact_wrapper_does_not_synthesize_a_second_srs_decision() -> None:
    source = Path("backtest_v8_engine.py").read_text()
    wrapper = _source_between(
        source,
        "    async def _v8_gated_evaluate_stop(",
        "    manager.strategy.evaluate_stop = _v8_gated_evaluate_stop",
    )
    assert "await _orig_evaluate_stop(" in wrapper
    assert "return should_exit, reason, qty" in wrapper
    assert "STRUCTURAL_RANGE_SHIFT_LONG_V8_" not in wrapper
    assert "STRUCTURAL_RANGE_SHIFT_SHORT_V8_" not in wrapper
    assert "_srs_kd_cross" not in wrapper


def test_srs_override_family_is_present_in_exact_contract() -> None:
    source = Path("backtest_v8_engine.py").read_text()
    exact = source[source.index("async def run_simulation_tradier(") :]
    for name in (
        "STRUCTURAL_RANGE_SHIFT_EXIT",
        "STRUCTURAL_RANGE_SHIFT_TF",
        "STRUCTURAL_RANGE_SHIFT_K_HIGH",
        "STRUCTURAL_RANGE_SHIFT_K_LOW",
        "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS",
    ):
        assert name in exact
