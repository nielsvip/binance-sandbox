"""Fail-closed low-timeframe guard used only by research/parity backtests.

It intentionally does not affect the live daemon.  When a 15m study is
requested, a 3m/5m indicator cannot silently remain a decision input: it is
replaced by the identically-named 15m series or removed.  Explicit low-TF
switches are disabled and TF selectors are clamped to 15m.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Mapping


LOW_TFS = ("3m", "5m")


def enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def guard_npz(npz: Mapping[str, Any], min_tf: str = "15m") -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Copy *npz* and eliminate low-TF decision series.

    A matching 15m field is used when it exists.  Otherwise the low field is
    removed, which makes the existing safe-access helpers return their neutral
    no-event values instead of leaking a 3m/5m decision into the study.
    """
    out = dict(npz)
    replaced: list[str] = []
    removed: list[str] = []
    for key in list(out):
        for low_tf in LOW_TFS:
            suffix = f"_{low_tf}"
            if not key.lower().endswith(suffix):
                continue
            parent = key[:-len(suffix)] + f"_{min_tf}"
            if parent in out:
                out[key] = out[parent]
                replaced.append(key)
            else:
                del out[key]
                removed.append(key)
            break
    return out, {"replaced_low_tf_arrays": sorted(replaced), "removed_low_tf_arrays": sorted(removed)}


def clamp_config(cfg: Any, min_tf: str = "15m") -> dict[str, list[str]]:
    """Disable named 3m/5m switches and clamp their selectors on a config."""
    names = [field.name for field in fields(cfg)] if is_dataclass(cfg) else list(vars(cfg))
    disabled: list[str] = []
    clamped: list[str] = []
    for name in names:
        try:
            value = getattr(cfg, name)
        except (AttributeError, TypeError):
            continue
        upper = name.upper()
        is_low_named = "_3M" in upper or "_5M" in upper
        if is_low_named and upper.endswith("_ENABLED") and bool(value):
            setattr(cfg, name, False)
            disabled.append(name)
        elif isinstance(value, str) and value.lower() in LOW_TFS:
            setattr(cfg, name, min_tf)
            clamped.append(name)
        elif isinstance(value, (list, tuple)):
            new = [min_tf if str(x).lower() in LOW_TFS else x for x in value]
            if list(value) != new:
                setattr(cfg, name, type(value)(new))
                clamped.append(name)
    setattr(cfg, "BASE_TF", min_tf)
    # Dataclasses capture their constructor defaults when the class is
    # generated, so changing ``Field.default`` / the class attribute alone
    # does not protect a config instance constructed after this call.  The
    # scalar engine imports decision managers after applying this guard; wrap
    # that constructor once so their fresh config also obeys the floor.
    if isinstance(cfg, type) and is_dataclass(cfg) and not getattr(
        cfg, "_min_decision_tf_guarded_init", False
    ):
        original_init = cfg.__init__

        def guarded_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            clamp_config(self, min_tf)

        cfg.__init__ = guarded_init
        cfg._min_decision_tf_guarded_init = True
    return {"disabled_low_tf_switches": sorted(disabled), "clamped_low_tf_selectors": sorted(clamped)}


def clamp_mapping(values: Mapping[str, Any], min_tf: str = "15m") -> dict[str, Any]:
    """Return a guarded override mapping for scalar config-module replays."""
    out = dict(values)
    for name, value in list(out.items()):
        upper = str(name).upper()
        if ("_3M" in upper or "_5M" in upper) and upper.endswith("_ENABLED"):
            out[name] = False
        elif isinstance(value, str) and value.lower() in LOW_TFS:
            out[name] = min_tf
    out["BASE_TF"] = min_tf
    return out
