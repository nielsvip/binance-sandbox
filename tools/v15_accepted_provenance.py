import hashlib
import json


def config_sha256(config):
    payload = json.dumps(config or {}, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def protect_accepted_set(record, current_config, proposed_config, promoted_count):
    if int(promoted_count or 0) > 0 or not isinstance(record, dict):
        return ""
    accepted_hash = str(record.get("accepted_set_sha256") or "")
    if not accepted_hash or config_sha256(current_config) != accepted_hash:
        return ""
    if config_sha256(proposed_config) == accepted_hash:
        return ""
    verdict = str(record.get("verdict") or "")
    if verdict not in ("KEEP_LIVE", "APPLY_CANDIDATE"):
        return ""
    return f"preserving H2H-accepted {verdict} set measured {record.get('measured_at') or 'unknown'}; incoming workbook has n_promoted=0"


def measurement_matches_current(decision, current_config, published_meta, candidate_config):
    if not isinstance(decision, dict) or not isinstance(published_meta, dict):
        return False, "missing measured decision or published-row metadata"
    if config_sha256(current_config) != decision.get("live_set_sha256"):
        return False, "live set changed after H2H measurement"
    if config_sha256(candidate_config) != decision.get("candidate_set_sha256"):
        return False, "published candidate set changed after H2H measurement"
    expected = decision.get("candidate_meta") or {}
    for key in ("history_id", "published_at", "winning_tag"):
        if expected.get(key) != published_meta.get(key):
            return False, f"published candidate {key} changed after H2H measurement"
    expected_window = decision.get("npz_window_sha256")
    actual_window = decision.get("npz_window_sha256_current")
    if expected_window and actual_window and expected_window != actual_window:
        return False, "NPZ measurement window changed"
    return True, "exact measured live and published sets still current"
