import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import npz_live_generator as live_npz
import tools.v15_preopen_7d_npz as overlay_npz


NY = ZoneInfo("America/New_York")


def test_overlay_keeps_rth_and_only_four_current_day_premarket_rows(tmp_path, monkeypatch):
    today = dt.datetime.now(NY).date()
    now = dt.datetime.combine(today, dt.time(6, 0, 30), NY)
    session_days = [today - dt.timedelta(days=n) for n in range(12, 0, -1)
                    if (today - dt.timedelta(days=n)).weekday() < 5]
    rth = [dt.datetime.combine(day, dt.time(9, 30), NY) + dt.timedelta(minutes=15 * i)
           for day in session_days for i in range(26)]
    pm = [dt.datetime.combine(today, dt.time(4, 0), NY) + dt.timedelta(minutes=15 * i)
          for i in range(9)]
    raw = pd.DataFrame({"close": np.ones(len(pm))}, index=pd.DatetimeIndex(
        [stamp.astimezone(dt.timezone.utc) for stamp in pm]))
    root = tmp_path
    npz_dir = root / "backtest_v8" / "indicators"
    npz_dir.mkdir(parents=True)
    timestamps = np.asarray([int(stamp.timestamp()) for stamp in rth], dtype=np.int64)
    np.savez_compressed(npz_dir / "TEST.npz", timestamps=timestamps,
                        timestamp_15m=timestamps.copy(), close=np.ones(len(timestamps)))
    monkeypatch.setattr(overlay_npz, "ROOT", root)
    monkeypatch.setattr(live_npz, "_resolve_kline_path", lambda *args: root / "TEST_15m.json")
    monkeypatch.setattr(live_npz, "_load_klines_df", lambda *args: raw.copy())
    monkeypatch.setattr(live_npz, "_drop_forming_bar", lambda frame, tf: frame)
    monkeypatch.setattr(live_npz, "_compute_row_for_15m_close",
                        lambda symbol, stamp, manager, calculator, mode: {
                            "timestamps": int(stamp.timestamp()),
                            "timestamp_15m": int(stamp.timestamp()),
                            "close": 2.0,
                        })

    manifest = overlay_npz.build_overlay("TEST", root / "overlay", now)
    with np.load(root / "overlay" / "TEST.npz") as result:
        result_ts = result["timestamps"].astype(np.int64)
        labels = [dt.datetime.fromtimestamp(int(value), dt.timezone.utc).astimezone(NY)
                  for value in result_ts]
    premarket = [stamp for stamp in labels if stamp.date() == today
                 and dt.time(4, 0) <= stamp.time() < dt.time(9, 30)]
    assert len(premarket) == 4
    assert all(stamp.date() == today for stamp in premarket)
    assert np.all(np.diff(result_ts) > 0)
    assert manifest["premarket_row_count"] == 4
    assert manifest["rth_row_count"] == int(sum(
        dt.time(9, 30) <= stamp.time() < dt.time(16, 0) and stamp.weekday() < 5
        for stamp in labels
    ))
