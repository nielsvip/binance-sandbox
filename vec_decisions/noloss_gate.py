"""NOLOSS_GATE — vector twin of live execute_now UNIVERSAL_NOLOSS_GATE (Agent C2, staged b5b).

LIVE SOURCE: ez_manage.py:32206-32420. When UNIVERSAL_NOLOSS_GATE (config.py:1487 default **False**, "OFF LIMITS per user") or the account is in
STRICT_NO_LOSS_ACCOUNTS, every REDUCE/CLOSE is BLOCKED while REAL gain (side-adjusted vs entry price) < COMMISSION_BUFFER_PCT (config 0.08, live code default 0.10),
unless: is_hedge / LIQUIDATION / FORCE_REDUCE / EMERGENCY_OVERSIZE in reason, or UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL (default True) and the reason contains ANY
substring of UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS (R1_DC_LOW4_3M_EMERGENCY, NEWBORN_LOSS_KILL, FROZEN_ACT_STOP_*, ALL_TF_AGAINST, HTF_AGAINST_FORCE_CLOSE, R2/R3, HEDGE_FAILED ...),
or STRUCTURAL_RANGE_SHIFT, or DC_RECOVERY_EXIT bypass (default off). WT_CROSS_EXIT / DC_BREAK etc. are explicitly NOT bypass reasons.
Vector hooks: the two generic technical close sites (exit_sig close `if closed:` and TECHNICAL_EXIT). Other special close sites already carry bypass-list reasons or
are gain-gated by construction; coverage is therefore PARTIAL and documented in MANIFEST.
"""
from __future__ import annotations


def noloss_blocks(cfg, reason, live_pnl_pct: float) -> bool:
    if str(getattr(cfg, 'MODE', 'crypto')) == 'tradier':
        # [PAR/002] live stocks tradier_manage.py:19670-19715 (strict accounts trb/trc): after the SRS / GAP_RISK paths EVERY later exit path returns NOLOSS_HOLD while
        # gain < NOLOSS_MIN_PROFIT_PCT_TRADIER (live config_tradier 0.01). Switch STOCKS_NOLOSS_HOLD_ENABLED (default OFF) with live threshold NOLOSS_MIN_PROFIT_PCT_TRADIER_LIVE=0.01.
        if not bool(getattr(cfg, 'STOCKS_NOLOSS_HOLD_ENABLED', False)):
            return False  # default OFF (behaviour-neutral): ON = live (TIM goes >80% on most stock sides, validity gate)
        _nl_min = float(getattr(cfg, 'NOLOSS_MIN_PROFIT_PCT_TRADIER_LIVE', 0.01) or 0.0)
        if _nl_min <= 0:
            return False
        r0 = str(reason or '').upper()
        if any(t in r0 for t in ('GAP_RISK', 'SRS', 'STRUCTURAL_RANGE_SHIFT', 'FROZEN_ACT_STOP', 'NEWBORN_LOSS_KILL', 'HTF_AGAINST_FORCE_CLOSE', 'ALL_TF_AGAINST')):
            return False
        return float(live_pnl_pct) < _nl_min
    if not bool(getattr(cfg, 'UNIVERSAL_NOLOSS_GATE', False)):
        return False
    r = str(reason or '').upper()
    if any(t in r for t in ('LIQUIDATION', 'FORCE_REDUCE', 'EMERGENCY_OVERSIZE', 'STRUCTURAL_RANGE_SHIFT')):
        return False
    if bool(getattr(cfg, 'UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL', True)):
        for b in (getattr(cfg, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', ()) or ()):
            if b and str(b).upper() in r:
                return False
    buf = float(getattr(cfg, 'COMMISSION_BUFFER_PCT', 0.10) or 0.0)
    return float(live_pnl_pct) < buf
