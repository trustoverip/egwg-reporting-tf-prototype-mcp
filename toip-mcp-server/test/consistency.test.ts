import { test } from 'node:test'
import assert from 'node:assert/strict'
import { assessConsistency } from '../src/consistency.js'
const doc = {
  schema_version: 1, identifier: 'urn:said:Edoc',
  document: { project: 'example', organizational_unit: ['Group'], title: 'Example', date: '2026-01-01' },
  provenance: { artifact_id: 'sha256:abc', source_system: 'Zoom',
    source_identifier: 'id', source_url: '', collected_at: '2026-01-01T00:00:00Z' },
  content: { content_type: 'Transcript', text: 'example content' },
}
const chunk = (id = 'urn:said:Edoc#chunk-001', parent = doc.identifier) => ({
  id, metadata: { schema_version: 1, document_id: parent, chunk_id: id,
    chunk_index: 1, text: 'example content', embedding_model: 'bge-small-en-v1.5' },
})
const curated = [{ filename: 'doc.json', parsed: doc }]
test('healthy handoff has no findings', () => {
  const report = assessConsistency([chunk()], curated)
  assert.equal(report.status, 'consistent')
  assert.deepEqual(report.findings, [])
})
test('orphaned index reference is visible, not treated as success', () => {
  const report = assessConsistency([chunk('urn:said:Eold#chunk-001', 'urn:said:Eold')], curated)
  assert.ok(report.findings.some(f => f.code === 'missing_parent_document'))
  assert.ok(report.findings.some(f => f.code === 'missing_indexed_document'))
})
test('malformed inputs and duplicate chunk identity are reported', () => {
  const report = assessConsistency([
    chunk(), chunk(), { id: 'bad', metadata: { document_id: '', chunk_id: '' } },
  ], [...curated, { filename: 'invalid.json', parsed: { schema_version: 1 } }])
  for (const code of ['invalid_curated_document', 'duplicate_chunk_identifier',
    'missing_document_reference']) assert.ok(report.findings.some(f => f.code === code), code)
})
test('mismatched chunk parent is detected', () => {
  const report = assessConsistency([chunk('urn:said:Eother#chunk-001')], curated)
  assert.ok(report.findings.some(f => f.code === 'invalid_chunk_identifier'))
})
test('unindexed and duplicate normalized documents are distinct findings', () => {
  const report = assessConsistency([], [...curated, { filename: 'dup.json', parsed: doc }])
  assert.ok(report.findings.some(f => f.code === 'missing_indexed_document'))
  assert.ok(report.findings.some(f => f.code === 'duplicate_document_identifier'))
})
