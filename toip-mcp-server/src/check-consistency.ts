/** Offline, read-only index consistency report. Does not embed, reindex or prune. */
import { LocalIndex } from 'vectra'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { assessConsistency, type CuratedEntry } from './consistency.js'
import { dataDir } from './documents.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
let indexPath = path.join(__dirname, '..', '.index')
let directory = dataDir()
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--index' && args[i + 1]) indexPath = args[++i]
  else if (args[i] === '--data' && args[i + 1]) directory = args[++i]
  else {
    console.error('Usage: npm run check-consistency -- [--index DIR] [--data DIR]')
    process.exit(2)
  }
}
async function main() {
  const index = new LocalIndex(indexPath)
  if (!(await index.isIndexCreated())) throw new Error('No index exists at ' + indexPath)
  const names = (await fs.readdir(directory)).filter(name => name.endsWith('.json')).sort()
  if (!names.length) throw new Error('No normalized documents in ' + directory)
  const curated: CuratedEntry[] = []
  for (const name of names) {
    try {
      curated.push({ filename: name, parsed: JSON.parse(await fs.readFile(path.join(directory, name), 'utf8')) })
    } catch (error) {
      curated.push({ filename: name, error: error instanceof Error ? error.message : String(error) })
    }
  }
  const items = (await index.listItems()).map(item => ({
    id: item.id, metadata: item.metadata as Record<string, unknown>,
  }))
  const report = assessConsistency(items, curated)
  console.log(JSON.stringify(report, null, 2))
  if (report.status === 'findings') process.exitCode = 1
}
main().catch(error => { console.error(error instanceof Error ? error.message : error); process.exitCode = 2 })
