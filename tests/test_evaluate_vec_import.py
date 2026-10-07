"""Import test for evaluate_vec.is_banned — covers top-level and canonical paths."""

def test_evaluate_vec_top_level_import():
    import evaluate_vec
    assert hasattr(evaluate_vec, "is_banned")
    assert callable(evaluate_vec.is_banned)
    # banned surfaces
    assert evaluate_vec.is_banned("LONG_ENABLED") is True
    assert evaluate_vec.is_banned("FOO_3M") is True
    assert evaluate_vec.is_banned("RSI") is False
    assert evaluate_vec.is_banned("") is True


def test_tools_opt_evaluate_vec_import():
    from tools.opt.evaluate_vec import banned_reason, is_banned

    assert is_banned("SHORT_ENABLED") is True
    assert is_banned("BAR_5m") is True
    assert is_banned("ENTRY_RSI") is False
    assert banned_reason("MODE") == "CONTROL_ONLY"
    assert banned_reason("X_3M") == "LOW_TF_3M_5M_SYNTHETIC"
    assert banned_reason("RSI") == ""


def test_shim_parity():
    import evaluate_vec as top
    from tools.opt import evaluate_vec as canonical

    for name in ["LONG_ENABLED", "FOO_3M", "FOO_5M", "RSI", "", "MODE", "SYMBOL"]:
        assert top.is_banned(name) == canonical.is_banned(name)
