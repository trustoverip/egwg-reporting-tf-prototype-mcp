import { LocalIndex } from 'vectra'
import { embedBatch } from './embed.js'
import fs from 'fs/promises'
import path from 'path'
import { fileURLToPath } from 'url'
import { chunkMetadata, isNormalizedDocument, toEmbeddedChunks } from './chunks.js'
import { dataDir } from './documents.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const DEFAULT_BATCH_SIZE = 32

const usage = `Usage: npm run ingest -- [options]

  --data <dir>     curated documents to index (default: ../mcp_data_generation/data/curated)
  --batch <n>      embedding batch size (default: ${DEFAULT_BATCH_SIZE})
  --limit <n>      stop after n documents, for a quick trial run
  --prune          drop indexed documents the curated directory no longer holds
  --rebuild        delete the index and start over instead of resuming
`

export interface Options {
  dataDir: string
  batchSize: number
  limit: number
  prune: boolean
  rebuild: boolean
}

function parseArgs(argv: string[]): Options {
  const options: Options = {
    dataDir: dataDir(),
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

async function readDocuments(dir: string): Promise<string[]> {
  let entries: string[]
  try {
    entries = await fs.readdir(dir)
  } catch {
    throw new Error(
      `Cannot read ${dir}. Generate curated documents first ` +
        `(python -m src.curate in mcp_data_generation), or pass --data.`
    )
  }
  return entries.filter((name) => name.endsWith('.json')).sort()
}

async function indexedDocs(index: LocalIndex): Promise<Map<string, string[]>> {
  const docs = new Map<string, string[]>()

  for (const item of await index.listItems()) {
    const id = item.metadata.document_id as string | undefined
    if (!id) {
      throw new Error(
        'The index was built before the #8 data model and cannot be resumed. ' +
          'Run `npm run ingest -- --rebuild`.'
      )
    }
    const itemIds = docs.get(id)
    if (itemIds) itemIds.push(item.id)
    else docs.set(id, [item.id])
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

  const already = options.rebuild ? new Map<string, string[]>() : await indexedDocs(index)
  if (already.size > 0) {
    console.log(`Resuming: ${already.size} document(s) already indexed`)
  }

  console.log(`Indexing from ${options.dataDir}`)

  let done = 0
  let skipped = 0
  let invalid = 0
  let chunkTotal = 0

  const present = new Set<string>()

  for (const file of files) {
    if (done >= options.limit) break

    let raw: string
    try {
      raw = await fs.readFile(path.join(options.dataDir, file), 'utf-8')
    } catch (error) {
      console.warn(`  ! ${file} could not be read (${(error as NodeJS.ErrnoException).code ?? error}), skipping`)
      invalid++
      continue
    }
    let doc: unknown
    try {
      doc = JSON.parse(raw)
    } catch (error) {
      console.warn(`  ! ${file} is not valid JSON (${(error as Error).message}), skipping`)
      invalid++
      continue
    }
    if (!isNormalizedDocument(doc)) {
      console.warn(`  ! ${file} is not a schema_version 1 Normalized Document, skipping`)
      invalid++
      continue
    }

    present.add(doc.identifier)

    if (already.has(doc.identifier)) {
      skipped++
      continue
    }

    const chunks = toEmbeddedChunks(doc)
    if (chunks.length === 0) {
      skipped++
      continue
    }

    const vectors = await embedBatch(
      chunks.map((chunk) => chunk.text),
      options.batchSize
    )

    await index.beginUpdate()
    try {
      for (let i = 0; i < chunks.length; i++) {
        await index.insertItem({ vector: vectors[i], metadata: chunkMetadata(chunks[i]) })
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

  const stale = [...already.keys()].filter((id) => !present.has(id))
  let pruned = 0

  if (options.prune && invalid > 0) {
    throw new Error(
      `--prune refused: ${invalid} file(s) in ${options.dataDir} could not be read or ` +
        `are not valid Normalized Documents, so their documents would look removed. ` +
        `Fix or remove them and run again.`
    )
  }

  if (options.prune && stale.length > 0) {
    await index.beginUpdate()
    try {
      for (const id of stale) {
        for (const itemId of already.get(id)!) {
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

  console.log(
    `Ingestion complete: ${done} document(s) indexed, ${chunkTotal} chunks, ` +
      `${skipped} already present` +
      (invalid ? `, ${invalid} invalid` : '') +
      (options.prune ? `, ${pruned} pruned` : '') +
      `.`
  )

  if (!options.prune && options.limit === Infinity && stale.length > 0) {
    console.log(
      `${stale.length} indexed document(s) are no longer in ${options.dataDir} ` +
        `(removed, or replaced by an edited version). Run with --prune to drop them.`
    )
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const options = parseArgs(process.argv.slice(2))
  const indexPath = path.join(__dirname, '..', '.index')
  await ingest(indexPath, options)
}
