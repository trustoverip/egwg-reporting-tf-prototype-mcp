# Reproducible retrieval-evidence evaluation

This independent test surface supports Knowledge Indexing / Testing & Evaluation
(issue #11) without changing the current data-model handoffs or the MCP server.
It evaluates *captured* search results, not an LLM-generated answer. It is
intentionally compatible with the current `search` / `npm run query -- --json`
shape and the proposed normalized search-result shape in PR #22.

## Run

Requires Node.js 20 or newer and no additional dependencies.

```sh
node --test evaluation/evaluate.test.mjs
node evaluation/evaluate.mjs evaluation/example-cases.json evaluation/example-results.json 5
```

The example is synthetic and only verifies the evaluator. Its numbers are
**not** a retrieval quality claim for the ToIP corpus.

## Capture a real benchmark

Create a human-reviewed `cases.json` array of objects with unique `id` and
`expected_document_ids` (at least one ID per query). Record the query and
relevance rationale alongside each case for reviewers, but the evaluator only
consumes IDs. For each query execute the MCP search tool, or the equivalent
`npm run query -- --json`, and preserve the `hits` array as
`{ "id": "<case-id>", "hits": [...] }` in `results.json`.

Do not commit restricted meeting content, identifying source URLs, access
tokens, or raw search snippets merely to publish a benchmark. Use synthetic
or explicitly releasable cases for public fixtures.

Metrics: mean recall@k and mean reciprocal rank. The report also exposes
per-hit evidence-field presence, with `unknown` kept separate from
`present`. A present field is **not** proof of validity. No field-presence
metric establishes an intact Raw Artifact join, historical accuracy,
authorization, or answer correctness.

## Compatibility and limits

The evaluator does not require an embedding model or live corpus. A change to
ranking, chunking, metadata filtering, document identity, or model version can
be compared using the *same* adjudicated cases across captured results. It
does not replace live end-to-end integration tests.

In the current flat-index format the source identifier is not always present;
in the PR #22 result model the joined source identifier may be available.
Consequently missing information is recorded as unknown, not silently
reported as a successful provenance join. Proposed server-side handling of
failed joins remains separately governed by issues #8 and #12.
