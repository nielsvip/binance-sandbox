"""Build an isolated expanded-population NN bundle without changing its base bundle."""

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


CATEGORIES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SHARED_STEMS = ("needed_filters", "switch_profile", "positives_all", "binding_map", "must_pos")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def declared_path(root, name):
    return Path(root) / ("data/brain" if name.startswith("v18_brain_") else "data/learn") / name


def checked_source(run, manifest, relative):
    expected = manifest.get("artifact_sha256", {}).get(relative)
    path = Path(run) / relative
    if not expected or not path.is_file() or sha(path) != expected:
        raise ValueError(f"expanded05 source artifact is missing or hash-mismatched: {relative}")
    return path, expected


def check_bundle(root):
    root = Path(root)
    declaration_path = root / "tools/v18_model_bundle.json"
    declaration = json.loads(declaration_path.read_text())
    expected = declaration.get("expected_sha256_by_filename", {})
    if len(expected) != 32:
        raise ValueError(f"bundle must declare 32 runtime artifacts: {root}")
    for name, digest in expected.items():
        path = declared_path(root, name)
        if not path.is_file() or sha(path) != digest:
            raise ValueError(f"bundle artifact mismatch: {root}/{name}")
    return declaration


def compile_candidate(base_bundle, reference_bundle, training_run, output):
    base_bundle = Path(base_bundle).resolve()
    reference_bundle = Path(reference_bundle).resolve()
    training_run = Path(training_run).resolve()
    output = Path(output).resolve()
    manifest_path = training_run / "manifest.json"
    manifest_sha = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "learner_candidate_built_not_deployed":
        raise ValueError("expanded05 training run is not a completed isolated candidate")
    if manifest.get("missing_context_inputs") or manifest.get("error"):
        raise ValueError("expanded05 manifest reports missing context or an error")
    phases = manifest.get("phases", [])
    if len(phases) < 8 or any(row.get("status") != "complete" or row.get("returncode") != 0 for row in phases):
        raise ValueError("expanded05 training phases are not all complete")
    if manifest.get("cut") != "2026-10-10T00:00:00Z":
        raise ValueError("expanded05 training cutoff differs from reviewed cutoff")
    base_declaration = check_bundle(base_bundle)
    reference_declaration = check_bundle(reference_bundle)
    expected_names = set(base_declaration["expected_sha256_by_filename"])
    if expected_names != set(reference_declaration["expected_sha256_by_filename"]):
        raise ValueError("reference and base candidate bundle declarations differ")
    learned_names = set()
    shared_sha = {}
    for category in CATEGORIES:
        learned_names.update({f"v18_brain_{category}.json", f"digest_{category}.json", f"linknet_proposals_{category}.json"})
    ancillary_names = expected_names - learned_names
    if len(ancillary_names) != 20:
        raise ValueError(f"expected exactly 20 shared ancillary artifacts, found {len(ancillary_names)}")
    for name in sorted(ancillary_names):
        base_path = declared_path(base_bundle, name)
        reference_path = declared_path(reference_bundle, name)
        if sha(base_path) != sha(reference_path):
            raise ValueError(f"base and reference ancillary differ: {name}")
        shared_sha[name] = sha(base_path)
    if output.exists():
        raise FileExistsError(f"fresh output path already exists: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(base_bundle, output)
    declaration_path = output / "tools/v18_model_bundle.json"
    declaration = json.loads(declaration_path.read_text())
    training_sources = {}
    for category in CATEGORIES:
        brain_rel = f"brain/v18_brain_{category}.json"
        digest_rel = f"context/data/learn/digest_{category}.json"
        proposal_rel = f"context/data/learn/linknet_proposals_v2_{category}.json"
        production_rel = f"context/data/learn/linknet/v2/production_{category}.json"
        brain_src, brain_hash = checked_source(training_run, manifest, brain_rel)
        digest_src, digest_hash = checked_source(training_run, manifest, digest_rel)
        proposal_src, proposal_hash = checked_source(training_run, manifest, proposal_rel)
        production_src, production_hash = checked_source(training_run, manifest, production_rel)

        brain_name = f"v18_brain_{category}.json"
        brain_dst = declared_path(output, brain_name)
        shutil.copyfile(brain_src, brain_dst)
        declaration["expected_sha256_by_filename"][brain_name] = brain_hash

        digest_name = f"digest_{category}.json"
        digest_dst = declared_path(output, digest_name)
        prior_digest = json.loads(digest_dst.read_text())
        new_digest = json.loads(digest_src.read_text())
        if not {"proposals", "scorer"}.issubset(new_digest):
            raise ValueError(f"expanded05 digest lacks proposals/scorer: {category}")
        compiled_digest = dict(prior_digest, proposals=new_digest["proposals"], scorer=new_digest["scorer"])
        digest_dst.write_text(json.dumps(compiled_digest, separators=(",", ":")))
        declaration["expected_sha256_by_filename"][digest_name] = sha(digest_dst)

        proposal_name = f"linknet_proposals_{category}.json"
        proposal_dst = declared_path(output, proposal_name)
        shutil.copyfile(proposal_src, proposal_dst)
        declaration["expected_sha256_by_filename"][proposal_name] = proposal_hash

        production = json.loads(production_src.read_text())
        activation_sources = {}
        for rel, digest in manifest["artifact_sha256"].items():
            path = Path(rel)
            if rel.startswith(f"context/data/learn/linknet/v2/") and category in path.name and path.name.startswith(("model_", "ds_", "report_", "production_")):
                checked_source(training_run, manifest, rel)
                activation_sources[path.name] = digest
        declaration["linknet_activation"]["by_category"][category] = {
            "filename": proposal_name,
            "production_scorer": production["production_scorer"],
            "source_artifacts_sha256": activation_sources,
            "source_compiled_filename": proposal_rel.rsplit("/", 1)[-1],
        }
        if "scorer_choices" in declaration:
            declaration["scorer_choices"][category] = production["production_scorer"]
        training_sources[category] = {
            brain_rel: brain_hash,
            digest_rel: digest_hash,
            proposal_rel: proposal_hash,
            production_rel: production_hash,
        }

    declaration.update(
        bundle_id="20261031_candidate_expanded05",
        status="isolated_review_not_submitted",
        compiled_at=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        scope="expanded population05 brain plus digest proposals/scorer and LinkNet v2 compiled proposals; shared 20 ancillary artifacts byte-identical to reference",
        compile_provenance={
            "source_run": str(training_run),
            "source_manifest_sha256": manifest_sha,
            "source_cut": manifest["cut"],
            "source_artifacts_sha256": training_sources,
            "unchanged_shared_ancillary_sha256": shared_sha,
            "status": "isolated_review_not_submitted",
        },
    )
    declaration_path.write_text(json.dumps(declaration, indent=2))
    checked_declaration = check_bundle(output)
    receipt = {
        "status": "ISOLATED_CANDIDATE_BUILT_NOT_SUBMITTED",
        "output": str(output),
        "source_run": str(training_run),
        "source_manifest_sha256": manifest_sha,
        "base_bundle": str(base_bundle),
        "reference_bundle": str(reference_bundle),
        "bundle_declaration_sha256": sha(declaration_path),
        "declared_artifacts": len(checked_declaration["expected_sha256_by_filename"]),
        "updated_learned_artifacts": len(learned_names),
        "unchanged_ancillary_artifacts": len(ancillary_names),
        "unchanged_ancillary_sha256": shared_sha,
        "training_sources_sha256": training_sources,
    }
    receipt_path = output.parent / f"{output.name}_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-bundle", required=True)
    parser.add_argument("--reference-bundle", required=True)
    parser.add_argument("--training-run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(compile_candidate(args.base_bundle, args.reference_bundle, args.training_run, args.out), indent=2))


if __name__ == "__main__":
    main()
