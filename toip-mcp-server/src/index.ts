import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { LocalIndex } from 'vectra'
import { z } from 'zod'
import path from 'path'
import { fileURLToPath } from 'url'
import { matchesWg, organizationalUnits, search } from './search.js'

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
      const units = organizationalUnits(m)

      return [
        `**${m.title}** (${m.date})`,
        `ID: ${m.doc_id} | ${m.content_type} | ${units.join(', ')}`,
        `Source: ${m.source_system}${m.source_url ? ` — ${m.source_url}` : ''}`,
        `Identifier: ${m.identifier}`,
        `Score: ${hit.score.toFixed(4)} (semantic ${hit.vector_score.toFixed(3)}, ` +
          `keyword ${hit.lexical_score.toFixed(3)})`,
        `Matched terms: ${hit.matched_terms.length ? hit.matched_terms.join(', ') : 'none'}`,
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

const LIST_DEFAULT_LIMIT = 50
const LIST_MAX_LIMIT = 500

server.tool(
  'list_documents',
  'List documents in the knowledge base, newest first, one page at a time. ' +
    'The corpus holds thousands of documents, so narrow with filter_wg or ' +
    'content_type and page with offset rather than asking for everything.',
  {
    filter_wg: z
      .string()
      .optional()
      .describe('Only documents whose working group contains this text (e.g. "Ecosystem Governance")'),
    content_type: z
      .string()
      .optional()
      .describe('Only documents of this content type, e.g. "Transcript", "AI Summary", "Wiki Page"'),
    limit: z
      .number()
      .int()
      .min(1)
      .max(LIST_MAX_LIMIT)
      .default(LIST_DEFAULT_LIMIT)
      .describe(`Documents per page (default ${LIST_DEFAULT_LIMIT}, max ${LIST_MAX_LIMIT})`),
    offset: z.number().int().min(0).default(0).describe('Documents to skip, for paging'),
  },
  async ({ filter_wg, content_type, limit, offset }) => {
    if (!(await index.isIndexCreated())) {
      return {
        content: [{ type: 'text' as const, text: 'No index found. Run `npm run ingest` first.' }],
        isError: true,
      }
    }

    interface Doc {
      title: string
      date: string
      content_type: string
      units: string[]
    }

    const docs = new Map<string, Doc>()
    for (const item of await index.listItems()) {
      const m = item.metadata as Record<string, string | number>
      const id = m.doc_id as string
      if (docs.has(id)) continue
      docs.set(id, {
        title: String(m.title ?? ''),
        date: String(m.date ?? ''),
        content_type: String(m.content_type ?? ''),
        units: organizationalUnits(m),
      })
    }

    const wantedType = content_type?.toLowerCase()
    const matching = [...docs.values()]
      .filter((d) => !filter_wg || matchesWg(d.units, filter_wg))
      .filter((d) => !wantedType || d.content_type.toLowerCase() === wantedType)
      .sort((a, b) => b.date.localeCompare(a.date) || a.title.localeCompare(b.title))

    const page = matching.slice(offset, offset + limit)

    const filters = [
      ...(filter_wg ? [`working group contains "${filter_wg}"`] : []),
      ...(content_type ? [`content type "${content_type}"`] : []),
    ]
    const header = [
      `${matching.length} of ${docs.size} documents` +
        (filters.length ? ` match ${filters.join(' and ')}` : ''),
      page.length
        ? `Showing ${offset + 1}-${offset + page.length}` +
          (offset + page.length < matching.length
            ? `; pass offset=${offset + page.length} for the next page.`
            : '.')
        : `Nothing to show at offset ${offset}.`,
      '',
    ].join('\n')

    const lines = page.map(
      (d) => `- **${d.title}** (${d.date}) [${d.content_type}] — ${d.units.join(', ')}`
    )

    return {
      content: [{ type: 'text' as const, text: header + lines.join('\n') }],
    }
  }
)

const transport = new StdioServerTransport()
await server.connect(transport)
