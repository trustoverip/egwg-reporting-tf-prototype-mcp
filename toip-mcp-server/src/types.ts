export interface NormalizedDocument {
  schema_version: 1
  identifier: string
  document: {
    project: string
    organizational_unit: string[]
    title: string
    date: string
  }
  provenance: {
    artifact_id: string
    source_system: string
    source_identifier: string
    source_url: string
    collected_at: string
  }
  content: {
    content_type: string
    text: string
  }
}

export interface EmbeddedChunk {
  schema_version: 1
  chunk_id: string
  document_id: string
  chunk_index: number
  content_type: string
  date: string
  organizational_unit: string[]
  text: string
  embedding_model: string
}

export interface SearchResult {
  schema_version: 1
  score: number
  chunk_id: string
  document_id: string
  title: string
  project: string
  organizational_unit: string[]
  date: string
  source: {
    system: string
    identifier: string
    url: string
  }
  content_type: string
  text: string
}

/**
 * A retrieval-time join may fail even when vector search found a chunk.
 * Only a resolved result may be presented as provenance-backed evidence.
 */
export type SearchResolution =
  | { status: 'resolved'; result: SearchResult }
  | {
      status: 'unresolved'
      reason: 'missing_document' | 'document_identifier_mismatch' | 'invalid_document_said'
      document_id: string
      chunk_id: string
    }
