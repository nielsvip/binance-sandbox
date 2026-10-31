"""Policy-selected ASR indicator views shared by native and vector scoring."""
from __future__ import annotations

import re
from collections.abc import Mapping


_MICRO_TOKEN = re.compile(r"_(?:1m|3m|5m)(?=_|$)")
_15M_TOKEN = re.compile(r"_15m(?=_|$)")
ASR_REQUIRED_LTF_KEYS = (
    "k_1m", "k_1m_prev", "d_1m", "k_3m", "d_3m", "k_3m_prev",
    "wt1_3m", "wt2_3m", "dc_basis_3m", "dc_high_3m", "dc_low_3m",
)


def selected_timeframe(tf, use_ltf):
    """Select the 15m floor when LTF signals are disabled."""
    tf = str(tf)
    return "15m" if not use_ltf and tf in ("1m", "3m", "5m") else tf


def _15m_key(key):
    return _MICRO_TOKEN.sub("_15m", str(key))


def _hydrate_ltf_aliases(mapping):
    result = dict(mapping)
    for key, value in mapping.items():
        key = str(key)
        if _15M_TOKEN.search(key):
            for timeframe in ("1m", "3m", "5m"):
                result[_15M_TOKEN.sub(f"_{timeframe}", key)] = value
    return result


def _view(mapping, use_ltf):
    if not isinstance(mapping, Mapping):
        return {}
    result = dict(mapping) if use_ltf else _hydrate_ltf_aliases(mapping)
    if use_ltf:
        return result
    for key in tuple(result):
        if not _MICRO_TOKEN.search(str(key)):
            continue
        pair = _15m_key(key)
        if pair in mapping:
            result[key] = mapping[pair]
        else:
            result.pop(key, None)
    return result


def paired_indicator_view(indicators, metrics, use_ltf):
    """Return copies; preserve ind-first lookup with a shared 15m peer fallback."""
    if not isinstance(indicators, Mapping):
        indicators = {}
    if not isinstance(metrics, Mapping):
        metrics = {}
    ind_view = dict(indicators) if use_ltf else _hydrate_ltf_aliases(indicators)
    metrics_view = dict(metrics) if use_ltf else _hydrate_ltf_aliases(metrics)
    if not use_ltf:
        for source, own, other in ((indicators, ind_view, metrics), (metrics, metrics_view, indicators)):
            for key in tuple(own):
                if not _MICRO_TOKEN.search(str(key)):
                    continue
                pair = _15m_key(key)
                if pair in source:
                    own[key] = source[pair]
                elif pair in other:
                    own[key] = other[pair]
                else:
                    own.pop(key, None)
    missing = []
    if use_ltf:
        for key in ASR_REQUIRED_LTF_KEYS:
            if key not in ind_view and key not in metrics_view:
                missing.append(key)
    return ind_view, metrics_view, tuple(missing)


def paired_event_view(events, use_ltf):
    """Select the paired 15m event stream when LTF signals are disabled."""
    if not isinstance(events, Mapping):
        return {}
    result = dict(events)
    if not use_ltf:
        if "15m" in events:
            result["3m"] = events["15m"]
        else:
            result.pop("3m", None)
    return result


def paired_npz_view(npz, use_ltf):
    """Return an array mapping with the same policy-selected key hydration."""
    if isinstance(npz, Mapping):
        mapping = npz
    else:
        try:
            mapping = {key: npz[key] for key in npz.files}
        except (AttributeError, TypeError):
            mapping = {}
    return _view(mapping, use_ltf)
