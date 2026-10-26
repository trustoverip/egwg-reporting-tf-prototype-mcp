# ToIP MCP Server

Local MCP server for hybrid semantic and keyword search over LFDT meeting
transcripts, closed captions, chat logs, AI summaries and Confluence wiki
pages. No API key required — embeddings run locally via `fastembed`.

## Setup

```bash
npm install
```

## Ingest

Reads the curated documents produced by `mcp_data_generation` (step 4), chunks
them, embeds with a local model, and writes a local vector index (~25 MB model
downloaded on first run):

```bash
npm run ingest
```

By default it reads `../mcp_data_generation/data/curated`. Point it elsewhere
with `--data` or `TOIP_DATA_DIR`:

```bash
npm run ingest -- --data /path/to/curated
TOIP_DATA_DIR=/path/to/curated npm run ingest
```

Options:

| Option | Effect |
|---|---|
| `--data <dir>` | curated documents to index |
| `--batch <n>` | embedding batch size (default 32) |
| `--limit <n>` | stop after n documents — useful for a trial run |
| `--prune` | drop indexed documents the curated directory no longer holds |
| `--rebuild` | delete the index and start over |

**Ingest resumes, and notices changed documents.** Each document is committed
in its own transaction, so an interrupted run picks up where it left off.

A document is skipped only when the `identifier` in the index matches the one
in the curated document. That identifier is a SAID computed over the whole
document, so any change to its content produces a different one: a re-curated
document is re-embedded and its old chunks are deleted in the same transaction,
rather than being skipped because its `doc_id` was already present. The closing
summary reports how many were replaced that way.

A document *removed* from curation is a different case: the run walks the
curated directory, so a document that is no longer there is never looked at and
its chunks stay indexed and keep turning up in searches. `--prune` deletes
them. It is the index's half of the `--prune` the curation tools do upstream,
and it refuses to run with `--limit`, because a limited run never sees the rest
of the corpus and would prune documents that are still there.

`--rebuild` is still there for starting clean.

The full corpus is large — 6766 documents at the last count (4663 from Zoom,
2103 from Confluence), which is tens of thousands of chunks, the better part
of an hour on a laptop, and an index of several hundred MB. Try `--limit 20`
first.

## How search works

Search is **hybrid**: semantic similarity and keyword matching, fused by rank.
Pure vector search misbehaved on this corpus in two ways that mattered.

First, meeting transcripts are stylistically near-identical, so cosine scores
compress into a narrow band — everything useful landed between 0.73 and 0.76,
and a deliberately bad query scored higher than four of five good hits. No
threshold can separate a real match from a near miss on a scale like that.

Second, a distinctive query term could be missing from every result. Asking
about "SAIDs" returned five chunks that never mention them, because the
embedding matched the surrounding meeting-speak instead.

So `search` over-fetches candidates by vector (10 per requested result, at
least 50), rescores them on keyword overlap, and combines the two rankings with
Reciprocal Rank Fusion. RRF fuses *ranks* rather than scores, which is exactly
what a compressed score scale needs — it does not care that one signal spans
0.73–0.76 and the other 0–1.

Three details make the keyword half work:

- **Local IDF.** A term's weight comes from how many *candidates* contain it,
  not the whole corpus. A word present in every candidate carries no
  information for that query, which is the judgement we want and one a global
  IDF would miss.
- **Light stemming.** A trailing plural `s` is dropped, so "SAIDs" matches
  "SAID" and "tools" matches "tool". Words of three characters or fewer are
  left alone.
- **One chunk per document.** Several adjacent chunks of one long transcript
  matching the same query crowds out every other meeting without adding
  anything. Pass `all_chunks` / `--all-chunks` to see them all.

Every result reports **which query terms it actually contains**. That is a far
better "did we find anything" signal than any score: a result set where nothing
matched any term means the corpus probably does not answer the question,
however confident the cosine looks. The MCP tool puts the count in its header.

## Query the index directly

Same search path as the MCP tool, without the server:

```bash
npm run query "bite size trust scripts"
npm run query -- --limit 10 --text 0 "bite size trust scripts"
npm run query -- --wg "Ecosystem Governance" "reporting templates"
```

| Option | Effect |
|---|---|
| `--limit N` | results to return, 1–20 (default 5) |
| `--wg NAME` | only meetings whose `organizational_unit` contains NAME |
| `--all-chunks` | allow several chunks from one document |
| `--text N` | characters of chunk text to print, `0` for none (default 300) |
| `--json` | raw result object instead of the summary |

`--wg` is applied inside the vector query, before ranking, so a small task
force is ranked on its own chunks rather than on whatever survived of the
global top 50. The match is a case-insensitive substring of any entry in
`organizational_unit`; it is resolved against the distinct values in the index
and passed to vectra as an exact-match list, because vectra's metadata filters
have no substring operator. The MCP `search` tool behaves identically.

## Run

```bash
npm start
```

## Cursor / Claude Desktop config

Add to your MCP config:

```json
{
  "mcpServers": {
    "toip-knowledge-base": {
      "command": "node",
      "args": ["/absolute/path/to/toip-mcp-server/dist/index.js"]
    }
  }
}
```

Or for development (no build step):

```json
{
  "mcpServers": {
    "toip-knowledge-base": {
      "command": "npx",
      "args": ["tsx", "/absolute/path/to/toip-mcp-server/src/index.ts"]
    }
  }
}
```

## Tools

- **search** — hybrid semantic + keyword search over all document chunks (see
  [How search works](#how-search-works)). Returns matching passages with title,
  date, WG, source, identifier, the component scores, and the query terms each
  passage contains. Optional `filter_wg` narrows by working group;
  `all_chunks` allows several chunks from one document.
- **list_documents** — list indexed documents with metadata, newest first,
  50 per page (`limit`, up to 500) with `offset` for paging. Optional
  `filter_wg` (substring of the working group) and `content_type` (exact,
  case-insensitive) narrow the list. The header reports how many documents
  match and the offset of the next page. The corpus holds thousands of
  documents, so an unfiltered listing is deliberately paged rather than
  returned whole.

## Data schema

Each document is a curated document from `mcp_data_generation` (see its
README, step 4):

| Field | Description |
|---|---|
| `id` | Stable human-readable identifier |
| `project` | e.g. "Trust Over IP Project (ToIP)", "Hiero", "Besu" |
| `organizational_unit` | WG/TF array |
| `date` | Meeting or publication date |
| `source_system` | `Zoom` or `Confluence` |
| `content_type` | Transcript, ClosedCaptioning, Chat, AI Summary, Wiki Page |
| `identifier` | SAID (`urn:said:...`) |
| `identifier_type` | `SAID` |
| `collected_at` | When the source file was ingested |
| `content` | Full text (chunked for search) |
