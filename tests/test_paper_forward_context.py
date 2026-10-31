from tools.paper_forward_context import derive_shared_context


def test_trc_context_selects_paper_account_and_releases_both_masks():
    context = derive_shared_context("stocks", "AAPL_LONG", "TRC_LIVE_5M_TRADING_ENABLED")
    assert context["status"] == "READY"
    assert context["account"] == "trc"
    assert context["experiment_context"] == {
        "release_nonvectorizable": True,
        "release_sub15m": True,
    }
    assert "default trb account does not apply" in context["coverage_note"]


def test_trc_context_is_deterministic_and_side_specific():
    first = derive_shared_context("stocks", "AAPL_LONG", "TRC_LIVE_5M_TRADING_ENABLED")
    same = derive_shared_context("stocks", "AAPL_LONG", "TRC_LIVE_5M_TRADING_ENABLED", "trb")
    other_side = derive_shared_context("stocks", "AAPL_SHORT", "TRC_LIVE_5M_TRADING_ENABLED")
    assert first["context_id"] == same["context_id"]
    assert first["context_id"] != other_side["context_id"]


def test_scalp_v2_context_marks_shared_mode_without_fabricating_candidate_list():
    context = derive_shared_context("crypto", "BTCUSDT_LONG", "SCALP_ACCOUNTS")
    assert context["status"] == "UNRESOLVED_SHARED_PREREQUISITE"
    assert context["shared_settings"] == {"SCALP_MODE": True}
    assert "candidate SCALP_ACCOUNTS value must contain the selected account" in context["prerequisites"]


def test_scalp_v3_context_records_membership_and_scanner_gap_without_approval_gate():
    context = derive_shared_context("crypto", "BTCUSDT_LONG", "SCALP_V3_ENABLED")
    assert context["status"] == "UNSUPPORTED_CONTEXT"
    assert context["coverage_status"] == "UNVERIFIED_SCANNER"
    assert context["shared_settings"] == {"SCALP_V3_ACCOUNTS": ["ang"]}
    assert "SCALP_V3_ENABLED" not in context["shared_settings"]
    assert all("authorization" not in item for item in context["prerequisites"])


def test_hedge_account_route_is_unverified_without_classifying_every_hedge_path_dead():
    context = derive_shared_context("crypto", "BTCUSDT_LONG", "HEDGE_ACCOUNTS")
    assert context["status"] == "UNVERIFIED_HEDGE_ROUTE"
    assert context["coverage_status"] == "SAME_SYMBOL_OPENER_UNAVAILABLE"
    assert context["shared_settings"] == {}
    assert "not classified here" in context["prerequisites"][0]


def test_unverified_ranking_invocation_is_not_marked_ready():
    context = derive_shared_context("crypto", "BTCUSDT_LONG", "SCALP_V3_BOOST_ENABLED")
    assert context["status"] == "UNRESOLVED_ENGINE_CALLSITE"
