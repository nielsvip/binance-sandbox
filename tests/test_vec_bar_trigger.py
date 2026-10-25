#!/usr/bin/env python3
"""Tests for tools/vec_bar_trigger.py — vector state the moment a kline lands."""

import time

from tools.vec_bar_trigger import (
    shard_sides,
    should_probe,
)


def test_shard_sides_filters():
    sets = {"A_LONG": 1, "A_SHORT": 1, "B_LONG": 1}

    def serves(ss, shard):
        return ss.endswith("_SHORT") if shard == "crypto_short" else True

    assert sorted(shard_sides(sets, serves, "crypto_short")) == ["A_SHORT"]
    assert sorted(shard_sides(sets, serves, "all")) == ["A_LONG", "A_SHORT", "B_LONG"]


def test_should_probe_on_mtime_change():
    seen = {}
    assert should_probe("A_LONG", 100.0, 100.0, seen) is True
    assert should_probe("A_LONG", 100.0, 100.0, seen) is False
    assert should_probe("A_LONG", 101.0, 100.0, seen) is True
    assert should_probe("A_LONG", 101.0, 100.0, seen) is False


def test_should_probe_new_symbol():
    assert should_probe("NEW_SHORT", 50.0, 60.0, {}) is True
