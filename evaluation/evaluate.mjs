#!/usr/bin/env node
/**
 * Deterministic offline evaluation of captured MCP search results.
 * This measures retrieval and evidence integrity, not answer correctness.
 * No network access, embedding model, vector index, or external dependencies.
 */
import fs from 'node:fs'
import { pathToFileURL } from 'node:url'

function fail(message) { throw new Error(message) }
function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(label + ' must be an object')
  return value
}
function nonempty(value) { return typeof value === 'string' && value.trim().length > 0 }
function uniqueStrings(values, label) {
  if (!Array.isArray(values) || values.some((v) => !nonempty(v)) || new Set(values).size !== values.length) {
    fail(label + ' must contain unique non-empty strings')
  }
  return values
}

/** Supports the current query --json shape and the proposed PR #22 result shape. */
export function normalizeHit(hit) {
  object(hit, 'hit')
  const m = hit.metadata && typeof hit.metadata === 'object' ? hit.metadata : hit
  const source = hit.source && typeof hit.source === 'object' ? hit.source : null
  const documentId = m.document_id ?? m.doc_id
  const identifier = m.identifier ?? m.document_id
  const sourceSystem = source ? source.system : m.source_system
  const sourceIdentifier = source ? source.identifier : m.source_identifier
  const sourceUrl = source ? source.url : m.source_url
  return { documentId, identifier, sourceSystem, sourceIdentifier, sourceUrl }
}

export function evaluate(cases, results, k = 5) {
  if (!Number.isInteger(k) || k < 1) fail('k must be a positive integer')
  if (!Array.isArray(cases) || !Array.isArray(results)) fail('cases and results must be arrays')
  const seen = new Set()
  const resultById = new Map()
  for (const entry of results) {
    object(entry, 'result')
    if (!nonempty(entry.id) || resultById.has(entry.id)) fail('result ids must be unique and non-empty')
    if (!Array.isArray(entry.hits)) fail('result ' + entry.id + ' must contain hits[]')
    resultById.set(entry.id, entry)
  }
  const rows = []
  for (const item of cases) {
    object(item, 'case')
    if (!nonempty(item.id) || seen.has(item.id)) fail('case ids must be unique and non-empty')
    seen.add(item.id)
    const expected = uniqueStrings(item.expected_document_ids, 'case ' + item.id + '.expected_document_ids')
    if (expected.length === 0) fail('case ' + item.id + ' requires expected documents')
    const observed = resultById.get(item.id)
    if (!observed) fail('missing captured result for ' + item.id)
    const hits = observed.hits.slice(0, k).map(normalizeHit)
    const ids = hits.map((h) => h.documentId)
    const relevant = ids.filter((id) => expected.includes(id))
    const first = ids.findIndex((id) => expected.includes(id))
    // Source URLs may legitimately be unavailable. An empty URL is not a failed join.
    // Source identifiers are not available in all pre-#22 index records.
    // Report unknown separately; never infer successful provenance from a doc_id alone.
    const evidence = hits.map((h) => ({
      document_id: nonempty(h.documentId) ? 'present' : 'missing',
      document_identifier: nonempty(h.identifier) ? 'present' : 'missing',
      source_system: nonempty(h.sourceSystem) ? 'present' : 'missing',
      source_identifier: nonempty(h.sourceIdentifier) ? 'present' : 'unknown',
      source_url: nonempty(h.sourceUrl) ? 'present' : 'unknown',
    }))
    rows.push({
      id: item.id, returned: hits.length, expected: expected.length,
      recall_at_k: new Set(relevant).size / expected.length,
      reciprocal_rank: first < 0 ? 0 : 1 / (first + 1),
      evidence,
    })
  }
  const avg = (field) => rows.length ? rows.reduce((s, r) => s + r[field], 0) / rows.length : null
  return {
    schema_version: 1, k, cases: rows.length,
    mean_recall_at_k: avg('recall_at_k'), mean_reciprocal_rank: avg('reciprocal_rank'),
    results: rows,
    limitations: [
      'Ranking metrics require manually curated expected document IDs.',
      'Provenance fields are inspected for presence, not independently verified against source artifacts.',
      'Unknown source identifiers/URLs do not imply broken or valid provenance.',
      'This benchmark does not establish answer correctness or authorization to access content.',
    ],
  }
}

export function main(argv) {
  if (argv.length !== 2 && argv.length !== 3) {
    fail('Usage: node evaluation/evaluate.mjs cases.json results.json [k]')
  }
  const [casesFile, resultsFile, rawK] = argv
  const cases = JSON.parse(fs.readFileSync(casesFile, 'utf8'))
  const results = JSON.parse(fs.readFileSync(resultsFile, 'utf8'))
  const report = evaluate(cases, results, rawK === undefined ? 5 : Number(rawK))
  process.stdout.write(JSON.stringify(report, null, 2) + '\n')
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { main(process.argv.slice(2)) }
  catch (error) { console.error(error instanceof Error ? error.message : error); process.exitCode = 1 }
}
