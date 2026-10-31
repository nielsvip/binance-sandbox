"""Explicit, inspectable policies for native-faithful scalar replay."""


def configure_replay_settings(config, config_class, modules, native_faithful):
    keys = (
        "STRICT_NO_LOSS_ACCOUNTS",
        "FAST_CUT_LOSS_THRESHOLD",
        "BREAKOUT_GUARD_LOSS_THRESHOLD",
        "STOP_LOSS_THRESHOLD",
        "UNIVERSAL_NOLOSS_GATE",
    )
    before = {key: getattr(config, key, None) for key in keys}
    mode = "native_faithful" if native_faithful else "legacy_sweep_compatibility"
    if not native_faithful:
        config_class.FAST_CUT_LOSS_THRESHOLD = -3.0
        config_class.BREAKOUT_GUARD_LOSS_THRESHOLD = -3.0
        config_class.STOP_LOSS_THRESHOLD = 3.0
        config.STRICT_NO_LOSS_ACCOUNTS = []
        config_class.STRICT_NO_LOSS_ACCOUNTS = []
        for module in modules:
            module_config = getattr(module, "config", None)
            if module_config is not None:
                module_config.STRICT_NO_LOSS_ACCOUNTS = []
                module_config.FAST_CUT_LOSS_THRESHOLD = -3.0
    effective = {key: getattr(config, key, None) for key in keys}
    return {"mode": mode, "before": before, "effective": effective}


def override_check_due(cadence, step):
    cadence = str(cadence or "disabled")
    if cadence == "disabled":
        return False
    if cadence == "per_bar":
        return int(step) > 0
    if cadence == "every_20_callbacks":
        return int(step) > 0 and int(step) % 20 == 0
    raise ValueError(f"unsupported override check replay cadence: {cadence}")
