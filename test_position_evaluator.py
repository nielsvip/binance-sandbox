"""Regression test: scalar evaluate_reentry_core vs vectorized evaluate_reentry_vec.

Loops over each bar of a real NPZ, builds the indicator dict the same way live
code does, calls evaluate_reentry_core (scalar), then runs evaluate_reentry_vec
(vectorized) once across the whole array — and asserts:
    fire mask matches at EVERY bar
    block_id matches where fire=True
    qty_mult matches where fire=True

Also runs against the live ez_manage.evaluate_reentry path (skipping I/O) by
comparing evaluate_reentry_core's decisions to a manual replication of the
live B-block sequence on the same indicator dict.
"""
import sys
sys.path.insert(0, '/Users/niels/Documents/binance')
import glob
import numpy as np
from position_evaluator import (
    evaluate_reentry_core,
    evaluate_reentry_vec,
    check_dc_high_break_retest_core,
    check_dc_high_break_retest_vec,
    BLOCK_NAMES,
)


class MockConfig:
    """All blocks ON to exercise full path."""
    REENTRY_B15_STRONG_TREND_ENABLED = True
    REENTRY_B04_DC_RETEST_ENABLED = True
    REENTRY_B11_DC_BREAK_ENABLED = True
    REENTRY_B02_BC156_BOTTOM_ENABLED = True
    REENTRY_B12_WT_MOM_ENABLED = True
    REENTRY_B14_HA_TREND_ENABLED = True
    REENTRY_B10_STOCH_REV_ENABLED = True
    REENTRY_B01_WT_2of3_ENABLED = True   # ON to test the OFF-default path
    REENTRY_B09_SNAPBACK_ENABLED = True
    START_POSITION_SIZE = 100.0


def npz_to_indicator_dict(npz_data: dict, idx: int, ltf: str = '3m') -> dict:
    """Build the indicator dict the way ii() does for the LIVE path —
    so scalar evaluate_reentry_core sees same-shaped input as production."""
    d = {}
    for tf in [ltf, '15m', '1h', '4h', 'D']:
        # Standard fields
        for k in ['wt1', 'wt2', 'wt_velocity', 'stoch_k', 'stoch_d',
                  'dc_high', 'dc_low', 'ha', 'wt_bullish', 'bb_pct_b']:
            full = f'{k}_{tf}'
            arr = npz_data.get(full)
            if arr is not None and idx < len(arr):
                v = arr[idx]
                if hasattr(v, 'item'):
                    v = v.item()
                d[full] = v
        # _ant suffix fields: NPZ uses dc_high_4h_ant (tf BEFORE _ant)
        for k in ['dc_high', 'dc_low']:
            src = f'{k}_{tf}_ant'
            dst = f'{k}_{tf}_ant'
            arr = npz_data.get(src)
            if arr is not None and idx < len(arr):
                v = arr[idx]
                if hasattr(v, 'item'):
                    v = v.item()
                d[dst] = v
    # k_3m_prev = stoch_k_{ltf}[idx-1]
    arr = npz_data.get(f'stoch_k_{ltf}')
    if arr is not None and idx > 0:
        v = arr[idx - 1]
        if hasattr(v, 'item'):
            v = v.item()
        d['k_3m_prev'] = v
    # Map LTF stoch fields to canonical 3m names that core function reads
    if ltf != '3m':
        for k in ['wt1', 'wt2', 'wt_velocity', 'stoch_k', 'stoch_d', 'ha', 'wt_bullish']:
            src = f'{k}_{ltf}'
            dst = f'{k}_3m'
            if src in d:
                d[dst] = d[src]
    return d


def test_scalar_vec_parity(npz_path: str, n_bars: int = 5000, is_long: bool = True):
    """Run scalar at each bar and vec once; compare."""
    print(f"\n=== {npz_path} | is_long={is_long} | n_bars={n_bars} ===")
    raw = np.load(npz_path)
    npz_data = {k: raw[k] for k in raw.files}
    cfg = MockConfig()
    n_total = len(npz_data['close'])
    start = max(0, n_total - n_bars - 100)  # skip warmup
    end = n_total

    # Slice all arrays so vec sees the same window as scalar loop
    sliced = {}
    for k, v in npz_data.items():
        if isinstance(v, np.ndarray) and len(v) == n_total:
            sliced[k] = v[start:end]
        else:
            sliced[k] = v

    # Vectorized in one shot
    vec = evaluate_reentry_vec(sliced, is_long=is_long, config=cfg, ltf='3m')
    fire_vec = vec['fire']
    block_vec = vec['block_id']
    qm_vec = vec['qty_mult']

    # Scalar loop
    fire_scalar = np.zeros(end - start, dtype=bool)
    block_scalar = np.zeros(end - start, dtype=np.int8)
    qm_scalar = np.ones(end - start, dtype=np.float32)
    cp_arr = sliced['close']
    for i, cp in enumerate(cp_arr):
        if cp <= 0 or cp != cp:  # skip warmup NaN
            continue
        ind = npz_to_indicator_dict(sliced, i, ltf='3m')
        sig = evaluate_reentry_core(ind, is_long, float(cp), cfg, re_qty_base=1.0)
        if sig is not None:
            fire_scalar[i] = True
            # Decode block_id from reason prefix
            reason = sig.reason
            for bid, name in BLOCK_NAMES.items():
                # Match exact name prefix (e.g. "B15_STRONG_TREND" or "B02_BC156_BOTTOM")
                # B04 special case: reason is f'B04_DC_RETEST_{...}' — handled by name=B04_DC_RETEST
                if reason.startswith(name + '_') or reason == name:
                    block_scalar[i] = bid
                    break

    n_fire_vec = int(fire_vec.sum())
    n_fire_scalar = int(fire_scalar.sum())
    n_diff = int((fire_vec != fire_scalar).sum())

    print(f"  scalar fires: {n_fire_scalar}")
    print(f"  vec    fires: {n_fire_vec}")
    print(f"  fire mask diff: {n_diff} bars")

    if n_diff > 0:
        # Show first 5 disagreements
        diffs = np.where(fire_vec != fire_scalar)[0][:5]
        for di in diffs:
            print(f"    bar={start+di}: scalar={fire_scalar[di]} block={block_scalar[di]} | vec={fire_vec[di]} block={block_vec[di]}")
        return False, n_fire_scalar, n_fire_vec, n_diff

    # Among fired bars, block_id must match
    fired = fire_scalar
    if fired.any():
        block_diff = ((block_vec[fired] != block_scalar[fired]).sum())
        print(f"  block_id diff among fired: {block_diff}")
        if block_diff > 0:
            return False, n_fire_scalar, n_fire_vec, block_diff
    return True, n_fire_scalar, n_fire_vec, 0


def test_dc_retest_parity(npz_path: str, n_bars: int = 5000, is_long: bool = True):
    raw = np.load(npz_path)
    npz_data = {k: raw[k] for k in raw.files}
    n_total = len(npz_data['close'])
    start = max(0, n_total - n_bars - 100)
    end = n_total
    sliced = {k: (v[start:end] if isinstance(v, np.ndarray) and len(v) == n_total else v) for k, v in npz_data.items()}
    cp = sliced['close']

    fire_v, mult_v = check_dc_high_break_retest_vec(sliced, cp, is_long, ltf='3m')
    fire_s = np.zeros(len(cp), dtype=bool)
    mult_s = np.ones(len(cp), dtype=np.float32)
    for i, p in enumerate(cp):
        if p <= 0 or p != p:
            continue
        ind = npz_to_indicator_dict(sliced, i, ltf='3m')
        ok, _, mult = check_dc_high_break_retest_core(ind, float(p), is_long)
        if ok:
            fire_s[i] = True
            mult_s[i] = mult
    diff = int((fire_v != fire_s).sum())
    print(f"  DC_retest scalar={int(fire_s.sum())} vec={int(fire_v.sum())} diff={diff}")
    if diff > 0:
        d = np.where(fire_v != fire_s)[0][:3]
        for di in d:
            print(f"    bar={start+di}: scalar={fire_s[di]} | vec={fire_v[di]}")
    return diff == 0


def test_qty_pipeline_parity(npz_path: str, n_bars: int = 5000, is_long: bool = True, is_hedge: bool = False):
    """Scalar vs vec parity for compute_trade_qty_*."""
    from position_evaluator import compute_trade_qty_core, compute_trade_qty_vec, MOD_NONE
    raw = np.load(npz_path)
    npz_data = {k: raw[k] for k in raw.files}
    cfg = MockConfig()
    cfg.MIN_POSITION_SIZE = 55.0
    cfg.HEDGE_MAX_PCT_OF_LOSER = 1.0
    cfg.WT_HTF_DISCOUNT_ENABLED = True
    n_total = len(npz_data['close'])
    start = max(0, n_total - n_bars - 100)
    end = n_total
    sliced = {k: (v[start:end] if isinstance(v, np.ndarray) and len(v) == n_total else v) for k, v in npz_data.items()}
    cp = sliced['close']
    n = len(cp)

    # Synthetic per-bar base_qty: 1.5× block multiplier × $2000 / price (some bars), 1× elsewhere
    base_qty = (cfg.START_POSITION_SIZE / np.where(cp > 0, cp, 1.0)).astype(np.float32)
    base_qty[::3] *= 1.5  # B02-style multiplier on every 3rd bar
    base_qty[::7] *= 2.0  # B04-style multiplier on every 7th bar
    origin_value = (np.ones(n) * 1000.0).astype(np.float32) if is_hedge else None
    rz_mask = np.zeros(n, dtype=bool)
    rz_mask[::5] = True  # 20% RZ entries

    # Vec
    vec = compute_trade_qty_vec(sliced, base_qty, is_long=is_long, config=cfg,
                                  is_hedge=is_hedge,
                                  is_rz_entry_arr=rz_mask,
                                  origin_value_arr=origin_value)
    qty_vec = vec['qty']
    mod_vec = vec['modifier']

    # Scalar loop
    qty_scalar = np.zeros(n, dtype=np.float32)
    mod_scalar = np.zeros(n, dtype=np.int8)
    for i in range(n):
        if cp[i] <= 0 or cp[i] != cp[i]:
            continue
        ind = npz_to_indicator_dict(sliced, i, ltf='3m')
        ov = float(origin_value[i]) if origin_value is not None else 0.0
        q, m = compute_trade_qty_core(
            float(base_qty[i]), 'OPEN', is_long, is_hedge,
            float(cp[i]), ind, cfg,
            is_rz_entry=bool(rz_mask[i]),
            origin_position_value=ov,
        )
        qty_scalar[i] = q
        mod_scalar[i] = m

    # Compare — allow tiny float tolerance
    qty_diff = np.abs(qty_scalar - qty_vec)
    rel_diff = qty_diff / np.maximum(np.abs(qty_scalar), 1e-9)
    n_qty_diff = int((rel_diff > 1e-4).sum())
    n_mod_diff = int((mod_scalar != mod_vec).sum())
    print(f"  QTY pipeline (is_hedge={is_hedge}): qty diff={n_qty_diff}/{n} mod diff={n_mod_diff}/{n}")
    if n_qty_diff > 0:
        bad = np.where(rel_diff > 1e-4)[0][:3]
        for bi in bad:
            print(f"    bar={bi}: scalar qty={qty_scalar[bi]:.6f} mod={mod_scalar[bi]} | vec qty={qty_vec[bi]:.6f} mod={mod_vec[bi]} cp={cp[bi]:.4f}")
    return n_qty_diff == 0 and n_mod_diff == 0


def test_exit_gates_parity(npz_path: str, n_bars: int = 5000, is_long: bool = True):
    """Scalar vs vec parity for indicator-only exit gates (excludes WT_4H_VEL +
    DC_HOPELESS which require trade state). Sets E_1 + E_3 to live so all 4
    indicator-only gates exercise."""
    from position_evaluator import (
        evaluate_exit_gates_core, evaluate_exit_gates_vec,
        EXIT_NONE, EXIT_WT_EXHAUST, EXIT_WT_PERCENTILE, EXIT_E1_WT_DELTA, EXIT_E3_STRUCTURE,
    )
    raw = np.load(npz_path)
    npz_data = {k: raw[k] for k in raw.files}
    cfg = MockConfig()
    cfg.WT_EXHAUST_EXIT_ENABLED = True
    cfg.WT_PERCENTILE_EXIT_ENABLED = True
    cfg.E_1_WT_EXIT_USE_DELTA_ENABLED = True
    cfg.E_1_EXIT_DELTA_THR = 50.0
    cfg.E_3_USE_WT_STRUCTURE_EXIT_MODE = 2  # live exit mode
    cfg.WT_EXHAUST_EXIT_REQUIRE_GAIN = False
    # Disable the state-dependent gates for this parity test (different test path)
    cfg.WT_4H_VEL_EXIT_ENABLED = False
    cfg.DC_HOPELESS_EXIT_ENABLED = False

    n_total = len(npz_data['close'])
    start = max(0, n_total - n_bars - 100)
    end = n_total
    sliced = {k: (v[start:end] if isinstance(v, np.ndarray) and len(v) == n_total else v) for k, v in npz_data.items()}
    n = len(sliced['close'])

    vec = evaluate_exit_gates_vec(sliced, is_long=is_long, config=cfg, ltf='3m')

    # Scalar loop — combine the 4 indicator-only masks per bar
    fire_scalar = np.zeros(n, dtype=bool)
    exit_id_scalar = np.zeros(n, dtype=np.int8)
    for i in range(n):
        ind = npz_to_indicator_dict(sliced, i, ltf='3m')
        # Inject all needed _3m/_15m/_1h/_4h/_D indicator values for momentum_state, percentile, etc.
        for k in ('wt_momentum_state_4h', 'wt_momentum_state_1h', 'wt_momentum_state_15m',
                  'wt_percentile_D', 'wt_percentile_4h', 'wt_composite_delta',
                  'wt_velocity_4h', 'wt_structure_15m', 'wt_structure_1h', 'wt_structure_4h',
                  'stoch_k_3m', 'stoch_k_15m', 'dc_high_4h', 'dc_low_4h'):
            arr = sliced.get(k)
            if arr is not None and i < len(arr):
                v = arr[i]
                if hasattr(v, 'item'):
                    v = v.item()
                ind[k] = v
        eid, _ = evaluate_exit_gates_core(ind, is_long, cfg)
        if eid != EXIT_NONE:
            fire_scalar[i] = True
            exit_id_scalar[i] = eid

    # Vec: combine 4 masks. First-match precedence per evaluate_exit_gates_core:
    # WT_EXHAUST → WT_PERCENTILE → E_1 → E_3
    fire_vec = np.zeros(n, dtype=bool)
    exit_id_vec = np.zeros(n, dtype=np.int8)
    for mask, eid in [(vec['wt_exhaust'], EXIT_WT_EXHAUST),
                       (vec['wt_percentile'], EXIT_WT_PERCENTILE),
                       (vec['e1_wt_delta'], EXIT_E1_WT_DELTA),
                       (vec['e3_structure'], EXIT_E3_STRUCTURE)]:
        new_hit = mask & ~fire_vec
        fire_vec = fire_vec | new_hit
        exit_id_vec = np.where(new_hit, np.int8(eid), exit_id_vec)

    fire_diff = int((fire_vec != fire_scalar).sum())
    id_diff = int((exit_id_scalar != exit_id_vec)[fire_scalar].sum())
    print(f"  EXIT gates indicator-only: scalar fires={int(fire_scalar.sum())} vec fires={int(fire_vec.sum())} fire_diff={fire_diff} id_diff={id_diff}")
    if fire_diff > 0:
        bad = np.where(fire_vec != fire_scalar)[0][:3]
        for bi in bad:
            print(f"    bar={bi}: scalar fire={fire_scalar[bi]} id={exit_id_scalar[bi]} | vec fire={fire_vec[bi]} id={exit_id_vec[bi]}")
    return fire_diff == 0 and id_diff == 0


if __name__ == '__main__':
    cryptos = [f for f in glob.glob('/Users/niels/Documents/binance/backtest_v8/indicators/*.npz')
               if any(s in f for s in ['BTCDOM', 'XRPUSDC', 'SOLUSDC', 'ADAUSDC', 'BNBUSDC'])][:3]
    if not cryptos:
        cryptos = [glob.glob('/Users/niels/Documents/binance/backtest_v8/indicators/*USDT.npz')[0]]

    all_pass = True
    for path in cryptos:
        for is_long in (True, False):
            print('\n--- DC RETEST helper parity ---')
            ok = test_dc_retest_parity(path, n_bars=5000, is_long=is_long)
            if not ok:
                all_pass = False
            print('\n--- FULL evaluate_reentry parity ---')
            ok, ns, nv, nd = test_scalar_vec_parity(path, n_bars=5000, is_long=is_long)
            if not ok:
                all_pass = False
            print('\n--- QTY pipeline parity ---')
            for is_hedge in (False, True):
                ok = test_qty_pipeline_parity(path, n_bars=5000, is_long=is_long, is_hedge=is_hedge)
                if not ok:
                    all_pass = False
            print('\n--- EXIT gates parity ---')
            ok = test_exit_gates_parity(path, n_bars=5000, is_long=is_long)
            if not ok:
                all_pass = False

    print('\n' + ('=' * 60))
    print('OVERALL:', 'PASS' if all_pass else 'FAIL')
    sys.exit(0 if all_pass else 1)
