# End-to-end prototype: runbook, evidence and limitations

## At a glance

This branch tests the **existing** ToIP knowledge-retrieval pipeline, from generated source artifacts through normalization and indexing to calls made by an actual MCP stdio client. It is a **synthetic demonstration** and a diagnostic baseline for [upstream issue #26](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26), not a production deployment or a new implementation of the pipeline.

| Question | Answer |
| --- | --- |
| Can an unfamiliar contributor reproduce a complete test without organizational credentials? | **Yes:** the Actions workflow has successfully executed the local synthetic path. |
| Has it been tested with real ToIP meeting and wiki knowledge? | **No:** a representative authorized corpus and human-adjudicated relevance evaluation are still required. |
| Is all tested behavior correct? | **No:** missing curated parent records can yield normal-looking retrieval results on the pinned upstream baseline; see [PR #24](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/24). |
| Does green CI mean issue #26 is closed? | **No:** CI means the declared test procedure executed successfully, including observation of an expected known defect. |
| Where should a reviewer begin? | [Run the demonstration](#run-the-demonstration), then [interpret the evidence](#how-to-read-the-evidence) and [review the outstanding acceptance requirements](#what-remains-for-upstream-issue-26). |

### Reader paths

**Adopter:** follow the local instructions and inspect the synthetic query and MCP report. **Reviewer:** use the pinned upstream baseline, the verified Actions run and the reported failure modes to judge the evidence. **Maintainer:** keep the three assurance PRs independent and reconcile real-corpus testing before determining whether #26 is complete.

## Observed results and disposition (2026-11-11)

[Actions run 43013672692](https://github.com/sankarshanmukhopadhyay/egwg-reporting-tf-prototype-mcp/actions/runs/43013672692)
completed successfully for branch commit `0ccf1aa785a63977c39b1544c762fd4491eb978c`. An earlier [substantive test run](https://github.com/sankarshanmukhopadhyay/egwg-reporting-tf-prototype-mcp/actions/runs/43013470881) passed at `2b621924`; subsequent documentation-only commits were revalidated by the latest run.
The workflow executed the existing Python tests and TypeScript tests/build,
generated and self-verified three normalized documents (one Zoom, two
Confluence), indexed three chunks, and repeated indexing with all three
documents skipped as already present. The CLI query and actual stdio
MCP-client calls succeeded for discovery, pagination, filtering, empty
results and traceable search output.

The deliberately injected missing-parent scenario reproduced a **FAIL**:
unresolved hits can be presented as normal search results on upstream main.
This is evidence for prioritizing PR #24, **not** evidence that #24 has been
reviewed or merged. The Actions workflow is green because the diagnostic
probe itself executed and correctly recorded this known defect; its result
must not be interpreted as assurance PASS. The synthetic *execution* disposition is **CONDITIONAL PASS**, not a full
#26 acceptance decision: the provenance defect remains unresolved and
real-corpus validation is still required. The failure must not be silently
counted among passing assurance checks.
The run uploaded `prototype-e2e-evidence` containing the synthetic manifest,
CLI query and MCP client report (14-day retention). No approved real ToIP
corpus was evaluated. See the later November 12 consolidated section for additional
negative-case evidence; this paragraph reports the earlier baseline run.

This result describes the tested commit, not future changes. Recheck Actions on any subsequent review head.

## Consolidated evidence (November 12, 2026)

The existing CI-backed demonstration and [ManthanNimodiya's independent issue #26 assessment](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26#issuecomment-6817798276) exercised the same pinned upstream commit, but different synthetic corpora, runtimes and test matrices. **The observations are complementary, not interchangeable.** Manthan tested 8 documents/11 chunks using Node 22, Python 3.13 and 31 SDK calls. This fork's smaller repeatable fixture uses 3 documents/3 chunks with Node 24/Python 3.12. Neither evaluated authorized real-world source content, and his [script and logs](https://gist.github.com/ManthanNimodiya/022069a5186d763b4686cf4e22265449) have since been supplied for independent reproduction, but have not been executed by this fork's CI.

This fork now runs **isolated negative-case probes** in addition to the initial end-to-end smoke path. They record results in `normalization-failures.json` and `mcp-client-report.json`. They are diagnostic reproductions, **not** fixes to normalization, index pruning, or MCP retrieval. A recorded `FAIL` remains an assurance defect even when the automation successfully captures it.

| Boundary and finding | Evidence origin | Consolidated assessment |
| --- | --- | --- |
| Unchanged ingestion, curation and index reruns | Automated fork CI and Manthan's independent report | PASS for the synthetic inputs only |
| SDK discovery, search, filtering and pagination | Both; additional parameter/error cases reported by Manthan | Narrow synthetic client behavior verified; not client interoperability certification |
| Missing curated parent presented as ordinary retrieval result | Independently observed on both | FAIL; candidate change in upstream [PR #24](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/24), not integrated here |
| Edited source without ingestion/curation pruning can leave superseded content selected | Manthan; independently probed in fork normalization workflow | Material integrity failure, not a requirement to redesign curation under #26 |
| Altered curated metadata with unchanged SAID accepted on retrieval | Manthan; independently probed through fork stdio MCP client | Material integrity failure: a SAID-like reference is not verified integrity |
| Missing ingestion registry changes identifiers and suppresses artifact linkage | Manthan; independently probed in fork normalization workflow | Material provenance/identity-continuity failure |
| Stale chunks after edits; missing data directory | Manthan's report (fork also separately exercises missing-parent corpus) | Reported failures; full Manthan test matrix not yet independently incorporated |
| Malformed curated JSON interrupts ingest | Manthan's report | Reported on the pinned baseline; [PR #28](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/28) subsequently merged into upstream main, but is absent from the pinned control |
| Empty query, unknown WG, nonlexical query, listing identifiers | Manthan's report | Interface and evidence-interpretation limitations; do not convert weak candidate retrieval into an answer-quality claim |

The fork probes deliberately keep each altered input in an isolated temporary directory or independent MCP process. They do not change production implementation, trusted fixtures, or the original index. The external report's distinct observations must remain attributed to its author; automated reproductions are credited to the fork harness rather than misrepresented as Manthan's original scripts.

### Upstream #26 acceptance accounting

This is a **validation disposition**, not issue closure or a claim of upstream acceptance.

| Issue criterion | Evidence status |
| --- | --- |
| Pinned commit, runtime and manifest | PARTIAL: fully documented synthetic baseline; approved representative corpus still unavailable |
| Existing suites and build | PASS on CI synthetic workflow and separately reported by Manthan |
| Acquisition-to-retrieval pipeline | PASS for generated Zoom/Confluence fixtures only |
| Normalized records, verified SAIDs, unchanged rerun | PASS for unmodified generated fixtures; integrity/registry adverse failures recorded separately |
| Human-adjudicated real-corpus retrieval | NOT DONE; no authorization or real corpus available |
| Actual SDK MCP calls, filtering, pagination, errors | PASS on targeted fork paths; broader manual matrix reported by Manthan, [scripts/logs supplied](https://gist.github.com/ManthanNimodiya/022069a5186d763b4686cf4e22265449), independent replay pending |
| Attribution and adverse lineage/index behavior | PARTIAL: reproducible failures for important boundaries; not all negative cases automated |
| Independent assessment of PRs #23–#25 | PARTIAL: mapped by responsibility, not evaluated against their reviewed heads |
| Consolidated report and disposition | PARTIAL: synthetic evidence and known defects consolidated here; independent governance acceptance and real-corpus adjudication remain |

**Disposition: CONDITIONAL PASS for synthetic execution only; overall issue #26 remains INDETERMINATE pending approved real-corpus relevance adjudication and independent acceptance.** This distinction prevents missing evidence from being counted as PASS. Recording material defects does not itself require fixing them to complete the issue's validation objective; corrective changes belong to their respective owner workstreams.

## Purpose and authority

This is a **validation artifact**, not an alternative implementation, new data
model, production deployment, or claim of ToIP organizational knowledge quality.
The normative handoffs remain in `AGENTS.md`, implemented on upstream `main`.
The target for upstream contribution is
[issue #26](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/issues/26).

The initial control baseline is upstream commit
`7c35c6ba2dde40bd451bafe03685748167299271` (PR #22 integrated).
Fork branch: `test/end-to-end-prototype-validation`. No unmerged work from
#23, #24, or #25 is silently incorporated.

## What gets exercised

| Boundary | Input | Check | Does not prove |
| --- | --- | --- | --- |
| Acquisition fixture | Locally generated Zoom VTT and Confluence CSV | Format selection and raw-to-processed handoff | Live network acquisition, access to real Zoom/Confluence |
| Normalization | Processed records, ingestion registry | Source diversity, deterministic rerun, self-addressed curated documents | Authenticity of source or approval to publish it |
| Indexing | Curated synthetic documents | Indexed chunks, repeated ingestion, CLI results | Retrieval relevance on real ToIP corpus |
| MCP transport | Stdio child process, official client SDK | Tool discovery, search, listing, filters, pagination, empty results | Other MCP clients, hosts, or transport modes |
| Negative case | Existing index with intentionally absent parent corpus | Whether unresolved provenance is represented as normal evidence | Corrupt index detection or full Raw Artifact verification |

A SAID verifies the serialized curated representation. It does **not** by
itself authenticate the source system or its asserted authority. The vector
index is derived and rebuildable; search results are retrieval-time joins, not
authoritative records.

## Run the demonstration

Requirements: Python 3.12, Node.js 24, npm and network access **once** for
dependency and embedding-model installation. No account credentials, Zoom
download, or external ToIP corpus are needed for this synthetic demonstration.
The first `fastembed` invocation downloads a model, so an offline runtime
with no cached model cannot finish the indexing stage. The commands use `npm ci` rather than `npm install` to honor the lockfile. The `--rebuild` flag **deletes the index at `toip-mcp-server/.index`**; run in a disposable checkout or preserve any existing local index first.

Clone or check out this fork's `test/end-to-end-prototype-validation` branch and run the following commands **from the repository root**. The data generator creates an isolated corpus; the indexer writes to `toip-mcp-server/.index`.

```bash
cd mcp_data_generation
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover --start-directory tests --top-level-directory . --verbose
.venv/bin/python -m tests.prototype_demo --output ../.prototype-demo
.venv/bin/python -m tests.prototype_failures --output ../.prototype-demo
cd ../toip-mcp-server
npm ci
npm test
npm run build
npm run ingest -- --data ../.prototype-demo/curated --rebuild
npm run ingest -- --data ../.prototype-demo/curated
TOIP_DATA_DIR=../.prototype-demo/curated npm run query -- --json "governance framework"
TOIP_DEMO_OUTPUT=../.prototype-demo node test/prototype-client.mjs
```

`tests.prototype_demo` intentionally refuses an existing non-empty output
directory. To rerun from scratch, select a **new empty output directory** and
set `TOIP_DEMO_OUTPUT` consistently for the Node probe. Keep `TOIP_DATA_DIR` set for direct CLI queries, because ingestion's `--data` option does **not** persist a query-time data-directory setting. The MCP-client probe sets its own corpus path from `TOIP_DEMO_OUTPUT` and invokes the compiled server. Do not point these commands at production data or use `--prune` on an actual corpus.

After generating the fixture, run `python -m tests.prototype_failures --output ../.prototype-demo` **from `mcp_data_generation`** to reproduce normalization failures. This command leaves the baseline files untouched and writes a separate diagnostic report; the workflow invokes it automatically.

The demonstration uses the existing generated fixture builders from
`mcp_data_generation/tests/fixtures.py`: one synthetic Zoom transcript and
two synthetic Confluence pages plus an excluded historical version. It does not
redistribute meeting transcripts or Confluence exports.

## How to read the evidence

`manifest.json` reports input names/digests, number of curated documents,
sources, verified SAIDs, curated-output hashes and unchanged ingest/curation
reruns. `mcp-client-report.json` records tool discovery, pagination, filters,
retrieval, malformed requests, empty paths and adverse provenance probes.
`normalization-failures.json` records isolated normalization lifecycle failures. GitHub Actions also
uploads the synthetic CLI output as `query.json`.

The workflow publishes the four synthetic evidence files under its **prototype-e2e-evidence** artifact (14-day retention); the files also remain under `.prototype-demo/` when run locally:

| File | What it establishes | What it cannot establish |
| --- | --- | --- |
| `manifest.json` | Input hashes and source types, curated counts, verified SAIDs, rerun stability and curated hashes | Authenticity, consent or real-source coverage |
| `query.json` | CLI result structure, identifiers and source fields through the actual retrieval implementation | Semantic correctness of the passages or source authenticity |
| `mcp-client-report.json` | Actual SDK/stdio tool discovery, search, listing/filter/pagination checks, invalid request cases, missing-parent and metadata-tampering observations | Conformance across other MCP clients or production assurance |
| `normalization-failures.json` | Isolated source-revision and missing-registry reproductions using actual normalization entry points | Corrected behavior, source authenticity, or full corpus lifecycle coverage |

Reports use these **check outcomes**:

- **PASS**: executed check proved its *narrow* assertion on the pinned corpus.
- **FAIL**: observed behavior contradicts an expected boundary. In particular,
  main may render a missing-parent hit as ordinary retrieval evidence. This
  is an existing problem targeted by upstream PR #24, and must remain visible
  even if this demonstrator's CI completes successfully.
- **INDETERMINATE**: requested execution or evidence is missing or did not
  match a supported outcome. Do not translate this into PASS.

A green synthetic workflow is **not** an acceptance judgment on the overall
prototype. Unknown source authenticity, actual retrieval usefulness, and live
client interoperability remain unresolved until separately tested.

## What remains for upstream issue #26

Issue #26 also requires a representative, **approved** Zoom/Confluence corpus
and human-adjudicated retrieval. The synthetic run does not satisfy these
requirements. Record the following in an access-controlled validation report:

1. Repository commit, OS, runtimes, lockfiles, model version and configuration.
2. Source manifest: per-source hashes, capture date, access permissions,
   retention restrictions and whether transformed excerpts may be shared.
3. Input/output counts for acquisition, processing, curation and indexing.
4. Curated SAID verification and repeated-ingest/curation behavior.
5. A human-approved query set (exact term, conceptual match, source-specific,
   group-filtered, ambiguous, cross-document, and deliberately unsupported).
6. For each query: expected relevant source passage, actual retrieval order,
   false-relevance evidence, missing attribution and independently adjudicated
   judgment. Distinguish precision, recall and reciprocal-rank metrics where
   proposed in PR #23 from source authenticity.
7. Actual MCP-client captures for `search` and `list_documents`, filtering,
   pagination, empty/error paths, and source-reference inspection.
8. Injected or isolated missing-parent, mismatched identifier, stale-index and
   invalid-record scenarios; record both expected and actual outcomes.
9. One disposition: `PASS`, `CONDITIONAL PASS`, or `INDETERMINATE`, with
   blocking defects, material assurance gaps and non-blocking limitations
   identified separately.

Do not commit confidential meeting content, authentication tokens, sensitive
personal information or real-corpus outputs to public fixtures or Action
artifacts. Limit any shared report to non-sensitive counts, sanitized references
and independently reproducible assertions. The test workflow deliberately
publishes synthetic evidence only.

## Collaboration and upstream contribution

The existing work is complementary:

- [#23](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/23)
  supplies offline evaluation metrics, not a real-corpus result.
- [#24](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/24)
  addresses unresolved retrieval-time provenance joins.
- [#25](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/25)
  supplies read-only index consistency diagnostics.

Keep these PRs independent. Evaluate their **current reviewed heads** once
integrated and refresh the baseline explicitly. Do not duplicate production
logic inside this demonstrator. Manthan's independent findings are attributed in the consolidated evidence
above. His offered original client script and logs are not assumed available;
they should be reconciled when shared, rather than overwritten or described
as if executed in this fork.

Before upstream merge: confirm required contribution/DCO policy; verify the
current branch head in Actions; run system-level adversarial review; disclose
residual findings; obtain independent code and governance acceptance.
An AI-assisted implementation or self-review is not independent review.

## Current limits

The fixture is synthetic and small; its query matches are not evidence of
real-world ranking quality. The CLI and MCP interface are exercised through the
actual repository code but without remote consumer interoperability. The current
suite does not verify a full Raw Artifact record join, cannot attest content
authenticity and does not exercise live acquisition. An embedding-model download
may depend on availability of external hosting. These are deliberate
limitations, not implicitly completed issue #26 acceptance criteria.


## Candidate PR review-head assessment (November 13, 2026)

The following are **independent candidate changes**, not capabilities of pinned baseline `7c35c6b`. Their CI status and scope should be reassessed on the review heads below before merge.

| Candidate | Review head | Observed failure addressed | Evidence and outstanding boundary |
| --- | --- | --- | --- |
| [#23](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/23) | `9418e7d` | Retrieval ranking lacks a reproducible comparison surface | Offline evaluation utility can assess *captured* results. It does not establish real-corpus relevance or make ranking trustworthy without human-judged queries. |
| [#24](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/24) | `a85f169` | Missing/mismatched normalized parent yields an ordinary result | Candidate distinguishes unresolved retrieval hits; it does not independently validate SAID-bearing payload integrity, source-system authenticity or the artifact v2 contract. |
| [#25](https://github.com/trustoverip/egwg-reporting-tf-prototype-mcp/pull/25) | `d9a0c8f` | Stale index and missing parent can remain unnoticed until retrieval | Read-only index consistency diagnostics do not repair the index or establish correctness of curated record identity and content. |

**Evidence classification:** the candidate scopes and current heads above were inspected against the public PR descriptions; these are *assessment mappings*, not reports of independently rerunning each head's implementation tests. Each candidate requires its own passing CI and maintainer review. The pinned control baseline intentionally excludes all three. Upstream #28 has subsequently merged, but that correction is also absent from the pinned baseline.

**External independent contribution:** Manthan's [MCP SDK scripts and failure logs](https://gist.github.com/ManthanNimodiya/022069a5186d763b4686cf4e22265449) were supplied after the first validation report. They are linked for reproducibility, not misrepresented as incorporated into this CI workflow.

**Disposition for #26:** synthetic execution remains **CONDITIONAL PASS** and material adverse observations remain FAIL. Overall #26 remains **INDETERMINATE** until an authorized real-source corpus, adjudicated relevance evidence, and independent maintainer acceptance (or explicit acceptance of a reduced synthetic-only scope) are available. Do not tick real-corpus acceptance or infer upstream approval from this PR.
