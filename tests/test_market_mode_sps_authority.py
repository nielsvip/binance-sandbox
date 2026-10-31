import ast
import inspect
from types import SimpleNamespace


def test_psym_sps_raw_uses_market_mode_even_with_sqlite_sps_overrides(monkeypatch):
    import ez_manage as ez
    import per_sym_store

    seen = []
    monkeypatch.setattr(ez, "_ezm_base_config", SimpleNamespace(START_POSITION_SIZE=15.0))
    monkeypatch.setattr(ez, "_ezm_conviction_mult", lambda symbol, side: 1.0)
    monkeypatch.setattr(per_sym_store, "get_overrides", lambda ss: seen.append(ss) or {
        "START_POSITION_SIZE": 45.0,
        "START_POSITION_SIZE_OVERRIDE_USD": 90.0,
    })

    assert ez._psym_sps_raw("XMRUSDT", "LONG") == 15.0
    assert seen == []


def test_psym_sps_raw_preserves_explicit_conviction_multiplier(monkeypatch):
    import ez_manage as ez

    monkeypatch.setattr(ez, "_ezm_base_config", SimpleNamespace(START_POSITION_SIZE=15.0))
    monkeypatch.setattr(ez, "_ezm_conviction_mult", lambda symbol, side: 1.4)

    assert ez._psym_sps_raw("XMRUSDT", "LONG") == 21.0


def test_psym_sps_raw_has_no_per_sym_store_lookup():
    import ez_manage as ez

    tree = ast.parse(inspect.getsource(ez._psym_sps_raw))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_overrides"
        for node in calls
    )
