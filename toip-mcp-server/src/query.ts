import { LocalIndex } from 'vectra'
import path from 'path'
import { fileURLToPath } from 'url'
import { search } from './search.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const index = new LocalIndex(path.join(__dirname, '..', '.index'))

const usage = `Usage: npm run query -- [options] "your question"

  --limit N        results to return, 1-20 (default: 5)
  --wg NAME        only meetings whose organizational_unit contains NAME
  --all-chunks     allow several chunks from one document
  --text N         characters of chunk text to print, 0 for none (default: 300)
  --json           print the raw result object instead of a summary
`

const args = process.argv.slice(2)

function takeOption(name: string): string | undefined {
  const at = args.indexOf(name)
  if (at === -1) return undefined
  const value = args[at + 1]
  args.splice(at, 2)
  return value
}

function takeFlag(name: string): boolean {
  const at = args.indexOf(name)
  if (at === -1) return false
  args.splice(at, 1)
  return true
}

if (takeFlag('--help') || takeFlag('-h')) {
  console.log(usage)
  process.exit(0)
}

const asJson = takeFlag('--json')
const allChunks = takeFlag('--all-chunks')
const filterWg = takeOption('--wg')

function numericOption(name: string, fallback: number, max: number): number {
  const raw = takeOption(name)
  if (raw === undefined) return fallback
  const value = Number(raw)
  if (!Number.isInteger(value) || value < 0 || value > max) {
    console.error(`${name} must be an integer between 0 and ${max}`)
    console.error(usage)
    process.exit(1)
  }
  return value
}

const limit = numericOption('--limit', 5, 20)
const textChars = numericOption('--text', 300, 5000)

if (limit < 1) {
  console.error('--limit must be at least 1')
  process.exit(1)
}

const query = args.join(' ')
if (!query) {
  console.error(usage)
  process.exit(1)
}

if (!(await index.isIndexCreated())) {
  console.error('No index found. Run `npm run ingest` first.')
  process.exit(1)
}

const result = await search(index, query, limit, { filterWg, allChunks })

if (asJson) {
  console.log(JSON.stringify(result, null, 2))
  process.exit(0)
}

console.log(`query terms: ${result.query_terms.join(', ') || '(none)'}`)
console.log(`candidates:  ${result.candidates}`)
console.log('')

if (result.hits.length === 0) {
  console.log('No results.')
  process.exit(0)
}

for (const [i, hit] of result.hits.entries()) {
  const m = hit.metadata as Record<string, string | number>

  console.log(
    `${i + 1}. ${m.title}  (${m.date})  [${m.project} / ${m.content_type}]`
  )
  console.log(
    `   fused=${hit.score.toFixed(4)}  vector=${hit.vector_score.toFixed(3)}` +
      `  lexical=${hit.lexical_score.toFixed(3)}  chunk ${m.chunk_index}`
  )
  console.log(
    `   matched: ${hit.matched_terms.length ? hit.matched_terms.join(', ') : '(none)'}`
  )
  if (textChars > 0) {
    const text = String(m.chunk_text ?? '').replace(/\s+/g, ' ').slice(0, textChars)
    console.log(`   ${text}${text.length >= textChars ? '…' : ''}`)
  }
  console.log('')
}
