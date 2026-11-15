# Temporal identity, observation history, supersession and retention

**Status:** Informative assessment of the existing prototype; not a schema proposal,
new API, historical-verification guarantee, or change to the canonical handoffs
in [AGENTS.md](../AGENTS.md). The authoritative design discussions remain
[issue #8](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/8)
and the owning workstreams. The validation baseline and observed failures are
tracked in [issue #26](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26).

## Scope of the current implementation

The prototype indexes **current selected representations** of Zoom and
Confluence material. Its SAIDs identify exact canonical Normalized Document
records, **not** a logical page/meeting across versions. A document's human
source identifier may persist across revisions while its SAID changes. This
distinction is intentional under the ongoing issue #8 contract discussion.

| Field or mechanism | What the implementation establishes | What it does not establish |
| --- | --- | --- |
| `document.date` | The date extracted for the represented meeting/page; for some Confluence pages this is derived from a title or creation date. | An authoritative effective-from/effective-to interval for a decision or policy. |
| Registry `processed_at` | The **earliest recorded ingestion** timestamp associated with known source content, preserved across unchanged or equivalent re-exports. | The latest ingestion time, all observation times, or proof of source collection at that instant. |
| Curated `provenance.collected_at` | Currently inherits `processed_at` when the registry resolves; otherwise falls back to processed-file modification time. It participates in the document SAID. | An independently verified source-side collection event or a reliable history of observations. |
| Proposed Raw Artifact `source_last_modified` in issue #8 | A place to retain a source-system-supplied modification timestamp if collected. | The domain validity of an assertion, completeness of source history, or the time the system learned the fact. |
| Source `source_identifier` | A reference to an originating page/meeting identifier. | A verified globally stable logical-object identity or a historical version identifier. |
| `artifact_id` and document SAID | Content addressing of the corresponding byte sequence or serialized normalized record, subject to verification at consumption. | Temporal continuity, source authenticity, effective-time semantics, or which representation was current at a past time. |
| Coordinated `--prune` operations | Remove no-longer-selected derived records from processed, curated and indexed state, subject to each stage's documented rules. | A retained revision ledger, audit archive, or reproducible historical snapshot. |

The Confluence selection logic deliberately **excludes historical version
rows** from the current ingestion corpus. The source export may contain those
rows, but their presence in raw input does not make them independently
addressable historical records in this prototype. An archived raw export may
allow later reprocessing; the pipeline does not guarantee that it is retained
or that every intermediate observation was captured.

## Separate the four identities

1. **Logical source object:** A continuing meeting or wiki page referenced
   by a source identifier. The prototype does not define cross-observation
   identity rules beyond its source-specific parsing conventions.
2. **Raw source representation:** The exact collected file bytes, potentially
   one of several versions of one logical object. Content hashes identify
   bytes, not the object's continuity.
3. **Normalized representation:** The exact SAID-bearing document, including
   its current provenance fields. Changing any identity-bearing field
   changes the representation's identity.
4. **Observation:** Evidence that a particular representation was available
   to the collector/system at some time. A first-ingested timestamp is not
   an append-only observation history.

Do not substitute a file path, registry key, normalized SAID, or filesystem
modification timestamp for a version-independent logical identity.

## Temporal questions and supported answers

| Question | Current outcome |
| --- | --- |
| What text is indexed now for this query? | Supported as ordinary search, subject to the documented integrity/provenance defects. |
| When is the represented meeting/page dated? | The `document.date` field can be returned with its parser-defined limitations. |
| When was this content first ingested? | Usually recoverable from the registry; not necessarily when the upstream source was collected or last observed. |
| What did the source say at time T? | **Not established:** historical Confluence versions are excluded and historical source snapshots are not indexed. |
| What had this repository observed by time T? | **Not established:** the registry stores latest mappings and a preserved first-seen timestamp, not a complete observation log. |
| Which assertion was valid at T as later understood at T2? | **Not established:** no domain-validity intervals or historical as-of retrieval contract exist. |

A result's `date`, `collected_at`, or SAID must not be presented as proof
that the statement was in force at a particular historical instant. Missing
historical evidence means **unknown**, not false, withdrawn, or never recorded.

## Supersession, pruning and integrity

A content revision may create a new processed SAID while the earlier processed
file remains until pruning. Curated files are keyed by a human-readable
source-derived name, so two distinct processed representations can compete for
one output path. The index may meanwhile continue to hold prior chunks until
it is pruned. None of these behaviors constitute an authoritative
supersession relation or temporal ordering.

The [issue #26 findings](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26)
report stale selection after revisions without coordinated pruning, loss of
provenance when the registry is missing, and retrieval of modified normalized
metadata without SAID verification. These are **present-tense integrity and
lifecycle defects**, not reasons to add bitemporal fields. Correct current
selection, source lineage and record-integrity enforcement before interpreting
retained revisions as historical evidence.

An explicit `--prune` is suitable for a **current-state derived index**.
Whether source evidence or historical normalized versions must be preserved
for audit is a separate retention decision, subject to source permissions,
privacy, licensing, deletion obligations and storage policy. Deleting derived
chunks does not necessarily delete raw evidence; keeping raw evidence does
not automatically create a queryable history.

## Decision gates before any temporal feature

A future proposal should first specify a falsifiable user requirement:

- **Historical source retrieval:** Locate an actual immutable source version
  that existed at T, with provenance and a defensible version relationship.
- **Observation-time replay:** Reconstruct the set of representations the
  repository had observed by T from an immutable, ordered observation record.
- **Bitemporal assertions:** Answer both *valid at T1* and *known at T2*.
  This additionally requires explicitly sourced valid-time assertions; page
  or meeting dates alone are insufficient.

Only after selecting one of those requirements should the owning workstreams
choose the minimal retention mechanism, identity links, source-specific
version handling, and (if necessary) MCP query/response changes. The
canonical models under #8 should **not** be widened speculatively. In
particular, do not silently change a Normalized Document SAID into a logical
source identifier or add mutable status into its identity-bearing payload.

For a proposed historical capability, require at least:

1. A source-diverse fixture with known revisions, delayed observation, source
   deletion or withdrawal, and out-of-order events.
2. Independent verification of immutable evidence and explicit links from
   source object to each captured representation.
3. A defined policy for unavailable, inferred, contradicted, and backdated
   temporal assertions; do not turn these into verified effective times.
4. Explicit current-state versus as-of results, retention limits and negative
   cases preventing stale or missing evidence from looking authoritative.
5. A compatibility and privacy assessment for existing corpus, schema,
   index and MCP consumers.

These are **prospective acceptance questions**, not tests claimed to pass
under the current prototype.

## Workstream ownership and disposition

- **Content Acquisition (#9):** Authoritative source capture time,
  source-reported modification metadata, artifact identity, observation
  retention and version lineage when a historical requirement is accepted.
- **Knowledge Normalization (#10):** Exact representation identity, source
  references and no false substitution of filesystem fallbacks as collection
  evidence.
- **Knowledge Indexing (#11):** Current-state index lifecycle; historical
  indexing only if backed by a retained evidence model.
- **MCP Retrieval (#12):** Query and result time semantics only after
  source and observation contracts exist.
- **Testing & Evaluation (#26):** Reproduce current lifecycle and
  provenance failures without interpreting them as completed temporal support.

**Decision:** Clarify the current temporal contract now. Defer bitemporal
schema, event ledger and as-of API work until an accepted use case and
retention authority justify them. This document changes no runtime
behavior, schemas, or conformance claim.
