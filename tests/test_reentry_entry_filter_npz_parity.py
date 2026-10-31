"""Frozen NPZ check for the one-row native reentry-mask provider."""

import os
from pathlib import Path

import numpy as np
import pytest

import v12_quick_engine as V
from backtest_v8_harness import IndicatorStore
from vec_decisions.htf_causal_align import align_store
from vec_decisions.reentry_entry_filter import (
    compare_mask_coverage,
    vector_entry_mask_snapshot,
)


NPZ_PATH = Path(
    os.environ.get(
        "REENTRY_PARITY_NPZ", "/tmp/1000PEPEUSDC_30d_reentry_audit.npz"
    )
)


@pytest.mark.skipif(not NPZ_PATH.is_file(), reason="frozen 1000PEPE NPZ is unavailable")
def test_native_mask_provider_matches_v12_on_frozen_1000pepe_bars():
    with np.load(NPZ_PATH, allow_pickle=True) as data:
        raw = {key: data[key] for key in data.files}
    npz = align_store(raw, "1000PEPEUSDC", "crypto")
    store = IndicatorStore(str(NPZ_PATH))
    n = len(npz["timestamps"])
    indices = np.linspace(100, n - 1, 301, dtype=int)
    variants = (
        ("template", {}),
        (
            "mom3_15m",
            {"MOM3_FILTER_TF": "15m", "MOMENTUM_BREAKOUT_FILTER_TF": "OFF"},
        ),
        (
            "generic_recovery",
            {"DC_BREAK_FILTER_TF": "15m", "BB_RECOVERY_ENTRY_FILTER_TF": "15m"},
        ),
        (
            "history_retest_boundary",
            {
                "BREAKOUT_RETEST_FILTER_TF": "15m",
                "BREAKOUT_RETEST_ARMED_ENABLED": True,
                "DC_BREAK_FILTER_TF": "15m",
                "BB_RECOVERY_ENTRY_FILTER_TF": "15m",
            },
        ),
        (
            "filters_off",
            {
                "MOMENTUM_BREAKOUT_FILTER_TF": "OFF",
                "EMA50_15M_ENTRY_FILTER_ENABLED": False,
                "WT_DIV_ENTRY_GATE_ENABLED": False,
                "FUNDING_GATE_ENABLED": False,
            },
        ),
    )
    for variant, overrides in variants:
        for is_long in (True, False):
            cfg = V.ensure_template_layer(V.QuickConfig(), "1000PEPEUSDC", is_long)
            cfg.MODE = "crypto"
            cfg.BASE_TF = "15m"
            for key, value in overrides.items():
                setattr(cfg, key, value)
            probe = {}
            cfg._FX_PROBE_SINK = probe
            V.simulate_one(npz, "1000PEPEUSDC", is_long, cfg, force_initial_seed=True)
            names = probe["_entry_filter_mask_names"]
            masks = probe["_entry_filter_masks"]
            assert len(names) == len(masks), (variant, is_long, len(names), len(masks))
            boundary_indices = []
            close = np.asarray(npz["close"], dtype=float)
            close3 = np.asarray(npz.get("close_3bar_15m", np.zeros(n)), dtype=float)
            valid = np.isfinite(close) & np.isfinite(close3) & (close3 > 0)
            momentum = np.zeros(n, dtype=float)
            momentum[valid] = (close[valid] - close3[valid]) / close3[valid] * 100.0
            threshold = -1.0 if is_long else 1.0
            boundary_indices.extend(
                np.flatnonzero(valid)[
                    np.argsort(np.abs(momentum[valid] - threshold))[:20]
                ]
            )
            channel_key = "dc_high_15m_prev" if is_long else "dc_low_15m_prev"
            if channel_key in npz:
                channel = np.asarray(npz[channel_key], dtype=float)
                channel_valid = np.isfinite(close) & np.isfinite(channel) & (channel > 0)
                boundary_indices.extend(
                    np.flatnonzero(channel_valid)[
                        np.argsort(np.abs(close[channel_valid] - channel[channel_valid]))[:20]
                    ]
                )
            bb = np.asarray(npz.get("bb_pct_b_15m", np.full(n, np.nan)), dtype=float)
            bb_valid = np.isfinite(bb)
            boundary_indices.extend(
                np.flatnonzero(bb_valid)[np.argsort(np.abs(bb[bb_valid] - 0.5))[:20]]
            )
            all_indices = np.unique(np.concatenate((indices, np.asarray(boundary_indices, dtype=int))))
            for index in all_indices:
                snapshot = store.build_indicator_dict(int(index))
                native = vector_entry_mask_snapshot(
                    snapshot,
                    is_long,
                    cfg,
                    float(snapshot["current_price"]),
                    float(npz["timestamps"][index]),
                )
                coverage = compare_mask_coverage(names, native)
                assert coverage["counts_equal"] and coverage["producer_sets_equal"], (
                    variant,
                    is_long,
                    int(index),
                    coverage,
                )
                for name, mask in zip(names, masks):
                    assert native[name] == bool(mask[index]), (
                        variant,
                        is_long,
                        int(index),
                        name,
                        bool(mask[index]),
                        native[name],
                    )
