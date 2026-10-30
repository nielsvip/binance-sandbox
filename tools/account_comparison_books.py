"""Side-exact account books for the two-week 7D versus 30D comparison.

The SMA classifiers keep writing MEN and FIN independently. Only FIN's live
admission source is temporarily MEN's book; the source automatically returns
to FIN at the deadline. TRC's admission source is always TRB's book. Crypto
entry admission uses the explicit account key in tradeable_keys.json.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

FIN_MIRROR_START = dt.datetime(2026, 10, 26, 1, 10, 12, tzinfo=dt.timezone.utc)
FIN_MIRROR_END = FIN_MIRROR_START + dt.timedelta(days=14)


def source_account(account: str, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("account comparison time must be timezone-aware")
    if account == "fin" and FIN_MIRROR_START <= now < FIN_MIRROR_END:
        return "men"
    if account == "trc":
        return "trb"
    return account


def side_symbols(root: Path, account: str, side: str,
                 now: dt.datetime | None = None) -> set[str]:
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"invalid account side {side!r}")
    source = source_account(account, now)
    path = Path(root) / f"symbols_{source}_{side.lower()}.json"
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a side-specific JSON list")
    return {str(value).strip().upper() for value in data if str(value).strip()}


def side_allowed(root: Path, account: str, symbol: str, side: str,
                 now: dt.datetime | None = None) -> bool:
    try:
        return str(symbol).strip().upper() in side_symbols(root, account, side, now)
    except (OSError, ValueError, TypeError):
        return False


def crypto_entry_allowed(root: Path, account: str, symbol: str, side: str) -> bool:
    """The account's explicit tradeable key is the crypto entry authority."""
    try:
        keys = json.loads((Path(root) / "tradeable_keys.json").read_text())
        if isinstance(keys, dict):
            keys = keys.get("keys")
        return isinstance(keys, list) and f"{account}:{str(symbol).strip().upper()}_{side.upper()}" in keys
    except (OSError, ValueError, TypeError):
        return False
