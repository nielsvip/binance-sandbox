"""MOP-UP M 2026-10-04 — vec twin of ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC (C41 wire).

LIVE SOURCE (read-only reference): ez_manage.py::process_position ALL_TF_AGAINST_CLOSE
  block (:48266-48270): per-position cooldown dict; suppress the close when
  (now_ts - last_fire_ts) < COOLDOWN_SEC (default 30.0); record last_fire on fire
  (:48309). The vec exit mask (_atf_mask, v12:12532-12542) fires with NO cooldown.

TWIN: precompute gate — walk the mask in bar order, keep a fire, suppress later
  fires within cd_sec of the last KEPT fire (epoch ts, same rule as live).
  Default 30s on 15m bars is vacuous (proven: gated == raw on real NPZ); the knob
  binds on sweep (cd >> bar spacing suppresses re-fires).
BIBLE: §17 (vec default 30.0 == live 30.0); §18 (default vacuous = honest-0).
"""
from __future__ import annotations

import numpy as np


def gate_mask(mask, ts, cd_sec):
    """Return mask with re-fires inside cd_sec of the last kept fire removed."""
    m = np.asarray(mask, dtype=bool)
    n = len(m)
    if n == 0:
        return m
    try:
        cd = float(cd_sec)
    except Exception:
        return m
    if not (cd > 0):
        return m
    try:
        t = np.asarray(ts, dtype=np.float64)
    except Exception:
        return m
    if len(t) != n:
        return m
    out = np.zeros(n, dtype=bool)
    last = float("-inf")
    for i in range(n):
        if not m[i]:
            continue
        now = float(t[i])
        if (now - last) < cd:
            continue
        out[i] = True
        last = now
    return out


def gate_mask_scalar(mask, ts, i, cd_sec, last_fire_ts):
    """Scalar reference: may bar i fire given last kept-fire ts? Returns bool."""
    try:
        if not bool(np.asarray(mask)[i]):
            return False
        cd = float(cd_sec)
        if not (cd > 0):
            return True
        return (float(np.asarray(ts)[i]) - float(last_fire_ts)) >= cd
    except Exception:
        return False
