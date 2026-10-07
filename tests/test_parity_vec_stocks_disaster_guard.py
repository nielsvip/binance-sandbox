"""Lane D parity: stocks disaster guard — vec twin == the REAL live method on the same synthetic inputs.

The live method tradier_manage.TradierTradeManager._disaster_guard_for_entry is extracted with ast and executed
against stub globals (_cfg_auto -> QuickConfig attribute, get_indicators -> the bar's indicator dict). The vec twin
vec_decisions/stocks_disaster_guard.block_mask must agree bar-by-bar (forming day open fed identically to both).
"""
import ast
import datetime as dt
import logging
import pathlib
import sys
from zoneinfo import ZoneInfo

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NY = ZoneInfo("America/New_York")


def _cfg(**kw):
    import v12_quick_engine as V
    c = V.QuickConfig(); c.apply_tradier_defaults()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _safe(npz, k, n, default=0.0):
    a = npz.get(k)
    if isinstance(a, np.ndarray) and a.shape[:1] == (n,):
        return a
    return np.full(n, default, dtype=float)


def _quiet_logger():
    lg = logging.getLogger("dg_test")
    lg.disabled = True
    return lg


def _live_method(cfg):
    src = (ROOT / "tradier_manage.py").read_text()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_disaster_guard_for_entry":
            fn = node
            break
    assert fn is not None
    ns = {
        "_cfg_auto": lambda k, d=None: getattr(cfg, k, d),
        "logger": _quiet_logger(),
        "safe_fetch_float": lambda v, d=0.0: float(v) if v is not None else float(d),
        "datetime": dt.datetime, "timezone": dt.timezone,
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "tradier_manage._disaster_guard_for_entry", "exec"), ns)
    return ns["_disaster_guard_for_entry"]


class _Stub:
    def __init__(self):
        self.ind = None
        self.position_manager = None
        self._broker_preflight_cache = None

    def get_indicators(self, symbol):
        return self.ind


def _grid(seed):
    rng = np.random.default_rng(seed)
    start = dt.datetime(2026, 9, 28, 9, 30, tzinfo=NY)
    ts = []
    for d in range(5):
        for k in range(26):
            ts.append((start + dt.timedelta(days=d, minutes=15 * k)).timestamp())
    ts = np.array(ts)
    n = len(ts)
    close = 100 + np.cumsum(rng.normal(0, 0.8, n))
    z = {
        "open_15m": close + rng.normal(0, 0.5, n), "open_D": close + rng.normal(0, 2, n),
        "open_4h": close + rng.normal(0, 1.5, n), "close_4h": close + rng.normal(0, 1.5, n),
        "open_1h": close + rng.normal(0, 1, n), "close_1h": close + rng.normal(0, 1, n),
        "rsi_15m": rng.uniform(20, 80, n), "rsi_1h": rng.uniform(20, 80, n),
        "sma_200_15m": close + rng.normal(0, 3, n),
        "wt1_D": rng.normal(0, 20, n), "wt2_D": rng.normal(0, 20, n),
    }
    return z, n, ts, close


def test_vec_equals_real_live_method_both_sides():
    import vec_decisions.stocks_disaster_guard as G
    import vec_decisions.stocks_live_session_gates as S
    for seed in (1, 2, 3):
        z, n, ts, close = _grid(seed)
        rth = S.rth_mask(ts, 15.0)
        day_open = G.forming_day_open(ts, z["open_15m"], z["open_D"], rth)
        for kw in ({}, {"DG_HTF_ALIGN_REQUIRE_1H": True}, {"PENNY_STOCK_LONG_BLOCK_PRICE_USD": 101.0}):
            cfg = _cfg(**kw)
            live = _live_method(cfg)
            stub = _Stub()
            for is_long in (True, False):
                m = G.block_mask(z, n, is_long, cfg, close, _safe, ts, rth)
                for i in range(n):
                    ind = {k: float(v[i]) for k, v in z.items()}
                    ind["open_D"] = float(day_open[i]); ind["close_D"] = float(close[i])
                    stub.ind = ind
                    blk, _ = live(stub, "trb:X_L", "trb", "X", "LONG" if is_long else "SHORT", 1.0, float(close[i]), "TEST_OPEN")
                    assert bool(m[i]) == bool(blk), (seed, kw, is_long, i, bool(m[i]), blk)
                    assert G.live_scalar(ind, float(close[i]), is_long, cfg) == bool(blk)


def test_forming_day_open_is_first_rth_bar():
    import vec_decisions.stocks_disaster_guard as G
    import vec_decisions.stocks_live_session_gates as S
    z, n, ts, close = _grid(5)
    do = G.forming_day_open(ts, z["open_15m"], z["open_D"], S.rth_mask(ts, 15.0))
    for d in range(5):
        assert np.all(do[d * 26:(d + 1) * 26] == z["open_15m"][d * 26])


def test_master_off_and_engine_wiring():
    import vec_decisions.stocks_disaster_guard as G
    z, n, ts, close = _grid(4)
    assert G.block_mask(z, n, True, _cfg(DISASTER_GUARD_ENABLED=False), close, _safe, ts, np.ones(n, bool)) is None
    assert _cfg().DISASTER_GUARD_ENABLED is True
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_sdg.block_mask(npz, n, is_long, cfg, close, _safe, _sg_ts" in src and "_dg_block[i]" in src


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_"):
            f(); print("PASS", k)
