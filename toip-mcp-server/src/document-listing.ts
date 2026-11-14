import { organizationalUnits } from './documents.js'
import type { DocumentHeader } from './documents.js'

export interface ListedDocument {
  title: string
  date: string
  content_type: string
  units: string[]
}

/**
 * Classify distinct indexed document IDs, not chunks. A missing or invalid
 * curated parent never becomes a successful document listing.
 */
export function summarizeIndexedDocuments(
  items: ReadonlyArray<{ metadata: unknown }>,
  documents: ReadonlyMap<string, DocumentHeader>
): { docs: Map<string, ListedDocument>; withheldIds: Set<string>; allWithheld: boolean } {
  const docs = new Map<string, ListedDocument>()
  const withheldIds = new Set<string>()
  for (const item of items) {
    const m = item.metadata as Record<string, unknown>
    const id = typeof m?.document_id === 'string' ? m.document_id : ''
    if (docs.has(id) || withheldIds.has(id)) continue
    const doc = documents.get(id)
    if (!doc) {
      withheldIds.add(id)
      continue
    }
    docs.set(id, {
      title: doc.document.title,
      date: String(m.date ?? ''),
      content_type: String(m.content_type ?? ''),
      units: organizationalUnits(m),
    })
  }
  return { docs, withheldIds, allWithheld: items.length > 0 && docs.size === 0 }
}
