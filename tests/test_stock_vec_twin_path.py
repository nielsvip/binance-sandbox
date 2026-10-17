"""USER 2026-10-17 "fix the stock vector-decided path first, then enforce it" — contracts of the stocks twin path."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vec_provenance as VP  # noqa: E402


def test_provenance_survives_the_queue_hop(monkeypatch):
    """Dispatch site marks -> queued order copies current() -> worker re-marks before ensure_future -> gate sees it."""
    monkeypatch.setattr(VP, "mode", lambda: "enforce")
    pk = "trb:AMD_LONG"
    queue = []

    async def dispatch():
        tok = VP.mark(pk, 1700000000.0, "OPEN", "VEC_EXACT_TWIN")
        try:
            order = {"pk": pk}
            pv = VP.current()
            if isinstance(pv, dict) and pv.get("pk") == pk:
                order["vec_provenance"] = dict(pv)
            queue.append(order)
        finally:
            VP.reset(tok)

    async def execute():
        return VP.gate(pk, "BUY", "LONG", is_full_close=False, action="OPEN")

    async def worker():
        order = queue.pop(0)
        tok = None
        pv = order.get("vec_provenance")
        if isinstance(pv, dict) and pv.get("pk") == pk:
            tok = VP.mark(pk, pv.get("bar_ts"), pv.get("act"), pv.get("kind"))
        task = asyncio.ensure_future(execute())
        if tok is not None:
            VP.reset(tok)
        return await task

    async def main():
        await asyncio.ensure_future(dispatch())
        return await asyncio.ensure_future(worker())

    ok, why = asyncio.run(main())
    assert ok and why.startswith("vec:VEC_EXACT_TWIN:OPEN")


def test_no_provenance_open_is_refused_exit_never(monkeypatch):
    monkeypatch.setattr(VP, "mode", lambda: "enforce")
    ok, why = VP.gate("trb:AMD_LONG", "BUY", "LONG", is_full_close=False, action="OPEN")
    assert not ok and "no vec provenance" in why
    ok, _ = VP.gate("trb:AMD_LONG", "SELL", "LONG", is_full_close=True, action="CLOSE")
    assert ok


def test_stock_twin_path_is_wired():
    src = (ROOT / "tradier_manage.py").read_text()
    assert "async def _vec_exact_process_twin(" in src
    assert 'getattr(config, "TRADIER_VEC_EXACT_ENGINE", "npz_twin")' in src
    assert "_vx.take_all, symbol, position_side, _now, want" in src
    assert '_vprov_t.mark(position_key, _bar_ts, _ev["type"], "VEC_EXACT_TWIN")' in src
    assert 'order["vec_provenance"] = dict(_pv)' in src
    assert "_pv_tok = _vprov_c.mark(position_key" in src
    assert "pop_expired_exits(symbol, position_side)" in src


def test_authority_switches_are_config_only():
    import per_sym_store as S

    for k in ("PARITY_VEC_EXACT_MODE", "STRICT_VEC_PARITY_GATE_EXITS", "TRADIER_HERMETIC_ENFORCE", "TRADIER_VEC_EXACT_ENGINE"):
        assert k in S.LIVE_AUTHORITY_KNOBS
    d = {k: False for k in S.LIVE_AUTHORITY_KNOBS}
    d["START_POSITION_SIZE"] = 1.0
    out = S._mandate_sanitize(d)
    assert out == {"START_POSITION_SIZE": 1.0}
