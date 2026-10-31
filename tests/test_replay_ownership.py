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
