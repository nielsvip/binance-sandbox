"""VEC_EXACT dispatch parity (USER 2026-10-10 TOTAL PARITY: live trades EXACTLY what the chart shows).

One stuck key used to stall the whole ENTRY sweep sequentially (no timeout), so later vec
OPENs died silently (guardian VEC_DECISION_NOT_FILLED) and slow fills landed outside the 600s
window. live_twins/vec_dispatch.run_all bounds concurrency + per-key timeout; both dispatchers
must use it and must log every skip/block with a BLOCKED keyword (no silent ghosts).
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_run_all_hang_does_not_block_sweep():
    import live_twins.vec_dispatch as vd

    async def hang():
        await asyncio.sleep(30)

    async def fast():
        await asyncio.sleep(0.01)
        return "FILLED"

    async def main():
        return await vd.run_all([("k_hang", hang), ("k_fast", fast)], limit=2, timeout_s=0.2, label="TEST")

    res = asyncio.run(main())
    assert res[0] == (False, "BLOCKED_DISPATCH_TIMEOUT"), res
    assert res[1] == (True, "FILLED"), res


def test_run_all_error_captured_and_ordered():
    import live_twins.vec_dispatch as vd

    async def boom():
        raise RuntimeError("ghost")

    async def ok():
        return "OK"

    res = asyncio.run(vd.run_all([("a", boom), ("b", ok)], limit=2, timeout_s=5.0, label="TEST"))
    assert res[0][0] is False and res[0][1].startswith("RuntimeError"), res
    assert res[1] == (True, "OK"), res


def test_run_all_bounded_concurrency():
    import live_twins.vec_dispatch as vd

    live = {"cur": 0, "peak": 0}

    async def worker():
        live["cur"] += 1
        live["peak"] = max(live["peak"], live["cur"])
        await asyncio.sleep(0.05)
        live["cur"] -= 1
        return "x"

    res = asyncio.run(vd.run_all([(f"k{i}", worker) for i in range(6)], limit=2, timeout_s=5.0, label="TEST"))
    assert all(ok for ok, _ in res), res
    assert live["peak"] <= 2, live


def test_entry_dispatcher_uses_bounded_dispatch_and_logs_skip():
    src = (ROOT / "ez_positions_quick.py").read_text()
    assert "vec_dispatch" in src and "run_all" in src, "ENTRY dispatcher must dispatch via vec_dispatch.run_all"
    assert "SKIPPED_BLOCKED_DIVERGENT_HOLD" in src, "live-holds-but-vec-OPEN skip must be logged (no silent ghost)"
    assert "VEC_EXACT_DISPATCH_TIMEOUT_S" in src, "ENTRY timeout must be tunable"


def test_exit_dispatcher_timeboxed():
    src = (ROOT / "ez_manage.py").read_text()
    assert src.count("BLOCKED_DISPATCH_TIMEOUT") >= 2, "EXIT + converge CLOSE must both time-box dispatch"
    assert "VEC_EXACT_DISPATCH_TIMEOUT_S" in src


def test_no_native_trim_while_twin_owns_exit():
    src = (ROOT / "ez_manage.py").read_text()
    assert "if not _vx_native_off() and await _pp_ang_timing_exits" in src, "native ANG trims must stay off while the twin owns EXIT (twin-idle = nobody acts)"


def test_vec_maker_timeout_falls_back_to_market():
    src = (ROOT / "ez_manage.py").read_text()
    assert "_is_vec_exact_order" in src and "[VEC_MAKER_FALLBACK]" in src, "vec OPEN maker-timeout must verify + fall to MARKET (no silent flat)"
    assert "[VEC_MAKER_SUPPRESS]" in src, "any fill sign must suppress the fallback (no double-open)"
    assert "[VEC_MAKER_SUPPRESS_UNKNOWN]" in src, "unverifiable-after-retries must suppress LOUD"


def test_prefilter_skips_refused_before_compute():
    epq = (ROOT / "ez_positions_quick.py").read_text()
    ezm = (ROOT / "ez_manage.py").read_text()
    assert "ENTRY prefilter: skipped" in epq, "ENTRY must prefilter non-tradeable before take/dispatch"
    assert "prefilter: flat + non-tradeable, skipping twin eval" in ezm, "EXIT must skip twin eval when flat + refused"
    assert epq.count("load_tradeable") >= 1 and ezm.count("load_tradeable") >= 1


def test_exit_skips_are_visible_not_silent():
    src = (ROOT / "ez_manage.py").read_text()
    assert "SKIPPED_BLOCKED_DIVERGENT_FLAT" in src, "flat-live exit skip must log BLOCKED (no ghost)"
    assert "SKIPPED_BLOCKED_ZERO_QTY" in src, "zero-qty exit skip must log BLOCKED (no ghost)"


def test_vec_final_floor_after_gauntlet():
    src = (ROOT / "ez_manage.py").read_text()
    assert "[VEC_EXACT_SIZE_FLOOR_FINAL]" in src, "vec OPEN/AUGMENT must re-floor after the last multiplier (veto impossible)"
    assert "if _vec_exact_reason_ok(reason) and not is_reduce and not is_hedge:" in src


def test_dust_close_wires_market_dust_open_suppressed():
    src = (ROOT / "ez_manage.py").read_text()
    assert "[DUST_CLOSE_MARKET]" in src, "dust CLOSE/REDUCE must wire sanctioned MARKET (never abandoned)"
    assert "never open dust" in src, "dust OPENs must stay suppressed"


def test_ground_rules_maker_120_and_verified_cancel():
    src = (ROOT / "ez_manage.py").read_text()
    assert "TIMEOUT = 120.0" in src, "maker chase must be 120s before MARKET fallback (ground rule)"
    assert src.count("[RUNAWAY_UNVERIFIED]") >= 2, "BUY and SELL runaway must verify cancel before market (ground rule)"


def test_converge_skips_twin_owned_keys():
    src = (ROOT / "ez_manage.py").read_text()
    assert "if _cvg_on and _vx.owned_has(position_key)" in src, "converge must never touch twin-owned keys (only legacy)"


def test_converge_to_flat_killed_by_default():
    src = (ROOT / "ez_manage.py").read_text()
    assert 'VEC_CONVERGE_TO_FLAT_ENABLED", False' in src, "converge must default OFF: only a real vec CLOSE act closes"
    assert "if _cvg_on and _amt_end > 0" in src, "converge CLOSE must be gated on the master"


def test_execute_now_step1_step2_blocks_are_logged():
    """USER 2026-10-10: ang:ASTSUSDT_SHORT vec OPEN died silently between STEP1_LOCK and
    STEP2_POS_FETCH (guardian VEC_DECISION_NOT_FILLED, zero refusal lines). Every BLOCK
    return in that region must log position_key first — safety gates may refuse, never silently."""
    lines = (ROOT / "ez_manage.py").read_text().splitlines()
    step1 = next(i for i, l in enumerate(lines) if "STEP1_LOCK action=" in l)
    step2 = next(i for i, l in enumerate(lines) if "STEP2_POS_FETCH action=" in l)
    assert step1 < step2
    region = lines[step1:step2]
    checked = 0
    for idx, line in enumerate(region):
        s = line.strip()
        if s.startswith("return ") and ("BLOCK" in s or "ORDER_DEDUPE" in s):
            checked += 1
            window = "\n".join(region[max(0, idx - 6):idx])
            assert "logger." in window and "position_key" in window, f"silent BLOCK return between STEP1 and STEP2: {s}"
    assert checked >= 2, "expected at least post-fill + dedupe BLOCK returns in STEP1->STEP2"
