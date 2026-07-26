from tools.audit_dc_break_entry_wiring import audit_repo, audit_sources


def test_current_registry_switch_is_proven_disconnected_not_confused_with_daytrade():
    result = audit_repo()
    assert result["classification"] == "DISCONNECTED_STALE_REGISTRY_ROW"
    assert not result["registry_switch_declared"]
    assert not result["registry_switch_read"]
    assert result["disabled_swing_branch_fail_closed"]
    assert result["separate_daytrade_switch_declared"]
    assert result["separate_daytrade_switch_read"]
    assert result["separate_daytrade_loop_launched"]


def test_audit_fails_closed_if_named_switch_is_reintroduced():
    result = audit_sources(
        "DC_BREAK_ENTRY_ENABLED: bool = True\nDC_DAYTRADE_ENABLED: bool = True",
        "getattr(config, 'DC_BREAK_ENTRY_DISABLED', True)\n"
        "getattr(config, 'DC_DAYTRADE_ENABLED', True)\n"
        "self.daytrade_wing = StockDaytradeWing(self)\n"
        "self.daytrade_wing.run_loop()",
    )
    assert result["classification"] == "REQUIRES_FRESH_MANUAL_AUDIT"
    assert not result["research_reconstruction_only"]
