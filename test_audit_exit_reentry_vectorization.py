from tools import audit_exit_reentry_vectorization as audit


def test_scope_is_exact_only_exit_or_reentry():
    assert audit.relevant_exact_only(
        {
            "param": "EXIT_X",
            "group": "EXIT",
            "precedence_layer": "EXIT_SOURCE",
            "differential_readiness": "READY_EXACT_ONLY",
        }
    )
    assert audit.relevant_exact_only(
        {
            "param": "RECLAIM_X",
            "group": "ENTRY",
            "precedence_layer": "REENTRY",
            "differential_readiness": "READY_EXACT_ONLY",
        }
    )
    assert not audit.relevant_exact_only(
        {
            "param": "ENTRY_X",
            "group": "ENTRY",
            "precedence_layer": "ENTRY_SOURCE",
            "differential_readiness": "READY_EXACT_ONLY",
        }
    )


def test_parity_receipt_requires_action_and_trade_fingerprints():
    valid, reason = audit.parity_receipt_valid(
        {
            "status": "PASS",
            "exact_action_fingerprint": "a",
            "vector_action_fingerprint": "a",
            "exact_trade_fingerprint": "t",
            "vector_trade_fingerprint": "t",
            "sampled_fire_events": 2,
            "sampled_nonfire_events": 3,
        }
    )
    assert valid and reason == "PASS"

    valid, reason = audit.parity_receipt_valid(
        {
            "status": "PASS",
            "exact_action_fingerprint": "a",
            "vector_action_fingerprint": "a",
        }
    )
    assert not valid
    assert reason == "ACTION_AND_TRADE_FINGERPRINTS_REQUIRED"


def test_action_fingerprint_is_order_independent_and_sensitive():
    rows = [
        {
            "ts": 2,
            "action": "CLOSE",
            "side": "LONG",
            "qty": 1,
            "price": 20,
            "reason": "EXIT",
        },
        {
            "ts": 1,
            "action": "OPEN",
            "side": "LONG",
            "qty": 1,
            "price": 10,
            "reason": "ENTRY",
        },
    ]
    assert audit.canonical_fingerprint(rows) == audit.canonical_fingerprint(
        list(reversed(rows))
    )
    changed = [dict(row) for row in rows]
    changed[0]["price"] = 21
    assert audit.canonical_fingerprint(rows) != audit.canonical_fingerprint(
        changed
    )


def test_existing_shared_vec_modules_are_blocked_until_exact_consumes_them():
    blocker, module = audit.classify_blocker(
        {
            "param": "STRUCTURAL_RANGE_SHIFT_EXIT",
            "family": "STRUCTURAL_RANGE_SHIFT",
            "group": "EXIT",
            "precedence_layer": "FAMILY_MASTER",
        }
    )
    assert blocker == "SHARED_CORE_NOT_CONSUMED_BY_EXACT_ENGINE"
    assert module.endswith("structural_range_shift.py")


def test_reentry_name_collision_is_not_misrepresented_as_adapter():
    blocker, module = audit.classify_blocker(
        {
            "param": "REENTRY_BREAKOUT_ENABLED",
            "family": "REENTRY_BREAKOUT",
            "group": "ENTRY",
            "precedence_layer": "FAMILY_MASTER",
        }
    )
    assert blocker == "NAME_COLLISION_DIFFERENT_ACTION"
    assert module == "vec_decisions/reentry_breakout.py"


def test_full_audit_is_diagnostic_only_and_has_no_approved_adapters():
    payload = audit.build()
    assert payload["paths_audited"] > 100
    assert payload["tier"] == "VEC_DIAGNOSTIC"
    assert payload["param_cells_engine_write_allowed"] is False
    assert payload["exact_completion_credit"] is False
    assert payload["approved_adapter_count"] == 0
    assert all(row["status"] == "BLOCKED" for row in payload["details"])
