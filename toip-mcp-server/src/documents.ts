import fs from 'fs/promises'
import path from 'path'
import { fileURLToPath } from 'url'
import { isNormalizedDocument } from './chunks.js'
import { verifyNormalizedSaid } from './said.js'
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

export interface DocumentLoadState {
  documents: Map<string, DocumentHeader>
  invalidIds: Set<string>
}

/** A corrupt competing copy invalidates the same claimed identifier, too. */
export async function loadDocumentState(dir: string): Promise<DocumentLoadState> {
  const state: DocumentLoadState = {
    documents: new Map<string, DocumentHeader>(),
    invalidIds: new Set<string>(),
  }
  let entries: string[]
  try {
    entries = await fs.readdir(dir)
  } catch {
    return state
  }

  for (const name of entries.filter((entry) => entry.endsWith('.json')).sort()) {
    let parsed: unknown
    try {
      parsed = JSON.parse(await fs.readFile(path.join(dir, name), 'utf-8'))
    } catch {
      continue
    }
    if (typeof parsed !== 'object' || parsed === null) continue
    const identifier = 'identifier' in parsed ? parsed.identifier : undefined
    if (typeof identifier !== 'string' || !identifier) continue
    if (!isNormalizedDocument(parsed) || !verifyNormalizedSaid(parsed)) {
      state.invalidIds.add(identifier)
      state.documents.delete(identifier)
      console.error(`warning: withholding curated document with invalid schema/SAID: ${name}`)
      continue
    }
    if (state.invalidIds.has(identifier)) continue
    state.documents.set(identifier, {
      schema_version: parsed.schema_version,
      identifier: parsed.identifier,
      document: parsed.document,
      provenance: parsed.provenance,
      content: { content_type: parsed.content.content_type },
    })
  }
  return state
}

export async function loadDocuments(dir: string): Promise<Map<string, DocumentHeader>> {
  return (await loadDocumentState(dir)).documents
}

let cached: Promise<DocumentLoadState> | undefined

function documentState(): Promise<DocumentLoadState> {
  cached ??= loadDocumentState(dataDir())
  return cached
}

export async function documentStore(): Promise<Map<string, DocumentHeader>> {
  return (await documentState()).documents
}

export async function invalidDocumentIds(): Promise<ReadonlySet<string>> {
  return (await documentState()).invalidIds
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
  doc: DocumentHeader | undefined,
  invalidIds: ReadonlySet<string> = new Set()
): SearchResolution {
  const documentId = String(metadata.document_id ?? '')
  const chunkId = String(metadata.chunk_id ?? '')
  if (!doc) {
    return { status: 'unresolved', reason: invalidIds.has(documentId) ? 'invalid_document_said' : 'missing_document', document_id: documentId, chunk_id: chunkId }
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
