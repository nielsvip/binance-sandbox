"""Cache one STDEV detector result within a single live entry iteration."""


def evaluate_stdev_once(cache, detector, *args, **kwargs):
    """Return one detector result per iteration, including a cached None."""
    if not cache.get("evaluated", False):
        cache["result"] = detector(*args, **kwargs)
        cache["evaluated"] = True
    return cache.get("result")
