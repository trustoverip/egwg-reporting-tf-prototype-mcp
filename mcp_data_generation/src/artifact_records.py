"""Materialize byte-verified Raw Artifact records from the existing ingestion registry.

The registry's processed_at is a first-processing timestamp, not evidence of
when a file was originally collected. This tool does not infer source
authenticity or collection timestamps from local file metadata.
"""

import argparse
import hashlib
import json
from pathlib import Path

ATTRIBUTION = ("authoritative_source", "collector_observation", "local_fallback",
               "manual_entry", "unknown")


def build_catalog(raw_dir: Path, registry: dict) -> dict[str, dict]:
    """Fail closed before writing if any registered source cannot be verified."""
    raw_dir = raw_dir.resolve()
    catalog: dict[str, dict] = {}
    for key, entry in sorted(registry.items()):
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise ValueError("registry must map relative file paths to objects")
        rel = Path(key)
        location = (raw_dir / rel).resolve()
        if rel.is_absolute() or not location.is_relative_to(raw_dir) or location == raw_dir:
            raise ValueError(f"unsafe registry path: {key!r}")
        expected = entry.get("file_hash")
        if (not isinstance(expected, str) or len(expected) != 64 or
                any(c not in "0123456789abcdef" for c in expected)):
            raise ValueError(f"missing or invalid source hash for {key!r}")
        try:
            with location.open("rb") as data:
                digest = hashlib.file_digest(data, "sha256").hexdigest()
        except OSError as exc:
            raise ValueError(f"source bytes unavailable for {key!r}") from exc
        if digest != expected:
            raise ValueError(f"registry/source digest mismatch for {key!r}")
        artifact_id = f"sha256:{digest}"
        source_system = rel.parts[0] if len(rel.parts) > 1 else None
        observation = {
            "registry_path": rel.as_posix(),
            "file_name": location.name,
            "source_system": source_system,
            "source_identifier": None,
            "source_url": None,
            "source_last_modified": None,
            "collected_at": None,
            "registry_first_processed_at": entry.get("processed_at"),
            "provenance_attribution": {
                "artifact_id": "collector_observation",
                "source_system": "local_fallback" if source_system else "unknown",
                "source_identifier": "unknown",
                "source_url": "unknown",
                "source_last_modified": "unknown",
                "collected_at": "unknown",
                "registry_first_processed_at": (
                    "collector_observation" if entry.get("processed_at") else "unknown"
                ),
            },
        }
        record = catalog.setdefault(artifact_id, {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "observations": [],
        })
        record["observations"].append(observation)
    return catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export verified artifact records; raw cache stays read-only")
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    raw_dir = args.raw.resolve()
    output = args.output.resolve()
    if output == raw_dir or output.is_relative_to(raw_dir):
        parser.error("--output must not be within the raw source tree")
    if not raw_dir.is_dir():
        parser.error("--raw must be an existing directory")
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        if not isinstance(registry, dict):
            raise ValueError("registry root must be an object")
        records = build_catalog(raw_dir, registry)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    output.mkdir(parents=True, exist_ok=True)
    for artifact_id, record in sorted(records.items()):
        target = output / (artifact_id.removeprefix("sha256:") + ".json")
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)
    print(f"Materialized {len(records)} byte-verified artifact(s); no source authenticity is asserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
