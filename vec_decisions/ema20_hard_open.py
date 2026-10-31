import numpy as np


def all_ema20_against(price, levels, is_long):
    price = np.asarray(price, dtype=float)
    values = np.stack([np.broadcast_to(np.asarray(value, dtype=float), price.shape) for value in levels])
    valid = values > 0
    against = price < values if is_long else price > values
    return (np.sum(valid, axis=0) >= 2) & np.all(~valid | against, axis=0)


def hard_open_mask(npz, n, is_long, close, safe):
    return all_ema20_against(close, [safe(npz, f'ema_20_{tf}', n, 0.0) for tf in ('15m', '1h', '4h')], is_long)


def hard_open_live(indicators, price, is_long):
    return bool(all_ema20_against(price, [float(indicators.get(f'ema_20_{tf}', 0.0) or 0.0) for tf in ('15m', '1h', '4h')], is_long))
