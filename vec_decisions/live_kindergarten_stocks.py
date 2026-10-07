"""live_kindergarten_stocks (Agent C wave 3, batch b6) — numpy twin of the STOCKS live KINDERGARTEN / EMA_9_21 ENTRY GATE:
tradier_manage.should_enter_long 27441-27520 / should_enter_short 27803-27860 (`_cfg_auto('EMA_9_21_FILTER_ENABLED') or _cfg_auto('KINDERGARTEN_EMA_GATE_ENABLED')`).
It is a HARD VETO (`return False`) on the entry decision, NOT an additive entry signal (the vec used `_base_entry | _kg_signal` = the opposite semantic).
Semantics: checks = [9/21 per TF in EMA_9_21_FILTER_TFS (default EMA_9_21_TIMEFRAME '1h'): LONG ok iff ema_9_above_21_tf != 0 ; SHORT ok iff != 1]
  + (EMA_50_200_FILTER_ENABLED) ema_50_above_200 per EMA_50_200_TFS + (SMA_50_FILTER_ENABLED) price vs sma_50_{SMA_50_TIMEFRAME} + (SMA_200_FILTER_ENABLED) sma_200.
  No check available -> pass. KINDERGARTEN_CUMULATIVE_MODE (default True): every KINDERGARTEN_STRICT_TFS-listed TF check must pass and passing >= KINDERGARTEN_CUMULATIVE_MIN_TFS (live default 1).
  Legacy (CUMULATIVE_MODE False): single EMA_9_21_TIMEFRAME 9/21 check. EMA_9_21_FILTER_MIN_TFS has NO live read (vec-only name) — ignored here.
Crypto live KG is a different function (ez_manage._kindergarten_ema_gate) -> vec_decisions/live_kindergarten_gate.py."""
import numpy as np


def _tfs(s):
    return [t.strip() for t in str(s or "").split(",") if t.strip()]


def pass_mask(npz, n, is_long, cfg, close, safe):
    if not (bool(getattr(cfg, "EMA_9_21_FILTER_ENABLED", False)) or bool(getattr(cfg, "KINDERGARTEN_EMA_GATE_ENABLED", False))):
        return None
    cum = bool(getattr(cfg, "KINDERGARTEN_CUMULATIVE_MODE", True))
    checks = []   # (name, bool[n])
    tf1 = str(getattr(cfg, "EMA_9_21_TIMEFRAME", "1h") or "1h")
    if cum:
        for tf in _tfs(getattr(cfg, "EMA_9_21_FILTER_TFS", tf1)) or [tf1]:
            k = f"ema_9_above_21_{tf}"
            if k in npz:
                a = safe(npz, k, n, 0.0)
                checks.append(("EMA9_21_" + tf, (a != 0.0) if is_long else (a != 1.0)))
        if bool(getattr(cfg, "EMA_50_200_FILTER_ENABLED", False)):
            for tf in _tfs(getattr(cfg, "EMA_50_200_TFS", getattr(cfg, "EMA_50_200_TIMEFRAME", "D"))) or ["D"]:
                k = f"ema_50_above_200_{tf}"
                if k in npz:
                    a = safe(npz, k, n, 0.0)
                    checks.append(("EMA50_200_" + tf, (a != 0.0) if is_long else (a != 1.0)))
        px = np.asarray(close, dtype=float)
        for flag, tfk, nm, key in (("SMA_50_FILTER_ENABLED", "SMA_50_TIMEFRAME", "SMA50_", "sma_50_"), ("SMA_200_FILTER_ENABLED", "SMA_200_TIMEFRAME", "SMA200_", "sma_200_")):
            if bool(getattr(cfg, flag, False)):
                tf = str(getattr(cfg, tfk, "D") or "D")
                k = f"{key}{tf}"
                if k in npz:
                    sma = safe(npz, k, n, 0.0)
                    ok = (px > sma) if is_long else (px < sma)
                    checks.append((nm + tf, np.where((sma > 0) & (px > 0), ok, True)))   # live: check only added when price>0 (sma presence); invalid -> neutral pass
        if not checks:
            return None
        ok = np.ones(n, dtype=bool)
        strict = _tfs(getattr(cfg, "KINDERGARTEN_STRICT_TFS", ""))
        if strict:
            for nm, v in checks:
                if any(t in nm for t in strict):
                    ok &= v
        passing = np.zeros(n, dtype=np.int16)
        for _, v in checks:
            passing += v.astype(np.int16)
        from vec_decisions.kg_entry_gate import effective_min_tfs as _emt
        ok &= passing >= min(_emt(lambda k, d=None: getattr(cfg, k, d)), len(checks))
        return ok
    k = f"ema_9_above_21_{tf1}"
    if k not in npz:
        return None
    a = safe(npz, k, n, -1.0)
    return (a != 0.0) if is_long else (a != 1.0)
