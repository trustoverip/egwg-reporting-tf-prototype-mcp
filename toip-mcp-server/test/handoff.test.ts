import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'fs/promises'
import os from 'os'
import path from 'path'
import { fileURLToPath } from 'url'
import {
  chunkMetadata,
  chunkText,
  EMBEDDING_MODEL,
  isNormalizedDocument,
  toEmbeddedChunks,
} from '../src/chunks.js'
import { loadDocuments, organizationalUnits, toSearchResult } from '../src/documents.js'
import type { NormalizedDocument } from '../src/types.js'
import { summarizeResolutions } from '../src/resolution-summary.js'
import { summarizeIndexedDocuments } from '../src/document-listing.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const examplePath = path.join(
  here,
  '..',
  '..',
  'mcp_data_generation',
  'tests',
  'normalized_document.example.json'
)

async function example(): Promise<NormalizedDocument> {
  const parsed: unknown = JSON.parse(await fs.readFile(examplePath, 'utf-8'))
  assert.ok(isNormalizedDocument(parsed))
  return parsed
}

const legacy = {
  id: 'zoom-example-project-transcript-GMT20260211-150000',
  project: 'Example Project',
  organizational_unit: ['Example Working Group'],
  identifier: 'urn:said:Elegacy',
  identifier_type: 'SAID',
  content: 'flat text',
}

test('the example produced by curate.py is accepted and the flat shape is not', async () => {
  await example()
  assert.equal(isNormalizedDocument(legacy), false)
})

test('a document becomes Embedded Chunks in the #8 shape', async () => {
  const doc = await example()
  const [chunk, ...rest] = toEmbeddedChunks(doc)

  assert.equal(rest.length, 0)
  assert.deepEqual(Object.keys(chunk), [
    'schema_version',
    'chunk_id',
    'document_id',
    'chunk_index',
    'content_type',
    'date',
    'organizational_unit',
    'text',
    'embedding_model',
  ])
  assert.equal(chunk.chunk_id, `${doc.identifier}#chunk-001`)
  assert.equal(chunk.document_id, doc.identifier)
  assert.equal(chunk.chunk_index, 1)
  assert.equal(chunk.content_type, doc.content.content_type)
  assert.equal(chunk.date, doc.document.date)
  assert.deepEqual(chunk.organizational_unit, doc.document.organizational_unit)
  assert.equal(chunk.text, doc.content.text)
  assert.equal(chunk.embedding_model, EMBEDDING_MODEL)
})

test('chunks overlap without a duplicate tail', () => {
  const words = (n: number) => Array.from({ length: n }, (_, i) => `w${i}`).join(' ')

  assert.equal(chunkText(words(500)).length, 1)
  const chunks = chunkText(words(1000))
  assert.equal(chunks.length, 3)
  assert.ok(chunks[1].startsWith('w450 '))
  assert.ok(chunks[2].endsWith('w999'))
  assert.deepEqual(chunkText(''), [])
})

test('index metadata holds only scalars and keeps the working groups', async () => {
  const [chunk] = toEmbeddedChunks(await example())
  const metadata = chunkMetadata(chunk)

  for (const value of Object.values(metadata)) {
    assert.ok(typeof value === 'string' || typeof value === 'number')
  }
  assert.deepEqual(organizationalUnits(metadata), chunk.organizational_unit)
})

test('search results join the chunk to its document', async () => {
  const doc = await example()
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'toip-handoff-'))
  try {
    await fs.writeFile(path.join(dir, 'example.json'), JSON.stringify(doc))
    await fs.writeFile(path.join(dir, 'legacy.json'), JSON.stringify(legacy))
    await fs.writeFile(path.join(dir, 'broken.json'), '{')

    const documents = await loadDocuments(dir)
    assert.deepEqual([...documents.keys()], [doc.identifier])
    assert.equal('text' in documents.get(doc.identifier)!.content, false)

    const [chunk] = toEmbeddedChunks(doc)
    const result = toSearchResult(chunkMetadata(chunk), 0.5, documents.get(doc.identifier))

    assert.deepEqual(result, {
      status: 'resolved',
      result: {
      schema_version: 1,
      score: 0.5,
      chunk_id: chunk.chunk_id,
      document_id: doc.identifier,
      title: doc.document.title,
      project: doc.document.project,
      organizational_unit: doc.document.organizational_unit,
      date: doc.document.date,
      source: {
        system: doc.provenance.source_system,
        identifier: doc.provenance.source_identifier,
        url: doc.provenance.source_url,
      },
      content_type: doc.content.content_type,
      text: chunk.text,
      },
    })
  } finally {
    await fs.rm(dir, { recursive: true, force: true })
  }
})

test('missing document is not represented as a resolved search result', async () => {
  const [chunk] = toEmbeddedChunks(await example())
  assert.deepEqual(toSearchResult(chunkMetadata(chunk), 0.1, undefined), {
    status: 'unresolved',
    reason: 'missing_document',
    chunk_id: chunk.chunk_id,
    document_id: chunk.document_id,
  })
})

test('inconsistent document identity is rejected rather than decorated with incorrect provenance', async () => {
  const doc = await example()
  const [chunk] = toEmbeddedChunks(doc)
  const mismatch = { ...doc, identifier: 'urn:said:another-document' }
  assert.deepEqual(toSearchResult(chunkMetadata(chunk), 0.1, mismatch), {
    status: 'unresolved',
    reason: 'document_identifier_mismatch',
    chunk_id: chunk.chunk_id,
    document_id: chunk.document_id,
  })
})

test('retrieval summaries count only resolved hits and their lexical matches', async () => {
  const doc = await example()
  const [chunk] = toEmbeddedChunks(doc)
  const metadata = chunkMetadata(chunk)
  const resolved = toSearchResult(metadata, 0.6, doc)
  const missing = toSearchResult(metadata, 0.6, undefined)
  const summary = summarizeResolutions([
    { resolution: resolved, matched_terms: [] },
    { resolution: resolved, matched_terms: ['trust'] },
    { resolution: missing, matched_terms: ['trust'] },
  ])
  assert.deepEqual(summary, { resolvedCount: 2, matchedCount: 1, unresolvedCount: 1, exitCode: 0 })
})

test('all-unresolved retrieval fails in both output modes; ordinary empty search does not', async () => {
  const [chunk] = toEmbeddedChunks(await example())
  const missing = toSearchResult(chunkMetadata(chunk), 0.6, undefined)
  assert.deepEqual(summarizeResolutions([
    { resolution: missing, matched_terms: ['trust'] },
  ]), { resolvedCount: 0, matchedCount: 0, unresolvedCount: 1, exitCode: 1 })
  assert.deepEqual(summarizeResolutions([]),
    { resolvedCount: 0, matchedCount: 0, unresolvedCount: 0, exitCode: 0 })
})

test('list_documents counts withheld documents rather than indexed chunks', async () => {
  const doc = await example()
  const available = new Map([[doc.identifier, {
    schema_version: doc.schema_version,
    identifier: doc.identifier,
    document: doc.document,
    provenance: doc.provenance,
    content: { content_type: doc.content.content_type },
  }]])
  const valid = { metadata: {
    document_id: doc.identifier, date: doc.document.date,
    content_type: doc.content.content_type,
    organizational_unit: JSON.stringify(doc.document.organizational_unit),
  } }
  const missing = { metadata: {
    ...valid.metadata, document_id: 'missing-document',
  } }
  const indexed = [valid, valid, missing, missing, missing, missing]
  const mixed = summarizeIndexedDocuments(indexed, available)
  assert.equal(mixed.docs.size, 1)
  assert.deepEqual([...mixed.withheldIds], ['missing-document'])
  assert.equal(mixed.allWithheld, false)
  assert.equal(mixed.docs.get(doc.identifier)?.title, doc.document.title)
  assert.equal(mixed.docs.get(doc.identifier)?.date, doc.document.date)

  const withheldOnly = summarizeIndexedDocuments([missing, missing, missing, missing], available)
  assert.equal(withheldOnly.docs.size, 0)
  assert.equal(withheldOnly.withheldIds.size, 1)
  assert.equal(withheldOnly.allWithheld, true)

  const emptyIndex = summarizeIndexedDocuments([], available)
  assert.equal(emptyIndex.docs.size, 0)
  assert.equal(emptyIndex.withheldIds.size, 0)
  assert.equal(emptyIndex.allWithheld, false)

  const validOnly = summarizeIndexedDocuments([valid], available)
  assert.equal(validOnly.allWithheld, false)
  // Filtering is applied after integrity classification, so a filter with
  // no matching valid documents is not an integrity error.
  assert.equal([...validOnly.docs.values()].filter(d => d.content_type === 'nonexistent').length, 0)
})
