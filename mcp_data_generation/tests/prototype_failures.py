"""Isolated, synthetic reproductions of normalization lifecycle failure modes.

Run after tests.prototype_demo creates the synthetic corpus. No production input
is modified. A reported FAIL is an observed integrity defect, not a passing
assurance assertion. The script exits nonzero for setup or unexpected outcomes.
"""
import argparse
import json
import shutil
import tempfile
from pathlib import Path

from src import curate, ingestion_engine


def docs(directory):
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(directory.glob("*.json"))]


def curate_at(processed, curated, registry):
    result = curate.main(["--processed", str(processed), "--curated", str(curated),
                          "--registry", str(registry), "--quiet"])
    if result != 0:
        raise RuntimeError(f"curation failed: {result}")


def run(output):
    output = output.resolve()
    if not (output / "manifest.json").is_file():
        raise RuntimeError("run tests.prototype_demo first")
    baseline = docs(output / "curated")
    if not baseline or not all(d["provenance"]["artifact_id"] for d in baseline):
        raise RuntimeError("expected verified baseline provenance")
    checks = {}

    with tempfile.TemporaryDirectory(prefix="toip-validation-") as temp:
        base = Path(temp)
        # A missing registry is not equivalent to valid source metadata.
        missing = base / "missing-registry"
        curate_at(output / "processed", missing, base / "nonexistent-registry.json")
        changed = docs(missing)
        missing_artifact = bool(changed) and all(
            not d["provenance"].get("artifact_id") for d in changed)
        changed_identity = {d["identifier"] for d in changed} != {
            d["identifier"] for d in baseline}
        checks["missing_registry_provenance"] = (
            "FAIL: missing registry emitted empty artifact identifiers and changed SAIDs"
            if missing_artifact and changed_identity else
            "INDETERMINATE: expected baseline difference not reproduced")

        # Exercise the actual ingestion->curation entry points after raw-source
        # modification WITHOUT --prune. Preserve the original inputs separately.
        revised = base / "revised"
        raw = revised / "raw"
        processed = revised / "processed"
        registry = revised / "registry.json"
        curated = revised / "curated"
        shutil.copytree(output / "raw", raw)
        shutil.copytree(output / "processed", processed)
        shutil.copy2(output / "registry.json", registry)
        targets = sorted(raw.rglob("*.transcript.vtt"))
        if len(targets) != 1:
            raise RuntimeError("expected exactly one synthetic Zoom transcript")
        marker = "PROTOTYPE_REVISED_SOURCE_CONTENT"
        with targets[0].open("a", encoding="utf-8") as stream:
            stream.write("\n00:00:50.000 --> 00:00:55.000\n" + marker + "\n")
        result = ingestion_engine.main([
            "--raw", str(raw), "--processed", str(processed),
            "--registry", str(registry), "--quiet"])
        if result != 0:
            raise RuntimeError("revision ingestion failed")
        curate_at(processed, curated, registry)
        revised_docs = docs(curated)
        zoom = [d for d in revised_docs
                if d["provenance"]["source_system"].lower() == "zoom"]
        if len(zoom) != 1:
            checks["source_revision_without_prune"] = (
                "INDETERMINATE: expected one curated Zoom document")
        elif marker not in zoom[0]["content"]["text"]:
            checks["source_revision_without_prune"] = (
                "FAIL: superseded content selected after source revision without prune")
        else:
            checks["source_revision_without_prune"] = "PASS: revised content selected"

    report = {
        "scope": "isolated synthetic normalization lifecycle probes on pinned baseline",
        "baseline": json.loads((output / "manifest.json").read_text())["baseline"],
        "checks": checks,
        "attribution": "Independent reproductions informed by ManthanNimodiya's issue #26 report; not his original scripts or logs.",
    }
    (output / "normalization-failures.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if any(x.startswith("INDETERMINATE") for x in checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
