/**
 * Hybrid search over the chunk index.
 *
 * Pure vector search on this corpus has two failure modes that showed up as
 * soon as the full index existed:
 *
 *   1. Meeting transcripts are stylistically identical, so cosine scores
 *      compress into a narrow band (~0.73-0.76 for everything useful). No
 *      threshold separates a real match from a near miss.
 *   2. A distinctive query term can be missing from every hit. Asking about
 *      "SAIDs" returned five chunks that never mention them, because the
 *      embedding matched the surrounding meeting-speak instead.
 *
 * So we over-fetch by vector, rescore the candidates lexically, and fuse the
 * two rankings with Reciprocal Rank Fusion. RRF combines *ranks*, not scores,
 * which is exactly what a compressed score scale needs — it does not care that
 * one signal spans 0.73-0.76 and the other 0-1.
 *
 * The hit also carries the query terms it actually contains. That is a far
 * more useful "did we find anything" signal than any score: zero matched terms
 * means the corpus probably does not answer the question, however confident
 * the cosine score looks.
 */

import type { LocalIndex } from 'vectra'
import { embed } from './embed.js'

/** Rank-fusion constant. 60 is the value from the original RRF paper. */
const RRF_K = 60

/** Never consider fewer than this many candidates, however small `limit` is. */
const MIN_CANDIDATES = 50

/** Candidates to pull per requested result. */
const CANDIDATE_FACTOR = 10

/**
 * Function words only. Corpus-ubiquitous terms like "meeting" are deliberately
 * absent: the local IDF below discounts them automatically, and hard-coding
 * them would break a query that is genuinely about meetings.
 */
const STOPWORDS = new Set([
  'a', 'about', 'all', 'also', 'an', 'and', 'any', 'are', 'as', 'at', 'be',
  'been', 'but', 'by', 'can', 'did', 'do', 'does', 'for', 'from', 'get', 'had',
  'has', 'have', 'how', 'i', 'if', 'in', 'into', 'is', 'it', 'its', 'just',
  'me', 'more', 'most', 'my', 'no', 'not', 'of', 'on', 'or', 'our', 'out',
  'over', 'should', 'so', 'some', 'such', 'than', 'that', 'the', 'their',
  'them', 'then', 'there', 'these', 'they', 'this', 'those', 'to', 'up', 'was',
  'we', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'will', 'with',
  'would', 'you', 'your',
])

export interface SearchHit {
  /** Fused RRF score. Comparable within one result set, not across queries. */
  score: number
  vector_score: number
  lexical_score: number
  /** Query terms this chunk actually contains. */
  matched_terms: string[]
  metadata: Record<string, unknown>
}

export interface SearchResult {
  hits: SearchHit[]
  /** Content terms the query was reduced to, after stopwords and stemming. */
  query_terms: string[]
  /** Candidates pulled from the vector index before fusion. */
  candidates: number
}

export interface SearchOptions {
  /** Substring match against organizational_unit. */
  filterWg?: string
  /** Keep every matching chunk instead of the best one per document. */
  allChunks?: boolean
}

/**
 * Split on runs of non-alphanumerics, so "post-quantum" yields "post" and
 * "quantum" and matches prose that writes it either way.
 */
function tokenize(text: string): string[] {
  return text.toLowerCase().match(/[a-z0-9]+/g) ?? []
}

/**
 * Drop a trailing plural "s". Crude next to a real stemmer, but it is what
 * makes "SAIDs" match "SAID" and "tools" match "tool", which is most of the
 * benefit on this corpus. Words of three characters or fewer are left alone so
 * "has" and "its" survive intact.
 */
function normalizeTerm(token: string): string {
  if (token.length > 3 && token.endsWith('s') && !token.endsWith('ss')) {
    return token.slice(0, -1)
  }
  return token
}

/** The distinctive terms of a query, deduplicated and order-preserved. */
export function queryTerms(query: string): string[] {
  const terms: string[] = []
  const seen = new Set<string>()

  for (const token of tokenize(query)) {
    if (token.length < 2 || STOPWORDS.has(token)) continue
    const term = normalizeTerm(token)
    if (seen.has(term)) continue
    seen.add(term)
    terms.push(term)
  }

  return terms
}

/** Term frequencies for one chunk, keyed by normalized term. */
function termCounts(text: string): Map<string, number> {
  const counts = new Map<string, number>()
  for (const token of tokenize(text)) {
    const term = normalizeTerm(token)
    counts.set(term, (counts.get(term) ?? 0) + 1)
  }
  return counts
}

function matchesWg(metadata: Record<string, unknown>, filterWg: string): boolean {
  let units: string[] = []
  try {
    units = JSON.parse((metadata.organizational_unit as string) ?? '[]')
  } catch {
    return false
  }
  const needle = filterWg.toLowerCase()
  return units.some((unit) => unit.toLowerCase().includes(needle))
}

export async function search(
  index: LocalIndex,
  query: string,
  limit: number,
  options: SearchOptions = {}
): Promise<SearchResult> {
  const terms = queryTerms(query)
  const depth = Math.max(MIN_CANDIDATES, limit * CANDIDATE_FACTOR)

  const raw = await index.queryItems(await embed(query), query, depth)

  // Filter before truncating, so a working-group filter narrows the candidate
  // pool rather than discarding results that already won.
  const candidates = options.filterWg
    ? raw.filter((r) => matchesWg(r.item.metadata as Record<string, unknown>, options.filterWg!))
    : raw

  if (candidates.length === 0) {
    return { hits: [], query_terms: terms, candidates: 0 }
  }

  const texts = candidates.map((r) => (r.item.metadata.chunk_text as string) ?? '')
  const counts = texts.map(termCounts)

  // Document frequency across the candidate set only. A term present in every
  // candidate carries no information *for this query*, which is precisely the
  // judgement we want and precisely what a global IDF would miss.
  const df = new Map<string, number>()
  for (const term of terms) {
    df.set(term, counts.filter((c) => c.has(term)).length)
  }

  const idf = new Map<string, number>()
  for (const term of terms) {
    idf.set(term, Math.log(1 + candidates.length / (1 + (df.get(term) ?? 0))))
  }

  const idfTotal = terms.reduce((sum, t) => sum + (idf.get(t) ?? 0), 0)

  const scored = candidates.map((result, i) => {
    const matched: string[] = []
    let lexical = 0

    for (const term of terms) {
      const tf = counts[i].get(term)
      if (!tf) continue
      matched.push(term)
      // Saturating term frequency: the second mention matters far less than
      // the first, and a chunk cannot win by repeating one word.
      lexical += (idf.get(term) ?? 0) * (1 + Math.log(tf))
    }

    return {
      metadata: result.item.metadata as Record<string, unknown>,
      vector_score: result.score,
      lexical_score: idfTotal > 0 ? lexical / idfTotal : 0,
      matched_terms: matched,
      vectorRank: i,
    }
  })

  // Lexical ranking. Candidates with no match at all are left out: they would
  // otherwise all tie and be ordered arbitrarily, injecting noise instead of
  // signal.
  const lexicalOrder = scored
    .filter((s) => s.lexical_score > 0)
    .sort((a, b) => b.lexical_score - a.lexical_score)

  const lexicalRank = new Map<typeof scored[number], number>()
  lexicalOrder.forEach((s, rank) => lexicalRank.set(s, rank))

  const fused = scored.map((s) => {
    let score = 1 / (RRF_K + s.vectorRank)
    const lr = lexicalRank.get(s)
    if (lr !== undefined) score += 1 / (RRF_K + lr)
    return { ...s, score }
  })

  fused.sort((a, b) => b.score - a.score)

  // Collapse to the best chunk per document unless asked otherwise. Several
  // adjacent chunks of one long transcript matching the same query crowds out
  // every other meeting without adding information.
  const hits: SearchHit[] = []
  const seenDocs = new Set<string>()

  for (const hit of fused) {
    if (!options.allChunks) {
      const docId = hit.metadata.doc_id as string
      if (seenDocs.has(docId)) continue
      seenDocs.add(docId)
    }
    hits.push({
      score: hit.score,
      vector_score: hit.vector_score,
      lexical_score: hit.lexical_score,
      matched_terms: hit.matched_terms,
      metadata: hit.metadata,
    })
    if (hits.length >= limit) break
  }

  return { hits, query_terms: terms, candidates: candidates.length }
}
