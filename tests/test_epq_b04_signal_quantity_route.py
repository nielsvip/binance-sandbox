import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import ez_positions_quick as epq


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Registry:
    def get_rating(self, *_args):
        return 0, "WAIT", "", time.time()


class _TradeManager:
    def __init__(self, key):
        self._allowed_accounts = []
        self.accounts = {"acct"}
        self.tradeable_keys = {key}
        self.positions = {}
        self.price_cache = {}
        self.min_qty = {}
        self.config = epq.config


class _Tracker:
    def __init__(self, key):
        self.tradeable_position_keys = {"acct": {key}}
        self.tradeable_keys = {key}
        self.positions_service = SimpleNamespace(positions_by_account={"acct": {}})
        self.last_check_times = {}
        self._processing_orders = {}
        self.entry_candidates = {}
        self._entry_candidates_dirty = {}
        self.exit_candidates = {}
        self.last_exit_prices = {}
        self.last_exit_times = {}
        self.registry = _Registry()
        self._entry_candidates_lock = _Lock()

    async def get_position(self, _key):
        return SimpleNamespace(
            positionAmt=0,
            gain=0,
            last_reduction_price=0,
            last_reduction_time=0,
            last_augmentation_time=datetime.now(timezone.utc),
            entry_price=0,
        )

    async def get_entry_candidate(self, _key):
        return None

    def _entry_template(self):
        return {"last_exit_timestamp": 0}

    def is_system_lagging(self, *_args):
        return False

    def get_last_check_time(self, *_args):
        return 0

    async def is_processing(self, *_args):
        return False

    async def set_processing(self, *_args):
        return None

    async def transition_to_exit(self, *_args, **_kwargs):
        return None

    async def clear_processing(self, *_args):
        return None

    async def revert_optimistic_exit(self, *_args):
        return None

    async def save_tracker(self, *_args, **_kwargs):
        return None

    async def register_check(self, *_args):
        return None


class _DataManager:
    delta_tracker = None

    async def get_hot_state(self, _symbol):
        return (
            {"_tick_ts": time.time()},
            {"current_price": 100.0},
            50,
            50,
            50,
            50,
            True,
        )

    async def get_fresh_price(self, _symbol):
        return 100.0, time.time()


async def _run_candidate(monkeypatch, signal_reason, signal_qty, dynamic_qty):
    key = "acct:FOOUSDT_LONG"
    captured = []

    async def evaluate(_ctx):
        return epq._EM_Signal(
            action="REENTRY",
            reason=signal_reason,
            conviction=80,
            quantity=signal_qty,
        )

    async def execute(*args, **kwargs):
        captured.append((args, kwargs))
        return True, "captured"

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(epq, "evaluate_reentry_epq", evaluate)
    monkeypatch.setattr(epq, "execute_trade_wrapper", execute)
    monkeypatch.setattr(epq, "log_stoch_snapshot", no_op)
    monkeypatch.setattr(epq, "record_trade_event", no_op)
    monkeypatch.setattr(epq, "calculate_dynamic_quantity", lambda *_args: dynamic_qty)
    monkeypatch.setattr(epq, "_sw_get", lambda _s, _side, _key, default: default)

    def psym(_symbol, _side, knob, default):
        if knob in ("START_POSITION_SIZE", "START_POSITION_SIZE_OVERRIDE_USD"):
            return 10.0
        if knob == "GOLDEN_RULE_HTF_MIN_TFS":
            return 0
        return default

    monkeypatch.setattr(epq, "_psym_get", psym)
    for name, value in {
        "PARITY_VEC_EXACT_MODE": False,
        "ABLATION_DISABLE_QUICK_ENTRY": False,
        "SCALP_MODE": False,
        "SCALP_V3_ENABLED": False,
        "SCALP_V3_DIAG_LOG": False,
        "CAT_SIDE_DEFAULTS_ENABLED": False,
        "EZ_REENTRY_INLINE_ENABLED": True,
        "EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED": True,
        "HLR_RALLY_ENABLED": False,
    }.items():
        monkeypatch.setattr(epq.config, name, value, raising=False)

    await epq.check_entry_candidates_for_account(
        _TradeManager(key),
        "acct",
        None,
        _Tracker(key),
        None,
        _DataManager(),
        position_keys=[key],
        force=True,
    )
    assert len(captured) == 1
    args = captured[0][0]
    return args[6], args[8], args[9]


def test_b04_signal_quantity_survives_actual_epq_caller(monkeypatch):
    action, qty, reason = asyncio.run(
        _run_candidate(monkeypatch, "B04_DC_RETEST_TEST", 0.25, dynamic_qty=0.7)
    )
    assert action == "OPEN"
    assert qty == 0.25
    assert reason.endswith("EVAL_REENTRY_EPQ_B04_DC_RETEST_TEST")


def test_b04_signal_quantity_obeys_native_12x_sps_cap(monkeypatch):
    _action, qty, reason = asyncio.run(
        _run_candidate(monkeypatch, "B04_DC_RETEST_TEST", 2.0, dynamic_qty=0.7)
    )
    assert qty == 1.2
    assert reason.endswith("EVAL_REENTRY_EPQ_B04_DC_RETEST_TEST")


def test_non_b04_reentry_keeps_dynamic_quantity(monkeypatch):
    _action, qty, reason = asyncio.run(
        _run_candidate(monkeypatch, "OTHER_REENTRY", 2.0, dynamic_qty=0.7)
    )
    assert qty == 0.7
    assert reason.endswith("EVAL_REENTRY_EPQ_OTHER_REENTRY")
