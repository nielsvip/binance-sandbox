"""shared_zone — single source for zone gate used by both VEC and LIVE."""
def is_zone_blocked(is_long: bool, zone_k: float, entry_zone_long: float, entry_zone_short: float) -> bool:
    try:
        zk = float(zone_k)
        ez = float(entry_zone_long)
        esz = float(entry_zone_short)
    except Exception:
        return False
    if is_long:
        return zk > ez
    return zk < esz
