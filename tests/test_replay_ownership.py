import asyncio
from types import SimpleNamespace
import pytest
from tools.replay_clock import ReplayEventLedger
from tools.replay_ownership import configure_replay_ownership, replay_ownership_scope

@pytest.mark.parametrize("native_mode", [False, True])
def test_ownership_change_is_explicit_reported_and_restored(monkeypatch, native_mode):
    config = SimpleNamespace(VEC_DICTATE_ONLY_ENABLED=True)
    if native_mode:
        monkeypatch.setenv("V12_REPLAY_NATIVE_OWNERSHIP", "1")
    else:
        monkeypatch.delenv("V12_REPLAY_NATIVE_OWNERSHIP", raising=False)
    @replay_ownership_scope
    async def simulate():
        report = configure_replay_ownership(config, "crypto")
        assert config.VEC_DICTATE_ONLY_ENABLED is not native_mode
        assert report["original_vec_dictate_only"] is True
        return ReplayEventLedger([1800], [2700])
    events = asyncio.run(simulate())
    assert config.VEC_DICTATE_ONLY_ENABLED is True
    assert events.replay_ownership[0]["effective_vec_dictate_only"] is not native_mode


def test_failure_restores_inherited_configuration_without_shadowing(monkeypatch):
    monkeypatch.setenv("V12_REPLAY_NATIVE_OWNERSHIP", "1")
    class Config:
        VEC_DICTATE_ONLY_ENABLED = True
    config = Config()
    @replay_ownership_scope
    async def simulate():
        configure_replay_ownership(config, "tradier")
        assert config.VEC_DICTATE_ONLY_ENABLED is False
        raise RuntimeError("native error must remain visible")
    with pytest.raises(RuntimeError, match="native error"):
        asyncio.run(simulate())
    assert config.VEC_DICTATE_ONLY_ENABLED is True
    assert "VEC_DICTATE_ONLY_ENABLED" not in vars(config)


def test_explicit_native_mode_overrides_only_ownership_key_in_per_symbol_resolver(monkeypatch):
    monkeypatch.setenv("V12_REPLAY_NATIVE_OWNERSHIP", "1")
    config = SimpleNamespace(VEC_DICTATE_ONLY_ENABLED=True)
    original = lambda key, default=None: {"VEC_DICTATE_ONLY_ENABLED": True, "MIN_GAIN": 3.0}.get(key, default)
    owner = SimpleNamespace(_cfg_auto=original)
    @replay_ownership_scope
    async def simulate():
        configure_replay_ownership(config, "tradier", owner, "_cfg_auto")
        assert owner._cfg_auto("VEC_DICTATE_ONLY_ENABLED", True) is False
        assert owner._cfg_auto("MIN_GAIN", 0) == 3.0
        assert owner._cfg_auto("OTHER", 17) == 17
        raise RuntimeError("preserve failure")
    with pytest.raises(RuntimeError, match="preserve failure"):
        asyncio.run(simulate())
    assert owner._cfg_auto is original
    assert owner._cfg_auto("VEC_DICTATE_ONLY_ENABLED") is True

@pytest.mark.parametrize("native_mode", [False, True])
def test_vec_driven_native_suppression_is_ownership_not_a_risk_gate(monkeypatch, native_mode):
    monkeypatch.setenv("V12_REPLAY_NATIVE_OWNERSHIP", "1" if native_mode else "0")
    config = SimpleNamespace(VEC_DICTATE_ONLY_ENABLED=True, VEC_DRIVEN_ENABLED=True, MIN_GAIN=3.0, FIXED_STOP_ENABLED=True)
    @replay_ownership_scope
    async def simulate():
        report = configure_replay_ownership(config, "crypto")
        assert config.VEC_DRIVEN_ENABLED is not native_mode
        assert config.MIN_GAIN == 3.0 and config.FIXED_STOP_ENABLED
        assert report["effective_ownership_flags"]["VEC_DRIVEN_ENABLED"] is not native_mode
        raise RuntimeError("expected failure")
    with pytest.raises(RuntimeError, match="expected failure"):
        asyncio.run(simulate())
    assert config.VEC_DRIVEN_ENABLED and config.VEC_DICTATE_ONLY_ENABLED
    assert config.MIN_GAIN == 3.0 and config.FIXED_STOP_ENABLED


@pytest.mark.parametrize("native_mode", [False, True])
def test_global_and_per_symbol_exact_owner_are_scoped_without_altering_risk(monkeypatch, native_mode):
    monkeypatch.setenv("V12_REPLAY_NATIVE_OWNERSHIP", "1" if native_mode else "0")
    config = SimpleNamespace(PARITY_VEC_EXACT_MODE=True, MIN_GAIN=3.)
    resolver = lambda symbol, side, key, default=None: {"PARITY_VEC_EXACT_MODE": True, "MIN_GAIN": 3.}.get(key, default)
    probe = lambda: config.PARITY_VEC_EXACT_MODE or resolver("TEST", "LONG", "PARITY_VEC_EXACT_MODE")
    owner = SimpleNamespace(_vec_exact_mode_on=probe, _psym_get=resolver)
    @replay_ownership_scope
    async def simulate():
        report = configure_replay_ownership(config, "crypto", ownership_owner=owner)
        assert owner._vec_exact_mode_on() is not native_mode
        assert owner._psym_get("TEST", "LONG", "PARITY_VEC_EXACT_MODE") is not native_mode
        assert owner._psym_get("TEST", "LONG", "MIN_GAIN") == 3.
        assert config.MIN_GAIN == 3.
        assert report["effective_ownership_flags"]["PARITY_VEC_EXACT_MODE"] is not native_mode
        raise RuntimeError("native failure")
    with pytest.raises(RuntimeError, match="native failure"):
        asyncio.run(simulate())
    assert owner._vec_exact_mode_on is probe and owner._psym_get is resolver
    assert config.PARITY_VEC_EXACT_MODE is True


def test_per_symbol_proxy_layer_and_distinct_manager_config_release_only_ownership(monkeypatch):
    monkeypatch.setenv("V12_REPLAY_NATIVE_OWNERSHIP", "1")
    base = SimpleNamespace(PARITY_VEC_EXACT_MODE=True, VEC_BACKSTOP_SYNC_ENABLED=True)
    manager = SimpleNamespace(PARITY_VEC_EXACT_MODE=True, VEC_BACKSTOP_SYNC_ENABLED=True, MIN_GAIN=4.5)
    stored = {"PARITY_VEC_EXACT_MODE": True, "VEC_BACKSTOP_ENABLED": True, "MIN_GAIN": 4.5}
    layer = lambda symbol, side: stored
    owner = SimpleNamespace(_ezm_per_sym_layer=layer)
    @replay_ownership_scope
    async def simulate():
        configure_replay_ownership(base, "crypto", ownership_owner=owner)
        configure_replay_ownership(manager, "crypto:manager")
        resolved = owner._ezm_per_sym_layer("TEST", "LONG")
        assert not (base.PARITY_VEC_EXACT_MODE or manager.PARITY_VEC_EXACT_MODE or resolved["PARITY_VEC_EXACT_MODE"])
        assert not manager.VEC_BACKSTOP_SYNC_ENABLED and not resolved["VEC_BACKSTOP_ENABLED"]
        assert manager.MIN_GAIN == resolved["MIN_GAIN"] == 4.5
        assert stored["PARITY_VEC_EXACT_MODE"] is True
    asyncio.run(simulate())
    assert base.PARITY_VEC_EXACT_MODE and manager.PARITY_VEC_EXACT_MODE and manager.VEC_BACKSTOP_SYNC_ENABLED
    assert owner._ezm_per_sym_layer is layer
