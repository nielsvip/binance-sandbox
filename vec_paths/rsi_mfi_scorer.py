"""vec_paths/rsi_mfi_scorer.py — RSI/MFI scoring block.

LIVE SOURCE: ez_positions_quick.py:AdvancedSignalRater.rate() lines 3169-3197.

Pure function. Same module called by live + vec backtest = bit-identical scoring.
"""
from __future__ import annotations
from typing import List, Tuple


def score_rsi_mfi(
    is_long: bool,
    is_exit: bool,
    rsi_1h: float,
    rsi_4h: float,
    mfi_1h: float,
    mfi_4h: float,
    mfi_D: float,
) -> Tuple[float, List[str]]:
    """Mirrors ez_positions_quick.py rate() lines 3169-3197 — verbatim semantics."""
    score = 0.0
    reasons: List[str] = []
    if is_exit:
        return score, reasons
    if is_long:
        if rsi_1h < 50: score += 6; reasons.append(f"RSI1H_MR({rsi_1h:.0f}+6)")
        if rsi_4h < 50: score += 10; reasons.append(f"RSI4H_MR({rsi_4h:.0f}+10)")
        if rsi_1h > 70: score -= 6; reasons.append(f"RSI1H_OB({rsi_1h:.0f}-6)")
        if rsi_4h > 70: score -= 10; reasons.append(f"RSI4H_OB({rsi_4h:.0f}-10)")
        if rsi_1h < 50 and rsi_4h < 50: score += 8; reasons.append(f"RSI_BOTH_MR(+8)")
        # MFI scoring — 4h MFI<40 = 63-96% win rate, >90% avg return
        if mfi_1h < 50: score += 7; reasons.append(f"MFI1H_OS({mfi_1h:.0f}+7)")
        if mfi_4h < 40: score += 14; reasons.append(f"MFI4H_OS({mfi_4h:.0f}+14)")
        if mfi_4h < 50: score += 6; reasons.append(f"MFI4H_LOW({mfi_4h:.0f}+6)")
        if mfi_D  < 40: score += 8; reasons.append(f"MFID_OS({mfi_D:.0f}+8)")
        if mfi_1h < 40 and mfi_4h < 40: score += 10; reasons.append(f"MFI_BOTH_OS(+10)")
        if mfi_1h > 70: score -= 8; reasons.append(f"MFI1H_OB({mfi_1h:.0f}-8)")
        if mfi_4h > 70: score -= 14; reasons.append(f"MFI4H_OB({mfi_4h:.0f}-14)")
    else:
        if rsi_1h > 50: score += 6; reasons.append(f"RSI1H_EXH({rsi_1h:.0f}+6)")
        if rsi_4h > 50: score += 10; reasons.append(f"RSI4H_EXH({rsi_4h:.0f}+10)")
        if rsi_1h < 30: score -= 6; reasons.append(f"RSI1H_OS({rsi_1h:.0f}-6)")
        if rsi_4h < 30: score -= 10; reasons.append(f"RSI4H_OS({rsi_4h:.0f}-10)")
        if rsi_1h > 50 and rsi_4h > 50: score += 8; reasons.append(f"RSI_BOTH_EXH(+8)")
        # MFI scoring — D MFI>60 = 93-100% win rate for shorts
        if mfi_1h > 60: score += 7; reasons.append(f"MFI1H_EXH({mfi_1h:.0f}+7)")
        if mfi_4h > 60: score += 14; reasons.append(f"MFI4H_EXH({mfi_4h:.0f}+14)")
        if mfi_4h > 50: score += 6; reasons.append(f"MFI4H_HIGH({mfi_4h:.0f}+6)")
        if mfi_D  > 60: score += 10; reasons.append(f"MFID_EXH({mfi_D:.0f}+10)")
        if mfi_1h > 60 and mfi_4h > 60: score += 10; reasons.append(f"MFI_BOTH_EXH(+10)")
        if mfi_1h < 30: score -= 8; reasons.append(f"MFI1H_OS({mfi_1h:.0f}-8)")
        if mfi_4h < 30: score -= 14; reasons.append(f"MFI4H_OS({mfi_4h:.0f}-14)")
    return score, reasons
