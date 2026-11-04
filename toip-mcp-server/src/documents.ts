import fs from 'fs/promises'
import path from 'path'
import { fileURLToPath } from 'url'
import { isNormalizedDocument } from './chunks.js'
import type { NormalizedDocument, SearchResolution } from './types.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export const DEFAULT_DATA_DIR = path.join(
  __dirname,
  '..',
  '..',
  'mcp_data_generation',
  'data',
  'curated'
)

export function dataDir(): string {
  return process.env.TOIP_DATA_DIR ?? DEFAULT_DATA_DIR
}

export type DocumentHeader = Omit<NormalizedDocument, 'content'> & {
  content: { content_type: string }
}

export async function loadDocuments(dir: string): Promise<Map<string, DocumentHeader>> {
  const documents = new Map<string, DocumentHeader>()
  let entries: string[]
  try {
    entries = await fs.readdir(dir)
  } catch {
    return documents
  }

  for (const name of entries.filter((entry) => entry.endsWith('.json'))) {
    let parsed: unknown
    try {
      parsed = JSON.parse(await fs.readFile(path.join(dir, name), 'utf-8'))
    } catch {
      continue
    }
    if (!isNormalizedDocument(parsed)) continue
    documents.set(parsed.identifier, {
      schema_version: parsed.schema_version,
      identifier: parsed.identifier,
      document: parsed.document,
      provenance: parsed.provenance,
      content: { content_type: parsed.content.content_type },
    })
  }

  return documents
}

let cached: Promise<Map<string, DocumentHeader>> | undefined

export function documentStore(): Promise<Map<string, DocumentHeader>> {
  cached ??= loadDocuments(dataDir())
  return cached
}

export function organizationalUnits(metadata: Record<string, unknown>): string[] {
  try {
    const units: unknown = JSON.parse((metadata.organizational_unit as string) ?? '[]')
    return Array.isArray(units) ? units.map(String) : []
  } catch {
    return []
  }
}

export function toSearchResult(
  metadata: Record<string, unknown>,
  score: number,
  doc: DocumentHeader | undefined
): SearchResolution {
  const documentId = String(metadata.document_id ?? '')
  const chunkId = String(metadata.chunk_id ?? '')
  if (!doc) {
    return { status: 'unresolved', reason: 'missing_document', document_id: documentId, chunk_id: chunkId }
  }
  if (doc.identifier !== documentId) {
    return {
      status: 'unresolved',
      reason: 'document_identifier_mismatch',
      document_id: documentId,
      chunk_id: chunkId,
    }
  }
  return { status: 'resolved', result: {
    schema_version: 1,
    score,
    chunk_id: String(metadata.chunk_id ?? ''),
    document_id: String(metadata.document_id ?? ''),
    title: doc?.document.title ?? '',
    project: doc?.document.project ?? '',
    organizational_unit: organizationalUnits(metadata),
    date: String(metadata.date ?? ''),
    source: {
      system: doc?.provenance.source_system ?? '',
      identifier: doc?.provenance.source_identifier ?? '',
      url: doc?.provenance.source_url ?? '',
    },
    content_type: String(metadata.content_type ?? ''),
    text: String(metadata.text ?? ''),
  } }
}
