"""2026-10-28: D's 7-day confirm cut shrank the crypto 30 d fit window to 23.6 d (< metrics.MIN_WINDOW_COVERAGE 0.80), so every
crypto board was refused ("npz covers 24d of the 30d window (78%)"). The holdout store wrapper must load days+confirm_days earlier
so the fit window stays a full window ending at the cut."""
import datetime
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v15_pilot as P  # noqa: E402
from tools.opt import evaluate_v12 as E  # noqa: E402
from tools.opt import metrics as M  # noqa: E402

LAST = datetime.datetime(2026, 10, 28, 15, 0, tzinfo=datetime.timezone.utc).timestamp()
FIRST = LAST - 120 * 86400.0
TS = np.arange(FIRST, LAST + 1, 900.0)


def _fake_store(sym, mode, start):
    t0 = datetime.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()
    ts = TS[TS >= t0]
    return {"timestamps": ts * 1000.0, "close": np.ones(len(ts))}


def _install(monkeypatch, tmp_path, confirm_days, days=0.0):
    npz = tmp_path / "ZZUSDT.npz"
    np.savez(npz, timestamps=TS * 1000.0)
    monkeypatch.setattr(P, "_npz_path", lambda ss: npz)
    monkeypatch.setattr(E, "_store", _fake_store)
    monkeypatch.setattr(P, "_HOLDOUT", {"days": days, "cut": None, "on": False, "installed": False, "confirm_days": confirm_days})
    cut = P._install_holdout("ZZUSDT_SHORT")
    return cut


def _fit_span_days(start):
    d = E._store("ZZUSDT", "crypto", start)
    sec = np.asarray(d["timestamps"], dtype="float64") / 1000.0
    return (sec[-1] - sec[0]) / 86400.0, sec[-1]


def test_crypto_30d_fit_window_survives_7d_confirm(monkeypatch, tmp_path):
    cut = _install(monkeypatch, tmp_path, confirm_days=7.0)
    assert abs(cut - (LAST - 7 * 86400.0)) < 1
    start = datetime.datetime.utcfromtimestamp(LAST - 30 * 86400.0).strftime("%Y-%m-%d")  # what evaluate_v12 asks for crypto 30 d
    span, end = _fit_span_days(start)
    assert end <= cut + 1e-6, "no bar after the cut may reach the fit"
    assert span >= 30.0 - 1.0
    assert span >= M.MIN_WINDOW_COVERAGE * 30


def test_confirm_off_leaves_store_untouched(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, confirm_days=0.0)
    start = datetime.datetime.utcfromtimestamp(LAST - 30 * 86400.0).strftime("%Y-%m-%d")
    d = E._store("ZZUSDT", "crypto", start)
    assert len(d["timestamps"]) == len(_fake_store("ZZUSDT", "crypto", start)["timestamps"])


def test_source_contract_shift():
    src = (ROOT / "v15_pilot.py").read_text()
    assert '_shift_d = int(-(-(float(_HOLDOUT["days"]) + float(_HOLDOUT.get("confirm_days") or 0.0)) // 1))' in src
    assert 'datetime.timedelta(days=_shift_d)' in src
