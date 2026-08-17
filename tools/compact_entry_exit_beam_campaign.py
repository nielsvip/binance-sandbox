#!/usr/bin/env python3
"""Create a compact, human-readable digest of a large beam campaign JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import run_entry_exit_beam_campaign as beam


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_family": row["entry_family"],
        "entry_artifact": row["entry_artifact"],
        "exit_family": row["exit_family"],
        "exit_params": row["exit_params"],
        "discovery_fold_gate_pass": row["discovery_fold_gate_pass"],
        "discovery_strict_fold_count": row["discovery_strict_fold_count"],
        "discovery_all_folds_strict": row["discovery_all_folds_strict"],
        "discovery_fold_evidence": row["discovery_fold_evidence"],
        "untouched_final_validation": row["untouched_final_validation"],
        "all_folds_strict": row["all_folds_strict"],
    }


def compact(source_path: Path) -> dict[str, Any]:
    raw_sha = _sha(source_path)
    source = json.loads(source_path.read_text())
    results = []
    for row in source["results"]:
        beam = [_candidate(item) for item in row["frozen_exit_beam"]]
        results.append(
            {
                "symbol": row["symbol"],
                "side": row["side"],
                "entry_family": row["entry_family"],
                "entry_artifact": row["entry_artifact"],
                "artifact": row["artifact"],
                "candidate_count": row["candidate_count"],
                "frozen_exit_beam": beam,
                "strict_survivor_count": sum(
                    item["all_folds_strict"] for item in beam
                ),
            }
        )
    discovery_all = sum(
        item["discovery_all_folds_strict"]
        for row in results
        for item in row["frozen_exit_beam"]
    )
    validation_strict = sum(
        item["untouched_final_validation"]["strict"]
        for row in results
        for item in row["frozen_exit_beam"]
    )
    all_strict = sum(
        item["all_folds_strict"]
        for row in results
        for item in row["frozen_exit_beam"]
    )
    return {
        "tier": "VEC_ENTRY_EXIT_DISCOVERY_BEAM_COMPACT",
        "campaign_id": source["campaign_id"],
        "created_utc": source["created_utc"],
        "contract": source["contract"],
        "source_archive": {
            "raw_path": str(source_path),
            "raw_sha256": raw_sha,
            "raw_bytes": source_path.stat().st_size,
            "gzip_path": str(source_path) + ".gz",
            "gzip_sha256": None,
            "gzip_bytes": None,
        },
        "counts": {
            "keys": len(source["keys"]),
            "entry_schedules": len(results),
            "candidate_rows": sum(r["candidate_count"] for r in results),
            "frozen_beam_rows": sum(
                len(r["frozen_exit_beam"]) for r in results
            ),
            "discovery_all_folds_strict": discovery_all,
            "untouched_final_strict": validation_strict,
            "all_folds_strict": all_strict,
            "exact_replay_queue": len(source["exact_replay_queue"]),
            "path_fleet_rows_appended": source[
                "path_fleet_rows_appended"
            ],
            "errors": len(source["errors"]),
        },
        "selected_entry_schedules": source["selected_entry_schedules"],
        "results": results,
        "exact_replay_queue": source["exact_replay_queue"],
        "errors": source["errors"],
    }


def compact_from_artifacts(
    source_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    """Rebuild only the frozen top beam, never loading the raw monolith."""
    raw_sha = _sha(source_path)
    results = []
    for entry_dir in sorted(artifact_root.glob("*/*")):
        generic_path = entry_dir / "generic" / "result.json"
        e05_path = entry_dir / "e05" / "result.json"
        peak_path = entry_dir / "peak" / "result.json"
        if not all(path.exists() for path in (generic_path, e05_path, peak_path)):
            continue
        generic = json.loads(generic_path.read_text())
        symbol = str(generic["symbol"]).upper()
        side = str(generic["side"]).upper()
        entry_artifact = generic["source_artifact"]
        source_entry = json.loads(
            (Path(entry_artifact) / "result.json").read_text()
        )
        entry_family = str(
            source_entry["manifest"].get("family", "ENTRY_LADDER_GREEN")
        )
        entry = {
            "symbol": symbol,
            "side": side,
            "family": entry_family,
            "artifact": entry_artifact,
        }
        candidates = []
        for adapter, path in (
            ("generic", generic_path),
            ("e05", e05_path),
            ("peak", peak_path),
        ):
            payload = generic if adapter == "generic" else json.loads(
                path.read_text()
            )
            for candidate in payload["candidates"]:
                if candidate["family"] not in beam.EXIT_TO_PATH:
                    continue
                candidates.append(
                    beam._candidate_discovery_summary(
                        candidate, adapter, entry
                    )
                )
        candidates.sort(key=beam._exit_rank)
        frozen = [
            _candidate(beam._public_candidate(row, True))
            for row in candidates[:8]
        ]
        results.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_family": entry_family,
                "entry_artifact": entry_artifact,
                "artifact": str(entry_dir),
                "candidate_count": len(candidates),
                "frozen_exit_beam": frozen,
                "strict_survivor_count": sum(
                    item["all_folds_strict"] for item in frozen
                ),
            }
        )
        del candidates, generic
    discovery_all = sum(
        item["discovery_all_folds_strict"]
        for row in results
        for item in row["frozen_exit_beam"]
    )
    validation_strict = sum(
        item["untouched_final_validation"]["strict"]
        for row in results
        for item in row["frozen_exit_beam"]
    )
    all_strict = sum(
        item["all_folds_strict"]
        for row in results
        for item in row["frozen_exit_beam"]
    )
    return {
        "tier": "VEC_ENTRY_EXIT_DISCOVERY_BEAM_COMPACT",
        "campaign_id": artifact_root.name,
        "contract": {
            "entry_overlay_blending": False,
            "selection_uses_discovery_only": True,
            "untouched_final_revealed_after_freeze": True,
            "every_fold_tim_gate_pct": [70.0, 80.0],
            "bh_capital_usd": 2000.0,
            "strategy_capacity_usd": 16000.0,
            "promotion_allowed": False,
        },
        "source_archive": {
            "raw_path": str(source_path),
            "raw_sha256": raw_sha,
            "raw_bytes": source_path.stat().st_size,
            "gzip_path": str(source_path) + ".gz",
            "gzip_sha256": None,
            "gzip_bytes": None,
        },
        "counts": {
            "keys": len({(r["symbol"], r["side"]) for r in results}),
            "entry_schedules": len(results),
            "candidate_rows": sum(r["candidate_count"] for r in results),
            "frozen_beam_rows": sum(
                len(r["frozen_exit_beam"]) for r in results
            ),
            "discovery_all_folds_strict": discovery_all,
            "untouched_final_strict": validation_strict,
            "all_folds_strict": all_strict,
            "exact_replay_queue": all_strict,
            "path_fleet_rows_appended": len(results),
            "errors": 0,
        },
        "selected_entry_schedules": [
            {
                "symbol": row["symbol"],
                "side": row["side"],
                "family": row["entry_family"],
                "artifact": row["entry_artifact"],
            }
            for row in results
        ],
        "results": results,
        "exact_replay_queue": [],
        "errors": [],
    }


def markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    lines = [
        f"# Entry/exit beam compact digest — {payload['campaign_id']}",
        "",
        f"- Keys: {counts['keys']}",
        f"- Frozen entry schedules: {counts['entry_schedules']}",
        f"- Vector exit candidates: {counts['candidate_rows']}",
        f"- Discovery-all-fold strict beam rows: "
        f"{counts['discovery_all_folds_strict']}",
        f"- Untouched-final strict beam rows: "
        f"{counts['untouched_final_strict']}",
        f"- Every-fold strict survivors / exact queue: "
        f"{counts['all_folds_strict']} / {counts['exact_replay_queue']}",
        f"- Fleet rows appended: {counts['path_fleet_rows_appended']}",
        "",
        "| key | entry | exit | discovery gates | final return / B&H / "
        "control | final TIM | final strict |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for result in payload["results"]:
        for row in result["frozen_exit_beam"]:
            if not (
                row["discovery_all_folds_strict"]
                or row["untouched_final_validation"]["strict"]
            ):
                continue
            final = row["untouched_final_validation"]["fold_evidence"]
            lines.append(
                f"| {result['symbol']}_{result['side']} | "
                f"{row['entry_family']} | {row['exit_family']} | "
                f"{row['discovery_fold_gate_pass']} | "
                f"{final['strategy_return_pct']:.2f}% / "
                f"{final['bh_return_pct']:.2f}% / "
                f"{final['same_entry_e02_return_pct']:.2f}% | "
                f"{final.get('weighted_tim_pct', final.get('tim_pct')):.2f}% | "
                f"{row['untouched_final_validation']['strict']} |"
            )
    archive = payload["source_archive"]
    lines += [
        "",
        "## Raw archive provenance",
        "",
        f"- Raw path before gzip: `{archive['raw_path']}`",
        f"- Raw SHA-256: `{archive['raw_sha256']}`",
        f"- Raw bytes: {archive['raw_bytes']}",
        f"- Gzip path: `{archive['gzip_path']}`",
        "",
    ]
    return "\n".join(lines)


def finalize_gzip(compact_path: Path, md_path: Path) -> None:
    payload = json.loads(compact_path.read_text())
    gzip_path = Path(payload["source_archive"]["gzip_path"])
    if not gzip_path.exists():
        raise FileNotFoundError(gzip_path)
    payload["source_archive"]["gzip_sha256"] = _sha(gzip_path)
    payload["source_archive"]["gzip_bytes"] = gzip_path.stat().st_size
    compact_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    md_path.write_text(markdown(payload) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path)
    ap.add_argument("--artifact-root", type=Path)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    ap.add_argument("--finalize-gzip", action="store_true")
    args = ap.parse_args()
    if args.finalize_gzip:
        finalize_gzip(args.json_out, args.md_out)
    else:
        if args.source is None:
            ap.error("--source required unless --finalize-gzip")
        payload = (
            compact_from_artifacts(args.source, args.artifact_root)
            if args.artifact_root is not None
            else compact(args.source)
        )
        args.json_out.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        )
        args.md_out.write_text(markdown(payload) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
