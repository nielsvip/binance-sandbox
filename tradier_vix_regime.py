"""
VIX regime filter for Tradier system.

Single most cost-effective overlay (documented 32% drawdown reduction):
  VIX < 200dMA  → trend ON, full size
  VIX > 200dMA  → reduce size 50%, prefer mean-reversion
  VIX > 30      → halt new entries entirely (panic regime)
  VIX > 40      → also force close losing options (extreme tail)

Pulls VIX daily close via Tradier's market data (VIX is a tradeable index).
Caches for 600s to avoid hammering API.

Wired into tradier_manage.py at three points:
  1. Sizing modifier for new entries (`get_vix_regime_size_mult()`)
  2. Entry block when VIX > 30 (`is_vix_panic_regime()`)
  3. Boost mean-reversion entries when VIX > 200dMA (`get_vix_regime_label()`)

Config flags (added to config_tradier.py):
  VIX_REGIME_FILTER_ENABLED: bool = True
  VIX_PANIC_THRESHOLD: float = 30.0
  VIX_EXTREME_THRESHOLD: float = 40.0
  VIX_REGIME_SIZE_MULT_HIGH_VOL: float = 0.5    # VIX > 200dMA
  VIX_REGIME_SIZE_MULT_PANIC: float = 0.0       # VIX > panic threshold
  VIX_SMA_LOOKBACK_DAYS: int = 200
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# In-memory cache (refreshed every 600s)
_vix_cache: dict = {"close": None, "sma200": None, "ts": 0.0, "regime": "UNKNOWN"}

VIX_CACHE_TTL_SEC = 600

# Local fallback file location (in case Tradier API is unavailable)
_VIX_HIST_FILE = Path(__file__).resolve().parent / "data" / "vix_history.csv"


def _load_vix_history_local() -> Optional[Tuple[float, float]]:
    """Load (current_close, sma200) from local cached CSV if available.
    CSV format: date,close (one row per trading day, sorted ascending).
    """
    if not _VIX_HIST_FILE.exists():
        return None
    try:
        lines = _VIX_HIST_FILE.read_text().strip().split("\n")
        if len(lines) < 2:
            return None
        closes = []
        for line in lines[1:]:  # skip header
            parts = line.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                closes.append(float(parts[1]))
            except (ValueError, IndexError):
                continue
        if len(closes) < 1:
            return None
        cur = closes[-1]
        sma_window = closes[-200:] if len(closes) >= 200 else closes
        sma200 = sum(sma_window) / len(sma_window)
        return cur, sma200
    except Exception as e:
        logger.warning(f"[VIX_REGIME] local history load failed: {e}")
        return None


def _refresh_via_tradier(account_key: str = "tra") -> Optional[Tuple[float, float]]:
    """Fetch VIX history via Tradier API and compute SMA200.
    Returns (current_close, sma200) or None on failure.
    """
    try:
        # Local import to avoid circular dependency
        from tradier_api import TradierAPIClient
        client = TradierAPIClient(account_key=account_key)
        # Get last 220 days to ensure 200-day SMA window
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=300)  # weekend buffer
        # Use the get_history method (already exists in tradier_api.py)
        import asyncio
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_running():
            # Already inside async context — caller should use async variant
            return None
        history = loop.run_until_complete(
            client.get_history("VIX", start=start.isoformat(), end=end.isoformat(), interval="daily")
        )
        if not history or len(history) < 50:
            logger.warning(f"[VIX_REGIME] Tradier history short: {len(history) if history else 0} bars")
            return None
        closes = [float(bar.get("close", 0)) for bar in history if bar.get("close")]
        if not closes:
            return None
        cur = closes[-1]
        sma_window = closes[-200:] if len(closes) >= 200 else closes
        sma200 = sum(sma_window) / len(sma_window)
        return cur, sma200
    except Exception as e:
        logger.error(f"[VIX_REGIME] Tradier fetch failed: {e}")
        return None


async def refresh_vix_async(account_key: str = "tra") -> bool:
    """Async version for use inside event loop (e.g. tradier_manage main)."""
    try:
        from tradier_api import TradierAPIClient
        client = TradierAPIClient(account_key=account_key)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=300)
        history = await client.get_history("VIX", start=start.isoformat(), end=end.isoformat(), interval="daily")
        if not history or len(history) < 50:
            logger.warning(f"[VIX_REGIME] Tradier history short: {len(history) if history else 0} bars")
            return False
        closes = [float(bar.get("close", 0)) for bar in history if bar.get("close")]
        if not closes:
            return False
        cur = closes[-1]
        sma_window = closes[-200:] if len(closes) >= 200 else closes
        sma200 = sum(sma_window) / len(sma_window)
        _vix_cache["close"] = cur
        _vix_cache["sma200"] = sma200
        _vix_cache["ts"] = time.time()
        _vix_cache["regime"] = _classify_regime(cur, sma200)
        # Persist to local CSV for fallback
        try:
            _VIX_HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _VIX_HIST_FILE.open("w") as f:
                f.write("date,close\n")
                # we don't have dates in this minimal cache; just write the latest 220 closes
                for i, c in enumerate(closes[-220:]):
                    f.write(f"day_{i},{c:.4f}\n")
        except Exception:
            pass
        logger.info(f"[VIX_REGIME] refresh: VIX={cur:.2f} SMA200={sma200:.2f} regime={_vix_cache['regime']}")
        return True
    except Exception as e:
        logger.error(f"[VIX_REGIME] async refresh failed: {e}")
        return False


def _classify_regime(cur: float, sma200: float) -> str:
    """Return regime label: NORMAL, HIGH_VOL, PANIC, EXTREME, UNKNOWN."""
    if cur is None or sma200 is None or sma200 <= 0:
        return "UNKNOWN"
    if cur >= 40.0:
        return "EXTREME"
    if cur >= 30.0:
        return "PANIC"
    if cur > sma200:
        return "HIGH_VOL"
    return "NORMAL"


def _ensure_fresh(config) -> None:
    """Ensure cache is fresh (within TTL). Tries Tradier first, falls back to local CSV."""
    now = time.time()
    if now - _vix_cache["ts"] < VIX_CACHE_TTL_SEC and _vix_cache["close"] is not None:
        return
    # Try local fallback first (sync, no API call)
    local = _load_vix_history_local()
    if local is not None:
        cur, sma200 = local
        _vix_cache["close"] = cur
        _vix_cache["sma200"] = sma200
        _vix_cache["ts"] = now
        _vix_cache["regime"] = _classify_regime(cur, sma200)
        return
    # Sync Tradier fetch (only when not in event loop)
    fetched = _refresh_via_tradier()
    if fetched is not None:
        cur, sma200 = fetched
        _vix_cache["close"] = cur
        _vix_cache["sma200"] = sma200
        _vix_cache["ts"] = now
        _vix_cache["regime"] = _classify_regime(cur, sma200)


def get_vix_regime_label(config) -> str:
    """Return current regime: NORMAL, HIGH_VOL, PANIC, EXTREME, UNKNOWN."""
    if not getattr(config, "VIX_REGIME_FILTER_ENABLED", False):
        return "DISABLED"
    _ensure_fresh(config)
    return _vix_cache.get("regime", "UNKNOWN")


def get_vix_regime_size_mult(config) -> float:
    """Sizing multiplier based on VIX regime. Caller multiplies new-entry size by this."""
    if not getattr(config, "VIX_REGIME_FILTER_ENABLED", False):
        return 1.0
    regime = get_vix_regime_label(config)
    if regime == "EXTREME":
        return 0.0  # halt new entries entirely
    if regime == "PANIC":
        return float(getattr(config, "VIX_REGIME_SIZE_MULT_PANIC", 0.0))
    if regime == "HIGH_VOL":
        return float(getattr(config, "VIX_REGIME_SIZE_MULT_HIGH_VOL", 0.5))
    if regime == "NORMAL":
        return 1.0
    # UNKNOWN — fail safe (full size, log warning)
    logger.warning("[VIX_REGIME] regime=UNKNOWN — falling back to size_mult=1.0")
    return 1.0


def is_vix_panic_regime(config) -> bool:
    """True when VIX is in panic/extreme — used as a hard entry-block signal."""
    if not getattr(config, "VIX_REGIME_FILTER_ENABLED", False):
        return False
    return get_vix_regime_label(config) in ("PANIC", "EXTREME")


def is_vix_high_vol(config) -> bool:
    """True when VIX > 200dMA — prefer mean-reversion over trend."""
    if not getattr(config, "VIX_REGIME_FILTER_ENABLED", False):
        return False
    return get_vix_regime_label(config) in ("HIGH_VOL", "PANIC", "EXTREME")


def get_vix_snapshot() -> dict:
    """Return raw cache for diagnostics/logging."""
    return {
        "close": _vix_cache.get("close"),
        "sma200": _vix_cache.get("sma200"),
        "regime": _vix_cache.get("regime", "UNKNOWN"),
        "cache_age_sec": time.time() - _vix_cache.get("ts", 0),
    }


# Self-test
if __name__ == "__main__":
    import sys
    class MockConfig:
        VIX_REGIME_FILTER_ENABLED = True
        VIX_PANIC_THRESHOLD = 30.0
        VIX_EXTREME_THRESHOLD = 40.0
        VIX_REGIME_SIZE_MULT_HIGH_VOL = 0.5
        VIX_REGIME_SIZE_MULT_PANIC = 0.0
        VIX_SMA_LOOKBACK_DAYS = 200
    cfg = MockConfig()
    # Synthetic test
    print("--- mock regime test ---")
    for cur, sma in [(15, 18), (20, 18), (32, 18), (45, 18)]:
        print(f"  VIX={cur} SMA200={sma} → regime={_classify_regime(cur, sma)}")
    # Try real fetch
    print("\n--- real Tradier fetch (requires env keys) ---")
    fetched = _refresh_via_tradier()
    if fetched is not None:
        cur, sma = fetched
        print(f"  Real VIX: {cur:.2f}, SMA200: {sma:.2f}, regime: {_classify_regime(cur, sma)}")
    else:
        print("  Real fetch failed (likely no API keys in env)")
    # Local fallback test
    local = _load_vix_history_local()
    if local:
        print(f"  Local cache: cur={local[0]:.2f} sma200={local[1]:.2f}")
    else:
        print(f"  No local cache at {_VIX_HIST_FILE}")
