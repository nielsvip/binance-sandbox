"""
rate_cascade.py — cascade multi-TF helpers for AdvancedSignalRater.rate().

User directive 2026-04-16: rate() must understand how TFs are intertwined.

Cascade = drill-down TF alignment starting at 3m, escalating to 15m, 1h, 4h, D.
For LONG: if k_3m >= threshold, check k_15m >= threshold. If yes, check k_1h.
Escalate as long as each next TF is also aligned. Depth 0-5.
Higher depth = broader multi-TF agreement = MORE bullish (or bearish for SHORT).

Separately: 3m delta slowdown / 3m WT reverse = immediate exit trigger.
"""


def cascade_k_depth(is_long, k_3m, k_15m, k_1h, k_4h, k_D, threshold=80.0):
    """Drill-down K cascade. LONG: each TF K >= threshold; SHORT: each TF K <= 100-threshold."""
    ks = [k_3m, k_15m, k_1h, k_4h, k_D]
    if is_long:
        bound = float(threshold)
        depth = 0
        for k in ks:
            if k >= bound:
                depth += 1
            else:
                break
        return depth
    inv = 100.0 - float(threshold)
    depth = 0
    for k in ks:
        if k <= inv:
            depth += 1
        else:
            break
    return depth


def cascade_wt_depth(is_long, wt1_3m, wt2_3m, wt1_15m, wt2_15m,
                     wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D):
    """Drill-down WT cascade. LONG: wt1 > wt2 each TF; SHORT: wt1 < wt2 each TF."""
    pairs = [(wt1_3m, wt2_3m), (wt1_15m, wt2_15m), (wt1_1h, wt2_1h),
             (wt1_4h, wt2_4h), (wt1_D, wt2_D)]
    depth = 0
    for w1, w2 in pairs:
        aligned = (w1 > w2) if is_long else (w1 < w2)
        if aligned:
            depth += 1
        else:
            break
    return depth


def cascade_dc_depth(is_long, price, dc_basis_3m, dc_basis_15m, dc_basis_1h,
                     dc_basis_4h, dc_basis_D):
    """Drill-down DC basis cascade. LONG: price above basis each TF; SHORT: below."""
    bases = [dc_basis_3m, dc_basis_15m, dc_basis_1h, dc_basis_4h, dc_basis_D]
    depth = 0
    for b in bases:
        if b is None or b <= 0:
            break
        aligned = (price > b) if is_long else (price < b)
        if aligned:
            depth += 1
        else:
            break
    return depth


def cascade_composite(k_depth, wt_depth, dc_depth):
    """Sum 0-15. Used as score bonus and RZ-entry confirmation gate."""
    return int(k_depth) + int(wt_depth) + int(dc_depth)


def compute_3m_delta_exit(is_long, wt_velocity_3m, wt_velocity_3m_prev,
                          wt1_3m, wt2_3m, wt1_3m_prev, wt2_3m_prev,
                          slowdown_ratio=0.5, strong_thresh=1.0, flip_thresh=0.5):
    """
    Immediate-exit trigger per user directive 2026-04-16:
      "3m delta slowdown / wt reverse which means immediate exit".

    Fires on ANY of:
      (a) 3m WT reverse: wt1 crossed wt2 against position this bar
      (b) 3m delta slowdown: velocity was strong, now <= slowdown_ratio * prev
      (c) 3m delta sign flip: velocity was >= flip_thresh, now opposite side
    """
    if is_long:
        wt_reverse = (wt1_3m_prev > wt2_3m_prev) and (wt1_3m < wt2_3m)
        vel_slow = (wt_velocity_3m_prev > strong_thresh) and \
                   (wt_velocity_3m < wt_velocity_3m_prev * slowdown_ratio)
        vel_flip = (wt_velocity_3m_prev > flip_thresh) and (wt_velocity_3m < 0)
        return bool(wt_reverse or vel_slow or vel_flip)
    wt_reverse = (wt1_3m_prev < wt2_3m_prev) and (wt1_3m > wt2_3m)
    vel_slow = (wt_velocity_3m_prev < -strong_thresh) and \
               (wt_velocity_3m > wt_velocity_3m_prev * slowdown_ratio)
    vel_flip = (wt_velocity_3m_prev < -flip_thresh) and (wt_velocity_3m > 0)
    return bool(wt_reverse or vel_slow or vel_flip)


def compute_reentry_crossback(is_long, current_price, last_exit_price,
                              wt_velocity_3m, wt1_3m, wt2_3m,
                              min_vel=0.5):
    """
    Full reentry trigger per user directive:
      "Reverse back up or cross exit price = full reentry".

    LONG: price crosses back above last_exit_price AND (wt_vel_3m>0 OR wt1>wt2).
    SHORT: mirror.
    """
    if last_exit_price is None or last_exit_price <= 0:
        return False
    if is_long:
        cross_back = current_price > last_exit_price
        delta_up = (wt_velocity_3m >= min_vel) or (wt1_3m > wt2_3m)
        return bool(cross_back and delta_up)
    cross_back = current_price < last_exit_price
    delta_down = (wt_velocity_3m <= -min_vel) or (wt1_3m < wt2_3m)
    return bool(cross_back and delta_down)
