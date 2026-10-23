"""Stock-split adjustment for the rankings price pipeline (USER 2026-10-23).

An unadjusted stock split masquerades as a violent price move: a 10:1 split
prints a -90% bar, a 1:20 reverse split a +1900% bar. Both flow straight into
`calculate_relative_gains` (ez_rankings.py:2781, regression slope over close)
and `calculate_weighted_gains` (ez_rankings.py:3525, weighted per-bar
pct_change), corrupting scores, winner/loser sides and symbols_*.json.

Price sources adjusted by this module (split enters every one of them raw):
* crypto: ez_rankings._load_klines_from_file (:1782, {SYM}_{tf}.json klines)
  -> convert_cached_klines_to_analysis_df (:1866) -> gain fns above.
* stocks: TradierAPIClient.get_timesales/get_history (tradier_api.py:545/555,
  Tradier ships UNADJUSTED bars; the client has NO corporate-actions endpoint)
  -> TradierBarManager.get_latest -> convert_tradier_bars_to_analysis_df
  (tradier_rankings.py:648) -> the SAME ez_rankings gain fns (imported at
  tradier_rankings.py:127, used at :2419).

Split-event source: yfinance `Ticker.splits` — already a repo dep (used by
chart_server.py, btc/gold/oil monitors, tradier_manage.py:45126), so no new
dependencies. Events are cached under data/corporate_actions/; without either
cache or network the caller gets None and must flag [UNVERIFIED] — never a
fabricated series.

WIRING (needs an unlock of ez_rankings.py / tradier_rankings.py — NOT applied):
  from split_adjust import apply_split_adjustment, get_splits
  splits, prov = get_splits(symbol)
  df, rep = apply_split_adjustment(df, splits, symbol=symbol)
  if not rep["verified"]: flag "[UNVERIFIED]"
at ez_rankings.py:1866 (crypto) and tradier_rankings.py:648 (stocks).

APPLY ONLY TO UNADJUSTED FEEDS. Verified 2026-10-23: Tradier
/markets/history daily bars and yfinance history() are ALREADY split-adjusted
(NVDA 2024-06-07 = 120.88, not the traded 1208.80) — adjusting them again
manufactures a +900% jump. The corruption vector is the UNADJUSTED side only:
Tradier timesales intraday bars merged append-forever into {SYM}_{tf}.json by
merge_klines (tradier_indicators.py:2412), which keeps pre-split prints at
pre-split scale across the boundary.
"""

import json
import logging
import math
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = ROOT / "data" / "corporate_actions"
CACHE_TTL_S = 7 * 24 * 3600

PRICE_COLS = ("open", "high", "low", "close")
VOLUME_COL = "volume"
DATE_CANDIDATES = ("close_time", "timestamp_dt", "timestamp", "time")
NO_COVERAGE = "NO_COVERAGE"


def to_equity_symbol(symbol: Any) -> Optional[str]:
    """NVDAUSDT -> NVDA (yfinance equity form); bare NVDA passes through."""
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    out = symbol.strip().upper()
    for suffix in ("USDT", "USDC"):
        if out.endswith(suffix) and len(out) > len(suffix):
            out = out[: -len(suffix)]
            break
    return out or None


def _coerce_date(value: Any) -> Optional[date]:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def normalize_splits(events: Any) -> Tuple[List[Dict[str, Any]], int]:
    """Validate raw split events -> sorted [{ex_date, factor}] + dropped count.

    factor = new_shares / old_shares (10.0 = 10:1 forward, 0.05 = 1:20
    reverse). Same-date duplicates combine by product; factor 1.0, non-finite,
    non-positive or undated events are dropped, never applied.
    """
    dropped = 0
    merged: Dict[str, float] = {}
    if not isinstance(events, (list, tuple)):
        return [], 1 if events is not None else 0
    for event in events:
        try:
            factor = float(event.get("factor")) if isinstance(event, dict) else float(event)
            ex = _coerce_date(event.get("ex_date")) if isinstance(event, dict) else None
        except Exception:
            dropped += 1
            continue
        if ex is None or not math.isfinite(factor) or factor <= 0.0 or factor == 1.0:
            dropped += 1
            continue
        key = ex.isoformat()
        merged[key] = merged.get(key, 1.0) * factor
    clean = [{"ex_date": key, "factor": merged[key]} for key in sorted(merged)]
    return clean, dropped


def _bar_dates(df: pd.DataFrame) -> Tuple[List[Optional[date]], Optional[str]]:
    for col in DATE_CANDIDATES:
        if col in df.columns:
            try:
                parsed = pd.to_datetime(df[col], utc=True, errors="coerce")
                return [_coerce_date(v) for v in parsed], col
            except Exception:
                continue
    try:
        parsed = pd.to_datetime(df.index, utc=True, errors="coerce")
        return [_coerce_date(v) for v in parsed], "__index__"
    except Exception:
        return [None] * len(df), None


def apply_split_adjustment(df: pd.DataFrame, splits: Optional[List[Dict[str, Any]]], symbol: str = "") -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Backward-adjust pre-split prices (volumes inversely). Pure: input never mutated.

    splits=None means NO coverage -> frame returned unchanged with
    verified=False (caller must flag [UNVERIFIED]). splits=[] means coverage
    confirms no splits -> unchanged with verified=True.
    """
    report: Dict[str, Any] = {"symbol": symbol, "adjusted": False, "verified": False, "n_splits": 0, "n_invalid_dropped": 0, "n_undated_bars": 0, "max_jump_pct_before": None, "max_jump_pct_after": None, "reason": None}
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or "close" not in df.columns:
        report["reason"] = "EMPTY_FRAME"
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame(), report
    raw_close = pd.to_numeric(df["close"], errors="coerce")
    try:
        report["max_jump_pct_before"] = float(raw_close.pct_change().abs().max() * 100.0)
    except Exception:
        pass
    if splits is None:
        report["reason"] = NO_COVERAGE
        return df.copy(), report
    clean, dropped = normalize_splits(splits)
    report["n_invalid_dropped"] = dropped
    report["verified"] = True
    if not clean:
        if dropped:
            report["verified"] = False
            report["reason"] = "ALL_EVENTS_INVALID"
        return df.copy(), report
    report["n_splits"] = len(clean)
    bar_dates, _ = _bar_dates(df)
    ex_dates = [datetime.fromisoformat(e["ex_date"]).date() for e in clean]
    inv_factors = np.array([1.0 / e["factor"] for e in clean], dtype=float)
    mult = np.ones(len(df), dtype=float)
    for i, bar in enumerate(bar_dates):
        if bar is None:
            report["n_undated_bars"] += 1
            continue
        hits = [k for k, ex in enumerate(ex_dates) if ex > bar]
        if hits:
            mult[i] = float(np.prod(inv_factors[hits]))
    adj = df.copy()
    for col in PRICE_COLS:
        if col in adj.columns:
            adj[col] = pd.to_numeric(adj[col], errors="coerce") * mult
    if VOLUME_COL in adj.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            adj[VOLUME_COL] = pd.to_numeric(adj[VOLUME_COL], errors="coerce") / np.where(mult == 0.0, np.nan, mult)
    report["adjusted"] = True
    try:
        report["max_jump_pct_after"] = float(pd.to_numeric(adj["close"], errors="coerce").pct_change().abs().max() * 100.0)
    except Exception:
        pass
    return adj, report


def _read_cache(path: Path) -> Tuple[Optional[List[Dict[str, Any]]], float]:
    try:
        payload = json.loads(path.read_text())
        splits, _ = normalize_splits(payload.get("splits", []))
        updated = float(payload.get("updated_at", 0.0))
        return splits, updated
    except Exception:
        return None, 0.0


def _write_cache(path: Path, symbol: str, splits: List[Dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"symbol": symbol, "updated_at": time.time(), "source": "yfinance", "splits": splits}
        with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False) as tmp:
            json.dump(payload, tmp)
            tmp_path = tmp.name
        Path(tmp_path).replace(path)
    except Exception as e:
        logger.warning(f"[split_adjust] cache write failed for {symbol}: {e}")


def get_splits(symbol: str, cache_dir: Optional[Path] = None, ttl_s: float = CACHE_TTL_S) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """Split events for a ranking symbol. ([], fresh) = verified no splits.

    Returns (None, prov) when neither fresh cache, stale cache nor network can
    supply events — the caller must flag [UNVERIFIED] and use raw prices.
    """
    prov: Dict[str, Any] = {"symbol": symbol, "equity": None, "source": None, "cached": False, "stale": False}
    equity = to_equity_symbol(symbol)
    prov["equity"] = equity
    if not equity:
        return None, prov
    cache_path = (Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR) / f"{equity}.json"
    cached, updated = _read_cache(cache_path) if cache_path.exists() else (None, 0.0)
    if cached is not None and time.time() - updated < ttl_s:
        prov["source"], prov["cached"] = "cache", True
        return cached, prov
    try:
        import yfinance as yf
        series = yf.Ticker(equity).splits
        events = [{"ex_date": ts.date().isoformat(), "factor": float(val)} for ts, val in series.items()]
        clean, _ = normalize_splits(events)
        _write_cache(cache_path, equity, clean)
        prov["source"] = "yfinance"
        return clean, prov
    except Exception as e:
        logger.warning(f"[split_adjust] yfinance fetch failed for {equity}: {e}")
    if cached is not None:
        prov["source"], prov["cached"], prov["stale"] = "cache", True, True
        return cached, prov
    return None, prov


def current_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
