import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

import v12_quick_engine as engine


ROOT = Path(__file__).resolve().parents[1]


def test_arkm_published_set_exits_at_frozen_stop_before_later_exit_branches():
    db = ROOT / "data/hourly_reconfig/per_sym_store.db"
    npz = ROOT / "backtest_v8/indicators/ARKMUSDT.npz"
    if not db.exists() or not npz.exists():
        pytest.skip("ARKM published settings and NPZ are required for this incident replay")
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side='ARKMUSDT_LONG'").fetchone()
    if row is None:
        pytest.skip("ARKM published set unavailable")
    config = engine.QuickConfig()
    config.apply_cat_side_defaults("CRYPTO_LONG")
    for key, value in json.loads(row[0]).items():
        if hasattr(config, key):
            setattr(config, key, value)
    with np.load(npz, allow_pickle=True) as archive:
        data = {key: archive[key] for key in archive.files}
    length = len(data["timestamps"])
    start = int(np.searchsorted(data["timestamps"], data["timestamps"][-1] - 30 * 86400))
    data = {key: (value[start:] if isinstance(value, np.ndarray) and value.ndim and len(value) == length else value) for key, value in data.items()}
    result = engine.simulate_one(data, "ARKMUSDT", True, config)
    entry = next(i for i, event in enumerate(result["ledger"]) if event.get("type") == "OPEN" and event.get("ts") == 1792660500.0)
    exit_event = next(event for event in result["ledger"][entry + 1 :] if event.get("type") == "CLOSE")
    assert exit_event["exit_reason"] == "FIXED_STOP"
    assert abs(exit_event["fixed_stop_level"] - 0.12418874898925425) < 1e-8
    assert exit_event["ts"] < 1793130000
