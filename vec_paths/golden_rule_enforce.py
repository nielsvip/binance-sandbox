"""
vec_paths/golden_rule_enforce.py — GOLDEN_RULE enforcement loop twin (wide engines).

LIVE SOURCE: ez_manage.py:_golden_rule_loop() — breakout/retest two-phase entry:
  Phase 1 BREAKOUT: 1h DC/BB edge cross + WT aligned -> GOLDEN_RULE_MULT_BREAKOUT (0.1x).
  Phase 2 RETEST: back at dc_basis_1h + k turning + WT + 4h window -> GOLDEN_RULE_MULT_RETEST (5.0x).
  Then consensus (golden_rule_htf, activation [D,4h] + entry [1h,15m]) AND NOT HTF-vetoed.
Reason format: GOLDEN_RULE_{LONG|SHORT}_mult{mult} (live exact).

2026-10-06 USER full-parity: delegates to shared vec_decisions.golden_loop.scalar_fire.
Replaces the TF-tier cascade + UNION (cascade needed wt1_3m/wt1_5m = dead NPZ keys).
Native TF follows USE_1M_3M_SIGNALS_ENABLED (off -> 15m, == live NO-3M fallback).

RETURNS:
  dict {side, mult, target_usd, reason, gr_level, phase, action} or None if gate fails.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Dict


def _cfg(cfg: Any, name: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def check_golden_rule_enforce(
    store,
    bar_idx: int,
    sym: str,
    side: str,
    pos_state,
    cfg,
    mode: str = "crypto",
) -> Optional[Dict[str, Any]]:
    """Model the GOLDEN_RULE enforcement loop for one symbol/side at one bar.

    Args:
        store:     _NPZStore instance for this symbol.
        bar_idx:   Current bar index.
        sym:       Symbol name (for logging; not used in logic).
        side:      "LONG" or "SHORT".
        pos_state: _PositionState (for current qty approximation).
        cfg:       VecConfig with GOLDEN_RULE_* fields.
        mode:      "crypto" or "tradier".

    Returns:
        dict if the enforcement loop would fire OPEN/AUGMENT, None otherwise.
        dict keys:
          side        — "LONG" or "SHORT"
          mult        — float multiplier that fired
          target_usd  — float target notional in USD
          reason      — reason string matching live format
          gr_level    — which TF level fired ("15m", "1h", "4h", "D")
    """
    if not getattr(cfg, 'GOLDEN_RULE_ENABLED', True):
        return None

    # ── 2026-10-06 USER full-parity: delegate to shared vec_decisions.golden_loop ──
    # (live breakout/retest phases + consensus + veto + phase mults). Replaces the TF-tier
    # cascade + UNION (cascade required wt1_3m/wt1_5m = dead keys; live loop is AND).
    # State (prev_above/breakout_ts/prev_k) is derived causally from store at bar_idx/bar_idx-1
    # plus a ≤16-bar lookback for the last cross (== live 4h retest window on 15m bars).
    import vec_decisions.golden_loop as _gloop
    is_long = (side == "LONG")
    _need = ("close", "dc_high_1h", "dc_low_1h", "dc_basis_1h", "bb_upper_1h", "bb_lower_1h",
             "wt1_15m", "wt2_15m", "k_15m", "d_15m", "stoch_k_15m", "stoch_d_15m",
             "wt1_D", "wt2_D", "ha_color_D", "wt1_4h", "wt2_4h", "ha_color_4h")
    for _tf in ("15m", "1h", "4h", "D", "W"):
        _need += (f"rsi_{_tf}", f"mfi_{_tf}", f"dc_position_{_tf}", f"bb_pct_b_{_tf}",
                  f"relative_volume_{_tf}", f"stoch_k_{_tf}", f"stoch_d_{_tf}", f"adx_{_tf}",
                  f"macd_hist_{_tf}", f"ha_color_{_tf}", f"dc_high_{_tf}", f"dc_low_{_tf}",
                  f"bb_upper_{_tf}", f"bb_lower_{_tf}", f"close_{_tf}")
    row = {}
    for _k in _need:
        try:
            row[_k] = store.f(_k, bar_idx, 0.0)
        except Exception:
            row[_k] = 0.0
    try:
        _ts_now = float(store.f("timestamps", bar_idx, 0.0))
    except Exception:
        _ts_now = 0.0
    if _ts_now <= 0:
        try:
            _tss = store.ts(bar_idx)
            _ts_now = float(_tss) if _tss else 0.0
        except Exception:
            _ts_now = 0.0
    ntf = _gloop.native_tf(mode, cfg, row)
    if ntf in ("3m", "5m"):
        for _f in ("wt1", "wt2"):
            try:
                row[f"{_f}_{ntf}"] = store.f(f"{_f}_{ntf}", bar_idx, 0.0)
            except Exception:
                pass
    _px = float(row.get("close", 0.0) or 0.0)
    row["current_price"] = _px
    _dh = float(row.get("dc_high_1h", 0.0) or 0.0)
    _dl = float(row.get("dc_low_1h", 0.0) or 0.0)
    _bu = float(row.get("bb_upper_1h", 0.0) or 0.0)
    _bl = float(row.get("bb_lower_1h", 0.0) or 0.0)
    if is_long:
        _above_now = bool((_dh and _px > _dh) or (_bu and _px > _bu))
    else:
        _above_now = bool((_dl and _px < _dl) or (_bl and _px < _bl))
    _pi = max(0, int(bar_idx) - 1)
    try:
        _px_p = float(store.f("close", _pi, 0.0))
        _dh_p = float(store.f("dc_high_1h", _pi, 0.0)); _dl_p = float(store.f("dc_low_1h", _pi, 0.0))
        _bu_p = float(store.f("bb_upper_1h", _pi, 0.0)); _bl_p = float(store.f("bb_lower_1h", _pi, 0.0))
    except Exception:
        _px_p, _dh_p, _dl_p, _bu_p, _bl_p = 0.0, 0.0, 0.0, 0.0, 0.0
    if int(bar_idx) <= 0:
        _prev_above = False
    elif is_long:
        _prev_above = bool((_dh_p and _px_p > _dh_p) or (_bu_p and _px_p > _bu_p))
    else:
        _prev_above = bool((_dl_p and _px_p < _dl_p) or (_bl_p and _px_p < _bl_p))
    _kk = "k_15m" if ntf == "15m" else ("k_3m" if ntf == "3m" else "k_5m")
    _skk = "stoch_k_15m" if ntf == "15m" else ("stoch_k_3m" if ntf == "3m" else "stoch_k_5m")
    try:
        row[_kk + "_prev"] = float(store.f(_kk, _pi, store.f(_skk, _pi, 50.0)))
    except Exception:
        pass
    _win_s = float(_cfg(cfg, "GOLDEN_RULE_RETEST_WINDOW_S", 14400.0))
    _bk_ts = 0.0
    if _above_now and not _prev_above:
        _bk_ts = _ts_now
    else:
        _look = max(1, min(int(bar_idx), int(_win_s // 900) + 1))
        _a_prev = _prev_above
        for _j in range(int(bar_idx) - 1, int(bar_idx) - _look - 1, -1):
            if _j < 0:
                break
            try:
                _pxj = float(store.f("close", _j, 0.0))
                _dhj = float(store.f("dc_high_1h", _j, 0.0)); _dlj = float(store.f("dc_low_1h", _j, 0.0))
                _buj = float(store.f("bb_upper_1h", _j, 0.0)); _blj = float(store.f("bb_lower_1h", _j, 0.0))
            except Exception:
                break
            if is_long:
                _a_j = bool((_dhj and _pxj > _dhj) or (_buj and _pxj > _buj))
            else:
                _a_j = bool((_dlj and _pxj < _dlj) or (_blj and _pxj < _blj))
            if _a_prev and not _a_j:
                try:
                    _bk_ts = float(store.f("timestamps", _j + 1, 0.0)) or ((_ts_now - 900.0) if _ts_now > 0 else 0.0)
                except Exception:
                    _bk_ts = 0.0
                break
            _a_prev = _a_j
    _fire = _gloop.scalar_fire(row, _prev_above, _bk_ts, _ts_now, is_long, mode, cfg)
    if not _fire["fire"]:
        return None
    mult = float(_fire["mult"])
    base_usd = float(_cfg(cfg, "GOLDEN_RULE_BASE_USD", 5.0))
    price = _px
    if price <= 0:
        return None
    target_usd = base_usd * mult
    cur_qty = pos_state.qty if (pos_state.open) else 0.0
    cur_notional = cur_qty * price
    if cur_notional >= target_usd * 0.8:
        return None
    return {
        "side": side,
        "mult": mult,
        "target_usd": target_usd,
        "reason": _fire["reason"],
        "gr_level": "BREAKOUT" if _fire["phase"] == 1 else "RETEST",
        "phase": int(_fire["phase"]),
        "action": "OPEN" if not pos_state.open else "AUGMENT",
    }


