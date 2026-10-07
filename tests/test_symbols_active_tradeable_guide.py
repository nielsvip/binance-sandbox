"""Regression: symbols_active and indicators must be guided by tradeable_keys per-account, not symbols_active alone."""

import json
import pathlib


def test_tradeable_keys_is_per_account_guide():
    tk = json.loads(pathlib.Path("tradeable_keys.json").read_text())
    assert isinstance(tk, list) and 100 <= len(tk) <= 250, f"tradeable_keys should be 100-250 after 2d prune, got {len(tk)}"
    import collections
    c = collections.Counter([k.split(":")[0] for k in tk if ":" in k])
    # Each account trades different symbols; ang was 100 before 2d cap, now 28 after service prune
    assert 20 <= c["ang"] <= 60, f"ang should be 20-60 after 2d cap (was 100), got {c['ang']}"
    assert c["inf"] > 30, f"inf should have >30, got {c['inf']}"
    assert c["flz"] > 10, f"flz should have >10, got {c['flz']}"
    assert c["men"] >= 5
    assert c["fin"] >= 3


def test_symbols_active_includes_all_tradeable_bases():
    tk = json.loads(pathlib.Path("tradeable_keys.json").read_text())
    sa = set(json.loads(pathlib.Path("symbols_active.json").read_text()))
    bases = set()
    for k in tk:
        base = str(k).split(":", 1)[-1].rsplit("_", 1)[0].strip().upper() if ":" in str(k) else str(k).rsplit("_", 1)[0].strip().upper()
        if base:
            bases.add(base)
    missing = bases - sa
    assert not missing, f"symbols_active missing {len(missing)} tradeable bases: {sorted(list(missing))[:10]}"
    assert 60 <= len(sa) <= 140, f"symbols_active should be 60-140 covering tradeable bases + open, got {len(sa)}"


def test_ez_rankings_builds_from_tradeable_keys():
    src = pathlib.Path("ez_rankings.py").read_text()
    assert "tradeable_keys" in src, "ez_rankings must include tradeable_keys in all_active_symbols"
    assert "all_active_symbols" in src


def test_ez_indicators_loads_tradeable_keys():
    src = pathlib.Path("ez_indicators.py").read_text()
    assert "tradeable_keys" in src, "ez_indicators load_symbols must include tradeable_keys"
    # Must extract base symbol correctly
    assert "rsplit" in src or "split" in src


def test_ez_market_data_loads_tradeable_keys():
    src = pathlib.Path("ez_market_data.py").read_text()
    assert "tradeable_keys" in src, "ez_market_data must monitor tradeable_keys symbols"
    assert 'SYMBOLS_FILES' in src
    # Ensure SYMBOLS_FILES includes tradeable_keys.json
    assert "tradeable_keys.json" in src


def test_ez_manage_uses_tradeable_keys_per_account():
    src = pathlib.Path("ez_manage.py").read_text()
    assert "tradeable_keys" in src
    assert "position_key in trade_manager.tradeable_keys" in src or "pkey not in self.tradeable_keys" in src
