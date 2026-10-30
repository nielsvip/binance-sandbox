"""SESSION_OPEN_STOP_REFREEZE (USER 2026-10-13, stocks): at each market open a position carried overnight gets a NEW
FIXED_STOP at the current dc_15m_low -0.25% (vv short), using the unchanged math of vec_decisions/fixed_stop.py.

Switch SESSION_OPEN_STOP_REFREEZE_ENABLED (config_tradier.py + QuickConfig, default False, TEMPLATE_STOCKS_{LONG,SHORT}).
Live twin: tradier_manage.process_position FIXED_STOP block (once per position per ET date while RTH; opened before today).
Vec: session_open_mask() marks the first RTH bar of every ET trading date; simulate_one re-freezes pos["fixed_stop"] on that
bar when the position was entered on an earlier bar (= carried into the session), then the normal breach check runs.
"""
from __future__ import annotations

import numpy as np


def session_open_mask(ts_label, bar_minutes: float = 15.0) -> np.ndarray:
    """True on the first RTH bar (Mon-Fri 09:30-16:00 ET, bar fully inside) of each ET trading date."""
    import datetime as _dt

    import vec_decisions.stocks_live_session_gates as _slsg

    ts = np.asarray(ts_label, dtype=float)
    n = len(ts)
    out = np.zeros(n, dtype=bool)
    if n == 0 or getattr(_slsg, "_NY", None) is None:
        return out
    rth = _slsg.rth_mask(ts, bar_minutes)
    scale = 1000.0 if np.nanmax(np.abs(ts)) > 1e11 else 1.0
    seen = set()
    for i in range(n):
        if not rth[i] or not np.isfinite(ts[i]) or ts[i] <= 0:
            continue
        d = _dt.datetime.fromtimestamp(ts[i] / scale, _slsg._NY).date()
        if d not in seen:
            seen.add(d)
            out[i] = True
    return out
