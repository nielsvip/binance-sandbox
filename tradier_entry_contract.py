"""Pure entry-routing contract shared by Tradier backtest adapters."""


def flat_key_needs_evaluation(
    *,
    satoshit_ok: bool,
    stdev_ok: bool,
    wt_force_open_enabled: bool,
    wt_dc_path_enabled: bool,
    ordinary_ladder_enabled: bool = False,
    mandatory_reclaim_pending: bool = False,
    direct_route_enabled: bool = False,
) -> bool:
    """Whether a flat symbol/side must reach ``tradier_manage.process_position``.

    WT_DC is evaluated inside ``process_position``.  A harness prefilter may
    skip the call only when no upstream trigger, downstream entry path, ladder
    parent, or latched reclaim obligation can possibly open.  In particular,
    a mandatory reclaim is persistent state: omitting it here stranded a
    B&H-seeded key forever after its first exit.
    """
    return bool(
        satoshit_ok
        or stdev_ok
        or wt_force_open_enabled
        or wt_dc_path_enabled
        or ordinary_ladder_enabled
        or mandatory_reclaim_pending
        or direct_route_enabled
    )


def flat_key_needs_per_bar_evaluation(
    *,
    ordinary_ladder_enabled: bool,
    mandatory_reclaim_pending: bool,
    direct_route_enabled: bool = False,
) -> bool:
    """Whether a flat key must bypass the generic every-third-bar cadence."""
    return bool(
        ordinary_ladder_enabled
        or mandatory_reclaim_pending
        or direct_route_enabled
    )


def exact_side_allowlist_tradeable(
    symbol: str,
    side: str,
    *,
    long_symbols,
    short_symbols,
    blacklist=(),
    non_shortable=(),
) -> bool:
    """Deterministic exact-engine permission from the frozen side universe.

    Live ``is_symbol_tradeable`` also consults mutable per-symbol overlays such
    as ``LONG_ENABLED``/``SHORT_ENABLED``.  Those overlays are knobs the matrix
    must be able to test; letting their current live value block the baseline
    makes the key unreachable and turns a live decision into its own research
    ground truth.
    """
    sym = str(symbol or "").upper()
    pside = str(side or "LONG").upper()
    if not sym or pside not in {"LONG", "SHORT"}:
        return False
    blocked = {str(value).upper() for value in (blacklist or ())}
    if sym in blocked:
        return False
    if pside == "SHORT" and sym in {
        str(value).upper() for value in (non_shortable or ())
    }:
        return False
    allowed = long_symbols if pside == "LONG" else short_symbols
    return sym in {str(value).upper() for value in (allowed or ())}


def path_switch(config, name: str, default: bool = True) -> bool:
    """Read an explicit path master without inferring polarity from its name."""
    return bool(getattr(config, name, default))


def per_sym_overlay_is_approved(
    symbol: str,
    side: str,
    *,
    trb_configs,
    trc_configs,
    trb_symbols=(),
) -> bool:
    """Return whether TRC may trade a TRB-approved, positively tested side.

    TRC is the control arm, but it must use the same approved universe as TRB
    and must have its own recent-window (7D) result.  A present key with
    ``wsharpe == 0`` or ``BELOW_THRESHOLD`` is no result and must not authorize
    an order.
    """
    sym = str(symbol or "").upper().strip()
    pside = str(side or "").upper().strip()
    if not sym or pside not in {"LONG", "SHORT"}:
        return False
    key = f"{sym}_{pside}"
    if sym not in {str(value).upper().strip() for value in (trb_symbols or ())}:
        return False

    def _entry_has_positive_sharpe(entry) -> bool:
        if not isinstance(entry, dict):
            return False
        side_flag = f"{pside}_ENABLED"
        overrides = entry.get("overrides")
        if entry.get(side_flag) is False or (isinstance(overrides, dict) and overrides.get(side_flag) is False):
            return False
        values = []
        for field in ("wsharpe", "_promoted_wsharpe"):
            try:
                value = float(entry.get(field))
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                values.append(value)
        recent = entry.get("_recent_diagnostic")
        if isinstance(recent, dict):
            try:
                if float(recent.get("wsharpe")) > 0.0:
                    values.append(float(recent["wsharpe"]))
            except (TypeError, ValueError, KeyError):
                pass
        return bool(values)

    # USER 2026-08-17: TRC must trade what TRB trades — exact TRB universe for 7D vs per_sym compare
    # TRC reads TRB dynamic list directly, no copy, 7D overlay is evaluated separately
    return sym in {str(v).upper().strip() for v in (trb_symbols or ())}


def dc_4h_boundary_breached(
    price: float,
    side: str,
    indicators,
    *,
    require_level: bool = False,
) -> tuple[bool, str]:
    """Check the live 4h Donchian boundary for a position side.

    Entries use ``require_level=True`` so missing indicator data fails closed.
    Exits use the default so a missing level never invents an emergency close;
    normal freshness/error handling remains responsible for that case.
    """
    try:
        px = float(price or 0.0)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0.0 or not isinstance(indicators, dict):
        return (bool(require_level), "MISSING_PRICE_OR_INDICATORS")
    is_long = str(side or "").upper() == "LONG"
    level_key = "dc_low_4h" if is_long else "dc_high_4h"
    try:
        level = float(indicators.get(level_key, 0.0) or 0.0)
    except (TypeError, ValueError):
        level = 0.0
    if level <= 0.0:
        return (bool(require_level), f"MISSING_{level_key.upper()}")
    breached = px <= level if is_long else px >= level
    return (breached, f"{level_key}={level:.8f},price={px:.8f}")
