"""Scalar-array adapter for Tradier's ordered native entry score route."""
import numpy as np
import re


class _Tradeability:
    def __init__(self, symbol, is_long):
        self.symbol = symbol
        self.symbols_long_v12 = [symbol] if is_long else []
        self.symbols_short_v12 = [symbol] if not is_long else []

    def is_symbol_tradeable(self, symbol, account_key, side):
        return symbol == self.symbol and side == ("LONG" if self.symbols_long_v12 else "SHORT")


def _value_at(npz, key, index, n):
    try:
        raw = np.asarray(npz[key])
        if raw.ndim != 1 or raw.size != n:
            return None
        value = raw[index]
        return value.item() if hasattr(value, "item") else value
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def paired_lower_tf_indicators(indicators, ltf_enabled):
    """Pair native 3m/5m score inputs to their 15m twins under LTF-off."""
    result = dict(indicators or {})
    if ltf_enabled:
        return result
    for key in list(result):
        match = re.match(r"^(.*_)(?:3m|5m)(.*)$", str(key))
        if not match:
            continue
        paired = f"{match.group(1)}15m{match.group(2)}"
        if paired in result:
            result[key] = result[paired]
        else:
            result.pop(key, None)
    return result


def entry_score_arrays(npz, n, symbol, is_long, cfg):
    """Evaluate native StockStrategy score and final threshold in bar order.

    This calls the production scalar scorer for each prepared bar so every
    ordered pre-gate, contribution, recommendation and Sniper exception stays
    identical while the vector engine assembles its action mask.
    """
    if str(getattr(cfg, "MODE", "crypto")) != "tradier":
        return np.zeros(n, dtype=float), np.zeros(n, dtype=bool), [""] * n
    timestamps = np.asarray(npz.get("timestamps", []), dtype=float).reshape(-1)
    if timestamps.size != n:
        raise ValueError("Tradier signal score requires aligned NPZ timestamps")
    import tradier_manage as tm

    strategy = tm.StockStrategy.__new__(tm.StockStrategy)
    strategy.config = cfg
    strategy.trade_manager = _Tradeability(symbol, is_long)
    side = "LONG" if is_long else "SHORT"
    score_values = np.zeros(n, dtype=float)
    fires = np.zeros(n, dtype=bool)
    reasons = [""] * n
    cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
    try:
        for index in range(n):
            indicators = {}
            for key in npz:
                value = _value_at(npz, key, index, n)
                if value is not None:
                    indicators[key] = value
            ts_token = tm._TR_SCORE_TS_CTX.set(float(timestamps[index]))
            try:
                score, recommendation, reason = strategy.calculate_signal_score(
                    "v12", symbol, is_long, float(indicators.get("close", 0.0) or 0.0), indicators, None, is_exit=False
                )
                threshold = float(tm._cfg("ENTRY_SCORE_THRESHOLD", 12.0, "v12", symbol, side) or 0.0)
            finally:
                tm._TR_SCORE_TS_CTX.reset(ts_token)
            score_values[index] = float(score)
            native_reason = str(reason or "")
            fires[index] = score_values[index] >= threshold or (score_values[index] >= 6.0 and "Sniper" in native_reason)
            if fires[index]:
                if score_values[index] < threshold and "Sniper" in native_reason:
                    reasons[index] = f"SNIPER_{side}_{recommendation}"
                else:
                    reasons[index] = f"{side}_{recommendation}: {native_reason}"
    finally:
        tm._TR_SCORE_CFG_CTX.reset(cfg_token)
    return score_values, fires, reasons
