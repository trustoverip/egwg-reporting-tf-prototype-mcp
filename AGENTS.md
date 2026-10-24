# AGENTS.md

## Purpose

This repository is organized around the lifecycle of organizational knowledge.

Contributors should think in terms of architectural workstreams rather than current repository folders. Folder structure may evolve over time; workstream responsibilities should remain stable.

Before implementing a change:

1. Determine which workstream owns the change.
2. Determine which data model the change affects.
3. Implement the change within the owning workstream.
4. Avoid introducing responsibilities that belong to another workstream.

---

# Architectural Workstreams

## 1. Content Acquisition

Purpose:

Collect and preserve content from source systems.

Examples:

- Zoom collection
- Confluence collection
- GitHub collection
- YouTube collection
- Source synchronization
- Incremental updates

Owns:

- Raw source artifacts
- Source-specific connectors
- Collection metadata

Produces:

- Raw Artifact

Does Not Own:

- Normalization
- Chunking
- Embeddings
- MCP tools

---

## 2. Knowledge Normalization

Purpose:

Transform source-specific content into a canonical source-independent representation.

Examples:

- Source parsers
- Metadata inheritance
- Validation
- SAID generation
- Canonical document schema

Owns:

- Normalized Document

Produces:

- Normalized Document

Consumes:

- Raw Artifact

Does Not Own:

- Source collection
- Embeddings
- Vector indexes
- MCP APIs

---

## 3. Knowledge Indexing

Purpose:

Prepare normalized knowledge for semantic retrieval.

Examples:

- Chunking
- Embedding generation
- Vector index creation
- Reindexing
- Retrieval quality evaluation

Owns:

- Embedded Chunk
- Vector Index Entry

Consumes:

- Normalized Document

Produces:

- Embedded Chunk
- Vector Index Entry

Does Not Own:

- Source collection
- Source parsing
- MCP tool design

---

## 4. MCP Retrieval Layer

Purpose:

Expose indexed knowledge through MCP tools.

Examples:

- Search tools
- Metadata filtering
- Document discovery
- Response formatting

Owns:

- Search Result

Consumes:

- Vector Index Entry
- Embedded Chunk
- Normalized Document

Produces:

- Search Result

Does Not Own:

- Source ingestion
- Normalization
- Embedding generation
- Chunking

---

## 5. Testing & Evaluation

Purpose:

Validate system quality and retrieval effectiveness.

Examples:

- Retrieval testing
- Benchmark datasets
- Regression testing
- Evaluation workflows
- Development utilities
- Diagnostic tools

May interact with all workstreams but should not own production data models.

---

# Canonical Data Models

The platform uses explicit data-model handoffs between workstreams.

Each workstream owns specific models and is responsible only for the transformations within its scope.

The models below will be the architectural source of truth for information flowing through the platform.
Currently this is under development in https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/8

---

## Raw Artifact

System of record containing collected source content and source provenance.

Owned by:

```text
Content Acquisition
```

```json
{
  "artifact_id": "sha256:abc123...",

  "source_system": "Zoom",
  "source_identifier": "GMT20261001-145557",

  "source_url": "",
  "source_last_modified": "",
  "collected_at": "",

  "raw_files": [
    "GMT20261001-145557_Recording.transcript.vtt",
    "GMT20261001-145557_Recording.cc.vtt",
    "GMT20261001-145557_RecordingnewChat.txt"
  ]
}
```

Responsibilities:

- Preserve source content exactly as collected.
- Record collection metadata and provenance.
- Serve as the authoritative system of record.
- Remain independent of downstream normalization and retrieval concerns.

---

## Normalized Document

Canonical source-independent representation used throughout the platform.

Owned by:

```text
Knowledge Normalization
```

```json
{
  "schema_version": 1,

  "identifier": "urn:said:xyz",

  "document": {
    "project": "Trust over IP",
    "organizational_unit": [
      "Ecosystem Governance Working Group"
    ],
    "title": "Ecosystem Governance Working Group Meeting",
    "date": "2026-10-01"
  },

  "provenance": {
    "artifact_id": "sha256:abc123..."
  },

  "content": {
    "content_type": "Transcript",
    "text": "..."
  }
}
```

Responsibilities:

- Provide a canonical representation regardless of source system.
- Receive a persistent identifier (SAID).
- Preserve links back to source provenance.
- Act as the input to Knowledge Indexing.

---

## Embedded Chunk

Minimal searchable unit derived from a normalized document.

The Embedded Chunk intentionally carries fields such as date, content_type, and organizational_unit for filtering. This is selective propagation of retrieval-essential metadata rather than duplication of full provenance.

Owned by:

```text
Knowledge Indexing
```

```json
{
  "schema_version": 1,

  "chunk_id": "urn:said:xyz#chunk-001",

  "document_id": "urn:said:xyz",

  "chunk_index": 1,

  "content_type": "Transcript",
  "date": "2026-10-01",

  "organizational_unit": [
    "Ecosystem Governance Working Group"
  ],

  "text": "...",

  "embedding_model": "bge-small-en-v1.5"
}
```

Responsibilities:

- Store searchable text units.
- Support semantic retrieval.
- Contain metadata required for filtering.
- Reference the parent document.
- Remain independent of vector-index implementation details.

---

## Search Result

Retrieval-time representation returned to MCP clients.

Owned by:

```text
MCP Retrieval Layer
```

```json
{
  "schema_version": 1,

  "score": 0.92,

  "chunk_id": "urn:said:xyz#chunk-001",
  "document_id": "urn:said:xyz",

  "title": "Ecosystem Governance Working Group Meeting",

  "project": "Trust over IP",

  "organizational_unit": [
    "Ecosystem Governance Working Group"
  ],

  "date": "2026-10-01",

  "source": {
    "system": "Zoom",
    "identifier": "GMT20261001-145557",
    "url": ""
  },

  "content_type": "Transcript",

  "text": "matching passage..."
}
```

Responsibilities:

- Present retrieval results to MCP clients.
- Combine search relevance with document metadata.
- Provide sufficient provenance for users to locate the original source.
- Remain a retrieval-time construct rather than a persisted source-of-truth object.

---

## Vector Index

The vector index is a derived implementation artifact owned by Knowledge Indexing.

Unlike the models above, the vector index is not considered a source-of-truth business object.

The vector index:

- Is generated entirely from Embedded Chunks.
- Stores embeddings and retrieval-specific metadata.
- May be implemented using different vector technologies over time.
- Can be deleted and rebuilt from upstream data models.
- Must not become the authoritative source for document metadata or provenance.

Architecturally, the vector index exists to support search and retrieval and should be treated as a cacheable, rebuildable representation of indexed knowledge rather than a canonical data model.

---

# Data Flow

The intended flow of information is:

```text
Raw Artifact
    ↓
Normalized Document
    ↓
Embedded Chunk
    ↓
Vector Index Entry

Search Result
    ↑
Retrieval-time join
```

Information should move forward through the pipeline.

Avoid bypassing intermediate layers.

---

# Source of Truth Rules

Raw Artifact is the source of truth for collected content.

Normalized Document is the source of truth for normalized knowledge.

Embedded Chunk is the source of truth for retrieval units.

Vector Index Entry is a derived artifact and can be rebuilt at any time.

Search Result is a retrieval-time construct and should not be persisted as a source-of-truth object.

---

# Architectural Boundaries

Contributors should preserve separation between workstreams.

Examples:

✅ Good

- Zoom parser creates a Normalized Document.
- Indexing creates chunks from documents.
- MCP search joins retrieval results to document metadata.

❌ Avoid

- Embedding logic inside source parsers.
- Raw source artifacts embedded directly into MCP responses.
- MCP tools generating chunks.
- Chunking logic implemented inside data collection code.
- Vector index data becoming a source of truth.

---

# Repository Evolution

Repository folders may change over time.

Contributors should follow workstream ownership and data model ownership rather than assuming existing folder locations define architectural boundaries.

When introducing a new capability:

1. Identify the owning workstream.
2. Identify the affected data model.
3. Place implementation code in the area corresponding to that responsibility.
4. Avoid introducing cross-workstream dependencies where a data-model handoff already exists.
