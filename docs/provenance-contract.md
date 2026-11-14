# Provenance and identity handoff: proposed schema v2

**Status:** implementation proposal for [upstream issue #8](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/8), not an assertion that v2 has been implemented or approved. The deployed/implemented schema v1 remains described in [AGENTS.md](../AGENTS.md). Content Acquisition (#9), Knowledge Normalization (#10), Knowledge Indexing (#11), and MCP Retrieval (#12) retain ownership of their respective models. This document records candidate interoperable contracts, not independent assurance of source authenticity.

## Identity and authority boundaries

1. **Raw Artifact:** exactly one immutable collected file (one byte sequence). `artifact_id` is the lowercase `sha256:` prefix followed by 64 lowercase hexadecimal digits of SHA-256 over the original file bytes. The digest establishes byte identity against the recorded digest, not who published the bytes. Two file observations with identical bytes may share `artifact_id` even if their source locations or collection times differ. Source-object grouping (for example Zoom meeting identifiers) is separate and is not part of the artifact content digest. Distinct observations of the same bytes require separate observation metadata if retained.
2. **Normalized Document:** a source-independent derived record whose `identifier` is a URN SAID over the **complete serialized canonical record**, including `schema_version`, all document metadata, normalized content, and ordered provenance references, with `identifier` set to the project's reserved URN placeholder during digest computation. It identifies the exact canonical record, not a logical meeting or webpage across revisions. Existing `py_saidify_urn` serialization and Blake3-256 qualification are normative for this prototype pending independent interoperability vectors. Key order and array order therefore matter; producers must use the shared canonical construction and verification utility, not arbitrary JSON serialization.
3. **Embedded Chunk:** references the parent by `document_id`, has stable chunk ordinal/ID within a specific document and chunking strategy, and duplicates only metadata required to filter before ranking. It does not contain `artifact_ids`; no embedding vector appears in chunk JSON.
4. **Vector Index Entry:** points to a chunk and is rebuildable. Neither presence in the index nor similarity score establishes source integrity, attribution, or authority.
5. **Search Result:** created at retrieval time, not persisted as provenance authority. Resolves `Chunk → Normalized Document → Raw Artifact` and returns source attribution with explicit resolution state.

## Candidate v2 Raw Artifact / observation contract

The artifact record names file bytes. The fields accompanying an acquisition observation describe a *reported* source and collection, not an authenticated source claim:

```json
{
  "schema_version": 1,
  "artifact_id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "observations": [
    {
      "registry_path": "zoom/recording.vtt",
      "file_name": "recording.vtt",
      "source_system": "zoom",
      "source_identifier": null,
      "source_url": null,
      "source_last_modified": null,
      "collected_at": null,
      "registry_first_processed_at": "2026-10-01T15:05:23Z",
      "provenance_attribution": {
        "artifact_id": "collector_observation",
        "source_system": "local_fallback",
        "source_identifier": "unknown",
        "source_url": "unknown",
        "source_last_modified": "unknown",
        "collected_at": "unknown",
        "registry_first_processed_at": "collector_observation"
      }
    }
  ]
}
```

This is an acquisition metadata record *about* the raw bytes, not a replacement for those bytes. The example follows the proposed acquisition-side export in [PR #32](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/32): a single digest-keyed record may preserve multiple distinct observations. The registry path is a local collection reference, not a source-system identifier; the registry's first-processing timestamp is not a collection timestamp. Source identifiers and source URLs must remain unknown when no acquisition evidence exists. This format is a candidate pending upstream review, not an assertion of complete evidence. An implementation must make the bytes reachable for integrity verification or explicitly report them unavailable. It must not silently equate a filename, collection folder or a source-object identifier with the content hash. Metadata must not be silently overwritten when observations differ; preservation/retention of multiple observations is a separate acquisition lifecycle decision. `schema_version: 1` here versions the newly introduced Raw Artifact metadata format independently of normalized schema v2.

Allowed attribution categories: `authoritative_source` (reported directly by the designated source interface, without independent authenticity verification), `collector_observation` (recorded by acquisition), `local_fallback` (inferred from local filesystem or other fallback), `manual_entry` (human supplied), and `unknown` (origin absent or indeterminate). Attribution states **where a value came from**, not whether that value was verified. A missing value uses `null` with `unknown`; an empty string must not masquerade as successful attribution. Future verification claims require separate property, method, and evidence references.

## Candidate Normalized Document v2

```json
{
  "schema_version": 2,
  "identifier": "urn:said:E...",
  "document": {
    "project": "Trust over IP",
    "organizational_unit": ["Ecosystem Governance Working Group"],
    "title": "Meeting",
    "date": "2026-10-01"
  },
  "provenance": {
    "artifact_ids": ["sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
  },
  "content": { "content_type": "Transcript", "text": "..." }
}
```

The example identifier above is illustrative, **not** an executable SAID vector. The implementation PR must publish a reproducible valid vector generated by the canonical utility and a negative vector that fails verification.

The `artifact_ids` set must name **actual contributing source files consumed by normalization**, not every file associated with the same meeting or source object. Producers must deduplicate and lexicographically sort identifiers before SAID computation; at least one valid artifact reference is required for a fully traced v2 record. When contributing source bytes/registry associations cannot be established, producers must not invent an artifact reference. They must report the record as incomplete or reject canonical v2 emission under a documented failure mode. No conversion of a missing v1 `artifact_id` into `[""]`.

A changed normalized text, metadata value, provenance reference, or identity-bearing collection representation results in a different SAID. Attribution and observation metadata retained **outside** the v2 Normalized Document are not included in its SAID: changes to that external metadata do not mutate the normalized record's content identity. This is an explicit departure from v1, where source information and `collected_at` are embedded inside the SAID-bearing record. Collection observations are not a historical/as-of source of truth merely because they are retained.

## Retrieval and failure contract

- **resolved:** parent Normalized Document identifier is consistent and each required referenced Raw Artifact record resolves; any asserted byte-integrity check is independently performed and reported, not inferred from the reference.
- **partially_resolved:** parent resolves but at least one artifact record or required attribution is missing or inconsistent. Content may be returned with a machine-readable incomplete-provenance indication; complete source attribution must not be asserted.
- **unresolved:** parent record is missing, its claimed identity cannot be established, or it does not match the chunk reference. Do not emit an ordinary source-resolved Search Result.
- **unknown/unavailable:** distinct from a successful resolution or verification result. Resolution status is not the same as source authenticity.

Search Results may include `provenance.artifact_ids` derived **only** from the joined parent Normalized Document, never independently copied into chunks. Preserve existing human-readable `source` fields where evidence supports them. The retrieval layer must not remove v1 source fields before an implemented artifact record lookup exists. Reuse the work in [PR #24](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/24) where accepted rather than introducing a parallel resolution mechanism.

## Migration and compatibility gates

The current production handoff is `NormalizedDocument.schema_version: 1`, with a scalar `provenance.artifact_id` and source fields. Replacing that with `artifact_ids` **changes every affected SAID**, requires re-curation, invalidates derived chunk IDs, and requires an index rebuild. It is a semantic and wire-format breaking change even if some consumers still parse JSON. Candidate strategy: explicit migration/regeneration plus rebuild rather than indefinite dual-format runtime support; independent v1 consumers may require a separate transition decision.

The migration must (a) record baseline input and source hashes; (b) preserve existing v1 artifacts without rewriting raw bytes; (c) reconstruct membership only from actual recorded processing relationships; (d) report missing or contradictory provenance rather than fabricate it; (e) regenerate valid v2 SAIDs and derived indexes; (f) exercise MCP `search` and `list_documents` through the real consumer path; and (g) declare failure/rollback instructions. Keep v1 runtime acceptance until producers and consumers transition together.

**Acceptance evidence:** repeatable hash and SAID vectors, wrong-byte negative test, multiple-file membership tests, deterministic ordering, missing registry and missing parent cases, partially resolved artifact case, end-to-end retrieval test, migration/rebuild instructions, Python and TypeScript test/build results, and required DCO/CI checks. Findings tracked in [#26](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26) establish the broader prototype validation separately; #8 closure does not assert retrieval quality or production readiness.

## Non-goals

No claim of authenticated Zoom/Confluence origin, external historical verification, bitemporal storage, production certification, generalized evidence-scoring framework, new connectors, or redefinition of the agreed workstream boundaries. Existing upstream discussions and maintainers remain authoritative for final approval.
