"""Pure routing classifier for paper counterfactual paths.

This module classifies a path for paper analysis only. It does not score paths,
write metrics, change switches, or activate any trading behavior.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_LOW_TF = {"1m", "3m", "5m"}
_NONVECTOR_TF = {"tick", "webhook", "portfolio"}
_TF_RE = re.compile(r"(?<![0-9a-z])(1m|3m|5m|15m|1h|4h|d)(?![0-9a-z])", re.I)


def _result(route: str, switch: str | None, timeframe: str | None, evidence: list, **extra):
    return {
        "route": route,
        "canonical_switch": switch,
        "timeframe": timeframe,
        "evidence": evidence,
        **extra,
    }


def _metadata_tf(metadata: Mapping[str, Any]) -> str | None:
    for key in ("timeframe", "input_tf", "tf"):
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().lower()
    return None


def classify_path(path_name, metadata=None, venue=None):
    """Return a conservative counterfactual route for a named path.

    Explicitly verified aliases override stale metadata. Otherwise supplied
    metadata is used first; when absent, the optional parity family registry
    may supply its read-only family classification. Unknown evidence remains
    ``needs_wiring_audit``.
    """
    name = str(path_name or "").strip()
    upper = name.upper()
    meta = dict(metadata or {})
    venue_name = str(venue or meta.get("venue") or "").strip().lower()

    # Verified live crypto branch. The 3m check is a veto on the 1h decision,
    # not the principal trigger and therefore never changes the route.
    wt_alias = bool(re.match(r"^(?:QUICK_)?WT_CROSS_EXIT_1H_(BEAR|BULL)(?:_|$)", upper))
    if wt_alias and venue_name not in {"stocks", "stock", "tradier", "equity"}:
        return _result(
            "vector_backtest",
            "WT_CROSS_EXIT_ENABLED",
            "1h",
            [
                "verified crypto alias: ez_positions_quick.py WT_CROSS_EXIT_1h branch",
                "vector twin: vec_decisions/live_exit_chain.py step 6 and v12 prepare/step path",
                "3m veto is optional and subordinate to the 1h trigger",
            ],
            optional_native_veto={"canonical_switch": "LIVE_5m_trading_ENABLED", "timeframe": "3m"},
        )

    if upper.startswith("DELTA_EXIT_DC15M_FLOOR_BREAK"):
        return _result(
            "vector_backtest",
            "DC15M_FORCE_EXIT_ENABLED",
            "15m",
            [
                "verified shared live/vector twin: vec_decisions/live_native_exit_twins.py",
                "shared decision is wired in crypto and stocks live paths and v12 engine",
            ],
        )

    if upper.startswith("VEC_FWD_MISSING_FLAT"):
        return _result(
            "operational_reconciliation",
            None,
            None,
            [
                "verified board-convergence/backstop-close path",
                "ez_manage.py reconciliation branch near line 10070",
                "tradier_manage.py reconciliation branch near line 15506",
                "operational reconciliation, not a strategy family",
            ],
        )

    if re.match(r"^WT_15M_BOUNCE_OPEN_[LS](?:_|$)", upper):
        return _result(
            "vector_backtest",
            "WT_15M_BOUNCE_OPEN_ENABLED",
            "15m",
            [
                "verified vector entry path: v12_quick_engine.compute_entry_signals",
                "shared live twin: vec_decisions/twin_entry_ports_b.py",
            ],
        )

    if upper.startswith("QUICK_AUGMENTED_DC_BREAK_REDUCE_TO_MIN"):
        return _result(
            "vector_backtest",
            "AUGMENTED_DC_BREAK_ENABLED",
            "15m",
            [
                "verified live branch: ez_positions_quick.py augmented DC break reduce",
                "vector twin: vec_decisions/live_exit_chain.py steps 9 and prepare path",
            ],
            optional_native_variant={
                "timeframe": "3m",
                "note": "native channel variant is optional and separate from the 15m vector base",
            },
        )

    # Prefer caller-supplied metadata. Registry lookup is an optional fallback;
    # registry misses are not treated as proof of a non-vector path.
    tf = _metadata_tf(meta)
    vec_twin = meta.get("vec_twin")
    canonical_switch = meta.get("canonical_switch") or meta.get("gate_knob")
    if not meta:
        try:
            try:
                from tools.forward_parity import families
            except ModuleNotFoundError:
                from forward_parity import families
            rec = families.classify(name)
            if not str(rec.get("family", "")).startswith("UNCLASSIFIED:"):
                tf = str(rec.get("input_tf") or "").lower() or None
                vec_twin = rec.get("vec_twin")
                canonical_switch = rec.get("gate_knob")
                meta = {"registry_family": rec.get("family"), "gate_knob": canonical_switch}
        except (ImportError, AttributeError, TypeError):
            pass

    # Explicit timeframe tokens can refine supplied metadata only when the
    # metadata did not provide a timeframe.
    if tf is None:
        matches = [m.group(1).lower() for m in _TF_RE.finditer(name)]
        if matches:
            tf = matches[0]

    tf_minutes = re.fullmatch(r"(\d+)m", tf or "")
    if tf in _LOW_TF or (tf_minutes and int(tf_minutes.group(1)) < 15):
        return _result("native_counterfactual", canonical_switch, tf, [f"input timeframe {tf} is below 15m"])
    if tf in _NONVECTOR_TF or vec_twin is False:
        why = f"input timeframe {tf}" if tf in _NONVECTOR_TF else "metadata says vec_twin=False"
        return _result("nonvector_counterfactual", canonical_switch, tf, [why])
    if vec_twin is True and tf:
        return _result("vector_backtest", canonical_switch, tf, ["metadata confirms a vector twin"])
    return _result("needs_wiring_audit", None, tf, ["insufficient verified routing evidence"])
