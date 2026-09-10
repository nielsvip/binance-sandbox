#!/usr/bin/env python3
"""
CI hook: prevent /tmp ephemeral loss for audit artifacts.
Checks that durable copies exist and match /tmp sources when /tmp exists.
Run in pre-commit or CI: python3 tools/audit_artifact_guard.py --check
Regenerate/persist: python3 tools/audit_artifact_guard.py --persist
"""
import hashlib
import json
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TMP_TEMPLATE = pathlib.Path("/tmp/template_all_keys.json")
TMP_LIVE = pathlib.Path("/tmp/live_wiring_audit.json")
PERSISTED = [
    REPO / "data/reports/template_all_keys.json",
    REPO / "artifacts/audit/template_all_keys.json",
    REPO / "data/reports/live_wiring_audit.json",
    REPO / "artifacts/audit/live_wiring_audit.json",
]
MANIFEST = REPO / "data/reports/audit_artifacts_manifest.json"


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check() -> int:
    errors = []
    # 1. At least one durable copy per artifact must exist
    for name in ["template_all_keys.json", "live_wiring_audit.json"]:
        candidates = [REPO / f"data/reports/{name}", REPO / f"artifacts/audit/{name}"]
        found = [c for c in candidates if c.exists()]
        if not found:
            errors.append(f"MISSING durable copy for {name}: expected one of {candidates}")
        else:
            for c in found:
                try:
                    json.loads(c.read_text())
                except Exception as e:
                    errors.append(f"INVALID JSON {c}: {e}")
                if c.stat().st_size == 0:
                    errors.append(f"EMPTY {c}")
        # If /tmp source exists, durable must match its checksum
        tmp = pathlib.Path(f"/tmp/{name}")
        if tmp.exists():
            tmp_hash = sha256(tmp)
            for c in found:
                if sha256(c) != tmp_hash:
                    errors.append(
                        f"CHECKSUM MISMATCH {c} sha256={sha256(c)} != /tmp/{name} sha256={tmp_hash} (stale persist)"
                    )

    # 2. Manifest must exist and checksums must match persisted files
    if not MANIFEST.exists():
        errors.append(f"MISSING manifest {MANIFEST}")
    else:
        try:
            m = json.loads(MANIFEST.read_text())
            for key, info in m.get("artifacts", {}).items():
                exp = info.get("sha256")
                for dest in info.get("destinations", []):
                    p = REPO / dest if not pathlib.Path(dest).is_absolute() else pathlib.Path(dest)
                    # also try relative to repo
                    alt = REPO / dest
                    target = p if p.exists() else alt
                    if not target.exists():
                        errors.append(f"MANIFEST destination missing: {dest}")
                    elif sha256(target) != exp:
                        errors.append(f"MANIFEST checksum mismatch {target}: {sha256(target)} != {exp}")
        except Exception as e:
            errors.append(f"INVALID manifest {MANIFEST}: {e}")

    if errors:
        print("AUDIT ARTIFACT GUARD: FAILED", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("\nFix: python3 tools/audit_artifact_guard.py --persist  (re-copies /tmp -> durable)", file=sys.stderr)
        print("Or if /tmp was cleaned, restore from durable: cp data/reports/<artifact> /tmp/<artifact>", file=sys.stderr)
        return 1
    print("AUDIT ARTIFACT GUARD: OK - all durable copies present and checksums match")
    if TMP_TEMPLATE.exists():
        print(f"  /tmp/template_all_keys.json sha256={sha256(TMP_TEMPLATE)} lines={len(TMP_TEMPLATE.read_text().splitlines())}")
    if TMP_LIVE.exists():
        print(f"  /tmp/live_wiring_audit.json sha256={sha256(TMP_LIVE)} lines={len(TMP_LIVE.read_text().splitlines())}")
    for p in PERSISTED:
        if p.exists():
            print(f"  {p.relative_to(REPO)} sha256={sha256(p)}")
    return 0


def persist() -> int:
    copied = 0
    for tmp, name in [(TMP_TEMPLATE, "template_all_keys.json"), (TMP_LIVE, "live_wiring_audit.json")]:
        if not tmp.exists():
            print(f"SKIP {tmp} not found (already ephemeral-lost); durable copy is source of truth", file=sys.stderr)
            continue
        for dest in [REPO / f"data/reports/{name}", REPO / f"artifacts/audit/{name}"]:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp, dest)
            print(f"persisted {tmp} -> {dest} sha256={sha256(dest)}")
            copied += 1
    # regenerate manifest checksums
    if copied:
        import datetime

        def _sha(p):
            return sha256(p) if p.exists() else None

        t = TMP_TEMPLATE if TMP_TEMPLATE.exists() else REPO / "data/reports/template_all_keys.json"
        l = TMP_LIVE if TMP_LIVE.exists() else REPO / "data/reports/live_wiring_audit.json"
        manifest = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "artifacts": {},
            "ephemeral_risk": "/tmp is ephemeral (macOS periodic cleanup, reboot loss); this guard prevents loss",
        }
        if t.exists():
            manifest["artifacts"]["template_all_keys.json"] = {
                "source": str(TMP_TEMPLATE if TMP_TEMPLATE.exists() else t),
                "destinations": ["data/reports/template_all_keys.json", "artifacts/audit/template_all_keys.json"],
                "sha256": _sha(t),
                "bytes": t.stat().st_size,
            }
        if l.exists():
            manifest["artifacts"]["live_wiring_audit.json"] = {
                "source": str(TMP_LIVE if TMP_LIVE.exists() else l),
                "destinations": ["data/reports/live_wiring_audit.json", "artifacts/audit/live_wiring_audit.json"],
                "sha256": _sha(l),
                "bytes": l.stat().st_size,
            }
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=2))
        (REPO / "artifacts/audit/manifest.json").parent.mkdir(parents=True, exist_ok=True)
        (REPO / "artifacts/audit/manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"manifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    if "--persist" in sys.argv:
        sys.exit(persist())
    sys.exit(check())
