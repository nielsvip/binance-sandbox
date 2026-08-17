#!/usr/bin/env python3
"""Emit a deterministic exact-replay bundle for one frozen ladder artifact.

Research-only. A causal provenance problem produces a machine-readable blocked
receipt and exit status 2; it never weakens the engine contract or edits live
configuration, symbol universes, canonical NPZ files, or matrix cells.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v8_research_ladder_adapter import emit_replay_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--result",
        type=Path,
        help="result JSON inside the artifact (default: result.json)",
    )
    parser.add_argument(
        "--npz-path",
        type=Path,
        help=(
            "relocated immutable NPZ; accepted only when its SHA-256 matches "
            "the frozen artifact"
        ),
    )
    parser.add_argument("--account", default="trb")
    args = parser.parse_args()
    receipt = emit_replay_bundle(
        args.artifact,
        account=args.account,
        npz_path_override=args.npz_path,
        result_path=args.result,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "READY_FOR_EXACT_ENGINE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
