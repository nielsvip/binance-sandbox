"""Fail-closed master for native QUICK_REDUCE decisions lacking tested vector parity."""


def allow_native_quick_reduce(config, hard_exit_reason):
    """Allow hard exits; otherwise read the disabled master outside per-sym overrides."""
    if hard_exit_reason is not None:
        return True
    base_config = getattr(config, "_inner", config)
    return bool(getattr(base_config, "QUICK_REDUCE_NATIVE_ENABLED", False))
