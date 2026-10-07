"""Regression: v15_local_herd must not crash after 3s — UnboundLocalError me bug (2026-09-16).

Herd died every ~30s with `UnboundLocalError: cannot access local variable 'me'`
at line 318 because `me = hostname` was used before definition. Pilots then
appeared to "not run >3s" because herd kept restarting and NPZ-missing symbols
exit fast by design (skip-empty-baseline). This test ensures the module
imports, the tune-per-host block defines `me`, and max_parallel is sane."""

import ast
import pathlib


def test_herd_defines_me_before_use():
    p = pathlib.Path("tools/v15_local_herd.py")
    src = p.read_text()
    lines = src.splitlines()
    # There are two `me =` (tune block at ~308 and shard block at ~380). Use the one before the first use.
    first_use = None
    for i, l in enumerate(lines, 1):
        if '"htz-v15-s3" in me' in l:
            first_use = i
            break
    assert first_use is not None, "me use missing"
    # find last assign before first_use
    assign_before = None
    for i, l in enumerate(lines, 1):
        if "me = subprocess.check_output" in l and i < first_use:
            assign_before = i
    assert assign_before is not None, f"me not assigned before first use at {first_use}"
    assert assign_before < first_use


def test_herd_max_parallel_sane():
    p = pathlib.Path("tools/v15_local_herd.py")
    src = p.read_text()
    # s2 must be 2, s1 4, s3/s5 6 after 2026-09-16 fix (not 8 or 22 which OOM)
    assert "max_parallel = 2" in src, "s2 throttle missing"
    assert "max_parallel = 4" in src, "s1 throttle missing"
    assert "max_parallel = 6" in src, "s3/s5 throttle missing"
    # workers must be defined alongside
    assert "workers = 8" in src


def test_herd_compiles():
    import py_compile
    py_compile.compile("tools/v15_local_herd.py", doraise=True)
