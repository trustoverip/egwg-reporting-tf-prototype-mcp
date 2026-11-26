# Opt-in Normalized Document v2 producer (candidate)

The default remains `schema_version: 1`. Schema v2 remains an **opt-in, producer-only migration** until the MCP consumer supports v2 artifact resolution; neither this PR nor passing Python CI establishes an end-to-end MCP contract.

## Producer validation and fresh publication

This PR is deliberately stacked on [#32](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/32) so the Python tests call the **actual acquisition catalog exporter**, verify file hashes and exercise the complete catalog→normalization handoff. Merge #32 before #33; once #32 is merged, the shared diff will disappear.

From `mcp_data_generation`:

```sh
python -m src.artifact_records --raw data/raw --registry data/ingestion_registry.json --output data/artifacts
python -m src.curate --processed data/processed --registry data/ingestion_registry.json --artifact-catalog data/artifacts --schema-version 2 --curated data/curated-v2
python -m unittest discover --start-directory tests --top-level-directory . --verbose
```

**Use a fresh, non-existing `--curated` directory for each v2 publication**, including reruns; `--prune` is not applicable to this path. The producer validates the full batch and writes a staging directory alongside the destination. Only a fully generated corpus is renamed to the final output. Missing/contradictory membership, bad JSON, duplicate normalized IDs and unavailable catalog records abort before publication; on staging failure the temporary tree is removed. A `--dry-run` validates without creating the destination. This avoids misleading partially published v2 corpora. The existing v1 incremental processing path is unchanged.

Artifact membership is reconstructed from *explicit registry file-to-processed-document relationships*, never inferred from meeting names. Identical raw bytes deduplicate to one digest; distinct contributed bytes appear in sorted order. Registry processing time is not automatically collection time. A v2 SAID covers normalized content, metadata and sorted artifact references; collection observation metadata remains outside the identity-bearing record.

The regression suite covers the actual exporter-to-producer seam, multiple explicit references, unrelated records, duplicate bytes, deterministic SAIDs, altered bytes, malformed/missing artifact records, invalid/missing registry, late-batch failures, dry runs and existing destination refusal. A synthetic registry association test establishes deterministic membership semantics; it **does not** claim source authentication or demonstrate a production multi-connector acquisition trace.

## Consumer dependency and rollback

**Do not** point today's MCP server at `curated-v2` or reuse a v1 vector index. The future retrieval PR must integrate the join from PR #24, load v2 normalized documents and Raw Artifact records, distinguish resolved/partially resolved/unresolved provenance and test the real `search` and `list_documents` MCP paths. Rebuild index from v2 and exercise queries before retiring v1. See [#8](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/8) and [#26](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26).

Rollback: retain the v1 curated directory and original v1 index. The v2 publication goes to a separate destination and does not rewrite raw source files or v1 curated records; keep running the existing server/index configuration until v2 consumer integration succeeds. Publishing v2 alone neither modifies the MCP runtime nor makes v2 authoritative.

This candidate depends on upstream approval of the proposed [#31](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/31) model and [#32](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/32) acquisition behavior. Upstream reviewers retain the authority to change the schema before adoption.
