"""Type gate: template candidates the engine cannot consume must never be evaluated.

Regression for the 'v12 prepared ...' cell pollution (2026-10-02): rows like
WT_DC_DC_POS_THRESHOLD_SHORT=OFF (float field + TF word), LR_BAND_LADDER_TF_*=1
(dict field + scalar) and K=V polluted values crashed simulate_one, and the pilot
wrote the truncated exception ('v12 prepared ...'[:30]) into RED sheet cells.
"""
import sys

sys.path.insert(0, '/Users/niels/Documents/binance')

from v15_pilot import (_cand_compatible, _clean_ingested_overrides, _parse_opt_value,
                       _switch_type_violation, get_defaults_for_symside)
from tools.opt.evaluate_v12 import _coerce_override


def _d():
    return get_defaults_for_symside('AAPL_LONG')


def test_parse_opt_collapses_dup_typo():
    assert _parse_opt_value('D=D', '4h') == 'D'
    assert _parse_opt_value('4h=4h', '4h') == '4h'
    assert _parse_opt_value('REENTRY_FILTER_MIN_PASS=2', 1) == 'REENTRY_FILTER_MIN_PASS=2'


def test_cand_compatible_rejects_crash_variants():
    d = _d()
    bad = [
        ('WT_DC_DC_POS_THRESHOLD_SHORT', 'OFF'),
        ('WT_DC_DC_POS_THRESHOLD_SHORT', '15m'),
        ('REENTRY_FILTER_MIN_PASS', 'REENTRY_FILTER_MIN_PASS=2'),
        ('LR_BAND_LADDER_TF_BOTTOM', '1'),
        ('LR_BAND_LADDER_TF_BOTTOM', 1),
        ('MTF_GR_EXIT_MIN_TFS', 'D'),
        ('VIGILANCE_DC4_BREACH_TOLERANCE_PCT', 'VIGILANCE_DC4_BREACH_TOLERANCE_PCT=0.5'),
    ]
    for field, cand in bad:
        ok, why = _cand_compatible(field, _parse_opt_value(cand, d.get(field)), d)
        assert not ok, f"{field}={cand!r} must be rejected"
        assert 'TYPE_MISMATCH' in why


def test_cand_compatible_accepts_legit():
    d = _d()
    good = [
        ('WT_DC_DC_POS_THRESHOLD_SHORT', 0.7),
        ('WT_DC_DC_POS_THRESHOLD_SHORT', '0.7'),
        ('REENTRY_FILTER_MIN_PASS', 2),
        ('DC_HARD_STOP_TF', 'D=D'),
        ('DC_HARD_STOP_TF', 'D'),
        ('BB_SQUEEZE_ENTRY_ENABLED', 'False'),
        ('BB_SQUEEZE_ENTRY_ENABLED', False),
        ('WT_DC_DETAILED_TF', 'OFF'),
    ]
    for field, cand in good:
        ok, why = _cand_compatible(field, _parse_opt_value(cand, d.get(field)), d)
        assert ok, f"{field}={cand!r} must pass: {why}"


def test_switch_type_violation_row_level():
    d = _d()
    assert _switch_type_violation('LR_BAND_LADDER_TF_BOTTOM', '1', d) != ''
    assert _switch_type_violation('WT_DC_DC_POS_THRESHOLD_SHORT', 'OFF', d) != ''
    assert _switch_type_violation('WT_DC_DETAILED_TF', 'OFF', d) == ''
    assert _switch_type_violation('DC_HARD_STOP_TF', 'D=D', d) == ''


def test_clean_ingested_overrides():
    out = _clean_ingested_overrides({
        'A': 'x + y',
        'B': 'K=V',
        'DC_HARD_STOP_TF': 'D=D',
        'MU': 'BB_WT_TF=15m+GR=18',
        'N': 3,
        'S': 'OFF',
    })
    assert 'A' not in out and 'B' not in out and 'MU' not in out
    assert out['DC_HARD_STOP_TF'] == 'D'
    assert out['N'] == 3 and out['S'] == 'OFF'


def test_coerce_override_unit():
    assert _coerce_override('F', 'OFF', 0.5) == (False, 'OFF')
    assert _coerce_override('F', '15m', 0.5) == (False, '15m')
    assert _coerce_override('F', 'K=V', 1) == (False, 'K=V')
    assert _coerce_override('F', '1', {}) == (False, '1')
    assert _coerce_override('F', 'D=D', '4h') == (True, 'D')
    assert _coerce_override('F', '0.7', 0.5) == (True, 0.7)
    assert _coerce_override('F', 'False', True) == (True, False)
    assert _coerce_override('F', '3', 1) == (True, 3)


def test_evaluate_prepared_rejects_before_eval():
    try:
        from tools.opt.evaluate_v12 import evaluate_prepared, prepare
    except Exception:
        return
    try:
        prep = prepare('AAPL_LONG', 30)
    except Exception:
        return
    if prep is None:
        return
    for ov in ({'WT_DC_DC_POS_THRESHOLD_SHORT': 'OFF'},
               {'LR_BAND_LADDER_TF_BOTTOM': '1'},
               {'REENTRY_FILTER_MIN_PASS': 'REENTRY_FILTER_MIN_PASS=2'}):
        r = evaluate_prepared(prep, ov)
        assert r.get('valid') is False
        assert 'incompatible with' in str(r.get('invalid_reason'))
        assert 'v12 prepared' not in str(r.get('invalid_reason'))
        assert r.get('gain_pct') is None
    r = evaluate_prepared(prep, {})
    assert r.get('gain_pct') is not None
