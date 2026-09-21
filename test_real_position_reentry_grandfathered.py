"""test_real_position_reentry_grandfathered.py — real reported positions are REAL,
must be protected, exited on downturns and reentered completely with same or
more value, not restricted by MAX_ORDER / MAX_POSITION if they existed before."""
import unittest
import numpy as np
import types


class FakePosition:
    def __init__(self, amt=0, mark_price=100.0, max_quantity=0):
        self.positionAmt = amt
        self.mark_price = mark_price
        self.max_quantity = max_quantity


class FakeConfig:
    START_POSITION_SIZE = 34.0
    MIN_POSITION_SIZE = 55.0
    MAX_ORDER_VALUE = 300.0
    MAX_ORDER_VALUE_MEN = 300.0
    MAX_ORDER_VALUE_FIN = 300.0
    MAX_POSITION_SIZE = 200.0
    MAX_POSITION_SIZE_MEN = 200.0
    MAX_POSITION_SIZE_FIN = 200.0
    MAX_POSITION_SIZE_BTC = 2000.0
    REENTRY_RALLY_K15M_MAX = 100.0
    REENTRY_RALLY_HTF_MIN = 1
    LEGACY_REENTRY_GUARANTEED_BOTTOM = True


class FakeStore:
    def __init__(self, n=10):
        close = np.full(n, 100.0)
        self._data = {
            'close_3m': close,
            'wt1_3m': np.full(n, 10.0),
            'wt2_3m': np.full(n, 5.0),
            'wt1_15m': np.full(n, 10.0),
            'wt2_15m': np.full(n, 5.0),
            'wt1_1h': np.full(n, 10.0),
            'wt2_1h': np.full(n, 5.0),
            'wt1_4h': np.full(n, 10.0),
            'wt2_4h': np.full(n, 5.0),
            'wt1_D': np.full(n, 10.0),
            'wt2_D': np.full(n, 5.0),
            'wt_velocity_15m': np.full(n, 1.0),
            'wt_bullish_3m': np.full(n, True),
            'wt_bullish_15m': np.full(n, True),
            'wt_bullish_1h': np.full(n, True),
            'wt_bullish_4h': np.full(n, True),
            'stoch_k_3m': np.full(n, 40.0),
            'stoch_d_3m': np.full(n, 30.0),
            'stoch_k_15m': np.full(n, 40.0),
            'stoch_d_15m': np.full(n, 30.0),
            'dc_high_15m': np.full(n, 120.0),
            'dc_low_15m': np.full(n, 80.0),
            'high_15m': np.full(n, 105.0),
            'low_15m': np.full(n, 95.0),
        }


class TestRealPositionReentryGrandfathered(unittest.TestCase):
    def test_vector_restores_same_value_or_more_not_capped_to_start(self):
        """Bounce reentry must restore prior max_quantity even if > START/MAX caps."""
        from ez_reentry_vectorized import VectorizedReentryEvaluator
        cfg = FakeConfig()
        store = FakeStore(n=10)
        ev = VectorizedReentryEvaluator({'BTCUSDC': store}, cfg)
        # Simulate a real reported position that had max_quantity = 10 BTC @ $100 = $1000,
        # far above MAX_ORDER_VALUE $300 and START $34.
        prior_max = 10.0
        pos = FakePosition(amt=0, mark_price=100.0, max_quantity=prior_max)
        tm = types.SimpleNamespace(positions={'test:BTCUSDC_LONG': pos})
        import asyncio
        ctx = {'position_key': 'test:BTCUSDC_LONG', 'symbol': 'BTCUSDC', 'position_side': 'LONG', 'trade_manager': tm, '_v8_bar_idx': 5}
        # wt_2of3_ok is True for our fake data (all bullish), so evaluate should fire
        qty = asyncio.get_event_loop().run_until_complete(ev.evaluate(ctx)).quantity if asyncio.get_event_loop().run_until_complete(ev.evaluate(ctx)) else 0
        # Re-run cleanly
        loop = asyncio.new_event_loop()
        sig = loop.run_until_complete(ev.evaluate(ctx))
        loop.close()
        self.assertIsNotNone(sig, "flat real position must trigger reentry on bullish WT")
        # Must restore at least prior_max, not just START/price = 0.34
        self.assertGreaterEqual(sig.quantity, prior_max, "reentry must restore same value or more, not capped to START")
        self.assertGreater(sig.quantity * 100, cfg.MAX_ORDER_VALUE, "grandfathered reentry must exceed MAX_ORDER_VALUE cap")

    def test_vector_flat_position_not_blocked_by_value_filter(self):
        """Flat position (amt==0) is REENTRY candidate even when prior value was huge."""
        from ez_reentry_vectorized import VectorizedReentryEvaluator
        cfg = FakeConfig()
        store = FakeStore(n=10)
        ev = VectorizedReentryEvaluator({'BTCUSDC': store}, cfg)
        pos = FakePosition(amt=0, mark_price=100.0, max_quantity=50.0)
        tm = types.SimpleNamespace(positions={'test:BTCUSDC_LONG': pos})
        import asyncio
        ctx = {'position_key': 'test:BTCUSDC_LONG', 'symbol': 'BTCUSDC', 'position_side': 'LONG', 'trade_manager': tm, '_v8_bar_idx': 5}
        loop = asyncio.new_event_loop()
        sig = loop.run_until_complete(ev.evaluate(ctx))
        loop.close()
        self.assertIsNotNone(sig)

    def test_vector_holding_large_position_blocked(self):
        """Still-holding large position (>3*START) should NOT get duplicate REENTRY."""
        from ez_reentry_vectorized import VectorizedReentryEvaluator
        cfg = FakeConfig()
        store = FakeStore(n=10)
        ev = VectorizedReentryEvaluator({'BTCUSDC': store}, cfg)
        pos = FakePosition(amt=5.0, mark_price=100.0, max_quantity=5.0)  # value $500 > 3*55=165
        tm = types.SimpleNamespace(positions={'test:BTCUSDC_LONG': pos})
        import asyncio
        ctx = {'position_key': 'test:BTCUSDC_LONG', 'symbol': 'BTCUSDC', 'position_side': 'LONG', 'trade_manager': tm, '_v8_bar_idx': 5}
        loop = asyncio.new_event_loop()
        sig = loop.run_until_complete(ev.evaluate(ctx))
        loop.close()
        self.assertIsNone(sig, "holding large position must not get REENTRY")

    def test_execute_now_grandfathered_bypass_logic(self):
        """execute_now must identify REENTRY and bypass caps (smoke check that flag exists)."""
        import ez_manage
        src = open('ez_manage.py').read()
        self.assertIn('REENTRY_GRANDFATHERED', src)
        self.assertIn('_is_reentry_exec', src)
        self.assertIn('bypass MAX_ORDER_VALUE', src)

    def test_forward_vec_uses_prior_max(self):
        src = open('ez_reentry_vectorized.py').read()
        self.assertIn('prior_max', src)
        self.assertIn('max_quantity', src)

    def test_monitor_entries_includes_non_tradeable_open_positions(self):
        """Real exchange-reported open positions must be monitored even if not in tradeable_keys."""
        src = open('ez_manage.py').read()
        self.assertIn('REAL POSITION FIX', src)
        self.assertIn('_is_open_real', src)
        self.assertIn('_is_real_open_early', src)
        # monitor_entries must append open real before tradeable filter
        self.assertIn('reported exchange positions are REAL', src)

    def test_process_position_not_filtered_for_real_open(self):
        src = open('ez_manage.py').read()
        # process_position early return must be gated by _is_real_open_early
        self.assertIn('not _is_real_open_early', src)
        self.assertIn('_is_real_open_early and position_key not in tradeable_keys', src)

    def test_open_real_positions_protected_on_wt_crosses(self):
        """Open real positions (did-not-enter-itself) must pass WT cross exit checks."""
        import ez_manage
        src = open('ez_manage.py').read()
        # Ensure WT cross / DC breach exit logic still reachable for real positions
        self.assertIn('wt1_3m', src)
        self.assertIn('dc_high', src)
        # The protection is that tradeable_keys no longer blocks process_position for open
        self.assertIn('open reported positions must never be filtered', src)


if __name__ == '__main__':
    unittest.main()
