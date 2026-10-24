import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { LocalIndex } from 'vectra'
import { z } from 'zod'
import path from 'path'
import { fileURLToPath } from 'url'
import { search } from './search.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const INDEX_PATH = path.join(__dirname, '..', '.index')

const index = new LocalIndex(INDEX_PATH)

const server = new McpServer({
  name: 'toip-knowledge-base',
  version: '0.1.0',
})

server.tool(
  'search',
  'Search ToIP meeting transcripts, summaries, and chats. Combines semantic ' +
    'similarity with keyword matching, and reports which query terms each ' +
    'result actually contains.',
  {
    query: z.string().describe('Natural language search query'),
    top_k: z.number().int().min(1).max(20).default(5).describe('Number of results to return'),
    filter_wg: z.string().optional().describe('Filter by working group (e.g. "Ecosystem Governance Working Group")'),
    all_chunks: z
      .boolean()
      .default(false)
      .describe('Allow several chunks from the same document instead of the best one each'),
  },
  async ({ query, top_k, filter_wg, all_chunks }) => {
    if (!(await index.isIndexCreated())) {
      return {
        content: [{ type: 'text' as const, text: 'No index found. Run `npm run ingest` first.' }],
        isError: true,
      }
    }

    const { hits, query_terms } = await search(index, query, top_k, {
      filterWg: filter_wg,
      allChunks: all_chunks,
    })

    if (hits.length === 0) {
      return { content: [{ type: 'text' as const, text: 'No results found.' }] }
    }

    // Lead with what the query was reduced to and whether anything matched it
    // lexically. A caller that sees every result matching no terms should
    // treat the answers as loosely related rather than authoritative.
    const matchedAny = hits.filter((h) => h.matched_terms.length > 0).length
    const header = [
      `Query terms: ${query_terms.join(', ') || '(none)'}`,
      `${matchedAny} of ${hits.length} result(s) contain at least one query term.`,
      '',
    ].join('\n')

    const output = hits.map((hit) => {
      const m = hit.metadata as Record<string, string | number>
      let units: string[] = []
      try {
        units = JSON.parse((m.organizational_unit as string) ?? '[]')
      } catch {
        units = []
      }
      const shared = Number(m.shared_by ?? 1)

      return [
        `**${m.title}** (${m.date})`,
        `ID: ${m.doc_id} | ${m.content_type} | ${units.join(', ')}`,
        `Source: ${m.source_system}${m.source_url ? ` — ${m.source_url}` : ''}`,
        `Identifier: ${m.identifier}`,
        `Score: ${hit.score.toFixed(4)} (semantic ${hit.vector_score.toFixed(3)}, ` +
          `keyword ${hit.lexical_score.toFixed(3)})`,
        `Matched terms: ${hit.matched_terms.length ? hit.matched_terms.join(', ') : 'none'}`,
        ...(shared > 1
          ? [`Note: this text is repeated in ${shared} meetings, so it is recurring boilerplate.`]
          : []),
        ``,
        m.chunk_text as string,
        `---`,
      ].join('\n')
    })

    return {
      content: [{ type: 'text' as const, text: header + output.join('\n') }],
    }
  }
)

server.tool(
  'list_documents',
  'List all documents in the knowledge base with their metadata',
  {},
  async () => {
    const all = await index.listItems()
    const seen = new Set<string>()
    const docs: string[] = []

    for (const item of all) {
      const id = item.metadata.doc_id as string
      if (seen.has(id)) continue
      seen.add(id)
      const m = item.metadata
      let units: string[] = []
      try {
        units = JSON.parse((m.organizational_unit as string) ?? '[]')
      } catch {
        units = []
      }
      docs.push(`- **${m.title}** (${m.date}) [${m.content_type}] — ${units.join(', ')}`)
    }

    return {
      content: [{ type: 'text' as const, text: docs.join('\n') }],
    }
  }
)

const transport = new StdioServerTransport()
await server.connect(transport)
