"""Parity lock: ez_ system ONLY trades exact vectorized BEST trades (7D forward).

Durable keeper for 2026-09-26 20% churn incident — ensures:
- 1/3min trades switch OFF for parity (LIVE_5m + USE_1M_3M)
- Master switch disables ALL non-vectorized paths in entire ez_* scripts
- STRICT_VEC_PARITY_MODE gates execute_now to vec-achievable only
"""
def test_switch_1m3m_off_for_parity():
    import config
    cfg = config.Config()
    assert bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", True)) is False, "1/3m switch must be OFF for parity (USE_1M_3M_SIGNALS_ENABLED=False)"
    assert bool(getattr(cfg, "LIVE_5m_trading_ENABLED", True)) is False, "MASTER 1 must be OFF for parity (LIVE_5m_trading_ENABLED=False)"
    assert getattr(cfg, "BASE_TF", "15m") == "15m"
    assert getattr(cfg, "PARITY_MIN_DECISION_TF", "15m") == "15m"

def test_switch_master_non_vector_off():
    import config
    cfg = config.Config()
    assert bool(getattr(cfg, "PARITY_DISABLE_NON_VECTORIZABLE", False)) is True
    assert bool(getattr(cfg, "V12_PARITY_DISABLE_NON_VECTORIZABLE", False)) is True

def test_strict_vec_parity_gates_execute_now():
    import config
    cfg = config.Config()
    assert bool(getattr(cfg, "STRICT_VEC_PARITY_MODE", False)) is True, "STRICT_VEC_PARITY_MODE must be True — live trades ONLY vec-achievable reasons (SPREADSHEETS/BEST/)"
    # tradier side too
    import config_tradier
    tcfg = config_tradier.TradierConfig()
    assert bool(getattr(tcfg, "STRICT_VEC_PARITY_MODE", False)) is True
    assert bool(getattr(tcfg, "LIVE_5m_trading_ENABLED", True)) is False

def test_ez_manage_non_vec_knobs_force_off():
    import ez_manage
    assert hasattr(ez_manage, "_NON_VEC_KNOBS_EZ")
    assert len(ez_manage._NON_VEC_KNOBS_EZ) >= 20, "NON_VEC set must cover entire script, not stub"
    # when master ON, _psym_get must return default False for non-vec knob
    cfg = ez_manage.config
    # ensure master is True in loaded config
    assert bool(getattr(cfg, "PARITY_DISABLE_NON_VECTORIZABLE", False)) is True
    # force-off: GOLDEN_PULLBACK_ENABLED is in set, default False should win even if per_sym had True
    val = ez_manage._psym_get("BTCUSDT", "LONG", "GOLDEN_PULLBACK_ENABLED", True)
    assert val is False, "_psym_get must force non-vec knobs OFF when parity master True"
    val2 = ez_manage._psym_get("BTCUSDT", "LONG", "WT_3M_FORCE_OPEN_ENABLED", True)
    assert val2 is False
    # TF knob forced to None
    val3 = ez_manage._psym_get("BTCUSDT", "LONG", "LONG_STRUCT_EXIT_TF", "D")
    assert val3 == "None"

def test_ez_manage_1m3m_neutralized_when_live5m_off():
    import ez_manage
    import config
    # check source contains LIVE_5m check alongside USE_1M_3M
    src = open("ez_manage.py").read()
    assert 'LIVE_5m_trading_ENABLED' in src and 'USE_1M_3M_SIGNALS_ENABLED' in src
    assert 'if not bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED"' in src

def test_ez_positions_quick_parity_enforcement():
    import ez_positions_quick
    assert hasattr(ez_positions_quick, "_NON_VEC_KNOBS_QUICK")
    assert len(ez_positions_quick._NON_VEC_KNOBS_QUICK) >= 10
    cfg = ez_positions_quick.config
    assert bool(getattr(cfg, "PARITY_DISABLE_NON_VECTORIZABLE", False)) is True
    for k in ["GOLDEN_PULLBACK_ENABLED", "EXPLODING_LEDGER_ENABLED", "WT_3M_FORCE_OPEN_ENABLED"]:
        assert getattr(cfg, k, True) is False, f"{k} must be forced OFF in ez_positions_quick when parity master True"

def test_vec_parity_gate_allowlist_exists():
    from vec_paths.vec_parity_gate import is_vec_achievable
    # vector BEST trades must be achievable
    assert is_vec_achievable("OPEN_STRONG_BUY_k_15m") is True
    assert is_vec_achievable("GOLDEN_RULE_LONG_mult2") is True
    # live-only churn must be blocked
    assert is_vec_achievable("GOLDEN_PULLBACK") is False
    assert is_vec_achievable("DAEMON_PRICE_CROSS_REENTRY") is False
    assert is_vec_achievable("EXPLODING_LEDGER") is False
