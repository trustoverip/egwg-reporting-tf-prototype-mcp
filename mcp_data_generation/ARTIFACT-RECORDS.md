# Raw Artifact catalog (candidate acquisition implementation)

The existing ingestion registry contains a SHA-256 `file_hash` and a `processed_at` timestamp. It does **not** generally contain authoritative source identifiers, source URLs, source last-modified times, or an independently observed acquisition timestamp.

Run from `mcp_data_generation` with Python 3.11+:

```sh
python -m src.artifact_records --raw data/raw --registry data/ingestion_registry.json --output data/artifacts
python -m unittest tests.test_artifact_records
```

Use the appropriate existing raw and registry paths for your environment. The command reads every registered file, compares its actual SHA-256 digest with the registry, and writes an artifact catalog outside the raw tree. On any missing file, unsafe path, missing hash, or digest mismatch, it fails before writing records. Each content hash has a single record with an ordered `observations` array so identical bytes collected at multiple paths do not erase evidence of distinct observations. Each `registry_path` is a local registry reference, **not** a source URL.

The export is an **acquisition-side candidate**, not the v2 normalized document migration and not a guarantee of upstream source authenticity. A registry processing timestamp is exposed as `registry_first_processed_at`, never silently promoted to `collected_at`. Missing source evidence remains null/unknown. The catalog does not remove stale output files; do not treat an old output directory as a complete current-state snapshot. Use a fresh isolated output directory for each validation run.

Raw Artifact identity and observation metadata are distinct: the `artifact_id` hashes bytes, while the acquisition-side observation record states the limited facts available from the local registry. Before the catalog is used as an authoritative retrieval lookup, downstream #8 must settle schema compatibility, collection lifecycle, and joining rules with upstream maintainers.
