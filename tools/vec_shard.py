"""Per-cat_side shard filter for the forward lane (USER 2026-10-25).

S1 serves crypto_short, S2 crypto_long, S5 stock_long, S6 stock_short; Mac pulls
every shard and enforces. Activation is per-host supervisor env, NEVER this module:

    VEC_SHARD=all            (default: serve every sym_side — today's behavior)
    VEC_SHARD=crypto_short | crypto_long | stock_long | stock_short

Unknown/empty value fails OPEN (serve all + stderr warn): a typo must never
silently kill a host's lane. Venue calls the same authority the lane uses
(tools/opt/evaluate_v12.is_crypto), suffix fallback if that import fails.
"""
import os
import sys

SHARDS = ("crypto_long", "crypto_short", "stock_long", "stock_short")


def active_shard() -> str:
    v = (os.environ.get("VEC_SHARD") or "all").strip().lower()
    if v in ("", "all", "none"):
        return "all"
    if v in SHARDS:
        return v
    print("[vec_shard] WARN unknown VEC_SHARD=%r — failing open (serve all)" % v, file=sys.stderr)
    return "all"


def is_crypto_sym(sym: str) -> bool:
    try:
        from tools.opt import evaluate_v12 as _e
        return bool(_e.is_crypto(sym))
    except Exception:
        return str(sym or "").upper().endswith(("USDT", "USDC", "USD1"))


def cat_side_of(ss: str):
    s = str(ss or "")
    if s.endswith("_LONG"):
        side = "long"
        sym = s[:-5]
    elif s.endswith("_SHORT"):
        side = "short"
        sym = s[:-6]
    else:
        return None
    venue = "crypto" if is_crypto_sym(sym) else "stock"
    return "%s_%s" % (venue, side)


def serves(ss: str, shard: str = None) -> bool:
    sh = shard or active_shard()
    if sh == "all":
        return True
    return cat_side_of(ss) == sh


def describe() -> str:
    return "VEC_SHARD=%s" % active_shard()
