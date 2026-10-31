"""Ordered ranking and market-sentiment contributions from AdvancedSignalRater."""
from __future__ import annotations

import numpy as np


def apply_rank_sentiment_scalar(score, is_long, top_sent, bot_sent, rank_points, history_score_mod, has_ind, local_sent, global_sent, sentc):
    if is_long:
        if top_sent:
            score += 2.0
        elif bot_sent:
            score -= 5.0
    else:
        if bot_sent:
            score += 2.0
        elif top_sent:
            score -= 5.0
    if is_long:
        if rank_points >= 95:
            score += 4.0
        elif rank_points >= 90:
            score += 2.5
        elif rank_points >= 80:
            score += 1.5
        elif rank_points >= 50:
            score += 0.5
    else:
        if rank_points <= 5:
            score += 4.0
        elif rank_points <= 10:
            score += 2.5
        elif rank_points <= 20:
            score += 1.5
        elif rank_points <= 50:
            score += 0.5
    score += history_score_mod
    if has_ind:
        alpha = local_sent - global_sent
        if is_long:
            if sentc == "EXTREME_BULLISH":
                score += 2
            if sentc == "NEUTRAL":
                score -= 1
            if "BEARISH" in sentc:
                score -= 2
            if local_sent > 50:
                score += 1.5
            elif local_sent > 25:
                score += 0.5
            if global_sent < -20:
                score -= 3
            elif global_sent > 50:
                score += 3
            elif global_sent > 30:
                score += 1
        else:
            if sentc == "EXTREME_BEARISH":
                score += 2
            if sentc == "NEUTRAL":
                score -= 1
            if "BULLISH" in sentc:
                score -= 2
            if local_sent < -50:
                score += 1.5
            elif local_sent < -25:
                score += 0.5
            if global_sent > 20:
                score -= 3
            elif global_sent < -50:
                score += 3
            elif global_sent < -30:
                score += 1
        if is_long:
            if alpha > 50:
                score += 6.0
            elif alpha > 40:
                score += 3.5
            elif alpha > 20:
                score += 1.5
            elif alpha < -20:
                score -= 2.0
        else:
            if alpha < -50:
                score += 6.0
            elif alpha < -40:
                score += 3.5
            elif alpha < -20:
                score += 1.5
            elif alpha > 20:
                score -= 2.0
    return score


def apply_rank_sentiment_vec(score, is_long, top_sent, bot_sent, rank_points, history_score_mod, has_ind, local_sent, global_sent, sentc):
    values = np.broadcast_arrays(
        np.asarray(score, dtype=np.float64),
        np.asarray(top_sent, dtype=bool),
        np.asarray(bot_sent, dtype=bool),
        np.asarray(rank_points, dtype=np.float64),
        np.asarray(history_score_mod, dtype=np.float64),
        np.asarray(has_ind, dtype=bool),
        np.asarray(local_sent, dtype=np.float64),
        np.asarray(global_sent, dtype=np.float64),
        np.asarray(sentc, dtype=object),
    )
    out, top, bot, rank, history, has, local, global_, sent = [v.copy() for v in values]
    if is_long:
        out += np.where(top, 2.0, np.where(bot, -5.0, 0.0))
        out += np.select((rank >= 95, rank >= 90, rank >= 80, rank >= 50), (4.0, 2.5, 1.5, 0.5), default=0.0)
    else:
        out += np.where(bot, 2.0, np.where(top, -5.0, 0.0))
        out += np.select((rank <= 5, rank <= 10, rank <= 20, rank <= 50), (4.0, 2.5, 1.5, 0.5), default=0.0)
    out += history
    alpha = local - global_
    if is_long:
        out += has * np.where(sent == "EXTREME_BULLISH", 2.0, 0.0)
        out += has * np.where(sent == "NEUTRAL", -1.0, 0.0)
        out += has * np.where(np.char.find(sent.astype(str), "BEARISH") >= 0, -2.0, 0.0)
        out += has * np.select((local > 50, local > 25), (1.5, 0.5), default=0.0)
        out += has * np.select((global_ < -20, global_ > 50, global_ > 30), (-3.0, 3.0, 1.0), default=0.0)
        out += has * np.select((alpha > 50, alpha > 40, alpha > 20, alpha < -20), (6.0, 3.5, 1.5, -2.0), default=0.0)
    else:
        out += has * np.where(sent == "EXTREME_BEARISH", 2.0, 0.0)
        out += has * np.where(sent == "NEUTRAL", -1.0, 0.0)
        out += has * np.where(np.char.find(sent.astype(str), "BULLISH") >= 0, -2.0, 0.0)
        out += has * np.select((local < -50, local < -25), (1.5, 0.5), default=0.0)
        out += has * np.select((global_ > 20, global_ < -50, global_ < -30), (-3.0, 3.0, 1.0), default=0.0)
        out += has * np.select((alpha < -50, alpha < -40, alpha < -20, alpha > 20), (6.0, 3.5, 1.5, -2.0), default=0.0)
    return out
