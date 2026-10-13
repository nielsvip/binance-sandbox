"""NEVER-AGAIN 2026-10-13 (123456 incident): tests must never write to live
log files. A pytest run of the tradier backstop suite wrote a fixture order id
(123456, 'broker rejected: no position') into LIVE ~/logs, indistinguishable
from real broker events. Both managers now redirect every file handler to a
temp dir whenever pytest is present at import. These tests pin that routing —
they assert on handler paths, not content (account filters may drop records
without matching context, which is orthogonal)."""
import logging
import os
import tempfile


def _live_dirs():
    from pathlib import Path
    return {str(Path.home() / "logs")}


def _file_targets(logger_obj):
    out = []
    for h in getattr(logger_obj, "handlers", []):
        fn = getattr(h, "baseFilename", None)
        if fn:
            out.append(fn)
    return out


def test_tradier_test_run_never_touches_live_logs():
    import tradier_manage as tm
    targets = _file_targets(tm.logger)
    assert targets, "expected file handlers on tradier logger"
    tmp = os.path.realpath(tempfile.gettempdir())
    for t in targets:
        assert os.path.realpath(t).startswith(tmp), t
    for t in targets:
        for live in _live_dirs():
            assert not os.path.realpath(t).startswith(live + os.sep), t


def test_ez_test_run_never_touches_live_logs():
    import ez_manage as em
    targets = _file_targets(em.logger)
    assert targets, "expected file handlers on ez logger"
    tmp = os.path.realpath(tempfile.gettempdir())
    for t in targets:
        assert os.path.realpath(t).startswith(tmp), t
    for t in targets:
        for live in _live_dirs():
            assert not os.path.realpath(t).startswith(live + os.sep), t
