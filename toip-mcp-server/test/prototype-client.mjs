// Real MCP stdio consumer smoke test against the repository's generated corpus.
// This does not attest relevance on real ToIP material or validate source authenticity.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../..')
const evidenceDir = path.resolve(process.env.TOIP_DEMO_OUTPUT ?? path.join(root, '.prototype-demo'))
const dataDir = path.join(evidenceDir, 'curated')
const manifest = JSON.parse(await fs.readFile(path.join(evidenceDir, 'manifest.json'), 'utf8'))

async function connected(data) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.resolve(here, '../dist/index.js')],
    cwd: path.resolve(here, '..'),
    env: { ...process.env, TOIP_DATA_DIR: data },
    stderr: 'pipe',
  })
  const client = new Client({ name: 'prototype-e2e-check', version: '1.0.0' })
  await client.connect(transport)
  return { client, transport }
}
function body(result) {
  assert.ok(Array.isArray(result.content), 'MCP result must have content')
  return result.content.filter(x => x.type === 'text').map(x => x.text).join('\n')
}
async function tool(client, name, args) {
  return client.callTool({ name, arguments: args })
}

const report = {
  scope: 'synthetic fixture and actual stdio MCP client; no real-corpus relevance claim',
  baseline: manifest.baseline,
  checks: {},
  limitations: [],
}
const { client, transport } = await connected(dataDir)
try {
  const discovered = await client.listTools()
  const names = discovered.tools.map(t => t.name)
  assert.ok(names.includes('search') && names.includes('list_documents'))
  report.checks.discovery = 'PASS'

  const first = await tool(client, 'list_documents', { limit: 1, offset: 0 })
  const second = await tool(client, 'list_documents', { limit: 1, offset: 1 })
  assert.notEqual(first.isError, true)
  assert.notEqual(second.isError, true)
  assert.match(body(first), /Showing 1-1/)
  assert.match(body(second), /Showing 2-2/)
  assert.notEqual(body(first), body(second))
  report.checks.pagination = 'PASS'

  const typeFilter = await tool(client, 'list_documents', { content_type: 'Transcript', limit: 10 })
  assert.notEqual(typeFilter.isError, true)
  assert.match(body(typeFilter), /Transcript/)
  assert.doesNotMatch(body(typeFilter), /\[Wiki Page\]/)
  const wgFilter = await tool(client, 'list_documents', { filter_wg: 'Example Working Group', limit: 10 })
  assert.match(body(wgFilter), /Example Working Group/)
  assert.doesNotMatch(body(wgFilter), /\[Wiki Page\]/)
  const noMatch = await tool(client, 'list_documents', { filter_wg: 'definitely-no-such-group' })
  assert.match(body(noMatch), /0 of \d+ documents/)
  report.checks.filters_and_empty = 'PASS'

  const query = await tool(client, 'search', { query: 'governance framework agenda', top_k: 5 })
  const text = body(query)
  assert.notEqual(query.isError, true)
  assert.match(text, /Document: urn:said:E/)
  assert.match(text, /Source: (Zoom|Confluence)/)
  assert.match(text, /Matched terms:/)
  assert.doesNotMatch(text, /document not found in the curated directory/)
  report.checks.search_and_source_reference = 'PASS'

  const wrongGroup = await tool(client, 'search', {
    query: 'governance framework', filter_wg: 'definitely-no-such-group', top_k: 2,
  })
  assert.match(body(wrongGroup), /No results found/)
  report.checks.search_empty = 'PASS'
} finally {
  await transport.close()
}

// Negative probe: an index outlives its curated parents. Main currently turns
// unresolved matches into apparently ordinary results. Report the observation
// without encoding that unsafe behavior as a successful test.
const missingDir = path.join(evidenceDir, 'missing-parent-corpus')
await fs.mkdir(missingDir, { recursive: true })
const adverse = await connected(missingDir)
try {
  const result = await tool(adverse.client, 'search', { query: 'governance framework', top_k: 5 })
  const text = body(result)
  const unresolvedNormalResult = result.isError !== true &&
    /Document: urn:said:E/.test(text) &&
    /document not found in the curated directory/.test(text)
  report.checks.missing_parent_provenance = unresolvedNormalResult
    ? 'FAIL: unresolved search hit formatted as ordinary result (known upstream #24)'
    : result.isError === true
      ? 'PASS: returned explicit MCP error'
      : 'INDETERMINATE: inspect unexpected response'
  if (unresolvedNormalResult) {
    report.limitations.push('Upstream main fails closed-world provenance integrity on missing parents; tracked in PR #24.')
  }
} finally {
  await adverse.transport.close()
}

// Independent adverse reproduction: all curated documents retain their SAID
// but receive modified title/source metadata. Reuse the actual stdio client
// and preserve the indexed corpus. Do not mutate the baseline fixture.
const alteredDir = path.join(evidenceDir, 'tampered-curated')
await fs.mkdir(alteredDir, { recursive: true })
for (const entry of await fs.readdir(dataDir)) {
  if (!entry.endsWith('.json')) continue
  const doc = JSON.parse(await fs.readFile(path.join(dataDir, entry), 'utf8'))
  doc.document.title = 'PROTOTYPE_TAMPERED_TITLE'
  doc.provenance.source_url = 'https://example.invalid/tampered'
  await fs.writeFile(path.join(alteredDir, entry), JSON.stringify(doc))
}
const tampered = await connected(alteredDir)
try {
  const result = await tool(tampered.client, 'search', {
    query: 'governance framework agenda', top_k: 5,
  })
  const text = body(result)
  const accepted = result.isError !== true &&
    text.includes('PROTOTYPE_TAMPERED_TITLE') &&
    text.includes('https://example.invalid/tampered')
  report.checks.modified_document_said = accepted
    ? 'FAIL: retrieval accepted modified title/source metadata under unchanged SAID'
    : result.isError === true
      ? 'PASS: tampered corpus rejected by consumer'
      : 'INDETERMINATE: tampered metadata was not observed in response'
  if (accepted) {
    report.limitations.push('Normalized document integrity not verified when loading retrieval metadata; reported independently in issue #26.')
  }
} finally {
  await tampered.transport.close()
}

// A client contract check that complements the broader manual 31-call matrix.
const invalids = await connected(dataDir)
try {
  let rejected = 0
  for (const [name, args] of [
    ['search', { query: 'governance', top_k: 0 }],
    ['search', { query: 'governance', top_k: 21 }],
    ['search', { top_k: 5 }],
    ['list_documents', { limit: 0 }],
    ['list_documents', { offset: -1 }],
  ]) {
    try {
      const value = await tool(invalids.client, name, args)
      if (value.isError) rejected++
    } catch {
      // SDK may expose JSON-RPC invalid-params as an exception.
      rejected++
    }
  }
  report.checks.invalid_mcp_parameters = rejected === 5
    ? 'PASS: five invalid requests rejected'
    : 'FAIL: one or more invalid requests accepted'
} finally {
  await invalids.transport.close()
}

await fs.writeFile(path.join(evidenceDir, 'mcp-client-report.json'),
  JSON.stringify(report, null, 2) + '\n')
console.log(JSON.stringify(report, null, 2))
if (Object.values(report.checks).some(x => x.startsWith('INDETERMINATE'))) process.exitCode = 1
