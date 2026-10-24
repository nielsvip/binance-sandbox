"""B14 HA string coercion (2026-10-24 fix: live HA legs are 'green'/'red' strings —
raw strings made float() raise and the lane silently never fired)."""
from vec_decisions.check_entry_b14_ha_stack import b14_fires_scalar, ha_to_num


def test_ha_map():
    assert ha_to_num("green") == 1.0
    assert ha_to_num("RED") == -1.0
    assert ha_to_num("neutral") == 0.0
    assert ha_to_num("") == 0.0
    assert ha_to_num(None) == 0.0
    assert ha_to_num(1) == 1.0
    assert ha_to_num(-1) == -1.0


def test_b14_string_legs():
    assert b14_fires_scalar("green", "green", "green", 50, True) is True
    assert b14_fires_scalar("green", "green", "green", 65, True) is False
    assert b14_fires_scalar("red", "red", "red", 50, False) is True
    assert b14_fires_scalar("red", "red", "red", 30, False) is False
    assert b14_fires_scalar("neutral", "green", "green", 50, True) is False
