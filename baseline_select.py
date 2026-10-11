"""Baseline winner selection shared by v15 Layer-1 and the challenger-choose hook.

USER 2026-10-11: EVERY test runs the latest-best per_sym setting and the TEMPLATE_CAT_SIDE
settings in parallel and starts from the best option. Rule (mirrors Layer-1): credible first,
then higher gain, higher win-rate, lower dd, higher sharpe; ties go to the incumbent.
Pure functions so the rule is unit-tested without importing the pilot.
"""


def is_credible(vec, floor_trades, ultra_neg_pct):
    return (
        bool(vec.get("valid"))
        and int(vec.get("trades") or 0) >= floor_trades
        and float(vec.get("gain_pct") or -1e9) >= ultra_neg_pct
    )


def _wr(vec):
    for k in ("win_rate", "winrate", "wr", "WR"):
        if k in vec and vec[k] is not None:
            try:
                return float(vec[k])
            except Exception:
                pass
    try:
        return float(vec.get("pool_sharpe") or 0) * 0.1 + 0.5
    except Exception:
        return 0.5


def score(vec, floor_trades, ultra_neg_pct):
    return (
        1 if is_credible(vec, floor_trades, ultra_neg_pct) else 0,
        float(vec.get("gain_pct") or -1e9),
        _wr(vec),
        -float(vec.get("max_dd_pct") or 1e9),
        float(vec.get("pool_sharpe") or -1e9),
    )


def pick_winner(cands, floor_trades, ultra_neg_pct, incumbent=None):
    """cands: {label: vec}; None vecs skipped. Best score wins; ties -> incumbent, else first label."""
    best_label, best_score = None, None
    for label, vec in (cands or {}).items():
        if vec is None:
            continue
        s = score(vec, floor_trades, ultra_neg_pct)
        if (
            best_score is None
            or s > best_score
            or (s == best_score and label == incumbent)
        ):
            best_label, best_score = label, s
    return best_label
