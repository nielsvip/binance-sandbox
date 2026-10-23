"""Close-only adopted keys (USER 2026-10-23): a position on a non-tradeable key persists
for years and may only close its outstanding amount, never open further.

Lane side: run_bar emits no CONVERGE_OPEN intent for (acct, sym_side) pairs absent from
tradeable_keys.json (CLOSE side untouched). Consumer side: reconcile never OPENs a
non-tradeable key (CLOSE arm untouched). No network, no live orders."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ez_manage as EZ  # noqa: E402
from tools import pure_board_trader as pbt  # noqa: E402

from tests.test_vec_driven_consumer import (  # noqa: E402
    REG_LIVE,
    _run_loop,
    _setup,
)


def test_tradeable_pair_repo_keys():
    assert pbt.tradeable_pair("flz", "XRPUSDC_SHORT") is True
    assert pbt.tradeable_pair("inf", "GALAUSDT_SHORT") is False
    assert pbt.tradeable_pair("men", "NOPEUSDT_LONG") is False


def test_tradeable_pair_fail_open_on_unreadable_file(monkeypatch, tmp_path):
    monkeypatch.setattr(pbt, "ROOT", tmp_path)
    monkeypatch.setattr(pbt, "_TRADEABLE_MTIME", 0.0)
    assert pbt.tradeable_pair("any", "ANYUSDT_LONG") is True


def test_reconcile_never_opens_non_tradeable_but_still_closes(tmp_path, monkeypatch):
    # OPEN arm: board holds, live flat, key not tradeable -> no dispatch, no ledger row.
    base, vl, tm, calls = _setup(tmp_path / "o", monkeypatch, REG_LIVE)
    tm.tradeable_keys = set()
    (vl / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "LONG", "bar_ts": time.time() - 30}))
    monkeypatch.setattr(EZ._ezm_base_config, "VEC_DRIVEN_RECONCILE_GRACE_S", 0.0, raising=False)
    _run_loop(tm, monkeypatch, cycles=1)
    assert calls == []
    # CLOSE arm: board flat, live holds, key not tradeable -> CLOSE still dispatched.
    base2, vl2, tm2, calls2 = _setup(tmp_path / "c", monkeypatch, REG_LIVE, amts={"men:BTCUSDT_LONG": 5.0})
    tm2.tradeable_keys = set()
    (vl2 / "state" / "BTCUSDT_LONG.json").write_text(json.dumps({"target_state": "FLAT", "bar_ts": time.time() - 30}))
    _run_loop(tm2, monkeypatch, cycles=1)
    assert [c["action"] for c in calls2] == ["CLOSE"], calls2


def test_lane_counts_sidelined_counter():
    """Pin: the CONVERGE_OPEN gate counts SIDELINED_NON_TRADEABLE (grep-pin, repo convention)."""
    src = Path(pbt.__file__).read_text()
    i = src.index('conv = "CONVERGE_OPEN"')
    seg = src[i:i + 600]
    assert "tradeable_pair(acct, ss)" in seg and "SIDELINED_NON_TRADEABLE" in seg, seg
