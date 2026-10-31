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
