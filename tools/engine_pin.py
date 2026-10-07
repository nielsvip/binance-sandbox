#!/usr/bin/env python3
"""engine_pin — composite engine identity for the Monday T0 pin (no-freeze plan Phase 2b).

engine_md5 = md5hex over sorted "path=md5" lines of: v12_quick_engine.py + vec_decisions/*.py
(excluding test_*) + wt_dc_entry_scorer_vec.py + wt_dc_entry_scorer.py. A fixed file list would
rot (lanes keep landing modules); the glob + exclusion rule is the documented basis.
Cutover: Monday T0 (composite computed against fleet bytes, written to CURRENT.json with
engine_md5_basis). Until then CURRENT.engine_md5 stays the v12 file md5.
"""
import glob
import hashlib
import os
import sys

BASIS = ["v12_quick_engine.py", "wt_dc_entry_scorer_vec.py", "wt_dc_entry_scorer.py"]
GLOB = "vec_decisions/*.py"


def basis_files(root):
    files = [f for f in BASIS if os.path.exists(os.path.join(root, f))]
    for g in sorted(glob.glob(os.path.join(root, GLOB))):
        base = os.path.basename(g)
        if base.startswith("test_") or base.startswith("__"):
            continue
        files.append(os.path.relpath(g, root))
    return sorted(set(files))


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def composite(root):
    manifest = []
    for rel in basis_files(root):
        manifest.append(f"{rel}={file_md5(os.path.join(root, rel))}")
    body = "\n".join(manifest) + "\n"
    return hashlib.md5(body.encode()).hexdigest(), manifest


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    digest, manifest = composite(root)
    print(f"files: {len(manifest)}")
    print(f"composite: {digest}")
    if "--manifest" in sys.argv:
        print("".join(l + "\n" for l in manifest), end="")


if __name__ == "__main__":
    main()
