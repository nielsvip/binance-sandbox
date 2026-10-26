import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time


def guard(root: Path, progress_dir: Path, sym_side: str, expected_md5: str) -> dict:
    if not re.fullmatch(r"[A-Z0-9]+_(LONG|SHORT)", sym_side):
        raise ValueError(f"invalid sym_side: {sym_side}")
    engine = root / "v12_quick_engine.py"
    engine_md5 = hashlib.md5(engine.read_bytes()).hexdigest()
    if engine_md5 != expected_md5:
        raise RuntimeError(f"engine mismatch for {sym_side}: {engine_md5} != {expected_md5}")
    marker_dir = progress_dir / ".engine_vintage"
    marker = marker_dir / f"{sym_side}.json"
    old = json.loads(marker.read_text()) if marker.exists() else {}
    files = [
        progress_dir / f"{sym_side}_v14_progress.json",
        progress_dir / "v15_zero_red" / f"{sym_side}.jsonl",
        progress_dir / "v15_delta_log" / f"{sym_side}_jump.jsonl",
        root / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym_side}_30d_matrix.xlsx",
    ]
    archived = []
    if old.get("engine_md5") != engine_md5 and any(path.exists() for path in files):
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        archive = progress_dir / ".engine_vintage_archive" / f"{stamp}_{sym_side}_{old.get('engine_md5') or 'unmarked'}"
        archive.mkdir(parents=True, exist_ok=False)
        for path in files:
            if not path.is_file():
                continue
            rel = (Path("progress") / path.relative_to(progress_dir)) if path.is_relative_to(progress_dir) else (Path("workbook") / path.name)
            dest = archive / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            record = {"from": str(path), "to": str(dest), "md5": hashlib.md5(path.read_bytes()).hexdigest(), "mtime": path.stat().st_mtime}
            shutil.move(str(path), str(dest))
            archived.append(record)
        (archive / "manifest.json").write_text(json.dumps({"sym_side": sym_side, "old_engine_md5": old.get("engine_md5"), "new_engine_md5": engine_md5, "files": archived}, indent=2))
    marker_dir.mkdir(parents=True, exist_ok=True)
    fresh = marker.with_suffix(".json.tmp")
    fresh.write_text(json.dumps({"sym_side": sym_side, "engine_md5": engine_md5, "written_at": time.time()}, sort_keys=True))
    os.replace(fresh, marker)
    return {"sym_side": sym_side, "engine_md5": engine_md5, "archived": len(archived)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sym-side", required=True)
    parser.add_argument("--progress-dir", required=True)
    parser.add_argument("--expected-md5", required=True)
    args = parser.parse_args()
    print(json.dumps(guard(Path(__file__).resolve().parents[1], Path(args.progress_dir), args.sym_side, args.expected_md5)), flush=True)


if __name__ == "__main__":
    main()
