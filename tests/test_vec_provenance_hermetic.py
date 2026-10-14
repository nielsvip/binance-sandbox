"""2026-10-14 HERMETIC gate: exposure increases need structural vec provenance for the same key; exits never gated."""
import asyncio
import json
import live_twins.vec_provenance as VP


def _mode(tmp_path, monkeypatch, m):
    f = tmp_path / "hermetic_mode.json"
    f.write_text(json.dumps({"mode": m}))
    monkeypatch.setattr(VP, "_MODE_FILE", f)
    VP._MODE_CACHE.update({"mtime": None})


def test_exits_never_gated(tmp_path, monkeypatch):
    _mode(tmp_path, monkeypatch, "enforce")
    assert VP.gate("men:X_LONG", "SELL", "LONG")[0]                       # reduce long
    assert VP.gate("men:X_SHORT", "BUY", "SHORT")[0]                      # reduce short
    assert VP.gate("men:X_LONG", "BUY", "LONG", is_full_close=True)[0]
    assert VP.gate("men:X_LONG", "BUY", "LONG", action="CLOSE")[0]
    assert VP.gate("men:X_LONG", "BUY", "LONG", action="PLACE_STOP")[0]


def test_open_without_provenance_refused_in_enforce_logged_in_log(tmp_path, monkeypatch):
    _mode(tmp_path, monkeypatch, "enforce")
    ok, why = VP.gate("men:X_LONG", "BUY", "LONG", action="OPEN")
    assert not ok and "no vec provenance" in why
    _mode(tmp_path, monkeypatch, "log")
    ok, why = VP.gate("men:X_LONG", "BUY", "LONG", action="OPEN")
    assert ok and why


def test_provenance_survives_await_task_and_wait_for(tmp_path, monkeypatch):
    _mode(tmp_path, monkeypatch, "enforce")

    async def execute_now(pk):
        await asyncio.sleep(0)
        return VP.gate(pk, "SELL", "SHORT", action="OPEN")

    async def main():
        tok = VP.mark("fin:Y_SHORT", 123.0, "OPEN")
        try:
            a = await execute_now("fin:Y_SHORT")
            b = await asyncio.create_task(execute_now("fin:Y_SHORT"))
            c = await asyncio.wait_for(execute_now("fin:Y_SHORT"), 5)
            d = await execute_now("fin:Z_SHORT")                          # other key -> refused
        finally:
            VP.reset(tok)
        e = await execute_now("fin:Y_SHORT")                              # after reset -> refused
        return a, b, c, d, e

    a, b, c, d, e = asyncio.run(main())
    assert a[0] and b[0] and c[0] and a[1].startswith("vec:")
    assert not d[0] and not e[0]


def test_hedge_always_refused_in_enforce(tmp_path, monkeypatch):
    _mode(tmp_path, monkeypatch, "enforce")

    async def main():
        tok = VP.mark("ang:H_LONG", 1.0, "OPEN")
        try:
            return VP.gate("ang:H_LONG", "SELL", "SHORT", action="HEDGE_OPEN", is_hedge=True)
        finally:
            VP.reset(tok)

    assert asyncio.run(main())[0] is False
