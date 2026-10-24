# MCP Knowledge Retrieval Platform

## Overview

This project provides a framework for collecting, normalizing, indexing, and retrieving organizational knowledge from multiple source systems.

Content is collected in its original format, transformed into a common document model, assigned a SAID, indexed for semantic search, and exposed through an MCP server.

Supported and planned sources include:

- Zoom
- Confluence
- GitHub
- YouTube

---

## Architecture

```text
Source Systems
    ↓
Content Acquisition
    ↓
Knowledge Normalization
    ↓
Knowledge Indexing
    ↓
MCP Retrieval Layer
```

The platform is organized around architectural workstreams rather than repository structure.

For detailed architecture, data models, ownership boundaries, and contributor guidance, see:

```text
AGENTS.md
```

---

## Knowledge Lifecycle

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

---

## Major Workstreams

### Content Acquisition

Collect and preserve content from source systems.

Examples:

- Zoom
- Confluence
- GitHub
- YouTube
- Future content sources

Output:

```text
Raw Artifact
```

### Knowledge Normalization

Transform source-specific content into a canonical representation.

Examples:

- Source parsers
- Metadata inheritance
- Validation
- SAID generation

Output:

```text
Normalized Document
```

### Knowledge Indexing

Prepare normalized knowledge for semantic retrieval.

Examples:

- Chunking
- Embeddings
- Vector index creation

Output:

```text
Embedded Chunk
Vector Index Entry
```

### MCP Retrieval Layer

Expose indexed knowledge through MCP tools.

Examples:

- Search
- Metadata filtering
- Document discovery

Output:

```text
Search Result
```

### Testing & Evaluation

Validate retrieval quality and system behavior.

Examples:

- Retrieval testing
- Regression testing
- Benchmark datasets

---

## Repository Components

### mcp_data_generation

Responsible for knowledge normalization.

Examples:

- Parsing
- Validation
- Metadata inheritance
- SAID generation

Produces:

```text
Normalized Documents
```

### toip-mcp-server

Responsible for knowledge indexing and retrieval.

Examples:

- Chunking
- Embeddings
- Vector indexing
- MCP tools

Produces:

```text
Vector Index
MCP Search Tools
```

### Testing Utilities

Support validation, retrieval testing, and benchmarking.

---

## Design Principles

- Preserve raw content as the system of record.
- Normalize source content into a canonical representation.
- Maintain clear boundaries between workstreams.
- Treat vector indexes as rebuildable derived artifacts.
- Expose knowledge through retrieval rather than direct source access.

---

## Contributor Guidance

Contributors should review:

```text
AGENTS.md
```

before making architectural, data model, indexing, or retrieval changes.

AGENTS.md contains:

- Workstream definitions
- Data model definitions
- Ownership boundaries
- Architectural principles
- Contribution guidance
