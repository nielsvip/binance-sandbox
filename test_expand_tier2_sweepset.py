from expand_tier2_sweepset import typed_default


def test_declared_float_survives_integer_shaped_json_number():
    value = typed_default({"type": "float", "default": 1})
    assert value == 1.0
    assert type(value) is float


def test_declared_int_remains_integer():
    value = typed_default({"type": "int", "default": 3.0})
    assert value == 3
    assert type(value) is int
