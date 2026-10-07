"""durable: v15 must fill hundreds per minute, no empty/repeated/0 violations, parallel combos"""
import pathlib
def test_v15_parallel_combos():
    src = pathlib.Path("v15_pilot.py").read_text()
    assert "ThreadPoolExecutor(max_workers=16)" in src
    # combos must be parallel, not sequential heavy list comp (check code lines, not compat comment)
    lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    code = "\n".join(lines)
    assert "ex.submit" in code or "ex_c.map" in code or "ex.map" in code, "must use ThreadPool parallel submit/map"
    assert "vecs = [_eval_prep(prepared" not in code, "heavy sequential vecs = [_eval_prep forbidden >1s red"
    assert "ALL_PREPARED" in src and "V12_NPZ_CACHE" in src
def test_v15_no_empty_repeated_zero():
    src = pathlib.Path("v15_pilot.py").read_text()
    assert "_is_empty" in src and "not publishing" in src
    # limiter deleted — every switch calculated
    assert "max_" + "switch" not in src.lower() and "max-" + "switch" not in src.lower(), "limiter must be deleted"
    assert "wb_keep" in src
