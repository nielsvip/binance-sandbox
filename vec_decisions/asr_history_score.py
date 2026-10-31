"""Causal tracker-history score modifier for AdvancedSignalRater."""

import numpy as np


def history_score_scalar(tracker_data, is_exit, safe_float):
    """Return native ordered history modifier and reasons for one call."""
    result = 0.0
    reasons = []
    if tracker_data and not is_exit:
        win_rate = safe_float(tracker_data.get("win_rate_%", 0), 0)
        total_trades = int(safe_float(tracker_data.get("total_trades", 0), 0) or 0.0)
        if total_trades >= 3:
            if win_rate >= 70:
                result += 2.0
                reasons.append(f"HighWinRate_{win_rate:.0f}%")
            elif win_rate <= 30:
                result -= 3.0
                reasons.append(f"LowWinRate_{win_rate:.0f}%")
        total_pnl = safe_float(tracker_data.get("total_realized_pnl_$", 0), 0)
        if total_pnl < -50.0:
            result -= 2.0
            reasons.append("Historical_Loser")
        elif total_pnl > 50.0:
            result += 1.0
            reasons.append("Historical_Winner")
    return result, reasons


def history_score_array(history_present, win_rate, total_trades, total_realized_pnl, is_exit=False):
    """Return modifier and component masks from causally reconstructed tracker history."""
    present, win, trades, pnl = np.broadcast_arrays(
        np.asarray(history_present, dtype=bool),
        np.asarray(win_rate, dtype=np.float64),
        np.asarray(total_trades, dtype=np.float64),
        np.asarray(total_realized_pnl, dtype=np.float64),
    )
    present = present & (not is_exit)
    high_win = present & (trades >= 3) & (win >= 70)
    low_win = present & (trades >= 3) & (win <= 30)
    historical_loser = present & (pnl < -50.0)
    historical_winner = present & (pnl > 50.0)
    score = high_win.astype(float) * 2.0 - low_win.astype(float) * 3.0
    score -= historical_loser.astype(float) * 2.0
    score += historical_winner.astype(float)
    return score, {
        "HighWinRate": high_win,
        "LowWinRate": low_win,
        "Historical_Loser": historical_loser,
        "Historical_Winner": historical_winner,
    }
