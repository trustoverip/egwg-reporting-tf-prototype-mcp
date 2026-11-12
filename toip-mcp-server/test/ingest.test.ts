import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'fs/promises'
import os from 'os'
import path from 'path'
import { fileURLToPath } from 'url'
import { LocalIndex } from 'vectra'
import { chunkMetadata, toEmbeddedChunks } from '../src/chunks.js'
import { ingest, type Options } from '../src/ingest.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const examplePath = path.join(
  here,
  '..',
  '..',
  'mcp_data_generation',
  'tests',
  'normalized_document.example.json'
)

async function indexedWithUnreadableInput() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'toip-ingest-'))
  const indexPath = path.join(dir, 'index')
  const dataDir = path.join(dir, 'curated')
  await fs.mkdir(dataDir)

  const [chunk] = toEmbeddedChunks(JSON.parse(await fs.readFile(examplePath, 'utf-8')))
  const index = new LocalIndex(indexPath)
  await index.createIndex()
  await index.insertItem({ vector: [0.1, 0.2, 0.3], metadata: chunkMetadata(chunk) })

  await fs.writeFile(path.join(dataDir, 'broken.json'), '{')
  await fs.mkdir(path.join(dataDir, 'unreadable.json'))

  const options: Options = { dataDir, batchSize: 32, limit: Infinity, prune: false, rebuild: false }
  return { dir, index, indexPath, options }
}

test('unreadable and malformed files are skipped with distinct warnings', async (t) => {
  const { dir, index, indexPath, options } = await indexedWithUnreadableInput()
  const warn = t.mock.method(console, 'warn', () => {})
  t.mock.method(console, 'log', () => {})
  try {
    await ingest(indexPath, options)

    const warnings = warn.mock.calls.map((call) => String(call.arguments[0]))
    assert.ok(warnings.some((w) => w.includes('broken.json is not valid JSON')))
    assert.ok(warnings.some((w) => w.includes('unreadable.json could not be read')))
    assert.equal((await index.listItems()).length, 1)
  } finally {
    await fs.rm(dir, { recursive: true, force: true })
  }
})

test('--prune is refused when input files could not be validated', async (t) => {
  const { dir, index, indexPath, options } = await indexedWithUnreadableInput()
  t.mock.method(console, 'warn', () => {})
  t.mock.method(console, 'log', () => {})
  try {
    await assert.rejects(ingest(indexPath, { ...options, prune: true }), /--prune refused: 2 file/)
    assert.equal((await index.listItems()).length, 1)
  } finally {
    await fs.rm(dir, { recursive: true, force: true })
  }
})
