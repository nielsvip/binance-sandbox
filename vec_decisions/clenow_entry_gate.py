import numpy as np

def pass_mask(npz, n, is_long, cfg, close, safe):
    allowed = np.ones(n, dtype=bool)
    if str(getattr(cfg, "MODE", "crypto")) == "tradier":
        if bool(getattr(cfg, "CLENOW_ENABLED", False)):
            sma = safe(npz, "sma200_D", n, 0.0)
            price = safe(npz, "current_price", n, 0.0) if "current_price" in npz else np.asarray(close, dtype=float)
            price = np.where(price != 0, price, safe(npz, "close_5m", n, 0.0))
            allowed &= ~((sma > 0) & ((price < sma) if is_long else (price > sma)))
        if bool(getattr(cfg, "CLENOW_GATE_ENABLED", False)):
            allowed &= ~(safe(npz, "clenow_score", n, 0.0) < float(getattr(cfg, "CLENOW_GATE_MIN_SCORE", 15.0)))
        return allowed
    if not bool(getattr(cfg, "CLENOW_ENABLED", False)):
        return allowed
    sma = safe(npz, "sma_200_1h", n, 0.0)
    sma = np.where(np.isfinite(sma), sma, 0.0)
    price = np.asarray(close, dtype=float)
    price = np.where(np.isfinite(price), price, 0.0)
    allowed &= ~((sma > 0) & ((price <= sma) if is_long else (price >= sma)))
    if bool(getattr(cfg, "CLENOW_GATE_ENABLED", False)):
        threshold = float(getattr(cfg, "CLENOW_GATE_MIN_SCORE", 15.0))
        threshold = (threshold if np.isfinite(threshold) else 15.0) / 5.0
        score = np.divide((price - sma) * 100.0, sma, out=np.zeros(n), where=sma > 0)
        allowed &= ~((sma > 0) & (price > 0) & ((score < threshold) if is_long else (score > -threshold)))
    return allowed
