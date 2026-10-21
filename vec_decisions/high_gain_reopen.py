"""TR HIGH-GAIN REOPEN — vector twin of tradier_manage.periodic_direct_high_gain_reopen.

Live (tradier_manage.py:60123): head-gated by config_tradier.ENABLE_TR_HIGH_GAIN_REOPEN
(default False; this module never reads it — the vector switch below is the twin) and
skipped for account "tra" (sell-only). Otherwise every 150 s it re-runs entry evaluation
(monitor_entries -> process_position, event_type="reopen_high_gain") so a REDUCED
high-gain position lifecycle can re-open.

Vector semantics (stocks / MODE=tradier only, flat only): after the last CLOSE whose
lifecycle contained a REDUCE (partial profit lock — the "reduced" half) and whose
entry->exit move holds a high gain, re-enter within WINDOW bars when the 15m tape
confirms momentum. The momentum legs mirror live direct_high_gain_augmentation
(gain >= 0.5 %; LONG rsi<75, k>d, HA green, rel_vol>1.2; SHORT mirrored) mapped from
the live 5m TF to the NPZ 15m TF — backtest_v8/indicators/*.npz carries 15m/1h/4h/D
plus only sma_200_1m, NO 3m/5m/1m-WT, so the 5m legs are unwirable by construction.

ONE predicate for v12_quick_engine.simulate_one (single _hgr.fire call site, gated to
MODE == "tradier"); default OFF so the sweep baseline is untouched (BIBLE s14.1).
Fail-closed throughout: any missing/NaN input refuses the bar, never forces a trade.
"""

from __future__ import annotations

import math

REASON = "HIGH_GAIN_REOPEN"


def _num(v, d):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _get(cfg, k, d):
    v = cfg(k, d) if callable(cfg) else getattr(cfg, k, d)
    return d if v is None else v


def enabled(cfg) -> bool:
    v = _get(cfg, "TR_HIGH_GAIN_REOPEN_ENABLED", False)
    return (
        (str(v).strip().lower() in ("1", "true", "yes"))
        if isinstance(v, str)
        else bool(v)
    )


def _bar_of(t):
    for k in ("bar", "bar_exit", "bar_entry"):
        try:
            b = t.get(k, None)
            if b is not None:
                return int(b)
        except (TypeError, ValueError):
            continue
    return None


def was_reduced(close_trade, trades, events) -> bool:
    """True when the closed lifecycle was reduced: a REDUCE record belongs to it.

    trades-REDUCE records carry bar_entry (exact lifecycle match); events-REDUCE
    records carry only bar (window match inside [bar_entry, bar_exit]).
    """
    try:
        be = int(close_trade.get("bar_entry", -1))
        bx = int(close_trade.get("bar_exit", -1))
        if be < 0 or bx < be:
            return False
        seen = []
        try:
            seen = list(trades)
        except TypeError:
            pass
        try:
            seen = seen + list(events or [])
        except TypeError:
            pass
        for t in seen:
            try:
                if str(t.get("type", "")) != "REDUCE":
                    continue
            except AttributeError:
                continue
            rbe = t.get("bar_entry", None)
            if rbe is not None:
                try:
                    if int(rbe) == be:
                        return True
                except (TypeError, ValueError):
                    pass
                continue
            rb = _bar_of(t)
            if rb is not None and be <= rb <= bx:
                return True
        return False
    except Exception:
        return False


def lifecycle_gain_pct(is_long: bool, entry_price: float, exit_price: float) -> float:
    ep, xp = _num(entry_price, 0.0), _num(exit_price, 0.0)
    if ep <= 0 or xp <= 0:
        return 0.0
    m = (xp / ep - 1.0) * 100.0
    return m if is_long else -m


def fire(
    cfg,
    is_long: bool,
    bars_since_exit: int,
    entry_price: float,
    exit_price: float,
    close: float,
    rsi: float,
    k: float,
    d: float,
    ha_color: float,
    relvol: float,
    dc4h_low: float,
    dc4h_high: float,
) -> bool:
    """True when a reduced high-gain lifecycle may re-open on this bar."""
    try:
        if not enabled(cfg):
            return False
        try:
            bars = int(bars_since_exit)
        except (TypeError, ValueError):
            return False
        win = int(_num(_get(cfg, "TR_HIGH_GAIN_REOPEN_WINDOW_BARS", 12), 12))
        if win < 1 or not (1 <= bars <= win):
            return False
        gain = lifecycle_gain_pct(is_long, entry_price, exit_price)
        if gain < abs(_num(_get(cfg, "TR_HIGH_GAIN_REOPEN_MIN_GAIN_PCT", 0.5), 0.5)):
            return False
        px = _num(close, 0.0)
        if px <= 0:
            return False
        r, kk, dd = (
            _num(rsi, float("nan")),
            _num(k, float("nan")),
            _num(d, float("nan")),
        )
        if not (math.isfinite(r) and math.isfinite(kk) and math.isfinite(dd)):
            return False
        ha = _num(ha_color, 0.0)
        rv = _num(relvol, 0.0)
        if rv < abs(_num(_get(cfg, "TR_HIGH_GAIN_REOPEN_MIN_RELVOL", 1.2), 1.2)):
            return False
        if is_long:
            if r >= abs(
                _num(_get(cfg, "TR_HIGH_GAIN_REOPEN_RSI_LONG_MAX", 75.0), 75.0)
            ):
                return False
            if not (kk > dd):
                return False
            if not (ha > 0):
                return False
            lo = _num(dc4h_low, 0.0)
            if lo > 0 and px <= lo:
                return False
        else:
            if r <= abs(
                _num(_get(cfg, "TR_HIGH_GAIN_REOPEN_RSI_SHORT_MIN", 25.0), 25.0)
            ):
                return False
            if not (kk < dd):
                return False
            if not (ha < 0):
                return False
            hi = _num(dc4h_high, 0.0)
            if hi > 0 and px >= hi:
                return False
        return True
    except Exception:
        return False
