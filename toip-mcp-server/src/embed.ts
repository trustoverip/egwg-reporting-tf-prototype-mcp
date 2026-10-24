import { FlagEmbedding, EmbeddingModel } from 'fastembed'

let model: FlagEmbedding | null = null

async function getModel(): Promise<FlagEmbedding> {
  if (!model) {
    model = await FlagEmbedding.init({ model: EmbeddingModel.BGESmallENV15 })
  }
  return model
}

export async function embed(text: string): Promise<number[]> {
  const m = await getModel()
  for await (const batch of m.embed([text])) {
    return Array.from(batch[0])
  }
  throw new Error('No embedding returned')
}

/**
 * Embed many texts at once.
 *
 * The model runs far better on batches than on one string at a time, which
 * matters when a full ingest is tens of thousands of chunks.
 */
export async function embedBatch(
  texts: string[],
  batchSize = 32
): Promise<number[][]> {
  if (texts.length === 0) return []

  const m = await getModel()
  const vectors: number[][] = []

  for await (const batch of m.embed(texts, batchSize)) {
    for (const vector of batch) {
      vectors.push(Array.from(vector))
    }
  }

  if (vectors.length !== texts.length) {
    throw new Error(
      `Embedding count mismatch: got ${vectors.length} for ${texts.length} texts`
    )
  }

  return vectors
}
