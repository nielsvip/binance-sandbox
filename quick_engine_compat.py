"""quick_engine_compat — modern-engine replacement for banned v8_quick_engine.

2026-05-18: User mandate. OPUS_VOMIT (formerly v8_quick_engine) is BANNED — its
inflated Sharpe numbers wiped 30% of net worth in a week. Two daemons still
imported it: flz_hourly_reconfig.py and tradier_hourly_reconfig.py. This shim
provides the same surface — simulate(stores, cfg, capital) + QuickConfig — but
routes through vec_engine_v1.VecEngine, which is sample-floor-honest and gated
by metrics_guard.

The daemons interact with the engine in two ways:
  (a) Construct QuickConfig(), setattr knobs, set cfg.MODE='tradier'|'crypto'.
  (b) Call simulate({sym: npz}, cfg, capital=10000.0). The legacy engine wrote
      per-trade JSONL to env $V8_TRADES_OUT_DIR/$V8_TRADES_RUN_ID__<sym>.jsonl.
      Daemons read those JSONLs (NOT the return value) to compute time-weighted
      pool_sharpe and pick winners.

vec_engine_v1.VecEngine has been augmented (2026-05-18, same commit as this
shim) to honor the V8_TRADES_OUT_DIR / V8_TRADES_RUN_ID env vars and emit the
same JSONL layout — see `_emit_trade()` and the trade-log flush at the end of
VecEngine.simulate().

NO TRADE FABRICATION: every JSONL record corresponds to a real position close
inside VecEngine. side, entry_ts, exit_ts, pnl_pct are all derived from the
position state at the moment of close. No synthetic timestamps, no inflated
returns.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure we can find vec_engine_v1 regardless of cwd.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from vec_engine_v1 import VecConfig, VecEngine

# ─────────────────────────────────────────────────────────────────────────
# Re-export VecConfig under the legacy QuickConfig name.
# Daemons use:
#     cfg = QuickConfig()
#     if mode == "tradier": cfg.MODE = "tradier"
#     for k, v in overrides.items(): setattr(cfg, k, v)
#
# VecConfig is a @dataclass; arbitrary setattr works the same as on the legacy
# class. Daemons that try to set unknown knobs will succeed (Python dataclasses
# allow it post-init) — those settings simply have no effect inside VecEngine
# unless the knob is wired. Task 4 ensures every cluster knob is at least
# settable on VecConfig without AttributeError.
# ─────────────────────────────────────────────────────────────────────────
QuickConfig = VecConfig

# Cache one VecEngine per (mode, npz_dir) — VecEngine caches _NPZStore instances,
# so reusing avoids re-loading NPZs per candidate. Daemon workers spawn fresh
# Python processes per work-item via ProcessPoolExecutor, so this cache is
# effectively per-worker.
_ENGINE_CACHE: Dict[str, VecEngine] = {}


def _get_engine(mode: str) -> VecEngine:
    key = mode
    eng = _ENGINE_CACHE.get(key)
    if eng is None:
        eng = VecEngine(mode=mode)
        _ENGINE_CACHE[key] = eng
    return eng


def simulate(stores: Any, cfg: VecConfig, capital: float = 10000.0):
    """Drop-in replacement for v8_quick_engine.simulate.

    Legacy contract:
      stores: dict {sym: numpy_npz} OR iterable of (sym, npz) tuples
      cfg: QuickConfig (now VecConfig) — has .MODE attribute ('crypto'/'tradier')
      capital: starting USD (informational only — used by legacy for $pnl rounding)
    Returns: dict (legacy shape — see OPUS_VOMIT._finalize_result). Daemons do
      NOT read the return; they read the per-trade JSONL written via env vars.

    Side effects (REQUIRED by daemons):
      When env V8_TRADES_OUT_DIR is set:
        Write {out_dir}/{V8_TRADES_RUN_ID}__{sym}.jsonl with one JSON object per
        line, fields: symbol, side, entry_ts, exit_ts, entry_price, exit_price,
        pnl_pct, exit_reason, stream. VecEngine.simulate() handles this internally
        when the env vars are set (see vec_engine_v1.py).
    """
    # Extract symbol list from `stores` (dict, iterable of tuples, or list).
    if isinstance(stores, dict):
        symbols = list(stores.keys())
    else:
        try:
            symbols = [k for (k, _v) in stores]
        except Exception:
            symbols = list(stores)

    if not symbols:
        return {
            "pool_sharpe": 0.0, "sym_sharpe": 0.0, "trades": 0,
            "max_dd_pct": 0.0, "n_syms": 0, "years": 0.0,
            "acc_gain_pct": 0.0, "wins": 0, "losses": 0,
            "verdict": "NO_SYMBOLS",
            "note": "quick_engine_compat received empty stores",
        }

    mode = str(getattr(cfg, "MODE", "crypto") or "crypto").lower()
    if mode not in ("crypto", "tradier"):
        mode = "crypto"

    engine = _get_engine(mode)

    # Optional window bounds — daemons currently let VecEngine choose the full
    # NPZ overlap; they filter trades post-hoc by exit_ts in the JSONL.
    start_ts = getattr(cfg, "WINDOW_START_TS", None)
    end_ts = getattr(cfg, "WINDOW_END_TS", None)

    t0 = time.time()
    try:
        result = engine.simulate(
            symbols=symbols,
            cfg=cfg,
            start_ts=int(start_ts) if start_ts else None,
            end_ts=int(end_ts) if end_ts else None,
            capital=capital,
        )
    except Exception as exc:
        # Match legacy behavior: simulate exceptions propagate. Daemons catch
        # them in _worker_run_candidate and return error dicts.
        raise

    # Attach compat fields the legacy result had (most daemons ignore them, but
    # any downstream caller that inspects the return won't AttributeError).
    if "pnl" not in result:
        acc = float(result.get("acc_gain_pct", 0.0) or 0.0)
        result["pnl"] = round(acc / 100.0 * capital, 2)
    result.setdefault("symbols_used", len(symbols))
    result.setdefault("early_abort", False)
    result.setdefault("elapsed_s", round(time.time() - t0, 3))
    return result


__all__ = ["simulate", "QuickConfig", "VecConfig", "VecEngine"]
