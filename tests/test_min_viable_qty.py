"""Per-symbol minimum viability (USER 2026-10-11): every crypto order floors UP to
max(minQty x1.2, $6/price, MIN_NOTIONAL/price) from min_qty.json — losers trade, never vetoed, never exchange-rejected."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ez_manage as E


def test_min_viable_formula():
    assert E._min_viable_qty(0.001, 50.0, 100000.0) == 0.0012  # BTC: minQty x1.2 wins
    assert E._min_viable_qty(0.001, 50.0, 40000.0) == 0.00125  # BTC crash: MIN_NOTIONAL term wins (the fix)
    assert E._min_viable_qty(0.01, 5.0, 100.0) == 0.06  # $6/price wins
    assert E._min_viable_qty(0.01, 5.0, 0) == 0.0
    assert E._min_viable_qty(None, None, 100.0) == 0.06  # missing record -> $6 default
    assert E._min_viable_qty("x", "y", 100.0) == 0.0


def test_live_filter_records_grounded():
    mq = json.loads((ROOT / "min_qty.json").read_text())
    assert mq["BTCUSDC"] == {"minQty": 0.001, "minNotional": 50.0}
    assert mq["BTCUSDT"]["minNotional"] == 50.0
    assert mq["ETHUSDC"]["minNotional"] == 20.0
    assert all(v["minQty"] > 0 and v["minNotional"] > 0 for v in mq.values())
    sc = json.loads((ROOT / "symbol_configs.json").read_text())
    assert set(sc["BTCUSDC"]) == {"price_precision", "quantity_precision", "step_size", "tick_size"}


def test_floor_sites_and_loader_wired():
    src = (ROOT / "ez_manage.py").read_text()
    assert src.count("_min_viable_qty(self.min_qty.get(symbol, 0.0001), self.min_notional.get(symbol, 5.0), current_price)") >= 6
    assert "async def load_min_notional(self)" in src
    assert "self.min_notional = await self.load_min_notional()" in src
    assert "[MAKER_STEP_UP]" in src


def test_step_up_btc_sub_step():
    from decimal import Decimal
    mq = {"BTCUSDC": 0.001}
    mn = {"BTCUSDC": 50.0}
    got, stepped = E._step_up_to_min_viable(Decimal("0"), Decimal("0.001"), "BTCUSDC", 100000.0, mq, mn)
    assert stepped is True
    assert got == Decimal("0.002")  # floor 0.0012 -> ceil to step
    assert float(got) * 100000.0 >= 50.0  # placeable: clears BTC $50 minNotional


def test_step_up_below_min_nonzero():
    from decimal import Decimal
    mq = {"BTCUSDC": 0.001}
    mn = {"BTCUSDC": 50.0}
    got, stepped = E._step_up_to_min_viable(Decimal("0.001"), Decimal("0.001"), "BTCUSDC", 100000.0, mq, mn)
    assert stepped is True
    assert got == Decimal("0.002")


def test_step_up_leaves_healthy_qty():
    from decimal import Decimal
    mq = {"BTCUSDC": 0.001}
    mn = {"BTCUSDC": 50.0}
    got, stepped = E._step_up_to_min_viable(Decimal("0.005"), Decimal("0.001"), "BTCUSDC", 100000.0, mq, mn)
    assert stepped is False
    assert got == Decimal("0.005")


def test_step_up_non_crypto_untouched():
    from decimal import Decimal
    got, stepped = E._step_up_to_min_viable(Decimal("0"), Decimal("1"), "AAPL", 200.0, {}, {})
    assert stepped is False
    assert got == Decimal("0")


def test_step_up_bad_inputs_never_raise():
    from decimal import Decimal
    assert E._step_up_to_min_viable(Decimal("0"), Decimal("0.001"), "BTCUSDC", 0, {}, {})[1] is False
    assert E._step_up_to_min_viable(Decimal("0"), Decimal("0"), "BTCUSDC", 100000.0, {}, {})[1] is False
    assert E._step_up_to_min_viable("bogus", "bogus", "BTCUSDC", 100000.0, {}, {})[1] is False


def test_step_up_wired_at_both_wire_sites():
    src = (ROOT / "ez_manage.py").read_text()
    assert src.count("_step_up_to_min_viable(") >= 3  # def + maker + webhook
    assert "[MIN_QTY_STEP_UP]" in src
    assert "MARKET_ZERO_QTY" in src  # reduce/close path still refuses zero, never steps up
