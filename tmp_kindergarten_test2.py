import sys
sys.path.insert(0, '.')
import numpy as np
from tools.v12_numpy_staged_matrix import build_masks
with np.load('backtest_v8/indicators/BTCUSDC.npz', allow_pickle=True) as z:
    npz={k: z[k] for k in z.files}
print('bars', len(npz['close']))
base,err,_ = build_masks(npz, 'BTCUSDC', True, {})
print('base err', err)
if not err:
    print('base entry', int(base['entry'].sum()), 'reentry', int(base['reentry'].sum()))
tests = [
    ('KINDERGARTEN_CROSS_TYPE', 'ema9_21'),
    ('KINDERGARTEN_CROSS_TYPE', 'ema200'),
    ('EMA_9_21_TF', '1h'),
    ('EMA_9_21_TF', '4h'),
]
for sw,val in tests:
    m,err,_ = build_masks(npz, 'BTCUSDC', True, {sw: val})
    if err:
        print(f"{sw}={val} ERR {err}")
    else:
        base_h = hash((base['entry'].tobytes(), base['exit'].tobytes())) if not err else 0
        h = hash((m['entry'].tobytes(), m['exit'].tobytes()))
        print(f"{sw}={val} entry {int(m['entry'].sum())} {'DIFF' if h!=base_h else 'SAME'}")
