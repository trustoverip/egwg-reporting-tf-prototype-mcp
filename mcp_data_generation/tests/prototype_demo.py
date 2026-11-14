"""Synthetic end-to-end corpus generator and normalized-document validation.

Run from mcp_data_generation as:
  python -m tests.prototype_demo --output ../.prototype-demo

Uses the repository's existing fixture builder; no external ToIP corpus, accounts,
or meeting transcripts are embedded in the repository or published as artifacts.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from src import curate, ingestion_engine
from src.py_saidify import py_verify_urn
from tests.fixtures import CacheFixture


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(output: Path):
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"refusing non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    fixture = CacheFixture()
    fixture.setUp()
    try:
        fixture.write_zoom(fixture.raw / "zoom" / "example" / "governance")
        fixture.write_export(fixture.raw / "confluence" / "example.invalid" / "HOME-CSV")
        raw = output / "raw"
        shutil.copytree(fixture.raw, raw)
    finally:
        fixture.doCleanups()

    processed, curated = output / "processed", output / "curated"
    registry = output / "registry.json"
    args = ["--raw", str(raw), "--processed", str(processed), "--registry", str(registry), "--quiet"]
    if ingestion_engine.main(args) != 0:
        raise RuntimeError("ingestion returned non-zero")
    first_registry = registry.read_bytes()
    first_ids = {p.name: digest(p) for p in processed.glob("*.json")}
    if not first_ids:
        raise AssertionError("no processed documents")
    if ingestion_engine.main(args) != 0:
        raise RuntimeError("repeated ingestion returned non-zero")
    if first_registry != registry.read_bytes() or first_ids != {
        p.name: digest(p) for p in processed.glob("*.json")
    }:
        raise AssertionError("unchanged input changed registry or processed documents")

    curate_args = ["--processed", str(processed), "--curated", str(curated),
                   "--registry", str(registry), "--quiet"]
    if curate.main(curate_args) != 0:
        raise RuntimeError("curation returned non-zero")
    documents = [json.loads(p.read_text(encoding="utf-8"))
                 for p in sorted(curated.glob("*.json"))]
    if len(documents) < 3:
        raise AssertionError("expected at least two Confluence and one Zoom document")
    systems = {d["provenance"]["source_system"].lower() for d in documents}
    if systems != {"zoom", "confluence"}:
        raise AssertionError(f"missing source diversity: {systems}")
    ids = set()
    for doc in documents:
        identifier = doc["identifier"]
        if identifier in ids or not py_verify_urn(doc, identifier, "identifier"):
            raise AssertionError("duplicate or invalid curated document SAID")
        ids.add(identifier)
        for key in ("artifact_id", "source_identifier", "source_system"):
            if not doc["provenance"].get(key):
                raise AssertionError(f"missing {key} in {identifier}")
    curated_hashes = {p.name: digest(p) for p in curated.glob("*.json")}
    if curate.main(curate_args) != 0:
        raise RuntimeError("repeated curation returned non-zero")
    if curated_hashes != {p.name: digest(p) for p in curated.glob("*.json")}:
        raise AssertionError("unchanged curation changed output")

    raw_files = sorted(p for p in raw.rglob("*") if p.is_file())
    manifest = {
        "fixture_kind": "synthetic; not an evaluation of real ToIP knowledge",
        "baseline": "7c35c6ba2dde40bd451bafe03685748167299271",
        "inputs": [{"path": p.relative_to(raw).as_posix(), "sha256": digest(p)}
                   for p in raw_files],
        "normalized_documents": len(documents),
        "source_systems": sorted(systems),
        "saids_verified": len(ids),
        "ingestion_rerun": "unchanged",
        "curation_rerun": "unchanged",
        "curated_sha256": curated_hashes,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.output)
