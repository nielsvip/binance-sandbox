"""USER 2026-10-14 pair-in-RAM: when a pilot finishes a sym_side, the opposite side (if tradeable)
is calculated in the SAME process before the symbol leaves RAM — never a separate launch hours
later on a different NPZ vintage. Decision pins for v15_pilot._pair_partner_decision."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import v15_pilot as P


def test_pair_blocked_stocks_rule():
    b = P._pair_blocked
    kw = dict(trb_long={"PLTR"}, trb_short={"SNDK"}, mandatory_long={"SNDK", "MU"}, mandatory_short={"MU"}, non_shortable={"XYZ"})
    assert b("SNDK_LONG", **kw) is False  # mandatory LONG
    assert b("SNDK_SHORT", **kw) is False  # symbols_trb_short
    assert b("MU_SHORT", **kw) is False  # mandatory SHORT
    assert b("PLTR_SHORT", **kw) is True  # in trb_long only: other side not tradeable
    assert b("XYZ_SHORT", trb_short={"XYZ"}, mandatory_short=set(), non_shortable={"XYZ"}) is True  # NON_SHORTABLE wins
    assert b("BTCUSDT_LONG", tradeable_keys=["ang:BTCUSDT_LONG"]) is False
    assert b("BTCUSDT_SHORT", tradeable_keys=["ang:BTCUSDT_LONG"]) is True


def test_partner_decision_matrix():
    d = P._pair_partner_decision
    assert d("SNDK_LONG", None, "md5a") == "SNDK_SHORT"  # never calculated: run it
    assert d("SNDK_SHORT", "oldmd5", "newmd5") == "SNDK_LONG"  # stale vintage: recalculate
    assert d("SNDK_LONG", "md5a", "md5a") is None  # same NPZ vintage: already pair-calculated
    assert d("SNDK_LONG", None, "md5a", blocked=True) is None  # non-shortable / not tradeable
    assert d("SNDK_LONG", None, "md5a", template_ok=False) is None  # no template for the side
    assert d("SNDK", None, "md5a") is None  # not a sym_side
    assert d("BTCUSDT_SHORT", None, "m") == "BTCUSDT_LONG"
