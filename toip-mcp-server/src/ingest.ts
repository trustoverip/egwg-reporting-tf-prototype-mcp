import { LocalIndex } from 'vectra'
import { embedBatch } from './embed.js'
import fs from 'fs/promises'
import path from 'path'
import { fileURLToPath } from 'url'
import type { ToipDocument } from './types.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Curated documents produced by mcp_data_generation (step 4). Override with
 * --data or TOIP_DATA_DIR to point at another checkout.
 */
const DEFAULT_DATA_DIR = path.join(
  __dirname,
  '..',
  '..',
  'mcp_data_generation',
  'data',
  'curated'
)

const CHUNK_SIZE = 500
const CHUNK_OVERLAP = 50
const DEFAULT_BATCH_SIZE = 32

const usage = `Usage: npm run ingest -- [options]

  --data <dir>     curated documents to index (default: ../mcp_data_generation/data/curated)
  --batch <n>      embedding batch size (default: ${DEFAULT_BATCH_SIZE})
  --limit <n>      stop after n documents, for a quick trial run
  --prune          drop indexed documents the curated directory no longer holds
  --rebuild        delete the index and start over instead of resuming
`

interface Options {
  dataDir: string
  batchSize: number
  limit: number
  prune: boolean
  rebuild: boolean
}

function parseArgs(argv: string[]): Options {
  const options: Options = {
    dataDir: process.env.TOIP_DATA_DIR ?? DEFAULT_DATA_DIR,
    batchSize: DEFAULT_BATCH_SIZE,
    limit: Infinity,
    prune: false,
    rebuild: false,
  }

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--rebuild') {
      options.rebuild = true
    } else if (arg === '--prune') {
      options.prune = true
    } else if (arg === '--help' || arg === '-h') {
      console.log(usage)
      process.exit(0)
    } else if (arg === '--data') {
      const value = argv[++i]
      if (!value) throw new Error('--data needs a directory')
      options.dataDir = value
    } else if (arg === '--batch' || arg === '--limit') {
      const value = Number(argv[++i])
      if (!Number.isInteger(value) || value < 1) {
        throw new Error(`${arg} needs a positive integer`)
      }
      if (arg === '--batch') options.batchSize = value
      else options.limit = value
    } else {
      throw new Error(`Unknown option: ${arg}\n${usage}`)
    }
  }

  if (options.prune && options.limit !== Infinity) {
    throw new Error(
      '--prune cannot be used with --limit: a limited run never sees the ' +
        'rest of the corpus, so it would prune documents that are still there'
    )
  }

  return options
}

function chunkText(text: string, size: number, overlap: number): string[] {
  const words = text.split(' ')
  const chunks: string[] = []
  let i = 0
  while (i < words.length) {
    chunks.push(words.slice(i, i + size).join(' '))
    i += size - overlap
  }
  return chunks
}

async function readDocuments(dataDir: string): Promise<string[]> {
  let entries: string[]
  try {
    entries = await fs.readdir(dataDir)
  } catch {
    throw new Error(
      `Cannot read ${dataDir}. Generate curated documents first ` +
        `(python -m src.curate in mcp_data_generation), or pass --data.`
    )
  }
  return entries.filter((name) => name.endsWith('.json')).sort()
}

interface IndexedDoc {
  /** The document's SAID as indexed, which changes when its content does. */
  identifier: string
  /** Item ids of this document's chunks, so they can be replaced. */
  itemIds: string[]
}

/**
 * What the index already holds, keyed by doc_id.
 *
 * Resuming on doc_id alone is not enough: a document keeps its id when its
 * content changes, so a re-curated document would be skipped and its old
 * chunks would stay indexed forever. The identifier is a SAID computed over
 * the whole document, so comparing it detects a changed document without
 * storing anything extra.
 */
async function indexedDocs(index: LocalIndex): Promise<Map<string, IndexedDoc>> {
  const docs = new Map<string, IndexedDoc>()

  for (const item of await index.listItems()) {
    const id = item.metadata.doc_id as string | undefined
    if (!id) continue

    const existing = docs.get(id)
    if (existing) {
      existing.itemIds.push(item.id)
      continue
    }

    docs.set(id, {
      identifier: (item.metadata.identifier as string) ?? '',
      itemIds: [item.id],
    })
  }

  return docs
}

export async function ingest(indexPath: string, options: Options) {
  const index = new LocalIndex(indexPath)

  if (options.rebuild && (await index.isIndexCreated())) {
    console.log('Rebuilding: deleting existing index')
    await index.deleteIndex()
  }
  if (!(await index.isIndexCreated())) {
    await index.createIndex()
  }

  const files = await readDocuments(options.dataDir)
  if (files.length === 0) {
    throw new Error(`No .json documents in ${options.dataDir}`)
  }

  const already = options.rebuild
    ? new Map<string, IndexedDoc>()
    : await indexedDocs(index)
  if (already.size > 0) {
    console.log(`Resuming: ${already.size} document(s) already indexed`)
  }

  console.log(`Indexing from ${options.dataDir}`)

  let done = 0
  let skipped = 0
  let replaced = 0
  let chunkTotal = 0

  // Every doc_id the curated directory still holds, including the ones this
  // run skips as unchanged. --prune needs the whole set, not just the ones
  // that were re-embedded.
  const present = new Set<string>()

  for (const file of files) {
    if (done >= options.limit) break

    const raw = await fs.readFile(path.join(options.dataDir, file), 'utf-8')
    const doc = JSON.parse(raw) as ToipDocument

    const docId = doc.id || doc.identifier
    if (!docId) {
      console.warn(`  ! ${file} has no id or identifier, skipping`)
      continue
    }

    present.add(docId)

    const indexed = already.get(docId)
    const unchanged =
      indexed !== undefined &&
      indexed.identifier !== '' &&
      indexed.identifier === doc.identifier

    if (unchanged) {
      skipped++
      continue
    }

    const chunks = chunkText(doc.content, CHUNK_SIZE, CHUNK_OVERLAP)
    if (chunks.length === 0) {
      skipped++
      continue
    }

    const vectors = await embedBatch(chunks, options.batchSize)

    // One transaction per document, so an interrupted run resumes cleanly
    // from the last whole document rather than losing everything.
    await index.beginUpdate()
    try {
      // Replacing a changed document: drop its old chunks in the same
      // transaction that inserts the new ones, so the index never holds both.
      if (indexed !== undefined) {
        for (const itemId of indexed.itemIds) {
          await index.deleteItem(itemId)
        }
        replaced++
      }

      for (let i = 0; i < chunks.length; i++) {
        await index.insertItem({
          vector: vectors[i],
          metadata: {
            doc_id: docId,
            chunk_index: i,
            chunk_text: chunks[i],
            title: doc.title,
            date: doc.date,
            project: doc.project,
            organizational_unit: JSON.stringify(doc.organizational_unit ?? []),
            source_system: doc.source_system,
            asset_type: doc.asset_type,
            content_type: doc.content_type,
            identifier: doc.identifier,
            identifier_type: doc.identifier_type ?? '',
            source_url: doc.source_url ?? '',
            collected_at: doc.collected_at ?? '',
          },
        })
      }
      await index.endUpdate()
    } catch (error) {
      await index.cancelUpdate()
      throw error
    }

    done++
    chunkTotal += chunks.length

    if (done % 25 === 0 || done === 1) {
      const total = Math.min(files.length, options.limit)
      console.log(`  ${done}/${total} documents, ${chunkTotal} chunks`)
    }
  }

  // A document dropped from curation is invisible to the loop above, which
  // only ever walks documents that still exist, so its chunks would stay
  // indexed and keep turning up in searches. This is the index's half of the
  // --prune the curation tools do upstream.
  let pruned = 0

  if (options.prune) {
    const stale = [...already.keys()].filter((id) => !present.has(id))

    if (stale.length > 0) {
      await index.beginUpdate()
      try {
        for (const id of stale) {
          for (const itemId of already.get(id)!.itemIds) {
            await index.deleteItem(itemId)
          }
          pruned++
        }
        await index.endUpdate()
      } catch (error) {
        await index.cancelUpdate()
        throw error
      }
    }
  }

  console.log(
    `Ingestion complete: ${done} document(s) indexed ` +
      `(${replaced} replaced), ${chunkTotal} chunks, ` +
      `${skipped} already present` +
      (options.prune ? `, ${pruned} pruned` : '') +
      `.`
  )
}

const options = parseArgs(process.argv.slice(2))
const indexPath = path.join(__dirname, '..', '.index')
await ingest(indexPath, options)
