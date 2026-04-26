"""
Per-sector long/short ratio enforcement for Tradier equities.

Wires into tradier_manage.py at the OPEN gate. Blocks position-opens that would
push the per-sector L/S ratio out of [SECTOR_LS_RATIO_MIN, SECTOR_LS_RATIO_MAX].

Sectors loaded from stocks_sectors.json (9 sectors: tech_big, tech_growth,
metals_miners, energy_oil, industrials_ag, financials_etfs, misc_industrial,
mix_12, all). Each symbol may belong to multiple sectors — the function checks
against the most-specific sector it belongs to (skips 'all' and 'mix_12'
catch-alls when a more specific match exists).

Config flags (add to config_tradier.py):
    SECTOR_LS_RATIO_ENABLED: bool = True
    SECTOR_LS_RATIO_MIN: float = 0.50    # min L:S per sector (i.e., shorts can be up to 2x longs)
    SECTOR_LS_RATIO_MAX: float = 2.00    # max L:S per sector (i.e., longs can be up to 2x shorts)
    SECTOR_LS_MIN_POSITIONS: int = 3     # need ≥N positions in a sector before ratio enforced
    SECTOR_LS_RATIO_BYPASS_HEDGE: bool = True  # is_hedge opens skip the check

Usage in tradier_manage.py at execute_trade open path:
    from tradier_sector_ls_ratio import check_sector_ls_ratio
    ok, reason = check_sector_ls_ratio(account_key, symbol, target_side, all_positions, config)
    if not ok:
        return False, reason, 0
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)

# Cache the sector map at module load
_SECTOR_MAP_CACHE: Optional[Dict[str, list]] = None
_SYM2SECTOR_CACHE: Optional[Dict[str, str]] = None
_SECTORS_FILE = Path(__file__).resolve().parent / "stocks_sectors.json"

# Sectors that are catch-alls — only used if no more specific match
_CATCHALL_SECTORS = {"all", "mix_12"}


def _load_sector_map() -> Dict[str, list]:
    global _SECTOR_MAP_CACHE
    if _SECTOR_MAP_CACHE is not None:
        return _SECTOR_MAP_CACHE
    try:
        with open(_SECTORS_FILE) as f:
            data = json.load(f)
        # Strip _comment etc.
        out = {k: v for k, v in data.items() if isinstance(v, list)}
        _SECTOR_MAP_CACHE = out
        return out
    except Exception as e:
        logger.warning(f"[SECTOR_LS] failed to load {_SECTORS_FILE}: {e}")
        _SECTOR_MAP_CACHE = {}
        return {}


def _build_sym2sector() -> Dict[str, str]:
    """Build symbol → primary sector index. Specific sectors win over catch-alls."""
    global _SYM2SECTOR_CACHE
    if _SYM2SECTOR_CACHE is not None:
        return _SYM2SECTOR_CACHE
    smap = _load_sector_map()
    out: Dict[str, str] = {}
    # First pass: specific sectors only (skip catchalls)
    for sec, syms in smap.items():
        if sec in _CATCHALL_SECTORS:
            continue
        for s in syms:
            if s not in out:  # first specific sector wins
                out[s] = sec
    # Second pass: assign catchall for symbols still unassigned
    for sec, syms in smap.items():
        if sec not in _CATCHALL_SECTORS:
            continue
        for s in syms:
            if s not in out:
                out[s] = sec
    _SYM2SECTOR_CACHE = out
    return out


def get_sector(symbol: str) -> Optional[str]:
    """Return primary sector for a symbol, or None if not mapped."""
    return _build_sym2sector().get(symbol)


def count_sector_long_short(account_key: str, sector: str, all_positions: Dict[str, Any]) -> Tuple[int, int]:
    """Count active long and short positions in a sector for this account.
    `all_positions` is expected as {position_key: position_obj_or_dict} where:
      - key format: 'account:SYMBOL_LONG' or 'account:SYMBOL_SHORT'
      - position has positionAmt attribute or 'positionAmt' key
    """
    sym2sec = _build_sym2sector()
    longs = 0; shorts = 0
    prefix = f"{account_key}:"
    for pk, pos in all_positions.items():
        if not pk.startswith(prefix):
            continue
        # extract amt
        amt = 0.0
        try:
            if hasattr(pos, 'positionAmt'):
                amt = float(getattr(pos, 'positionAmt', 0) or 0)
            elif isinstance(pos, dict):
                amt = float(pos.get('positionAmt', 0) or 0)
        except Exception:
            continue
        if abs(amt) < 1e-6:
            continue
        # extract symbol & side
        try:
            sym_side = pk.split(':', 1)[1]
            sym, side = sym_side.rsplit('_', 1)
        except Exception:
            continue
        if sym2sec.get(sym) != sector:
            continue
        if side == 'LONG':
            longs += 1
        elif side == 'SHORT':
            shorts += 1
    return longs, shorts


def check_sector_ls_ratio(
    account_key: str,
    symbol: str,
    target_side: str,        # 'LONG' or 'SHORT'
    all_positions: Dict[str, Any],
    config: Any,
    is_hedge: bool = False,
) -> Tuple[bool, str]:
    """Check whether opening (account, symbol, target_side) keeps per-sector L/S in bounds.

    Returns (ok, reason). When ok=False, the reason starts with 'SECTOR_LS_BLOCK_' for grep.
    """
    if not getattr(config, 'SECTOR_LS_RATIO_ENABLED', False):
        return True, 'sector_ls_disabled'
    if is_hedge and getattr(config, 'SECTOR_LS_RATIO_BYPASS_HEDGE', True):
        return True, 'sector_ls_hedge_bypass'
    sector = get_sector(symbol)
    if sector is None:
        return True, f'sector_ls_no_mapping_for_{symbol}'

    longs, shorts = count_sector_long_short(account_key, sector, all_positions)
    new_long = longs + (1 if target_side == 'LONG' else 0)
    new_short = shorts + (1 if target_side == 'SHORT' else 0)
    total_after = new_long + new_short

    min_pos = int(getattr(config, 'SECTOR_LS_MIN_POSITIONS', 3))
    if total_after < min_pos:
        return True, f'sector_{sector}_n={total_after}<{min_pos}_no_check'

    min_r = float(getattr(config, 'SECTOR_LS_RATIO_MIN', 0.50))
    max_r = float(getattr(config, 'SECTOR_LS_RATIO_MAX', 2.00))

    # Compute ratio after the proposed open
    if new_short == 0:
        # All long in sector — ratio would be ∞. Block longs unless we have <min_pos
        ratio_after = float('inf')
        if target_side == 'LONG':
            return False, f'SECTOR_LS_BLOCK_{sector}_all_long_n={new_long}'
        return True, f'sector_{sector}_first_short_ok'
    if new_long == 0:
        ratio_after = 0.0
        if target_side == 'SHORT':
            return False, f'SECTOR_LS_BLOCK_{sector}_all_short_n={new_short}'
        return True, f'sector_{sector}_first_long_ok'

    ratio_after = new_long / new_short

    if min_r <= ratio_after <= max_r:
        return True, f'sector_{sector}_ls={ratio_after:.2f}_in_range'

    # Out of bounds — block if proposed open WORSENS the ratio
    ratio_before = (longs / shorts) if shorts > 0 else float('inf') if longs > 0 else 1.0
    if (ratio_before == 0.0) or (longs == 0 and shorts == 0):
        # vacuum — accept the first
        return True, f'sector_{sector}_vacuum_ok'
    # Worsens?
    if target_side == 'LONG':
        # adding long increases ratio
        if ratio_after > max_r and ratio_after >= ratio_before:
            return False, f'SECTOR_LS_BLOCK_{sector}_long_push_ratio_to_{ratio_after:.2f}>max_{max_r}'
    else:
        # adding short decreases ratio
        if ratio_after < min_r and ratio_after <= ratio_before:
            return False, f'SECTOR_LS_BLOCK_{sector}_short_push_ratio_to_{ratio_after:.2f}<min_{min_r}'

    return True, f'sector_{sector}_ls={ratio_after:.2f}_corrective'


def report_sector_ls(account_key: str, all_positions: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Snapshot of L/S ratio per sector for this account. Useful for logging/dashboard."""
    sym2sec = _build_sym2sector()
    by_sec: Dict[str, Dict[str, int]] = {}
    prefix = f"{account_key}:"
    for pk, pos in all_positions.items():
        if not pk.startswith(prefix):
            continue
        amt = 0.0
        try:
            if hasattr(pos, 'positionAmt'):
                amt = float(getattr(pos, 'positionAmt', 0) or 0)
            elif isinstance(pos, dict):
                amt = float(pos.get('positionAmt', 0) or 0)
        except Exception:
            continue
        if abs(amt) < 1e-6: continue
        try:
            sym_side = pk.split(':', 1)[1]
            sym, side = sym_side.rsplit('_', 1)
        except Exception:
            continue
        sec = sym2sec.get(sym, 'unmapped')
        d = by_sec.setdefault(sec, {'longs': 0, 'shorts': 0})
        if side == 'LONG': d['longs'] += 1
        elif side == 'SHORT': d['shorts'] += 1
    out: Dict[str, Dict[str, float]] = {}
    for sec, d in by_sec.items():
        L, S = d['longs'], d['shorts']
        ratio = (L / S) if S > 0 else (float('inf') if L > 0 else 0.0)
        out[sec] = {'longs': L, 'shorts': S, 'total': L + S, 'ratio': ratio}
    return out


# Self-test when run directly
if __name__ == '__main__':
    import sys
    smap = _load_sector_map()
    print(f'Loaded {len(smap)} sectors:')
    for sec, syms in smap.items():
        print(f'  {sec}: {len(syms)} symbols (sample: {syms[:5]})')
    sym2sec = _build_sym2sector()
    print(f'\nSymbol→sector map: {len(sym2sec)} entries')
    for s in ['AAPL', 'NVDA', 'XOM', 'GLD', 'JPM', 'NEM', 'WDAY', 'PLTR']:
        print(f'  {s} → {get_sector(s)}')
    # Mock test
    class MockCfg:
        SECTOR_LS_RATIO_ENABLED = True
        SECTOR_LS_RATIO_MIN = 0.50
        SECTOR_LS_RATIO_MAX = 2.00
        SECTOR_LS_MIN_POSITIONS = 3
        SECTOR_LS_RATIO_BYPASS_HEDGE = True
    mock_pos = {
        'trb:AAPL_LONG':  {'positionAmt': 1},
        'trb:NVDA_LONG':  {'positionAmt': 1},
        'trb:MSFT_LONG':  {'positionAmt': 1},
        'trb:AMD_SHORT':  {'positionAmt': 1},
    }
    cfg = MockCfg()
    print('\n--- Mock test: 3 longs + 1 short in tech_big (ratio = 3.0, max = 2.0) ---')
    ok, reason = check_sector_ls_ratio('trb', 'AVGO', 'LONG', mock_pos, cfg)
    print(f'  Open AVGO LONG: ok={ok}, reason={reason}')
    ok, reason = check_sector_ls_ratio('trb', 'AVGO', 'SHORT', mock_pos, cfg)
    print(f'  Open AVGO SHORT: ok={ok}, reason={reason}')
    print('\n--- Snapshot ---')
    snap = report_sector_ls('trb', mock_pos)
    for sec, d in snap.items():
        print(f'  {sec}: L={d["longs"]} S={d["shorts"]} ratio={d["ratio"]:.2f}')
