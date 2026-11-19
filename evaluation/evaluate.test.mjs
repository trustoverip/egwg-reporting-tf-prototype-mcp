import { test } from 'node:test'
import assert from 'node:assert/strict'
import { evaluate, normalizeHit } from './evaluate.mjs'

const cases = [{ id: 'q1', expected_document_ids: ['doc-a'] }]
test('pre-migration search JSON and proposed normalized results are both recognized', () => {
  assert.equal(normalizeHit({ metadata: { doc_id: 'doc-a', identifier: 'urn:said:a', source_system: 'Zoom' } }).documentId, 'doc-a')
  assert.equal(normalizeHit({ document_id: 'urn:said:b', source: { system: 'Confluence', identifier: 'page-2', url: '' } }).documentId, 'urn:said:b')
})
test('recall, reciprocal rank, and missing evidence remain explicit', () => {
  const report = evaluate(cases, [{ id: 'q1', hits: [
    { metadata: { doc_id: 'other', identifier: 'urn:said:other' } },
    { metadata: { doc_id: 'doc-a', identifier: 'urn:said:a', source_system: 'Zoom' } },
  ] }], 2)
  assert.equal(report.mean_recall_at_k, 1)
  assert.equal(report.mean_reciprocal_rank, 0.5)
  assert.equal(report.results[0].evidence[0].source_system, 'missing')
  assert.equal(report.results[0].evidence[1].source_identifier, 'unknown')
})
test('empty source URLs do not become false completeness claims', () => {
  const report = evaluate(cases, [{ id: 'q1', hits: [{
    document_id: 'doc-a', source: { system: 'Zoom', identifier: '', url: '' },
  }] }])
  assert.equal(report.results[0].evidence[0].source_identifier, 'unknown')
  assert.equal(report.results[0].evidence[0].source_url, 'unknown')
  assert.equal(report.results[0].evidence[0].document_identifier, 'present')
})
test('missing captures and contradictory input fail instead of producing passing metrics', () => {
  assert.throws(() => evaluate(cases, []), /missing captured result/)
  assert.throws(() => evaluate(cases, [{ id: 'q1', hits: [] }, { id: 'q1', hits: [] }]), /unique/)
  assert.throws(() => evaluate([{ id: 'q1', expected_document_ids: ['a', 'a'] }], [{ id: 'q1', hits: [] }]), /unique/)
  assert.throws(() => evaluate(cases, [{ id: 'q1', hits: [] }], 0), /positive integer/)
})
test('zero hits are an observed miss, not a successful retrieval', () => {
  const report = evaluate(cases, [{ id: 'q1', hits: [] }])
  assert.equal(report.mean_recall_at_k, 0)
  assert.equal(report.mean_reciprocal_rank, 0)
})
