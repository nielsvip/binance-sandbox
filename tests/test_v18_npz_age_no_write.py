"""USER LAW 2026-10-20 "max 15m old in the npz" + CLAUDE.md "never give an old test a newer date": a launch whose NPZ is
older than V15_NPZ_MAX_AGE_MIN (20) behind the latest closed bar stops before any eval / write (STALE_NPZ_SKIPPED); a launch
with no prepared NPZ writes nothing (no re-stamped workbook); BOARD_CTX + manifest carry npz_last_bar_ts / npz_age_min, the
manifest also cells_vintage + publish_reason."""
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_npz_age_stamp_crypto(tmp_path, monkeypatch):
    p = tmp_path / "X.npz"
    now = 1_800_000_000
    np.savez(p, timestamps=np.array([now - 3 * 3600, now - 2 * 3600], dtype="int64"))
    monkeypatch.setattr(P, "_npz_path", lambda ss: p)
    st = P.npz_age_stamp("XUSDT_LONG", now=now)
    assert st["npz_last_bar_ts"] == now - 7200
    from tools.npz_cutoffs import last_closed_bar
    assert st["npz_age_min"] == round((last_closed_bar(now) - (now - 7200)) / 60.0, 1)
    assert st["npz_age_min"] > 20


def test_npz_age_stamp_ms_timestamps_and_missing(tmp_path, monkeypatch):
    p = tmp_path / "Y.npz"
    now = 1_800_000_000
    from tools.npz_cutoffs import last_closed_bar
    np.savez(p, timestamps=np.array([last_closed_bar(now) * 1000], dtype="int64"))
    monkeypatch.setattr(P, "_npz_path", lambda ss: p)
    assert P.npz_age_stamp("YUSDC_SHORT", now=now)["npz_age_min"] == 0.0
    monkeypatch.setattr(P, "_npz_path", lambda ss: None)
    assert P.npz_age_stamp("YUSDC_SHORT")["npz_age_min"] is None


def test_pilot_wiring_pins():
    assert 'float(os.environ.get("V15_NPZ_MAX_AGE_MIN", "20") or 0)' in SRC
    assert 'os.environ.get("V12_ALLOW_STALE_NPZ") != "1" and _maxage > 0' in SRC
    i_age = SRC.index("STALE_NPZ_SKIPPED: NPZ last bar")
    i_hold = SRC.index("        _install_holdout(new_symside)\n")
    assert i_age < i_hold  # before any eval (holdout install precedes preload/baseline)
    assert '_skp = PROGRESS_DIR / "v18_skips" / f"{new_symside}.json"' in SRC
    # no prepared NPZ -> nothing written (the 0-trade baseline XLS path is no longer reachable without data)
    i_nw = SRC.index("[NO-WRITE] {new_symside} no prepared NPZ")
    i_zero = SRC.index("[0-TRADES-FAST-FAIL] {new_symside} 0 trades — will still write baseline XLS then skip sweep (only RAM, no fetch)")
    assert i_nw < i_zero
    assert '"npz_last_bar_ts": _NPZ_STAMP.get("npz_last_bar_ts"), "npz_age_min": _NPZ_STAMP.get("npz_age_min")' in SRC
    assert '_man["cells_vintage"] = _v18_vint.get("last_eval_ts")' in SRC and '_man["publish_reason"]' in SRC
