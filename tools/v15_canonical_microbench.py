"""Microbenchmark exact cache keys on saved live full sets and actual row metadata."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import pickle
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.v15_canonical_frame import CanonicalFrame


def main():
    root = Path(__file__).resolve().parents[1]
    sample = root / "data/reports/nextgen_shadow_EDUUSDT_LONG_sample.jsonl"
    patches = {}
    for line in sample.read_text().splitlines():
        row = json.loads(line)
        key = row.get("key")
        switch = row.get("switch") or (str(key).split("=", 1)[0] if key else None)
        cand = row.get("cand", str(key).split("=", 1)[1] if key and "=" in str(key) else None)
        if not switch or cand is None:
            continue
        def parse(value):
            try:
                return ast.literal_eval(str(value))
            except (ValueError, SyntaxError):
                return value
        patch = {switch: parse(cand)}
        label = row.get("filter") or row.get("label") or ""
        if "=" in label:
            name, value = label.split("=", 1)
            patch[name] = parse(value)
        patches[tuple(sorted((k, repr(v)) for k, v in patch.items()))] = patch
    patches = list(patches.values())
    sets = root / "data/live_baseline_snapshots"
    manifest = json.loads((sets / "manifest.json").read_text())
    results = []
    for sym_side in ("EDUUSDT_LONG", "ALGOUSDT_SHORT", "AMD_LONG", "COIN_SHORT"):
        source = sets / f"{sym_side}.json"
        source_bytes = source.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        base = json.loads(source_bytes)
        frame = CanonicalFrame(base, "initial")
        def old_key(patch):
            config = dict(base)
            config.update(patch)
            canonical = tuple(sorted((key, str(value)) for key, value in config.items()))
            return hashlib.blake2b(repr(canonical).encode(), digest_size=16).digest()
        for patch in patches:
            expected = dict(base)
            expected.update(patch)
            canonical = tuple(sorted((key, str(value)) for key, value in expected.items()))
            assert frame.canonical(patch, version="initial") == canonical
            assert frame.compact_key(patch, version="initial", window_days=30, prepared_id=1)[0] == old_key(patch)
            assert pickle.dumps(frame.task_config(patch, version="initial")) == pickle.dumps(expected)
        rounds = {"old_key_with_shallow_merge": [], "new_key_only": [], "old_key_and_task": [], "new_key_and_deepcopied_task": []}
        for _ in range(3):
            for name in rounds:
                started = time.process_time()
                for patch in patches:
                    if name.startswith("old"):
                        old_key(patch)
                        if name == "old_key_and_task":
                            task = dict(base)
                            task.update(patch)
                    else:
                        frame.compact_key(patch, version="initial", window_days=30, prepared_id=1)
                        if name == "new_key_and_deepcopied_task":
                            frame.task_config(patch, version="initial")
                rounds[name].append(time.process_time() - started)
        results.append({"sym_side": sym_side, "base_fields": len(base), "source_sha256": source_sha256, "cases": len(patches), "all_legacy_key_and_task_pickle_equal": True, "median_process_cpu_s_by_mode": {k: statistics.median(v) for k, v in rounds.items()}, "raw_process_cpu_s": rounds})
    print(json.dumps({"tested_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "sample_sha256": hashlib.sha256(sample.read_bytes()).hexdigest(), "live_export_generated_at_utc": datetime.datetime.fromtimestamp(manifest["generated_at"], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "results": results, "limits": ["Microbenchmark only; no board speedup or CPU reduction extrapolated", "Template candidate sanitizer not run; patches are parsed logged row/filter values", "New task construction deep-copies mutable values and is timed separately", "Frozen frame requires explicit generation invalidation on promotion/default changes"]}, indent=2))


if __name__ == "__main__":
    main()
