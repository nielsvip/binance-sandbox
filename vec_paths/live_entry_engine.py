"""vec_paths/live_entry_engine.py — vectorized port of the LIVE 4-engine entry vote.

Mirrors the live aggregator pattern used at:
  - ez_manage.py:1050 (_ee_reentry_boost) and :32561 (open-path vote loop)
  - tradier_manage.py:306 (_ee_reentry_boost) and :2657 (open-path vote loop)
  - ez_positions_quick.py:69 / :15356 (reentry vote)

Live aggregator (faithful):
  for engine in (wt, stoch, dc, htf, stdev_macro):
      if not cfg.LIVE_ENTRY_ENGINE_<NAME>_ENABLED: continue
      fire, reason, score = engine_fn(symbol, indicators, side)
      if fire and score >= cfg.LIVE_ENTRY_ENGINE_MIN_SCORE:
          score_max = max(score_max, score)
          reasons.append((name, score))
  if score_max > 0:
      final_score = base_score + (cfg.LIVE_ENTRY_ENGINE_BOOST_SCORE * score_max)
  pass = (final_score >= cfg.WT_DC_ENTRY_THRESHOLD)   # tradier path
  pass = (final_score >= cfg.ENTRY_SCORE_THRESHOLD)   # crypto path

In v8_vec_sweep.py we don't have access to the full `wt_dc_score_entry` base
score (that scorer lives in wt_dc_entry_scorer.py and has not been ported to
vec — out of scope for this task). For sweep purposes we treat `base_score=0`
and let the engine vote alone decide — i.e. the live entry-engine vote is the
ENTRY filter under sweep. This is the same semantic as setting
`LIVE_ENTRY_ENGINE_ENABLED=True` and `WT_DC_ENTRY_THRESHOLD=0` in live:
"any positive engine fire passes". When `WT_DC_ENTRY_THRESHOLD>0` is set in
SweepConfig, the engine boost alone must clear it.

Pure-function module: no I/O, no Redis, no logger, no config_tradier import.
cfg is a dict-like or SweepConfig with `getattr(cfg, KNOB, default)` access.
"""
from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
import os
import sys
import numpy as np

# Live entry engines live at repo root. When this module is imported via
# `from vec_paths.live_entry_engine import ...` with cwd=repo-root, sys.path
# includes cwd and the bare imports below resolve. When this module is run
# directly (`python vec_paths/live_entry_engine.py`) sys.path[0] is `vec_paths/`
# and the bare imports fail silently → engines treated as None → vec sweep
# silently no-ops. Add the repo root to path so the engines always resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Import the same scalar engines used live — they are pure functions.
try:
    from entry_engine_wt import should_fire_wt_entry as _scalar_wt
except Exception:
    _scalar_wt = None
try:
    from entry_engine_stoch import should_fire_stoch_entry as _scalar_stoch
except Exception:
    _scalar_stoch = None
try:
    from entry_engine_dc import should_fire_dc_entry as _scalar_dc
except Exception:
    _scalar_dc = None
try:
    from entry_engine_htf import should_fire_htf_entry as _scalar_htf
except Exception:
    _scalar_htf = None
try:
    from entry_engine_stdev_macro import should_fire_stdev_macro_entry as _scalar_stdev_macro
except Exception:
    _scalar_stdev_macro = None


# Fields each scalar engine reads from the indicators dict. We assemble a
# per-bar dict slice from the NPZ arrays before calling.
_WT_FIELDS = (
    "wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m",
    "wt1_1h", "wt2_1h", "wt1_4h", "wt2_4h",
    "wt1_D", "wt2_D",
    "wt_velocity_3m", "wt_velocity_1h", "wt_velocity_4h",
)
_STOCH_FIELDS = (
    "stoch_k_3m", "stoch_d_3m", "k_3m_prev", "d_3m_prev",
    "stoch_k_15m", "stoch_d_15m",
    "stoch_k_1h", "stoch_d_1h",
    "stoch_k_4h", "stoch_d_4h",
    # tradier alt — 5m base TF (caller may map to _3m if needed)
    "stoch_k_5m", "stoch_d_5m",
)
_DC_FIELDS = (
    "current_price", "mark_price", "close", "close_3m", "close_5m",
    "dc_high_3m", "dc_high_15m", "dc_high_1h", "dc_high_4h",
    "dc_low_3m", "dc_low_15m", "dc_low_1h", "dc_low_4h",
    "dc_basis_15m",
    "ha_3m",
    "wt1_3m", "wt2_3m",
)
_HTF_FIELDS = (
    "price", "current_price", "close",
    "sma_200_D",
    "dc_basis_D", "dc_basis_D_ant",
    "ha_4h", "ha_D",
    "stoch_k_4h", "k_4h",
)
_STDEV_FIELDS = (
    "bb_pct_b_D", "bb_pct_b_4h", "bb_pct_b_1h",
    "wt1_3m", "wt2_3m", "wt1_D", "wt2_D",
)

# Master list of all NPZ keys we may need. Caller can use this to pre-check.
ALL_REFERENCED_NPZ_FIELDS = sorted(set(
    _WT_FIELDS + _STOCH_FIELDS + _DC_FIELDS + _HTF_FIELDS + _STDEV_FIELDS
))


def _slice_indicators(npz: Dict[str, np.ndarray], idx: int, mode: str = "crypto") -> Dict[str, Any]:
    """Materialise an indicators dict from the NPZ at bar `idx`.

    Tradier NPZs use 5m base TF instead of 3m. For DC/Stoch engines whose
    field names hardcode `_3m`, we map 5m → 3m field names when in tradier
    mode so the scalar engines can read them without modification.
    """
    out: Dict[str, Any] = {}
    is_tradier = (mode or "").lower() in ("tradier", "stocks", "stock")
    for k in ALL_REFERENCED_NPZ_FIELDS:
        if k in npz:
            arr = npz[k]
            if idx < len(arr):
                v = arr[idx]
                # Convert numpy scalar -> python float for the scalar engines.
                if isinstance(v, (np.floating, np.integer)):
                    out[k] = float(v)
                elif isinstance(v, (bytes, np.bytes_)):
                    out[k] = v.decode("utf-8", errors="ignore")
                else:
                    out[k] = v
    # Tradier remap: 5m → 3m where the 3m fields are absent.
    if is_tradier:
        for src, dst in (
            ("stoch_k_5m", "stoch_k_3m"),
            ("stoch_d_5m", "stoch_d_3m"),
            ("close_5m", "close_3m"),
            ("wt1_5m", "wt1_3m"),
            ("wt2_5m", "wt2_3m"),
        ):
            if dst not in out and src in npz and idx < len(npz[src]):
                out[dst] = float(npz[src][idx])
    # Inject current_price from the best available source for DC engine
    if "current_price" not in out:
        for cand in ("close", "close_3m", "close_5m", "mark_price"):
            if cand in out and out[cand] > 0:
                out["current_price"] = out[cand]
                break
    # Inject `ha_3m` / `ha_4h` / `ha_D` as strings if numeric. Live indicators dict
    # uses 'green'/'red'/'neutral'. NPZ stores numeric (1 / -1 / 0) or bytes.
    for tf in ("3m", "5m", "15m", "1h", "4h", "D"):
        k_ha = f"ha_{tf}"
        if k_ha in out:
            v = out[k_ha]
            if isinstance(v, (int, float)):
                if v > 0:
                    out[k_ha] = "green"
                elif v < 0:
                    out[k_ha] = "red"
                else:
                    out[k_ha] = "neutral"
    # Tradier remap of ha_5m -> ha_3m for DC engine if needed
    if is_tradier and "ha_3m" not in out and "ha_5m" in out:
        out["ha_3m"] = out["ha_5m"]
    return out


def compute_engine_scores_vec(
    npz: Dict[str, np.ndarray],
    idx: int,
    side: str,
    cfg,
    symbol: str = "VEC",
    mode: str = "crypto",
) -> Dict[str, Tuple[bool, str, float]]:
    """Compute each engine's (fire, reason, score) at bar `idx`.

    Returns a dict like:
        {"wt": (fire, reason, score), "stoch": ..., "dc": ..., "htf": ..., "stdev_macro": ...}

    Only engines whose `LIVE_ENTRY_ENGINE_<NAME>_ENABLED` flag is True are
    invoked. Missing scalar imports are silently skipped (engine treated as OFF).
    """
    ind = _slice_indicators(npz, idx, mode=mode)
    results: Dict[str, Tuple[bool, str, float]] = {}
    pairs = (
        ("wt",          _scalar_wt,          "LIVE_ENTRY_ENGINE_WT_ENABLED"),
        ("stoch",       _scalar_stoch,       "LIVE_ENTRY_ENGINE_STOCH_ENABLED"),
        ("dc",          _scalar_dc,          "LIVE_ENTRY_ENGINE_DC_ENABLED"),
        ("htf",         _scalar_htf,         "LIVE_ENTRY_ENGINE_HTF_ENABLED"),
        ("stdev_macro", _scalar_stdev_macro, "LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED"),
    )
    for name, fn, flag in pairs:
        if fn is None:
            continue
        if not bool(getattr(cfg, flag, False)):
            continue
        try:
            fire, reason, score = fn(symbol, ind, side)
            results[name] = (bool(fire), str(reason), float(score))
        except Exception as exc:
            # Mirror live: never let an engine error block; record diagnostic.
            results[name] = (False, f"EXC({type(exc).__name__}:{exc})", 0.0)
    return results


def aggregate_entry_score_vec(
    engine_scores: Dict[str, Tuple[bool, str, float]],
    cfg,
) -> Tuple[float, list]:
    """Aggregate engine outputs into (boost_amount, reasons).

    Mirrors the live pattern exactly:
        score_max = max(score for (fire, _, score) in engines if fire and score >= MIN_SCORE)
        boost = BOOST_SCORE * score_max if score_max > 0 else 0.0
    """
    min_score = float(getattr(cfg, "LIVE_ENTRY_ENGINE_MIN_SCORE", 0.5))  # 2026-05-30: default 0.6→0.5 to match live config.py:302 (latent seam: stripped-config sweeps were 0.1 stricter than live, silently dropping marginal entries)
    boost_per_unit = float(getattr(cfg, "LIVE_ENTRY_ENGINE_BOOST_SCORE", 8.0))
    score_max = 0.0
    reasons = []
    for name, (fire, _reason, score) in engine_scores.items():
        if fire and score >= min_score:
            if score > score_max:
                score_max = score
            reasons.append((name, score))
    boost = boost_per_unit * score_max if score_max > 0 else 0.0
    return boost, reasons


def live_entry_engine_passes_vec(
    npz: Dict[str, np.ndarray],
    idx: int,
    side: str,
    base_score: float,
    cfg,
    symbol: str = "VEC",
    mode: str = "crypto",
) -> Tuple[bool, float, list]:
    """End-to-end entry vote at bar `idx`.

    Returns (passes, final_score, reasons).
        passes      True iff (base_score + boost) >= threshold.
        final_score base_score + boost (for telemetry).
        reasons     list of (engine_name, score) tuples that fired.

    Threshold is `WT_DC_ENTRY_THRESHOLD` if `mode == "tradier"`, else
    `ENTRY_SCORE_THRESHOLD`. Live uses WT_DC_ENTRY_THRESHOLD for tradier
    (tradier_manage.py:2676) and ENTRY_SCORE_THRESHOLD for crypto.

    Master flag `LIVE_ENTRY_ENGINE_ENABLED=False` → pass-through (returns
    `(True, base_score, [])`). Caller's pre-existing entry logic is preserved
    by NOT calling this function or by setting the master flag False.
    """
    if not bool(getattr(cfg, "LIVE_ENTRY_ENGINE_ENABLED", False)):
        return True, float(base_score), []
    scores = compute_engine_scores_vec(npz, idx, side, cfg, symbol=symbol, mode=mode)
    boost, reasons = aggregate_entry_score_vec(scores, cfg)
    final = float(base_score) + boost
    is_tradier = (mode or "").lower() in ("tradier", "stocks", "stock")
    if is_tradier:
        thr = float(getattr(cfg, "WT_DC_ENTRY_THRESHOLD", 0.0))
    else:
        thr = float(getattr(cfg, "ENTRY_SCORE_THRESHOLD", 0.0))
    return (final >= thr), final, reasons


# ---------------------------------------------------------------------------
# Self-test runnable via `python3 vec_paths/live_entry_engine.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import types

    class _Cfg:
        LIVE_ENTRY_ENGINE_ENABLED = True
        LIVE_ENTRY_ENGINE_WT_ENABLED = True
        LIVE_ENTRY_ENGINE_STOCH_ENABLED = True
        LIVE_ENTRY_ENGINE_DC_ENABLED = True
        LIVE_ENTRY_ENGINE_HTF_ENABLED = True
        LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED = False
        LIVE_ENTRY_ENGINE_MIN_SCORE = 0.5
        LIVE_ENTRY_ENGINE_BOOST_SCORE = 8.0
        WT_DC_ENTRY_THRESHOLD = 45.0
        ENTRY_SCORE_THRESHOLD = 18.0

    cfg = _Cfg()
    # Build a 10-bar synthetic NPZ with all WT_FULL alignment LONG at bar 5.
    n = 10
    npz = {}
    for f in ("wt1_3m", "wt1_15m", "wt1_1h", "wt1_4h", "wt1_D"):
        npz[f] = np.zeros(n, dtype=np.float32); npz[f][5] = 50.0
    for f in ("wt2_3m", "wt2_15m", "wt2_1h", "wt2_4h", "wt2_D"):
        npz[f] = np.zeros(n, dtype=np.float32); npz[f][5] = 20.0
    npz["wt_velocity_3m"] = np.zeros(n, dtype=np.float32); npz["wt_velocity_3m"][5] = 1.0
    npz["close"] = np.full(n, 110.0, dtype=np.float32)
    npz["dc_high_3m"] = np.full(n, 100.0, dtype=np.float32)
    npz["dc_high_15m"] = np.full(n, 105.0, dtype=np.float32)
    npz["dc_high_1h"] = np.full(n, 108.0, dtype=np.float32)
    npz["dc_low_4h"] = np.full(n, 80.0, dtype=np.float32)
    npz["wt1_3m"][5] = 50.0  # for DC bounce/wt check
    npz["wt2_3m"][5] = 20.0
    npz["ha_3m"] = np.ones(n, dtype=np.int8)  # green
    npz["sma_200_D"] = np.full(n, 100.0, dtype=np.float32)
    npz["dc_basis_D"] = np.full(n, 102.0, dtype=np.float32)
    npz["dc_basis_D_ant"] = np.full(n, 101.0, dtype=np.float32)
    npz["ha_4h"] = np.ones(n, dtype=np.int8)
    npz["ha_D"] = np.ones(n, dtype=np.int8)
    npz["stoch_k_4h"] = np.full(n, 60.0, dtype=np.float32)

    scores = compute_engine_scores_vec(npz, 5, "LONG", cfg)
    print("Engine scores at bar 5 LONG:")
    for k, v in scores.items():
        print(f"  {k}: fire={v[0]} score={v[2]:.2f} reason={v[1][:80]}")
    boost, reasons = aggregate_entry_score_vec(scores, cfg)
    print(f"Aggregate boost: {boost:.2f}  reasons: {reasons}")
    passes, final, reasons = live_entry_engine_passes_vec(
        npz, 5, "LONG", base_score=40.0, cfg=cfg, mode="tradier"
    )
    print(f"Tradier (base=40, thr=45): passes={passes} final={final:.2f}")
    passes, final, reasons = live_entry_engine_passes_vec(
        npz, 5, "LONG", base_score=0.0, cfg=cfg, mode="tradier"
    )
    print(f"Tradier (base=0, thr=45):  passes={passes} final={final:.2f}")
    print("OK")
