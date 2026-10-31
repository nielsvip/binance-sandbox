import functools
import os
from contextvars import ContextVar
from tools.replay_clock import begin_bar_coverage, end_bar_coverage, replay_bar_coverage

_scope = ContextVar("replay_ownership_scope", default=None)

def replay_ownership_scope(function):
    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        state = {"restores": [], "resolver_restores": [], "reports": []}
        token = _scope.set(state)
        coverage_token = begin_bar_coverage()
        try:
            result = await function(*args, **kwargs)
            if hasattr(result, "__dict__"):
                result.replay_ownership = list(state["reports"])
                result.replay_coverage = replay_bar_coverage()
            return result
        finally:
            for owner, name, original in reversed(state["resolver_restores"]):
                setattr(owner, name, original)
            for config, had_own, value in reversed(state["restores"]):
                if had_own:
                    setattr(config, "VEC_DICTATE_ONLY_ENABLED", value)
                else:
                    delattr(config, "VEC_DICTATE_ONLY_ENABLED")
            _scope.reset(token)
            end_bar_coverage(coverage_token)
    return wrapped

def configure_replay_ownership(config, venue, resolver_owner=None, resolver_name=None):
    state = _scope.get()
    if state is None:
        raise RuntimeError("Replay ownership configuration requires a scoped simulation")
    while True:
        try:
            inner = object.__getattribute__(config, "_inner")
        except (AttributeError, TypeError):
            break
        if inner is config:
            break
        config = inner
    original = bool(getattr(config, "VEC_DICTATE_ONLY_ENABLED", True))
    native_mode = os.environ.get("V12_REPLAY_NATIVE_OWNERSHIP") == "1"
    if native_mode:
        had_own = "VEC_DICTATE_ONLY_ENABLED" in vars(config)
        state["restores"].append((config, had_own, getattr(config, "VEC_DICTATE_ONLY_ENABLED", None)))
        setattr(config, "VEC_DICTATE_ONLY_ENABLED", False)
    if resolver_owner is not None and resolver_name is not None and native_mode:
        resolver = getattr(resolver_owner, resolver_name)
        def replay_resolver(key, *args, **kwargs):
            if key == "VEC_DICTATE_ONLY_ENABLED":
                return False
            return resolver(key, *args, **kwargs)
        state["resolver_restores"].append((resolver_owner, resolver_name, resolver))
        setattr(resolver_owner, resolver_name, replay_resolver)
    effective = bool(getattr(resolver_owner, resolver_name)("VEC_DICTATE_ONLY_ENABLED", True)) if resolver_owner is not None and resolver_name is not None else bool(getattr(config, "VEC_DICTATE_ONLY_ENABLED", True))
    report = {"venue": venue, "mode": "native_enabled_candidate" if native_mode else "configured_ownership", "original_vec_dictate_only": original, "effective_vec_dictate_only": effective, "ownership_resolver_overridden": native_mode and resolver_owner is not None, "production_ownership_changed": False}
    state["reports"].append(report)
    return report
