import type { SearchResolution } from './types.js'

/** Shared CLI/MCP reporting policy: withheld hits never count as returned matches. */
export function summarizeResolutions(
  entries: ReadonlyArray<{ resolution: SearchResolution; matched_terms: readonly string[] }>
): { resolvedCount: number; matchedCount: number; unresolvedCount: number; exitCode: 0 | 1 } {
  let resolvedCount = 0
  let matchedCount = 0
  let unresolvedCount = 0
  for (const entry of entries) {
    if (entry.resolution.status === 'unresolved') {
      unresolvedCount++
    } else {
      resolvedCount++
      if (entry.matched_terms.length > 0) matchedCount++
    }
  }
  return {
    resolvedCount, matchedCount, unresolvedCount,
    exitCode: resolvedCount === 0 && unresolvedCount > 0 ? 1 : 0,
  }
}
