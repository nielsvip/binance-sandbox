"""PARITY_VEC_EXACT_MODE for stocks — the live decision IS the vec decision (USER 2026-10-06).

USER: "THE SECOND A VECTORIZED TRADE WOULD OCCUR A LIVE TRADE OCCURS" / "Live … has to run through execute_trade_action and
execute_now … producing THE EXACT SAME RESULTS from the TEMPLATE_ and v12_quick."

Design: no reimplementation.  This helper keeps a per-symbol buffer of every completed indicator row that tradier_manage
receives (live: the bridge dict; backtest_v12_engine: IndicatorStore.build_indicator_dict, the same format).  On each new
row it rebuilds the NPZ-shaped mapping, applies the SAME preprocessing the sheet engine applies
(lifecycle_pilot.compact_to_completed_timeframe + min_decision_tf_guard.guard_npz / clamp_config), builds the SAME
QuickConfig the sheet builds (evaluate_v12.build_cfg_npz config half, fed by the promoted per-sym set) and runs the real
v12_quick_engine.simulate_one.  The vec ledger events stamped on the newest row are the decisions for this bar; tradier_manage
sends them through queue_trade_action -> execute_trade_action -> execute_now with the vec reason string.

Causality: simulate_one on a prefix only sees rows <= now, so the decision at the last row equals the full-window decision
only if the vec walk is causal.  A lookahead inside the vec therefore shows up as a live != vec trip (honest gap evidence).
FINAL_MTM (end-of-window mark-to-market) is never a decision.

Master: config_tradier.PARITY_VEC_EXACT_MODE (default False -> nothing here is called; live byte-identical).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import numpy as np

EXECUTION_TF = "15m"
_HA_BACK = {"red": -1.0, "neutral": 0.0, "green": 1.0}
# NPZ integer encodings (backtest_v8_precompute; decoded to these labels by backtest_v8_harness._INT_DECODE and sent as labels by the
# live bridge). The sheet engine reads the integer codes, so the live labels are mapped back to them.
_INT_ENCODE = {
    "wt_cross_": {"BEAR": -1.0, "NONE": 0.0, "BULL": 1.0},
    "wt_signal_": {"BEAR": -1.0, "NEUTRAL": 0.0, "BULL": 1.0},
    "wt_divergence_": {"BEAR": -1.0, "": 0.0, "BULL": 1.0},
    "wt_momentum_state_": {"EXHAUST_DOWN": -2.0, "IMPULSE_DOWN": -1.0, "NEUTRAL": 0.0, "IMPULSE_UP": 1.0, "EXHAUST_UP": 2.0},
    "wt_peak_structure_": {"LH": -1.0, "NEUTRAL": 0.0, "HH": 1.0},
    "wt_trough_structure_": {"LL": -1.0, "NEUTRAL": 0.0, "HL": 1.0},
    "wt_structure_": {"LH": -1.0, "NEUTRAL": 0.0, "HH": 1.0},
    "wt_wave_phase_": {"CONTRACTING": -1.0, "NEUTRAL": 0.0, "EXPANDING": 1.0},
    "wt_composite_bias": {"SHORT": -1.0, "NEUTRAL": 0.0, "LONG": 1.0},
}


def _encode_label(k: str, v: str):
    for pre, m in _INT_ENCODE.items():
        if k.startswith(pre):
            return m.get(v, v)
    return v
_DROP_PREFIX = ("_", "0", "age_")
_DROP_KEYS = {"current_price", "mark_price", "prev_price", "ts", "timestamp"}
_NON_DECISION_REASONS = ("FINAL_MTM",)
_HARNESS_ENV_SHIELD = ("V8_LADDER_ONLY_SIDE", "V8_BACKTEST_BYPASS_DRAWDOWN", "V8_SWEEP_MODE")


def _epoch(v: Any) -> Optional[float]:
    if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
        f = float(v)
        return f / 1000.0 if f > 1e11 else f
    if isinstance(v, str) and v:
        try:
            return datetime.strptime(v.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            return None
    return None


def row_ts(ind: Dict[str, Any]) -> Optional[float]:
    """bar identity of one indicator row: numeric 'ts' (backtest + bridge) else the ISO 'timestamp'."""
    t = _epoch(ind.get("ts"))
    return t if t else _epoch(ind.get("timestamp"))


def normalize_row(ind: Dict[str, Any]) -> Dict[str, Any]:
    """bridge-format row -> NPZ-format scalars (timestamps back to epoch, ha_* labels back to -1/0/1, transport keys dropped)."""
    out: Dict[str, Any] = {}
    for k, v in ind.items():
        if not isinstance(k, str) or k in _DROP_KEYS or k.startswith(_DROP_PREFIX):
            continue
        if k.startswith("timestamp_"):
            e = _epoch(v)
            out[k] = e if e is not None else 0.0
            continue
        if k.startswith("ha_") and isinstance(v, str):
            out[k] = _HA_BACK.get(v, 0.0)
            continue
        if isinstance(v, bool):
            out[k] = float(v)
        elif isinstance(v, (int, float, np.integer, np.floating)):
            out[k] = float(v)
        elif isinstance(v, str):
            out[k] = _encode_label(k, v)
    return out


class SymbolBuffer:
    def __init__(self) -> None:
        self.ts: List[float] = []
        self.rows: List[Dict[str, Any]] = []

    def ingest(self, ind: Dict[str, Any]) -> bool:
        t = row_ts(ind)
        if not t or (self.ts and t <= self.ts[-1]):
            return False
        self.ts.append(float(t))
        self.rows.append(normalize_row(ind))
        return True

    def npz(self) -> Dict[str, Any]:
        keys = set()
        for r in self.rows:
            keys.update(r.keys())
        n = len(self.rows)
        out: Dict[str, Any] = {"timestamps": np.asarray(self.ts, dtype="float64")}
        for k in keys:
            col = [r.get(k) for r in self.rows]
            if all(isinstance(x, str) or x is None for x in col):
                out[k] = np.asarray(["" if x is None else x for x in col])
            else:
                out[k] = np.asarray([float(x) if isinstance(x, (int, float)) else math.nan for x in col], dtype="float64")
        if "close" not in out and n:
            out["close"] = np.asarray([float(r.get("close", r.get("price", 0.0)) or 0.0) for r in self.rows], dtype="float64")
        return out


def build_cfg(symbol: str, side: str, overrides: Dict[str, Any]):
    """the config half of tools.opt.evaluate_v12.build_cfg_npz (stocks, sim_account trb) + the lifecycle_pilot
    15m guard steps.  Same helpers, same order; tests/test_parity_vec_exact_stocks.py pins field equality."""
    import v12_quick_engine as V
    from tools.opt import evaluate_v12 as E
    from min_decision_tf_guard import clamp_config
    cfg = V.QuickConfig()
    cfg.MODE = "tradier"
    cfg.apply_tradier_defaults()
    cfg.START_POSITION_SIZE = 500.0
    cfg.MAX_ORDER_VALUE = 7000.0
    cfg.apply_cat_side_defaults(__import__("cat_side_defaults").cat_side_of(symbol, side, cfg.MODE))
    E._apply_sweep_cat_overrides(cfg)
    for _aflag in ("SIM_ACCOUNT", "SIM_TRADING_ACCOUNT", "ACCOUNT", "ACCOUNT_KEYS"):
        if hasattr(cfg, _aflag):
            try:
                setattr(cfg, _aflag, "trb")
            except Exception:
                pass
    if "SIM_ACCOUNT" in (overrides or {}):
        _acct2 = str(overrides["SIM_ACCOUNT"]).lower()
        if _acct2 in ("trb", "trc", "tra"):
            cfg.SIM_ACCOUNT = _acct2
            cfg.SIM_TRADING_ACCOUNT = _acct2
    for k, v in (overrides or {}).items():
        if hasattr(cfg, k):
            _okok, _vok = E._coerce_override(k, v, getattr(cfg, k))
            setattr(cfg, k, _vok if _okok else v)
    E._expand_dependencies(cfg, overrides)
    E._apply_venue_band_defaults(cfg, overrides, "tradier")
    receipt = clamp_config(cfg, EXECUTION_TF)
    cfg._MIN_DECISION_TF_RECEIPT = receipt
    cfg.BASE_TF = EXECUTION_TF
    cfg.PARITY_MIN_DECISION_TF = EXECUTION_TF
    return cfg


def compact_completed(npz: Dict[str, Any], timeframe: str = EXECUTION_TF) -> Optional[Dict[str, Any]]:
    """lifecycle_pilot.compact_to_completed_timeframe without its >=100-event study floor (a live buffer starts small):
    keep the first row at which a new positive parent timestamp appears.  Identical rows for n>=100 (pinned by test)."""
    key = f"timestamp_{timeframe}"
    parent = np.asarray(npz.get(key, ()), dtype="float64")
    base = np.asarray(npz.get("timestamps", ()), dtype="float64")
    if len(parent) != len(base) or len(parent) < 1:
        return None
    changed = np.r_[True, parent[1:] != parent[:-1]]
    idx = np.flatnonzero((parent > 0) & np.isfinite(parent) & changed)
    if len(idx) < 2:
        return None
    n = len(base)
    return {k: (v[idx] if isinstance(v, np.ndarray) and v.ndim and len(v) == n else v) for k, v in npz.items()}


def prepare_npz(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """lifecycle_pilot preprocessing on the live buffer (compact to completed 15m parents, then the 3m/5m guard)."""
    from min_decision_tf_guard import guard_npz
    npz = compact_completed(raw)
    if npz is None:
        return None
    npz, _receipt = guard_npz(npz, EXECUTION_TF)
    return npz


def _ov_hash(ov: Dict[str, Any]) -> str:
    return hashlib.md5(json.dumps(ov or {}, sort_keys=True, default=str).encode()).hexdigest()


class VecExactOracle:
    """one per process (tradier_manage module global).  decide() returns the vec events stamped on the newest bar."""

    def __init__(self) -> None:
        self.buffers: Dict[str, SymbolBuffer] = {}
        self._cfg_cache: Dict[str, tuple] = {}
        self._last_eval: Dict[str, tuple] = {}
        self.telemetry: Dict[str, int] = {"ingested": 0, "evals": 0, "events": 0, "compact_short": 0, "errors": 0}

    def ingest(self, symbol: str, ind: Dict[str, Any]) -> bool:
        if not isinstance(ind, dict) or not ind:
            return False
        b = self.buffers.setdefault(str(symbol).upper(), SymbolBuffer())
        ok = b.ingest(ind)
        if ok:
            self.telemetry["ingested"] += 1
        return ok

    def _cfg(self, symbol: str, side: str, overrides: Dict[str, Any]):
        key = f"{symbol.upper()}_{side}"
        h = _ov_hash(overrides)
        hit = self._cfg_cache.get(key)
        if hit is None or hit[0] != h:
            hit = (h, build_cfg(symbol.upper(), side, dict(overrides or {})))
            self._cfg_cache[key] = hit
        return hit[1]

    def decide(self, symbol: str, side: str, overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
        """vec execution events (OPEN/AUGMENT/REDUCE/CLOSE) whose ts == newest buffered bar (FINAL_MTM excluded).
        Memoised per (symbol, side, newest bar, set hash): a second process_position call on the same bar returns [] so an
        event is dispatched once."""
        import copy
        import v12_quick_engine as V
        sym = str(symbol).upper()
        b = self.buffers.get(sym)
        if b is None or len(b.ts) < 2:
            return []
        last = b.ts[-1]
        key = f"{sym}_{side}"
        h = _ov_hash(overrides)
        if self._last_eval.get(key) == (last, h):
            return []
        self._last_eval[key] = (last, h)
        npz = prepare_npz(b.npz())
        if npz is None:
            self.telemetry["compact_short"] += 1
            return []
        cfg = copy.deepcopy(self._cfg(sym, side, overrides))
        cfg._PARITY_PREFIX_EVAL = True  # v12 simulate_one: decide the window's first 99 bars like the full-window run
        # the sheet engine runs outside any backtest harness: hide harness-only env switches the vec reads (vec_decisions/entry_vet_stocks._v8_bypass
        # reads V8_LADDER_ONLY_SIDE / V8_BACKTEST_BYPASS_DRAWDOWN; backtest_v12_engine.run_one sets V8_LADDER_ONLY_SIDE during the live leg).
        import os as _os
        _shield = {k: _os.environ.pop(k) for k in _HARNESS_ENV_SHIELD if k in _os.environ}
        try:
            res = V.simulate_one(npz, sym, side == "LONG", cfg) or {}
        except Exception:
            self.telemetry["errors"] += 1
            raise
        finally:
            _os.environ.update(_shield)
        self.telemetry["evals"] += 1
        ex_last = float(np.asarray(npz["timestamps"], dtype="float64")[-1])
        out = []
        for e in res.get("ledger") or []:
            if not isinstance(e, dict):
                continue
            t = _epoch(e.get("ts"))
            if t is None or abs(t - ex_last) > 1e-6:
                continue
            if any(r in str(e.get("reason") or "") for r in _NON_DECISION_REASONS):
                continue
            typ = str(e.get("type") or "").upper()
            if typ not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE"):
                continue
            out.append({"type": typ, "qty": float(e.get("qty") or 0.0), "price": float(e.get("price") or e.get("exit_price") or 0.0),
                        "reason": str(e.get("reason") or e.get("exit_reason") or typ), "ts": t})
        self.telemetry["events"] += len(out)
        return out


ORACLE = VecExactOracle()


def live_action_for(event: Dict[str, Any], position_amt: float) -> Optional[tuple]:
    """vec event -> (queue_trade_action action, override_qty) given the live position.  None = nothing to send
    (vec OPEN while live already holds / vec exit while live is flat: the divergence stays visible in the trip report)."""
    typ = event["type"]
    held = abs(float(position_amt or 0.0)) > 0
    if typ == "OPEN":
        return None if held else ("OPEN", float(event["qty"]))
    if typ == "AUGMENT":
        return ("AUGMENT", float(event["qty"])) if held else None
    if typ == "REDUCE":
        return ("REDUCE", min(float(event["qty"]), abs(float(position_amt)))) if held else None
    if typ == "CLOSE":
        return ("CLOSE", abs(float(position_amt))) if held else None
    return None


def vec_reason(event: Dict[str, Any]) -> str:
    return f"VEC_EXACT_{event['type']}_{event['reason']}"[:160]


def _set_dir() -> str:
    import sys
    for _m in ("tradier_manage", "config_tradier"):
        mod = sys.modules.get(_m)
        cfg = getattr(mod, "config", None) or getattr(mod, "TradierConfig", None) if mod is not None else None
        if cfg is not None and hasattr(cfg, "PARITY_VEC_EXACT_SET_DIR"):
            return str(getattr(cfg, "PARITY_VEC_EXACT_SET_DIR", "") or "")
    return ""


def overrides_for(symbol: str, side: str, getter: Optional[Callable[[str], Any]] = None, set_dir: Optional[str] = None) -> Dict[str, Any]:
    """the set this sym_side runs: config_tradier.PARITY_VEC_EXACT_SET_DIR/<SYM_SIDE>.json (frozen sheet set) when configured,
    else per_sym_store.get_overrides (the promoted row; NOTE a store row can carry keys the sheet never measured)."""
    ss = f"{str(symbol).upper()}_{side}"
    d = _set_dir() if set_dir is None else set_dir
    if d:
        import pathlib
        p = pathlib.Path(d) / f"{ss}.json"
        if p.exists():
            body = json.loads(p.read_text())
            ov = body.get("cumulative_overrides") or body.get("best_overrides") or body.get("overrides") or body
            return {k: v for k, v in dict(ov).items() if k not in ("PARITY_VEC_EXACT_MODE", "PARITY_VEC_EXACT_SET_DIR")}
    if getter is None:
        import per_sym_store as _pss
        getter = _pss.get_overrides
    try:
        ov = getter(f"{str(symbol).upper()}_{side}") or {}
    except Exception:
        ov = {}
    return dict(ov) if isinstance(ov, dict) else {}


def record_dispatch(base_path: Any, position_key: str, event: Dict[str, Any], action: Optional[tuple], result: Any) -> None:
    """append-only audit of every exact-mode decision (data/reports/vec_exact/dispatch_<UTCdate>.jsonl): vec event, live action sent, queue result."""
    try:
        import pathlib
        d = pathlib.Path(str(base_path)) / "data" / "reports" / "vec_exact"
        d.mkdir(parents=True, exist_ok=True)
        import time as _time
        day = _time.strftime("%Y%m%d", _time.gmtime(float(event.get("ts") or 0.0)))
        row = {"pk": position_key, "bar_ts": event.get("ts"), "vec_type": event.get("type"), "vec_reason": str(event.get("reason"))[:120], "vec_qty": event.get("qty"),
               "sent": list(action) if action else None, "result": str(result)[:80]}
        with open(d / f"dispatch_{day}.jsonl", "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as e:
        ORACLE.telemetry["dispatch_log_error"] = ORACLE.telemetry.get("dispatch_log_error", 0) + 1
        ORACLE.telemetry["dispatch_log_last_error"] = f"{type(e).__name__}: {e}"[:160]
        if ORACLE.telemetry["dispatch_log_error"] == 1:
            import os as _os
            _os.write(2, f"VEC_EXACT_DISPATCH_LOG_ERROR {ORACLE.telemetry['dispatch_log_last_error']}\n".encode())
