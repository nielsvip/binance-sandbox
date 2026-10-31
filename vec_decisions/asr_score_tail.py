import numpy as np

def finish_scalar(cfg, score, current_price, is_long, dc_high_1h, dc_high_3m, dc_low_1h, dc_low_3m, k_1m, k_3m, true_lag, rec):
    breakout = bool(getattr(cfg, "OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS", True)) and ((is_long and ((dc_high_1h > 0 and current_price > dc_high_1h) or (dc_high_3m > 0 and current_price > dc_high_3m))) or ((not is_long) and ((dc_low_1h > 0 and current_price < dc_low_1h) or (dc_low_3m > 0 and current_price < dc_low_3m))))
    if not breakout and ((is_long and ((k_1m > 70 and true_lag < 10.0) or k_3m > 80)) or (not is_long and ((k_1m < 30 and true_lag < 10.0) or k_3m < 20))):
        score = 0.2 * score
    final_score = max(0, min(60, int(round(score))))
    if final_score >= 29:
        rec = "STRONG_BUY" if is_long else "STRONG_SELL"
    elif final_score >= 24:
        rec = "GOOD_BUY" if is_long else "GOOD_SELL"
    elif final_score >= 18:
        rec = "WEAK_BUY" if is_long else "WEAK_SELL"
    return final_score, rec

def finish_array(cfg, score, current_price, is_long, dc_high_1h, dc_high_3m, dc_low_1h, dc_low_3m, k_1m, k_3m, true_lag, rec):
    score, price, high_1h, high_3m, low_1h, low_3m, k1, k3, lag = np.broadcast_arrays(*[np.asarray(value, dtype=float) for value in (score, current_price, dc_high_1h, dc_high_3m, dc_low_1h, dc_low_3m, k_1m, k_3m, true_lag)])
    breakout = ((high_1h > 0) & (price > high_1h)) | ((high_3m > 0) & (price > high_3m)) if is_long else ((low_1h > 0) & (price < low_1h)) | ((low_3m > 0) & (price < low_3m))
    breakout &= bool(getattr(cfg, "OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS", True))
    extreme = ((k1 > 70) & (lag < 10.0)) | (k3 > 80) if is_long else ((k1 < 30) & (lag < 10.0)) | (k3 < 20)
    adjusted = np.where(~breakout & extreme, score * 0.2, score)
    if np.isnan(adjusted).any():
        raise ValueError("cannot convert float NaN to integer")
    if np.isinf(adjusted).any():
        raise OverflowError("cannot convert float infinity to integer")
    final_score = np.clip(np.rint(adjusted), 0, 60).astype(np.int64)
    recommendations = np.broadcast_to(np.asarray(rec, dtype=object), final_score.shape).copy()
    recommendations[final_score >= 18] = "WEAK_BUY" if is_long else "WEAK_SELL"
    recommendations[final_score >= 24] = "GOOD_BUY" if is_long else "GOOD_SELL"
    recommendations[final_score >= 29] = "STRONG_BUY" if is_long else "STRONG_SELL"
    return final_score, recommendations
