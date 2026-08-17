from datetime import datetime, timezone
from pathlib import Path

from position_time_contract import parse_position_timestamp


ROOT = Path(__file__).resolve().parent


def test_persisted_opened_at_is_parsed_without_entry_time():
    opened_at = parse_position_timestamp("2026-08-03T13:00:00.000000Z")

    assert isinstance(opened_at, datetime)
    assert opened_at.tzinfo is not None
    assert opened_at.astimezone(timezone.utc).isoformat().startswith(
        "2026-08-03T13:00:00"
    )


def test_invalid_position_timestamp_is_not_newborn_zero():
    assert parse_position_timestamp(None) is None
    assert parse_position_timestamp("") is None
    assert parse_position_timestamp("not-a-time") is None


def test_r1_unknown_age_fails_closed_and_global_off_is_master():
    source = (ROOT / "tradier_manage.py").read_text()
    config = (ROOT / "config_tradier.py").read_text()
    block = source[
        source.index("# R1 — DC_LOW4 EMERGENCY CLOSE"):
        source.index("# HYBRID EXIT", source.index("# R1 — DC_LOW4 EMERGENCY CLOSE"))
    ]

    assert "_r1_age_min = None" in block
    assert "_r1_age_min is not None" in block
    assert "R1_SKIPPED_OPEN_TIME_UNAVAILABLE" in block
    assert "parse_position_timestamp(getattr(position, 'opened_at', None))" in block
    assert "R1_DC_LOW4_3M_EMERGENCY_ENABLED', False" in block
    assert "R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = False" in config
    assert "param == \"R1_DC_LOW4_3M_EMERGENCY_ENABLED\"" in source
    assert "obsolete value and would then ignore" in source
    assert "disable the WT15-confirmed emergency contract" not in source
    hourly = (ROOT / "tradier_hourly_reconfig.py").read_text()
    strict = hourly[
        hourly.index('(\"R1R2_strict\", {'):
        hourly.index('(\"RULE_A_on\", {')
    ]
    assert '"R1_DC_LOW4_3M_EMERGENCY_ENABLED": True' not in strict
    assert strict.count('"R1_DC_LOW4_3M_EMERGENCY_ENABLED": False') == 2
    assert strict.count('"R1_NEWBORN_WINDOW_MIN": -1.0') == 2
    position_source = (ROOT / "tradier_positions.py").read_text()
    assert "self.opened_at = parse_position_timestamp(self.opened_at)" in position_source
