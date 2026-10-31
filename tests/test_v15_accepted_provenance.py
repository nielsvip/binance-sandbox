from tools.v15_accepted_provenance import config_sha256, measurement_matches_current, protect_accepted_set


def test_h2h_accepted_live_set_is_preserved_when_no_switch_was_promoted():
    accepted = {"A": True, "B": 2}
    record = {"accepted_set_sha256": config_sha256(accepted), "verdict": "KEEP_LIVE", "measured_at": "2026-10-31T05:00:00Z"}
    assert "preserving H2H-accepted" in protect_accepted_set(record, accepted, {"A": False}, 0)


def test_acceptance_guard_does_not_block_new_positive_promotion_or_same_set():
    accepted = {"A": True}
    record = {"accepted_set_sha256": config_sha256(accepted), "verdict": "APPLY_CANDIDATE"}
    assert protect_accepted_set(record, accepted, {"A": False}, 1) == ""
    assert protect_accepted_set(record, accepted, accepted, 0) == ""


def test_acceptance_guard_uses_hash_compare_and_swap():
    accepted = {"A": True}
    changed = {"A": False}
    stale_record = {"accepted_set_sha256": config_sha256({"A": None}), "verdict": "KEEP_LIVE"}
    assert protect_accepted_set(stale_record, accepted, changed, 0) == ""


def test_measurement_cas_requires_exact_current_live_candidate_and_publish_row():
    live = {"A": True}
    candidate = {"A": False}
    meta = {"history_id": 42, "published_at": "2026-10-31T04:22:30Z", "winning_tag": "pilot"}
    decision = {
        "live_set_sha256": config_sha256(live),
        "candidate_set_sha256": config_sha256(candidate),
        "candidate_meta": dict(meta),
    }
    assert measurement_matches_current(decision, live, meta, candidate)[0]
    assert not measurement_matches_current(decision, {"A": None}, meta, candidate)[0]
    assert not measurement_matches_current(decision, live, {**meta, "history_id": 43}, candidate)[0]
    assert not measurement_matches_current(decision, live, meta, {"A": 1})[0]
