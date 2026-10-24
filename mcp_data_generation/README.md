# MCP Data Generation

Tools for turning LFDT source material into standardized JSON documents that
can later be enriched with SAIDs, embeddings, vector search, and MCP retrieval.

Two source systems are supported. A parser is chosen by the **first path
component under the raw root**, so `zoom/...` is parsed as Zoom and
`confluence/...` as Confluence.

## Zoom

| Source file | Content type |
|-------------|--------------|
| `GMT<date>-<time>_Recording.cc.vtt` | `ClosedCaptioning` |
| `GMT<date>-<time>_Recording.transcript.vtt` | `Transcript` |
| `GMT<date>-<time>_RecordingnewChat.txt` | `Chat` |
| `GMT<date>-<time>_Recording.summary.md` | `AI Summary` |

A file is only ingested when its name matches
`^GMT\d{8}-\d{6}_Recording.*\.(txt|vtt|md)$`. Markdown is only accepted as
`*.summary.md`; any other markdown beside the assets is skipped. The fetcher's
`fetch-manifest.tsv` is ignored as bookkeeping.

## Confluence

Confluence can export a space three ways, and a cache may hold all of them. The
format is decided per file, so the directory holding an export can be named
anything:

```text
confluence/<site>/export-users.csv       site user directory (see below)

confluence/<site>/<SPACEKEY>/            HTML export
    index.html                           space details and page list
    <Page-Title>_<pageid>.html           one file per page
    styles/site.css
    attachments/<pageid>/<id>.<ext>

confluence/<site>/<any-dir>/             XML export
    entities.xml                         every object in the space
    exportDescriptor.properties          space key, export type, build
    attachments/<pageid>/<id>/<ver>

confluence/<site>/<any-dir>/             CSV export
    content.csv                          a row per page, per version, per draft
    bodycontent.csv                      the body of each of those
    spaces.csv                           the space's key and name
    exportDescriptor.properties          space key, export type, build
    <plugin tables>.csv                  calendars, inline tasks, sidebar links
    attachments/<pageid>/<id>/<ver>
```

Pages from any format become documents with content type `Wiki Page`.
**Attachments are never read** — a space export's PDFs, slide decks,
spreadsheets and video. Extracting their text would need per-format parsers
this pipeline does not have, so the walker skips any `attachments` or `styles`
directory outright rather than hashing 12.7 GB to offer each file to a parser
that will decline it. They are counted as `ignored`, not `no document`.

### Which space, not which format

The formats are not rival copies of one space, so what matters first is which
space an export covers. Four exports were evaluated for this cache:

| Export | Space | Current pages | Extracted text | Kept |
|---|---|---|---|---|
| CSV | `HOME` — the ToIP wiki | 2,103 | 9.5 MB | yes |
| XML | `HOME` — same wiki | 2,101 | 9.9 MB | no, see below |
| XML | `WGPTT` — a page-tree template space | 80 | 0.2 MB | no |
| HTML | `WGPTT` — same template space | 74 | 0.2 MB | no |

Only the `HOME` CSV export is in the cache now. `WGPTT` was dropped once the
wiki itself was available: 45 of its 80 pages were near-identical copies of
`HOME` pages, around 19 were Lorem ipsum scaffolding, and the space's own home
page reads "Example Working Group Page Tree Template". Keeping it would have
added 45 duplicates competing with their originals in search results. All three
parsers remain, because which formats a future export arrives in is not ours to
choose.

Where two exports cover *different* spaces, nothing is preferred: each
contributes its own pages.

Where two cover the **same** space, the CSV export wins. Measured over the
2,101 pages the two `HOME` exports shared, extracted text was identical on
2,098, the XML export held no live page the CSV one lacked, and the CSV export
held two the XML one did not — at a thirtieth of the cost (3 s and 75 MB of
memory against 90 s and 1.9 GB). An XML or HTML page is still parsed when the
CSV export has nothing for that page id, so the formats stay a union:

```text
Confluence SHARED: CSV export supersedes 1 XML page(s); keeping 1 the CSV export does not cover
```

Without that check both exports contribute every page and the space is indexed
twice, under the same page id with different SAIDs — which nothing downstream
would catch.

### Where two exports do overlap, neither wins outright

The `WGPTT` XML and HTML exports covered the same space, so they were
compared. Both are gone from the cache now, but the comparison is what decided
the precedence rules the parsers still apply:

| | HTML export | XML export |
|---|---|---|
| Pages for this space | 74 | 80 |
| Page hierarchy | scraped from breadcrumbs | real parent/child links |
| Dates | byline, created *or* last modified, space timezone | true `creationDate`, UTC |
| Macro output (file lists, attachments) | rendered | macro call only, so no text |
| User mentions | rendered as display names | account id only |
| Historical versions | absent | 123, skipped |

The XML export holds 9 pages the HTML export omits. The HTML export holds 3 the
XML export cannot produce text for — pages whose body is nothing but a macro,
which the HTML export has already rendered.

Bodies are not fuller in XML. Over the 71 pages both hold, extracted text is
shorter in XML on 53 and never longer, by a median of 22 characters. The raw
storage markup is several times larger than the HTML page source, but that is
markup, not text.

So those two are combined rather than ranked. Where an XML export covers a
space its pages win, and an HTML page is still parsed when the XML export has
no document for that page id. Every page survives and none is indexed twice.

Precedence across all three formats is CSV, then XML, then HTML, and it is a
union at every step: a format contributes any page id the one above it does not
have. The run names each supersession, so a full pipeline run states what it
chose rather than leaving it to be inferred:

```text
Confluence CSV HOME: 2103 page(s) from 14552 row(s) (12383 historical, 66 without usable text)
Confluence HOME: CSV export supersedes 2101 XML page(s); keeping 0 the CSV export does not cover
Confluence WGPTT: XML export supersedes 71 HTML page(s); keeping 3 the XML export does not cover
```

### Two traps in the CSV export

**A space export is a backup, so it holds more than the wiki shows.** Besides
superseded versions it carries pages in the trash and unpublished drafts. The
XML export marks these with `contentStatus`, and for `HOME` that was 90 deleted
pages and 54 drafts sitting among what otherwise looked like current content,
including whole page trees someone had removed. A search answering from a
trashed page quotes retracted material, so only `contentStatus=current` is
indexed; the field is read permissively, so an export that does not record it
at all is taken at its word rather than discarded. The CSV export says the same
thing with `content_status`, where a draft is `draft` and the trash is absent
altogether.

**The page count in the export is not the number of pages.**
`space_export_counts.csv` reports `Page,14552`, and `content.csv` really does
hold 14,552 rows marked `content_status=current`. Only **2,169** of them are
pages — the other 12,383 are historical versions. The discriminator is
`spaceid`: it is set on a current page and empty on a version, because a
version belongs to its page rather than to the space. `prevver` does *not*
discriminate; it is set on nearly every row, and on a version it points at the
current page, not at the version before it. Taking the header count at face
value would index seven superseded copies of every page. It is also why
`bodycontent.csv` is 186 MB for 22 MB of current page bodies — so bodies for
anything but a current page are dropped as the file is streamed, which keeps
the parse to about 75 MB of memory and three seconds.

**A `.csv.gz` suffix says nothing about the contents.** Confluence shipped this
export with all 49 of its files named `.gz` and not one of them compressed —
every one began with ASCII rather than the gzip magic bytes `1f 8b`. The files
in the cache are now gzipped for real. So the suffix is never trusted in either
direction: each file is sniffed and decompressed only if it actually carries
those bytes, and both states read correctly.

### User mentions

Both machine-readable exports record a mention only as an Atlassian account id,
so a meeting's participant list would come out as a row of empty commas — and
participants are exactly what someone searches a meeting page for.

The fix is a site user directory, exported from Atlassian admin as
`export-users.csv` and dropped beside the space exports. It maps every account
on the site to a display name:

| Name source | `HOME` mentions resolved | `WGPTT` mentions resolved |
|---|---|---|
| `export-users.csv` | 25,411 of 25,565 (99.4%) | 1,003 of 1,086 (92.4%) |
| HTML export harvest | 12,116 of 25,565 (47.4%) | 1,003 of 1,086 (92.4%) |

Only the `User id` and `User name` columns are read. The file also holds email
addresses, group membership, org roles and last-seen dates, and none of that
belongs in a document that gets indexed and searched.

The directory is also cleaner than the HTML harvest, which renders a departed
member as `Ken Adler (Deactivated)` — a UI annotation, not a name. The two
agree on 52 of the 56 accounts they both know and the directory is better on
the other four.

The CSV export's own `user_mapping.csv` is no help: `username`,
`lower_username` and `aaid` all equal `user_key` in 346 of its 352 rows, and
not one value contains a space.

Without a directory, the XML parser falls back to harvesting names from a
sibling HTML export, and the CSV parser says so and drops mentions. Accounts
missing from every source are deleted users with no name to recover, and are
dropped rather than written out as "Former user (Deleted)".

### Derived fields

Everything is derived from the export, so a bare export ingests with no
configuration:

| Field | Derived from |
|---|---|
| `project` | space name |
| `organizational_unit` | ancestor page titles (CSV, XML) or breadcrumb trail (HTML) |
| `title` | page title, with the `"<Space> : "` prefix stripped |
| `source_identifier` | page id, e.g. `24609309` |
| `source_url` | `https://<site>/wiki/spaces/<KEY>/pages/<pageid>` |
| `date` | a leading `YYYY-MM-DD` in the page title, else the page's creation date, else empty |
| `asset_type` | `Meeting` when the title starts with a date, else `Document` |

A `metadata.json` anywhere above the file still wins, which is how `project` is
set to an LFDT project name rather than the Confluence space name. Neither
space name here is usable as one: `WGPTT` is "Working Group Page Tree Template"
and `HOME` is "Home".

One quirk the date rule faithfully reproduces: two `HOME` pages are dated 2002
and 2019 because their titles say so (`2002-12-19 TEv2 Toolkit Dev Meeting
Agenda` — TEv2 work began in 2022). The title is what a reader means by a
meeting page's date, so a typo in a title becomes a typo in the field.

### Text extraction

Markup becomes blank-line-separated blocks: headings keep their level as `##`,
lists keep their nesting as indented `-` lines, table rows join with ` | `, and
Confluence tasks are treated as a list. Inline `<style>` and `<script>`,
images, macro parameters and template placeholder text are dropped. A link's
visible text is its `ac:plain-text-link-body` when it has one and the
reference's own title otherwise — these differ far more often than not, so
emitting both would duplicate some links and misrepresent the rest.

One trap worth recording: a page body is itself wrapped in CDATA inside
`entities.xml`, so a CDATA section *within* a body closes with `]] >` rather
than `]]>`. Python's `html.parser` mishandles that and returns everything after
the first one as raw text, collapsing the rest of the page into one unparsed
blob. The parser unwraps CDATA sections before parsing.

A page whose body yields no text at all produces no document.

### Dropped pages

A space export is a faithful copy of the space, spam and all. `EXCLUDED_PAGES`
in `src/parsers/confluence.py` maps a page id to the reason it is dropped, and
the page is dropped whichever export format it arrives in:

```python
EXCLUDED_PAGES: dict[str, str] = {
    "24610187": "spam",
}
```

The list is deliberately by page id rather than by anything read from the page.
A rule that inspected content — off-topic vocabulary, say — would also throw
away real pages that happen to read oddly, and would do it silently.

Adding an id removes the page from the parser, but its old document is already
written. Re-run the engine with `--force --prune`, then `src.curate --prune`,
then the indexer with `--prune`: without the last one its chunks stay in the
index and keep turning up in searches.

AI summaries are trimmed before indexing: the generated `# Meeting Summary for
...` heading and the `- **Key:** value` metadata bullets are dropped, because
title, date and meeting id are already fields on the document and the Zoom UUID
is pure noise for retrieval. What is embedded is the prose — agenda, overview,
details and next steps. A summary left with nothing after trimming (LFX
sometimes records one with no overview) produces no document at all.

---

## Setup

```bash
cd mcp_data_generation
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

`blake3` is required for SAID generation.

---

## Step 1: Collect raw data

`scripts/fetch-zoom-assets.zsh` (in the repository root) downloads transcripts,
captions, chat and AI summaries for a project's LFX calendar. It uses only
public endpoints — no login or API token.

```bash
# Write straight into the durable cache
./scripts/fetch-zoom-assets.zsh \
    --slug lf-decentralized-trust \
    --from 2025-01-01 \
    --out ~/W/github-mirror/LFDT-way/mcp-cache/zoom
```

A parent calendar such as `lf-decentralized-trust` fans out into one directory
per project, using the slug LFX itself uses in the calendar URL — a meeting
shown at `.../meetings/besu` lands in `besu/`. Fetching a single project
directly (`--slug ToIP`) produces the same shape, just one project deep.

The run is resumable: files already on disk are skipped unless `--force` is
given. `--list-only` previews the meetings without fetching. See
`./scripts/fetch-zoom-assets.zsh --help` for the full option list and for what
the `ok` / `missing` / `error` states in the manifest mean.

### Layout

The ingester takes the **first path component under the raw root** as the source
system, so assets must live one level down in a directory named for their
source:

```text
<raw root>/
├── zoom/                               <- source system
│   ├── toip/                           <- project (LFX calendar slug)
│   │   └── egwg_reporting_tools_task_force_meeting/
│   │       ├── metadata.json
│   │       ├── GMT20261021-160053_Recording.transcript.vtt
│   │       ├── GMT20261021-160053_Recording.cc.vtt
│   │       ├── GMT20261021-160053_RecordingnewChat.txt
│   │       └── GMT20261021-160053_Recording.summary.md
│   ├── besu/
│   │   └── besu_contributors_meeting/
│   │       └── ...
│   └── fetch-manifest.tsv
└── confluence/                         <- source system
    └── lf-toip.atlassian.net/          <- site
        ├── export-users.csv            <- site user directory, for mentions
        └── HOME-CSV/                   <- a space export
            ├── content.csv
            ├── bodycontent.csv
            ├── spaces.csv
            └── exportDescriptor.properties
```

An export directory can be called anything. Confluence names the download, not
the directory, so an unpacked archive may well land somewhere called `csv`
while holding the XML export. Each export is recognised by the files inside it
and its space key is read from its own metadata, so renaming a directory
changes nothing about what is parsed — but it does change the registry keys, so
a rename needs a `--prune` run to retire the old ones.

Confluence's own `.csv.gz` names are not reliable either — it shipped 49 files
so named with none of them compressed. Every export file is sniffed for the
gzip magic bytes instead, so `content.csv` and `content.csv.gz` are both read
correctly whatever is inside them.

### Organizational metadata

Each directory may hold a `metadata.json`. The parser walks **upwards** from a
file until it finds one, so metadata set high in the tree is inherited by
everything beneath it.

The fetcher writes one per meeting series, taking the project display name and
calendar URL from the LFX API rather than assuming a project:

```json
{
  "project": "Besu",
  "organizational_unit": ["Besu Contributors Meeting"],
  "title": "Besu Contributors Meeting",
  "source_url": "https://zoom-lfx.platform.linuxfoundation.org/meetings/besu"
}
```

For Zoom, `project`, `organizational_unit` and `title` are required —
ingestion raises `FileNotFoundError` for any asset with no `metadata.json`
above it. For Confluence a `metadata.json` is optional, since the export
carries the same information; where one exists its values win.

---

## Step 2: Generate JSON documents

```bash
cd mcp_data_generation
./.venv/bin/python -m src.ingestion_engine
```

Run it as a **module** (`-m src.ingestion_engine`), not as a file path:
`python src/ingestion_engine.py` fails because the `src` package would not
resolve.

This walks the raw tree, detects the source system, parses each file, generates
a SAID, and writes one JSON document per file into `data/processed`.

### Using a read-only cache

Raw source material is not kept in this repository. It lives in
[LFDT-way/mcp-cache](https://github.com/LFDT-way/mcp-cache) so the downloads
are durable and shared rather than re-fetched per checkout. Zoom assets are
about 34 MB packed and 113 MB checked out; the Confluence export is 185 MB.

Only the files a parser reads are kept there. A Confluence space export arrives
with far more: 45 further database tables, and attachments that for one space
came to 8,047 files and 12.7 GB — PDFs, slide decks, spreadsheets and video
that nothing here can extract text from. Those dominate a clone for content
that never reaches the index, so they are left out of the cache, and the walker
skips any `attachments` or `styles` directory regardless (see
[Confluence](#confluence) above).

The raw tree is only ever read — nothing inside it is created, modified or
deleted — so it can be a shared durable cache while output goes elsewhere:

```bash
./.venv/bin/python -m src.ingestion_engine \
    --raw ~/W/github-mirror/LFDT-way/mcp-cache
```

Paths resolve from the command line, then the environment, then the defaults:

| Option | Environment variable | Default |
|--------|---------------------|---------|
| `--raw` | `MCP_RAW_DIR` | `data/raw` |
| `--processed` | `MCP_PROCESSED_DIR` | `data/processed` |
| `--registry` | `MCP_REGISTRY_FILE` | `data/ingestion_registry.json` |

So a cache can also be selected per shell:

```bash
export MCP_RAW_DIR=~/W/github-mirror/LFDT-way/mcp-cache
./.venv/bin/python -m src.ingestion_engine
```

The engine **refuses to start** if `--processed` or `--registry` would land
inside the raw tree, which keeps a cache checkout clean.

Other options: `--force` reprocesses everything, `--dry-run` reports what would
happen and writes nothing, `--quiet` prints only the closing summary.

### Clearing out strays, and removing a source

Documents are written as `<said>.json`, so changing a parser gives the same
source file a new SAID and leaves the old document behind. `--prune` deletes
documents in `--processed` that no registry entry points at:

```bash
./.venv/bin/python -m src.ingestion_engine \
    --raw ~/W/github-mirror/LFDT-way/mcp-cache --force --prune
```

It is deliberately narrow. A file is only deleted when it parses as JSON and
its `identifier` equals its filename stem, so anything else sharing the
directory is reported and left alone. A document whose SAID is still in the
registry is kept, so sources that were not part of this run keep their
documents. Pruning is skipped entirely — with a warning — when the registry is
empty or the run matched no source files, since either would make every
document look like a stray.

`--prune` also forgets registry entries whose source file is **gone from the
raw tree**, which is what makes removing or renaming a source take effect. The
walk only ever visits files that exist, so a deleted source is never
reconsidered: its entry would stay, the entry would keep reading as "these
documents are still wanted", and its documents would become permanent. Deleting
a space export from the cache would otherwise leave its pages in the corpus for
good, and *renaming* its directory is worse — the old entry keeps the old
documents alive while the new path is ingested as a new source, so the pages
end up in twice.

This part trusts `--raw` to be the whole cache, which is why it is tied to
`--prune` rather than done on every run: a run pointed at part of the tree
would see the rest as vanished. Nothing is lost either way, since a later full
run re-ingests the file and writes the entry again. Each forgotten entry is
named in the output.

Pair it with `--dry-run` to see what would go before anything is deleted.

Dot-directories under the raw root are skipped, so pointing `--raw` at a git
checkout does not walk `.git`.

### Ingestion registry

The registry records a SHA-256 per ingested file so unchanged files are skipped
on later runs. The hash is taken over the file's **uncompressed** contents, so
a gzipped source and the same source expanded hash alike and recompressing the
cache is invisible to the pipeline:

```json
{
  "zoom/egwg_reporting_tools_task_force_meeting/GMT20261021-160053_Recording.transcript.vtt": {
    "file_hash": "...",
    "document_saids": ["..."],
    "processed_at": "..."
  }
}
```

A parser usually turns one file into one document, but it may return several —
a Confluence XML export is a whole space in a single file — so an entry records
a **list** of SAIDs. Entries written before this carry a single
`document_said` and are still read.

`processed_at` is the time the content was *first* ingested, not the time the
last run touched it: an unchanged file keeps its original timestamp even under
`--force`. Curation copies it into the document as `collected_at`, which the
document's SAID covers, so bumping it would give every document a new SAID for
no change in content and invalidate every downstream index.

It is inherited by **content**, not by registry key: either the file hash
matches an entry already recorded, or one of the SAIDs the file produced does.
Renaming a source, moving it elsewhere in the cache, recompressing it or
re-exporting the same space in another format therefore keeps the identifiers
of the documents it produces. Keying it by path cost exactly that once:
renaming the Confluence exports and gzipping them re-dated 2,103 documents
whose text had not changed.

The two lookups cover different failures. The hash survives a rename and holds
even if a parser changes what it extracts; the SAIDs survive anything that
rewrites the container rather than the text. Where a key is known at several
timestamps the earliest wins, since `processed_at` means *first* ingested.

### Published content hashes

The cache states those hashes itself rather than leaving each consumer to
compute its own: the hash is the raw artifact's id, and that belongs to the
tree that owns the artifacts. A `content-sha256.tsv` sits at the root of each
collection unit — whatever is fetched or exported as a whole — and covers every
file beneath it by relative path:

```text
content-sha256.tsv                                  the cache's own files
confluence/lf-toip.atlassian.net/content-sha256.tsv  the site's files,
                                                     e.g. export-users.csv
confluence/lf-toip.atlassian.net/HOME-CSV/content-sha256.tsv   one space export
zoom/content-sha256.tsv                              every series, 4,879 files
```

A space export arrives and is replaced as a unit, so it carries its own; the
Zoom tree is fetched as a whole, so one manifest covers all of its children
however deep they sit. Compression has nothing to do with it — the Zoom assets
are plain text and are hashed just the same, because the point is to state what
was collected, not to undo a container. Anything no designated root covers
records itself where it sits, so no file in the cache goes unhashed.

```bash
python -m src.cache_manifest --raw ~/W/github-mirror/LFDT-way/mcp-cache
python -m src.cache_manifest --raw ~/W/github-mirror/LFDT-way/mcp-cache --write
```

Checking is the default and writes nothing, exiting non-zero if a recorded hash
no longer matches, a file has no recorded hash, a manifest names a file that is
gone, or a manifest outlives the files it covered. `--write` is the only tool
here permitted to modify the raw tree, and only ever these manifests.

A file that stops yielding a document — an HTML page superseded by an XML
export, say — has its registry entry dropped, so `--prune` can collect the
documents it used to produce. Without that they would be permanent.

Keys are **relative to the raw root**, so the same registry stays valid across
different checkouts of the same cache. It is written atomically (temp file then
rename) and lives outside the raw tree.

---

## Checking document identity

```bash
cd mcp_data_generation
python -m unittest discover --start-directory tests --top-level-directory .
```

Twenty-seven checks over generated fixtures — a Confluence CSV export, a Zoom
series, a cache laid out like the real one. No network, no credentials and no
clone of the cache, so the whole suite runs in under a second; GitHub Actions
runs it on every pull request (`.github/workflows/document-identity.yml`).

They come in two halves, around the one invariant that matters: **a document's
identifier changes when its content changes, and never otherwise.**

| Content already known | keeps every identifier |
| --- | --- |
| A repeated run | registry byte-identical |
| `--force` | registry byte-identical |
| Compressing an export, and expanding it again | hash and identifiers unmoved |
| Renaming the export directory | identifiers unmoved, key updated |
| Renaming *and* compressing | the change that re-dated the real corpus |
| Reading the cache from another path | identifiers unmoved |

| Content not known | gets a new identifier, or none |
| --- | --- |
| A page added to an export | one new identifier, the others untouched |
| A page edited | its identifier replaced, no superseded copy left behind |
| An unseen export | dated now, not inherited from its neighbour |
| A video, an empty transcript, an `attachments` tree | no document, no registry entry, clean exit |
| A source that is gone | entry forgotten and its documents pruned |

The fixtures are generated rather than committed. A Confluence CSV export is
four files of mutually consistent tables, and a checked-in copy would drift
from what the parser expects without anyone noticing; generating them also lets
a test say what it varies — one page body, one file name, one compression —
instead of diffing two trees.

## Step 3: Review metadata

Generated documents inherit `project`, `organizational_unit`, `title` and
`source_url` from the nearest `metadata.json`. Fixing a value means editing that
`metadata.json` and re-running with `--force`, rather than hand-editing the
generated JSON — generated documents are overwritten on every reprocess.

`source_url` is empty unless set in `metadata.json`.

---

## Step 4: Curate

Processed documents carry a bare SAID and nothing else to identify them.
Curating adds the three fields consumers expect and re-derives the identifier
as a URN SAID:

```bash
./.venv/bin/python -m src.curate
```

Reads `data/processed`, writes `data/curated`. The processed tree is only read.

| Added field | Value |
|---|---|
| `id` | human-readable and unique, e.g. `zoom-hiero-ai-summary-GMT20260407-135959` |
| `identifier_type` | always `SAID` |
| `collected_at` | when the source file was ingested, taken from the registry |

`identifier` is re-derived over the finished document as `urn:said:E...`, so it
still self-verifies with `py_verify_urn`.

The `id` includes the project because the same recording is occasionally
cross-listed under two projects — 14 such pairs exist in the current corpus, and
without the project they would collide.

Documents are named `<id>.json`, so re-running overwrites in place. `--prune`
clears curated documents the current run did not produce; `--dry-run`,
`--quiet` and `--registry` behave as in step 2.

### Recurring boilerplate

Curating strips recurring boilerplate from **AI summaries only**, and this one
matters more than it sounds. LFX copies a meeting series' standing agenda into
every summary it generates for that series, so the same paragraphs appear in
dozens of documents. In a short summary the agenda can be *most* of the
document — one 954-character summary of a test recording was 730 characters of
agenda — which makes its embedding describe the series rather than what the
meeting actually covered. Those documents then crowd the top of any search for
that series: a search for what one task force decided returned five hits that
were the same agenda text on five different dates.

A block of at least 12 words appearing in more than one summary is boilerplate.
It is **kept in the earliest summary that carries it** and dropped from the
rest, so the text stays findable exactly once rather than filling a result
page. A heading left with nothing beneath it goes too, unless what follows is a
deeper subsection. On the current corpus that trims 647 blocks from 647
documents.

**Why only AI summaries.** The pathology belongs to machine-generated text.
Human-authored pages repeat text deliberately: a Confluence meeting page
carries forward the task force's chair roster, the meeting schedule and the
Zoom links, and that is exactly the substance someone searches for. Applying
the rule to wiki pages removed all three from a January 2021 meeting page,
cutting it from 4255 characters to 2427 — a clear regression. Measured over the
current corpus the repeated blocks divide cleanly by source:

| Source / content type | Repeated blocks | Generated? |
|---|---|---|
| Zoom / AI Summary | 53 | yes — stripped |
| Confluence / Wiki Page | 58 | no — left alone |
| Zoom / Transcript | 3 | a recording cross-listed under two projects — left alone |

Document frequency cannot make this call: Confluence blocks repeat across as
many as 28 pages and summary agendas across as few as 2, so the ranges overlap
and no threshold separates them. `BOILERPLATE_CONTENT_TYPES` in `src/curate.py`
holds the eligible content types.

**No document is ever left empty.** An AI summary whose only text is the
standing agenda is, for 29 meetings here, the sole evidence the meeting
happened at all: there is no transcript, chat or captions. Such a document is
kept exactly as it was and the run reports it. Boilerplate is worth removing
because it crowds real content out of search results, and a document holding
nothing else is not crowding anything out.

Pass `--keep-boilerplate` to disable the pass entirely.

Because trimming changes `content`, it changes the SAID, so run `--prune`
alongside it to clear the documents written under the old identifiers.

Curated documents are what `toip-mcp-server` indexes.

---

## Content item model

`src/models.py` defines the document written to `data/processed`:

```json
{
  "identifier": "",
  "project": "",
  "organizational_unit": [],
  "source_system": "Zoom",
  "source_identifier": "",
  "source_url": "",
  "date": "",
  "asset_type": "Meeting",
  "content_type": "",
  "title": "",
  "content": ""
}
```

| Field | Zoom | Confluence |
|-------|------|------------|
| `identifier` | SAID generated over the document | same |
| `project` | From `metadata.json` | space name, or `metadata.json` |
| `organizational_unit` | From `metadata.json` (list) | breadcrumb trail, or `metadata.json` |
| `source_system` | `Zoom` | `Confluence` |
| `source_identifier` | Recording stem, e.g. `GMT20261021-160053` | page id, e.g. `24609309` |
| `source_url` | From `metadata.json`; empty if unset | reconstructed wiki page URL |
| `date` | Parsed from the filename | page title, else the page creation date |
| `asset_type` | `Meeting` | `Meeting` if the title is dated, else `Document` |
| `content_type` | `Transcript`, `ClosedCaptioning`, `Chat` or `AI Summary` | `Wiki Page` |
| `title` | From `metadata.json` | page title |
| `content` | Text to embed and search | same |

VTT files are flattened to plain text: `WEBVTT`, cue numbers and timestamp lines
are stripped and the remainder joined with spaces. Chat `.txt` files are stored
verbatim, preserving Zoom's `HH:MM:SS<TAB>Sender:<TAB>message` layout. Summary
`.md` files keep their markdown structure minus the front matter described
above. Confluence pages are reduced to text blocks as described under
[Confluence](#confluence).

A source file that yields no text after extraction is reported as
`no document` and no JSON is written for it.

The SAID is produced by `src/py_saidify.py` over the `identifier` field.

---

## Folder structure

```text
data/
├── raw/           # Original files (or point --raw at a shared cache)
├── processed/     # JSON documents written by the ingestion engine
├── curated/       # Documents with id / identifier_type / collected_at
└── ingestion_registry.json
```

---

## Future phases

```text
Raw Files -> Processed JSON -> Curated JSON -> Chunking -> Embeddings -> Vector DB -> MCP Server -> AI Client
```

Planned parsers: GitHub, YouTube. Each is added under `src/parsers/` and
dispatched by its source-system directory name, as `zoom` and `confluence`
already are.

The Confluence parser is seven modules, because one source system has three
export formats and they share most of their work:

| Module | Role |
|---|---|
| `confluence.py` | picks the format per file, combines the exports, drops excluded pages |
| `confluence_csv.py` | CSV export: table dump to documents |
| `confluence_xml.py` | XML export: object graph to documents |
| `confluence_html.py` | HTML export: rendered pages to documents |
| `confluence_storage.py` | storage-format markup and dates, shared by CSV and XML |
| `confluence_markup.py` | markup-to-text-blocks base, shared by all three |
| `confluence_users.py` | site account id to display name |

The split follows the work rather than the formats: storage format is one
markup language whichever export carries it, and an account id means the same
thing everywhere.

A parser may return a single `Document` or a list of them.
