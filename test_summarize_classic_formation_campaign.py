import gzip
import json
from pathlib import Path

from tools import summarize_classic_formation_campaign as summary


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def test_compact_luna_input_is_hash_bound_and_side_scoped(tmp_path):
    campaign = tmp_path / summary.CAMPAIGN_REL
    repo_root = Path(__file__).resolve().parent
    for relative in (
        Path("tools/run_classic_formation_universe.py"),
        Path("v8_vec_sweep.py"),
        Path("classic_formations.py"),
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((repo_root / relative).read_bytes())
    rows = []
    # 58 long + 57 short cells over 108 underlyings (seven dual-side).
    keys = [(f"S{i:03d}", "LONG") for i in range(58)] + [
        (f"S{i:03d}", "SHORT") for i in range(51, 108)
    ]
    for index, (symbol, side) in enumerate(keys):
        strategy = 12.0 if side == "LONG" else 6.0
        bh = 8.0 if side == "LONG" else 3.0
        variants = {}
        for variant in summary.EXPECTED_VARIANTS:
            if variant == "baseline":
                family = action = timeframe = None
                gain = 5.0
                action_count = 0
                fingerprint = "base"
            else:
                family, action, timeframe = variant.rsplit("_", 2)
                gain = strategy
                action_count = 1
                fingerprint = f"changed-{variant}"
            variants[variant] = {
                "total_gain_pct": gain,
                "trades": 2 if variant == "baseline" else 3,
                "max_dd_pct": -2.0 if variant == "baseline" else -1.0,
                "formation_action_count": action_count,
                "action_fingerprint": fingerprint,
                "family": family,
                "action": action,
                "timeframe": timeframe,
            }
        rows.append(
            {
                "key": f"{symbol}_{side}",
                "symbol": symbol,
                "side": side,
                "bh_return_pct": bh,
                "variants": variants,
            }
        )
    source_hashes = {
        "runner": summary.sha256(tmp_path / "tools/run_classic_formation_universe.py"),
        "v8_vec_sweep": summary.sha256(tmp_path / "v8_vec_sweep.py"),
        "classic_formations": summary.sha256(tmp_path / "classic_formations.py"),
    }
    source_receipts = {}
    request_id = "classic-formations-test-v4"
    for window in ("train", "holdout"):
        per_key = campaign / window / "per_key_results.json.gz"
        per_key.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(per_key, "wt", encoding="utf-8") as handle:
            json.dump(rows, handle)
        key_names = [row["key"] for row in rows]
        receipt = {
            "request_id": f"{request_id}-{window}",
            "key_count": 115,
            "attempted_key_count": 115,
            "ready_key_count": 115,
            "completed_key_count": 115,
            "failed_key_count": 0,
            "failed_keys": [],
            "missing_npz_keys": [],
            "keys": key_names,
            "key_sha256": summary.stable_hash(key_names),
            "variant_count": 71,
            "variants": list(summary.EXPECTED_VARIANTS),
            "formation_timeframes": list(summary.EXPECTED_TIMEFRAMES),
            "window_start": "2025-01-01",
            "window_end_exclusive": "2025-12-01" if window == "train" else None,
            "direction_contract": {"SHORT_INVERSE_BH": "inverse"},
            "source_sha256": source_hashes,
            "npz_universe_sha256": "same-npz",
            "active_config_overrides_sha256": "same-overrides",
            "config": {"FORMATION_TFS": "15m,1h,4h,D"},
            "matrix_written": False,
            "database_written": False,
            "live_written": False,
            "artifacts": {
                "per_key_results": str(per_key.relative_to(tmp_path)),
                "per_key_results_sha256": summary.sha256(per_key),
            },
        }
        receipt_path = campaign / window / "campaign_receipt.json"
        write_json(receipt_path, receipt)
        source_receipts[window] = {"sha256": summary.sha256(receipt_path)}
    write_json(
        campaign / "CLASSIC_FORMATION_CAMPAIGN_SUMMARY.json",
        {
            "status": "COMPLETE_HOLDOUT_RANKED",
            "request_id": request_id,
            "key_count": 115,
            "underlying_symbols": 108,
            "variant_count_per_window": 71,
            "timeframes": list(summary.EXPECTED_TIMEFRAMES),
            "matrix_written": False,
            "database_written": False,
            "live_config_written": False,
            "receipts": source_receipts,
        },
    )

    payload = summary.build(tmp_path)

    assert payload["status"] == "READY_FOR_LUNA_RANKING"
    assert len(payload["rows"]) == 426
    assert len(payload["paired_effects"]) == 2 * 115 * 70
    long_row = next(
        row
        for row in payload["rows"]
        if row["window"] == "holdout"
        and row["variant"] == "double_top_bottom_entry_15m"
        and row["scope"] == "LONG"
    )
    assert long_row["cells"] == 58
    assert long_row["positive_delta_vs_side_correct_bh_fraction"] == 1.0
    assert (campaign / summary.OUTPUT_NAME).is_file()
