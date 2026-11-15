/**
 * Read-only reconciliation of a derived vector index against normalized documents.
 * Presence and structural coherence are not proofs of source authenticity.
 */
import { isNormalizedDocument } from './chunks.js'

export interface IndexedEntry {
  id: string
  metadata: Record<string, unknown>
}
export interface CuratedEntry {
  filename: string
  parsed?: unknown
  error?: string
}
export interface Finding {
  code: 'invalid_curated_document' | 'duplicate_document_identifier' |
    'missing_document_reference' | 'missing_parent_document' |
    'invalid_chunk_identifier' | 'duplicate_chunk_identifier' |
    'invalid_chunk_metadata' | 'missing_indexed_document'
  subject: string
  detail: string
}

const str = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0

export function assessConsistency(
  indexed: IndexedEntry[],
  curated: CuratedEntry[],
): { schema_version: 1; index_entries: number; normalized_documents: number; findings: Finding[]; status: 'consistent' | 'findings' } {
  const findings: Finding[] = []
  const documents = new Map<string, string>()
  const indexedDocs = new Set<string>()
  const chunks = new Set<string>()
  for (const entry of curated) {
    const doc = entry.parsed
    if (entry.error || !isNormalizedDocument(doc)) {
      findings.push({ code: 'invalid_curated_document', subject: entry.filename,
        detail: entry.error ?? 'not a schema_version 1 normalized document' })
      continue
    }
    const first = documents.get(doc.identifier)
    if (first) {
      findings.push({ code: 'duplicate_document_identifier', subject: entry.filename,
        detail: 'identifier also occurs in ' + first })
    } else {
      documents.set(doc.identifier, entry.filename)
    }
  }
  for (const item of indexed) {
    const m = item.metadata
    const docId = m.document_id
    const chunkId = m.chunk_id
    if (!str(docId)) {
      findings.push({ code: 'missing_document_reference', subject: item.id,
        detail: 'indexed entry has no document_id' })
      continue
    }
    indexedDocs.add(docId)
    if (!documents.has(docId)) {
      findings.push({ code: 'missing_parent_document', subject: item.id,
        detail: 'no readable normalized document for ' + docId })
    }
    if (!str(chunkId) || !/^.+#chunk-[0-9]+$/.test(chunkId) ||
        !chunkId.startsWith(docId + '#chunk-')) {
      findings.push({ code: 'invalid_chunk_identifier', subject: item.id,
        detail: 'chunk_id is absent or does not reference its parent' })
    } else if (chunks.has(chunkId)) {
      findings.push({ code: 'duplicate_chunk_identifier', subject: item.id,
        detail: 'duplicate chunk_id ' + chunkId })
    } else {
      chunks.add(chunkId)
    }
    if (!Number.isInteger(m.chunk_index) || (m.chunk_index as number) < 1 ||
        !str(m.text) || !str(m.embedding_model) || m.schema_version !== 1) {
      findings.push({ code: 'invalid_chunk_metadata', subject: item.id,
        detail: 'schema_version, chunk_index, text or embedding_model invalid' })
    }
  }
  for (const docId of documents.keys()) {
    if (!indexedDocs.has(docId)) {
      findings.push({ code: 'missing_indexed_document', subject: docId,
        detail: 'normalized document has no indexed chunks (may be empty or pending ingest)' })
    }
  }
  findings.sort((a, b) => a.code.localeCompare(b.code) || a.subject.localeCompare(b.subject))
  return { schema_version: 1, index_entries: indexed.length,
    normalized_documents: documents.size, findings,
    status: findings.length ? 'findings' : 'consistent' }
}
