import { hash } from '@huggingface/blake3-jit'
import { timingSafeEqual } from 'node:crypto'
import type { NormalizedDocument } from './types.js'

/**
 * Recompute the Python py_saidify_urn convention for the complete v1 record.
 * Object key and array ordering are identity-bearing. Do not mutate the input.
 * Verifying an identifier is not verification of a source's authenticity.
 */
export function verifyNormalizedSaid(doc: NormalizedDocument): boolean {
  if (!/^urn:said:E[A-Za-z0-9_-]{43}$/.test(doc.identifier)) return false
  const placeholder = 'urn:said:' + '#'.repeat(44)
  const serialized = JSON.stringify({ ...doc, identifier: placeholder })
  const digest = hash(Buffer.from(serialized, 'utf8'))
  const qualified = 'urn:said:E' + Buffer.from(
    new Uint8Array([0, ...digest])
  ).toString('base64url').slice(1)
  const claimed = Buffer.from(doc.identifier)
  const expected = Buffer.from(qualified)
  return claimed.length === expected.length && timingSafeEqual(claimed, expected)
}
