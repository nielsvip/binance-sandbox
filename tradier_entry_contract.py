"""Pure entry-routing contract shared by Tradier backtest adapters."""


def flat_key_needs_evaluation(
    *,
    satoshit_ok: bool,
    stdev_ok: bool,
    wt_force_open_enabled: bool,
    wt_dc_path_enabled: bool,
) -> bool:
    """Whether a flat symbol/side must reach ``tradier_manage.process_position``.

    WT_DC is evaluated inside ``process_position``.  A harness prefilter may
    skip the call only when no upstream trigger and no downstream WT_DC path
    can possibly open.  Omitting ``wt_dc_path_enabled`` made isolated WT_DC
    exact replays produce zero trades unless an unrelated SATOSHIT/STDEV/force
    opener happened to route the key.
    """
    return bool(
        satoshit_ok
        or stdev_ok
        or wt_force_open_enabled
        or wt_dc_path_enabled
    )


def path_switch(config, name: str, default: bool = True) -> bool:
    """Read an explicit path master without inferring polarity from its name."""
    return bool(getattr(config, name, default))
