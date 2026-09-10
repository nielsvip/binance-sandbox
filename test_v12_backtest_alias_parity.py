import json

import backtest_v12_engine as engine


def test_explicit_canonical_override_reaches_live_alias():
    expanded = engine._v12_expand_explicit_tradier_aliases({
        "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER": 31,
    })
    assert expanded["K_ZONE_ENTRY_BONUS_TRADIER"] == 31


def test_explicit_live_alias_reaches_canonical_switch():
    expanded = engine._v12_expand_explicit_tradier_aliases({
        "RSI_ENTRY_LONG_TRADIER": 39,
    })
    assert expanded["TRADIER_RSI_ENTRY_LONG_TRADIER"] == 39


def test_explicit_twins_are_never_overwritten():
    expanded = engine._v12_expand_explicit_tradier_aliases({
        "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER": 31,
        "K_ZONE_ENTRY_BONUS_TRADIER": 44,
    })
    assert expanded["TRADIER_K_ZONE_ENTRY_BONUS_TRADIER"] == 31
    assert expanded["K_ZONE_ENTRY_BONUS_TRADIER"] == 44


def test_read_probe_counts_live_alias_for_canonical_target(monkeypatch, tmp_path):
    target = tmp_path / "probe.json"
    monkeypatch.setattr(engine, "_V8_PATH_PROBE_PARAM", "TRADIER_RSI_ENTRY_LONG_TRADIER")
    monkeypatch.setattr(engine, "_V8_PATH_PROBE_TELEMETRY_FILE", str(target))
    monkeypatch.setattr(engine, "_V8_PATH_PROBE_READS", {})
    engine._v8_probe_config_read("RSI_ENTRY_LONG_TRADIER")
    engine._v8_write_path_probe_telemetry()
    payload = json.loads(target.read_text())
    assert payload["read_count"] == 1
    assert payload["equivalent_names"] == [
        "RSI_ENTRY_LONG_TRADIER",
        "TRADIER_RSI_ENTRY_LONG_TRADIER",
    ]
