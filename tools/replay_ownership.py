import functools
import os
from contextvars import ContextVar
from tools.replay_clock import begin_bar_coverage, end_bar_coverage, replay_bar_coverage

_scope = ContextVar("replay_ownership_scope", default=None)
_OWNERSHIP_FLAGS = ("VEC_DICTATE_ONLY_ENABLED", "VEC_DRIVEN_ENABLED", "PARITY_VEC_EXACT_MODE", "VEC_BACKSTOP_SYNC_ENABLED", "VEC_BACKSTOP_ENABLED")

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
            for config, name, had_own, value in reversed(state["restores"]):
                if had_own:
                    setattr(config, name, value)
                else:
                    delattr(config, name)
            _scope.reset(token)
            end_bar_coverage(coverage_token)
    return wrapped

def configure_replay_ownership(config, venue, resolver_owner=None, resolver_name=None, ownership_owner=None):
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
    original_flags = {name: bool(getattr(config, name, name == "VEC_DICTATE_ONLY_ENABLED")) for name in _OWNERSHIP_FLAGS}
    if native_mode:
        for name in _OWNERSHIP_FLAGS:
            had_own = name in vars(config)
            state["restores"].append((config, name, had_own, getattr(config, name, None)))
            setattr(config, name, False)
    if resolver_owner is not None and resolver_name is not None and native_mode:
        resolver = getattr(resolver_owner, resolver_name)
        def replay_resolver(key, *args, **kwargs):
            if key in _OWNERSHIP_FLAGS:
                return False
            return resolver(key, *args, **kwargs)
        state["resolver_restores"].append((resolver_owner, resolver_name, resolver))
        setattr(resolver_owner, resolver_name, replay_resolver)
    probes = []
    if ownership_owner is not None and native_mode:
        probe = getattr(ownership_owner, "_vec_exact_mode_on", None)
        if probe is not None:
            state["resolver_restores"].append((ownership_owner, "_vec_exact_mode_on", probe))
            setattr(ownership_owner, "_vec_exact_mode_on", lambda: False)
            probes.append("_vec_exact_mode_on")
        per_symbol = getattr(ownership_owner, "_psym_get", None)
        if per_symbol is not None:
            def replay_per_symbol(symbol, side, key, *args, **kwargs):
                return False if key in _OWNERSHIP_FLAGS else per_symbol(symbol, side, key, *args, **kwargs)
            state["resolver_restores"].append((ownership_owner, "_psym_get", per_symbol))
            setattr(ownership_owner, "_psym_get", replay_per_symbol)
            probes.append("_psym_get:ownership_keys_only")
        layer = getattr(ownership_owner, "_ezm_per_sym_layer", None)
        if layer is not None:
            def replay_layer(*args, **kwargs):
                values = layer(*args, **kwargs)
                return {**values, **{name: False for name in _OWNERSHIP_FLAGS}} if isinstance(values, dict) else values
            state["resolver_restores"].append((ownership_owner, "_ezm_per_sym_layer", layer))
            setattr(ownership_owner, "_ezm_per_sym_layer", replay_layer)
            probes.append("_ezm_per_sym_layer:ownership_keys_only")
    effective = bool(getattr(resolver_owner, resolver_name)("VEC_DICTATE_ONLY_ENABLED", True)) if resolver_owner is not None and resolver_name is not None else bool(getattr(config, "VEC_DICTATE_ONLY_ENABLED", True))
    report = {"venue": venue, "mode": "native_enabled_candidate" if native_mode else "configured_ownership", "original_vec_dictate_only": original, "effective_vec_dictate_only": effective, "ownership_resolver_overridden": native_mode and resolver_owner is not None, "production_ownership_changed": False}
    report["ownership_probes_overridden"] = probes
    report["original_ownership_flags"] = original_flags
    report["effective_ownership_flags"] = {name: bool(getattr(config, name, name == "VEC_DICTATE_ONLY_ENABLED")) for name in _OWNERSHIP_FLAGS}
    state["reports"].append(report)
    return report
