"""Turn processed documents into curated ones.

A curated document is the Normalized Document from issue #8 and AGENTS.md:

    schema_version   1
    identifier       URN SAID (``urn:said:E...``) over the finished document,
                     so it self-verifies with ``py_verify_urn``
    document         project, organizational_unit, title, date
    provenance       artifact_id (``sha256:`` of the source file's contents,
                     from the ingestion registry), plus source_system,
                     source_identifier, source_url and collected_at until
                     Content Acquisition publishes Raw Artifact records
    content          content_type, text

Curating also strips recurring boilerplate. LFX copies a meeting series'
standing agenda into every instance of that series, so the same paragraphs
appear in dozens of AI summaries. In a short summary the agenda can be most of
the document, which makes its embedding match the series rather than what the
meeting actually covered. Each repeated block is kept in the earliest document
that carries it and dropped from the rest, so the text stays findable exactly
once instead of filling a result page.

The processed tree is only ever read. Curated documents are named after a
human-readable id, e.g. ``zoom-hiero-ai-summary-GMT20260407-135959.json``, so
re-running overwrites in place rather than accumulating strays; ``--prune``
clears any left by an earlier id scheme. The id names the file only and is not
part of the document.

    python -m src.curate
    python -m src.curate --processed data/processed --curated data/curated
"""

from collections.abc import Iterable, Iterator
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

from src.py_saidify import py_saidify_urn

# A block has to be at least this many words to count as boilerplate. Short
# repeated lines ("## Agenda", "Yeah.") are either structure or conversational
# filler, and removing them would gain nothing.
MIN_BLOCK_WORDS = 12

# Only these content types are searched for boilerplate.
#
# The pathology is specific to machine-generated summaries: LFX prepends a
# meeting series' standing agenda to every summary it writes, so the same
# paragraphs recur dozens of times. Human-authored pages also repeat text, but
# deliberately -- a wiki meeting page carries forward the task force's chair
# roster, the meeting schedule and the Zoom links, and that is the substance
# someone searches for, not noise. Measured over the current corpus the
# repeated blocks divide cleanly: 53 in Zoom AI summaries, 58 in Confluence
# wiki pages, 3 in Zoom transcripts (a recording cross-listed under two
# projects). Only the first group is generated boilerplate.
#
# Document frequency cannot make this call. Confluence blocks repeat across as
# many as 28 pages and summary agendas across as few as 2, so the ranges
# overlap and no threshold separates them.
BOILERPLATE_CONTENT_TYPES = frozenset({"AI Summary"})

DEFAULT_PROCESSED_DIR = Path("data/processed")
DEFAULT_CURATED_DIR = Path("data/curated")
DEFAULT_REGISTRY_FILE = Path("data/ingestion_registry.json")

SCHEMA_VERSION = 1


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def build_id(document: dict) -> str:
    """Human-readable, unique id.

    Source system, project, content type and the recording stem together are
    unique across the corpus: the same recording is occasionally cross-listed
    under two projects, which is why the project has to be part of the id.
    """

    return "-".join(
        part
        for part in (
            _slug(document.get("source_system", "")),
            _slug(document.get("project", "")),
            _slug(document.get("content_type", "")),
            document.get("source_identifier", "") or "unknown",
        )
        if part
    )


def _blocks(content: str) -> list[str]:
    """Split content into paragraph blocks on blank lines."""

    return [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]


def _block_key(block: str) -> str:
    """Identity of a block, ignoring whitespace and case differences."""

    normalized = " ".join(block.split()).lower()

    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _heading_level(block: str) -> int:
    """Markdown heading depth, or 0 if the block is not a heading."""

    stripped = block.lstrip()
    if not stripped.startswith("#"):
        return 0

    return len(stripped) - len(stripped.lstrip("#"))


def _doc_sort_key(document: dict) -> tuple[str, str]:
    """Which document keeps a shared block: the earliest, then by id.

    Including the id keeps the choice stable when two documents share a date,
    so repeated runs strip the same copies.
    """

    return (document.get("date", ""), build_id(document))


def _iter_documents(paths: list[Path]) -> Iterator[dict]:
    """Yield each processed document in turn, holding only one at a time."""

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                yield json.load(f)
        except (OSError, json.JSONDecodeError):
            continue


def boilerplate_owners(documents: Iterable[dict]) -> dict[str, tuple[str, str]]:
    """Map each repeated block to the one document allowed to keep it.

    A block counts as boilerplate when it appears in more than one document
    whose content type is in ``BOILERPLATE_CONTENT_TYPES``. Only blocks of at
    least ``MIN_BLOCK_WORDS`` words are considered.
    """

    first_seen: dict[str, tuple[str, str]] = {}
    repeated: set[str] = set()

    for document in documents:
        if document.get("content_type") not in BOILERPLATE_CONTENT_TYPES:
            continue

        key = _doc_sort_key(document)
        seen: set[str] = set()

        for block in _blocks(document.get("content", "")):
            if len(block.split()) < MIN_BLOCK_WORDS:
                continue

            block_key = _block_key(block)

            # A block repeated inside one document is not boilerplate; what
            # matters is appearing in more than one document.
            if block_key in seen:
                continue
            seen.add(block_key)

            if block_key in first_seen:
                repeated.add(block_key)
                if key < first_seen[block_key]:
                    first_seen[block_key] = key
            else:
                first_seen[block_key] = key

    return {block_key: first_seen[block_key] for block_key in repeated}


def strip_boilerplate(
    document: dict, owners: dict[str, tuple[str, str]]
) -> tuple[str, int, bool]:
    """Remove repeated blocks this document does not own.

    A document is never left empty. Stripping everything would happen in two
    situations, and losing the document is wrong in both:

    * an AI summary whose only text is the series' standing agenda, where the
      summary is the sole record that the meeting took place at all; and
    * a recording cross-listed under two projects, where a transcript is a
      single block and so looks "repeated" in its entirety.

    In either case the document is kept exactly as it was. Boilerplate is worth
    removing because it crowds real content out of search results, and a
    document with no other content is not crowding anything out.

    Returns (content, blocks_removed, kept_whole).
    """

    content = document.get("content", "")
    if not content:
        return content, 0, False

    if document.get("content_type") not in BOILERPLATE_CONTENT_TYPES:
        return content, 0, False

    key = _doc_sort_key(document)
    kept: list[str] = []
    removed = 0

    for block in _blocks(content):
        if len(block.split()) >= MIN_BLOCK_WORDS:
            block_key = _block_key(block)
            owner = owners.get(block_key)
            if owner is not None and owner != key:
                removed += 1
                continue
        kept.append(block)

    if removed == 0:
        return content, 0, False

    # Drop a heading left with nothing beneath it, which is what removing an
    # "## Agenda" body leaves behind. A heading is empty when the next block is
    # a heading at the same or a shallower level, or when nothing follows it;
    # a deeper heading is a subsection, so the parent stays.
    pruned: list[str] = []
    for i, block in enumerate(kept):
        level = _heading_level(block)
        if level:
            following = kept[i + 1] if i + 1 < len(kept) else None
            if following is None:
                continue
            next_level = _heading_level(following)
            if next_level and next_level <= level:
                continue
        pruned.append(block)

    stripped = "\n\n".join(pruned)

    if not stripped.strip():
        return content, 0, True

    return stripped, removed, False


def _source_index(registry_file: Path) -> dict[str, tuple[str, str]]:
    """Map a processed SAID to (collected_at, artifact_id) of its source file.

    When the same content came from several files, the earliest ingestion wins
    so the choice does not depend on registry order.
    """

    if not registry_file.is_file():
        raise ValueError(f"ingestion registry is required for provenance: {registry_file}")

    try:
        with open(registry_file, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read ingestion registry: {registry_file}") from exc
    if not isinstance(registry, dict):
        raise ValueError("ingestion registry must contain a JSON object")

    # A file may yield several documents, so an entry records a list of SAIDs.
    # Entries written before that carry a single "document_said".
    sources: dict[str, tuple[str, str]] = {}

    for path_key, entry in registry.items():
        if not isinstance(entry, dict):
            raise ValueError(f"invalid ingestion registry entry: {path_key}")
        processed_at = entry.get("processed_at")
        if not isinstance(processed_at, str) or not processed_at.strip():
            raise ValueError(f"missing ingestion timestamp for registry entry: {path_key}")
        file_hash = entry.get("file_hash", "")
        if not isinstance(file_hash, str) or re.fullmatch(r"[0-9a-f]{64}", file_hash) is None:
            raise ValueError(f"invalid file hash in ingestion registry: {path_key}")
        artifact_id = f"sha256:{file_hash}"
        saids = entry.get("document_saids")

        if not isinstance(saids, list):
            said = entry.get("document_said")
            saids = [said] if said else []
        if not all(isinstance(said, str) and said for said in saids):
            raise ValueError(f"invalid document references in ingestion registry: {path_key}")

        for said in saids:
            candidate = (processed_at, artifact_id)
            if said not in sources or candidate < sources[said]:
                sources[said] = candidate

    return sources


def _artifact_membership(registry_file: Path, catalog_dir: Path) -> dict[str, list[str]]:
    """Map processed document SAIDs to all *registered contributing* file digests.

    This uses explicit ingestion relationships, never filename or meeting-ID
    proximity. The catalog is required but does not authenticate a source.
    """
    if not registry_file.is_file():
        raise ValueError(f"registry is unavailable: {registry_file}")
    if not catalog_dir.is_dir():
        raise ValueError(f"artifact catalog is unavailable: {catalog_dir}")
    try:
        registry = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("could not read artifact membership registry") from exc
    if not isinstance(registry, dict):
        raise ValueError("registry must be a JSON object")
    membership: dict[str, set[str]] = {}
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            raise ValueError(f"invalid registry entry: {key}")
        processed_at = entry.get("processed_at")
        if not isinstance(processed_at, str) or not processed_at.strip():
            raise ValueError(f"missing ingestion timestamp for {key}")
        digest = entry.get("file_hash")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"missing/invalid source digest for {key}")
        artifact_id = f"sha256:{digest}"
        record_path = catalog_dir / f"{digest}.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"artifact record missing or invalid for {key}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"invalid artifact record for {key}")
        if record.get("artifact_id") != artifact_id or record.get("schema_version") != 1:
            raise ValueError(f"artifact identity mismatch for {key}")
        observations = record.get("observations")
        if not isinstance(observations, list) or not any(
            isinstance(obs, dict) and obs.get("registry_path") == key
            for obs in observations
        ):
            raise ValueError(f"source association not evidenced for {key}")
        saids = entry.get("document_saids")
        if not isinstance(saids, list):
            legacy = entry.get("document_said")
            saids = [legacy] if legacy else []
        for said in saids:
            if not isinstance(said, str) or not said:
                raise ValueError(f"invalid processed document identifier for {key}")
            membership.setdefault(said, set()).add(artifact_id)
    return {said: sorted(ids) for said, ids in membership.items()}


def curate_document(
    document: dict, collected_at: str, artifact_id: str,
    *, schema_version: int = 1, artifact_ids: list[str] | None = None
) -> tuple[str, dict]:
    """Return (urn, curated document) for one processed document.

    Key order is part of the document: the SAID is computed over the serialized
    dict, so a stable order keeps the SAID stable.
    """

    if schema_version not in (1, 2):
        raise ValueError("unsupported normalized schema version")
    if schema_version == 2 and (not artifact_ids or not all(
        isinstance(artifact, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", artifact)
        for artifact in artifact_ids
    )):
        raise ValueError("v2 requires evidenced, nonempty artifact identifiers")

    curated = {
        "schema_version": schema_version,
        "identifier": "",
        "document": {
            "project": document.get("project", ""),
            "organizational_unit": document.get("organizational_unit", []),
            "title": document.get("title", ""),
            "date": document.get("date", ""),
        },
        "provenance": (
            {"artifact_ids": sorted(set(artifact_ids))}
            if schema_version == 2
            else {
                "artifact_id": artifact_id,
                "source_system": document.get("source_system", ""),
                "source_identifier": document.get("source_identifier", ""),
                "source_url": document.get("source_url", ""),
                "collected_at": collected_at,
            }
        ),
        "content": {
            "content_type": document.get("content_type", ""),
            "text": document.get("content", ""),
        },
    }

    return py_saidify_urn(curated, label="identifier")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.curate",
        description=(
            "Turn processed documents into Normalized Documents (issue #8) "
            "identified by a URN SAID."
        ),
    )
    parser.add_argument(
        "--processed",
        default=os.environ.get("MCP_PROCESSED_DIR", DEFAULT_PROCESSED_DIR),
        help="processed documents to read (default: data/processed)",
    )
    parser.add_argument(
        "--curated",
        default=os.environ.get("MCP_CURATED_DIR", DEFAULT_CURATED_DIR),
        help="where curated documents are written (default: data/curated)",
    )
    parser.add_argument(
        "--registry",
        default=os.environ.get("MCP_REGISTRY_FILE", DEFAULT_REGISTRY_FILE),
        help="ingestion registry, read for collected_at and artifact_id",
    )
    parser.add_argument(
        "--schema-version",
        type=int, choices=(1, 2), default=1,
        help="normalized document format (default: 1; v2 opt-in and requires artifact catalog)",
    )
    parser.add_argument(
        "--artifact-catalog", type=Path,
        help="verified acquisition catalog directory, required for schema v2",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete curated documents this run did not produce",
    )
    parser.add_argument(
        "--keep-boilerplate",
        action="store_true",
        help=(
            "keep recurring blocks in every document instead of only in the "
            "earliest one that carries them"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report only; write nothing"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only print the closing summary"
    )
    return parser.parse_args(argv)


def _curate_v2_fresh(
    processed_dir: Path, curated_dir: Path, membership: dict[str, list[str]],
    *, keep_boilerplate: bool, dry_run: bool, quiet: bool,
) -> int:
    """Validate every v2 input before publishing a new output directory.

    Staging in a sibling directory makes missing or contradictory provenance
    fail without leaving a partially published corpus. The existing v1
    incremental path is deliberately untouched.
    """
    if curated_dir.exists():
        raise SystemExit(
            "schema v2 requires a fresh --curated directory; "
            "choose a new destination for deterministic regeneration"
        )
    paths = [path for path in sorted(processed_dir.glob("*.json"))
             if path.stem in membership]
    stale = len(list(processed_dir.glob("*.json"))) - len(paths)
    if stale:
        warn(f"ignoring {stale} stale processed document(s) outside current registry membership")
    missing = sorted(said for said in membership
                     if not (processed_dir / f"{said}.json").is_file())
    if missing:
        raise SystemExit(
            f"schema v2 cannot publish: {len(missing)} registry document(s) "
            "are missing from the processed tree"
        )
    if not paths:
        raise SystemExit("schema v2 refuses to publish an empty corpus")
    owners = {} if keep_boilerplate else boilerplate_owners(_iter_documents(paths))
    planned: dict[str, str] = {}
    names: dict[str, str] = {}
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"schema v2 unreadable processed input: {path.name}") from exc
        if not isinstance(document, dict):
            raise SystemExit(f"schema v2 invalid processed input: {path.name}")
        said = document.get("identifier")
        if not isinstance(said, str) or not membership.get(said):
            raise SystemExit(
                f"schema v2 cannot establish contributing artifacts for {path.name}"
            )
        if owners:
            content, removed, kept_whole = strip_boilerplate(document, owners)
            if removed and not kept_whole:
                document["content"] = content
        try:
            urn, curated = curate_document(
                document, "", "", schema_version=2, artifact_ids=membership[said]
            )
            document_id = build_id(document)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SystemExit(f"schema v2 cannot normalize {path.name}: {exc}") from exc
        if document_id in names:
            raise SystemExit(
                f"schema v2 duplicate document id {document_id}: "
                f"{path.name} and {names[document_id]}"
            )
        names[document_id] = path.name
        planned[f"{document_id}.json"] = (
            json.dumps(curated, indent=2, ensure_ascii=False) + "\n"
        )
        if not quiet:
            print(f"validate {document_id} -> {urn}")
    if dry_run:
        print(f"validated {len(planned)} v2 document(s); no files written")
        return 0

    curated_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".curated-v2-", dir=curated_dir.parent))
    try:
        for name, payload in planned.items():
            (stage / name).write_text(payload, encoding="utf-8")
        # Refuse an existing output even if it was created while staging.
        if curated_dir.exists():
            raise SystemExit("schema v2 output appeared during staging; refusing overwrite")
        stage.rename(curated_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"published {len(planned)} schema v2 document(s) to {curated_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    processed_dir = Path(args.processed).expanduser().resolve()
    curated_dir = Path(args.curated).expanduser().resolve()
    registry_file = Path(args.registry).expanduser().resolve()

    if not processed_dir.is_dir():
        raise SystemExit(f"processed tree not found: {processed_dir}")

    if curated_dir == processed_dir:
        raise SystemExit("--curated must differ from --processed")

    if args.schema_version == 2:
        if args.artifact_catalog is None:
            raise SystemExit("schema v2 requires --artifact-catalog")
        if args.prune:
            raise SystemExit("schema v2 publishes a fresh directory; --prune is unsupported")
        try:
            membership = _artifact_membership(
                registry_file, args.artifact_catalog.expanduser().resolve()
            )
        except ValueError as exc:
            raise SystemExit(f"schema v2 source evidence unavailable: {exc}") from exc
        return _curate_v2_fresh(
            processed_dir, curated_dir, membership,
            keep_boilerplate=args.keep_boilerplate,
            dry_run=args.dry_run, quiet=args.quiet,
        )

    try:
        sources = _source_index(registry_file)
    except ValueError as exc:
        raise SystemExit(f"curation provenance unavailable: {exc}") from exc

    print(f"processed {processed_dir}  (read-only)")
    print(f"curated   {curated_dir}")
    if args.dry_run:
        print("dry-run: nothing will be written")

    # Processed files are cached outputs; registry membership selects current versions.
    paths: list[Path] = []
    stale = 0
    for path in sorted(processed_dir.glob("*.json")):
        if path.stem not in sources:
            stale += 1
            continue
        paths.append(path)
    if stale:
        warn(f"ignoring {stale} processed document(s) no longer referenced by the ingestion registry")
    missing = sorted(said for said in sources if not (processed_dir / f"{said}.json").is_file())
    if missing:
        raise SystemExit(
            f"curation cannot publish: {len(missing)} registry document(s) "
            "are missing from the processed tree"
        )
    if not args.dry_run:
        curated_dir.mkdir(parents=True, exist_ok=True)

    owners: dict[str, tuple[str, str]] = {}
    if args.keep_boilerplate:
        print("boilerplate: keeping repeated blocks in every document")
    else:
        owners = boilerplate_owners(_iter_documents(paths))
        print(f"boilerplate: {len(owners)} repeated block(s) found across the corpus")

    written = 0
    unchanged = 0
    failed = 0
    trimmed = 0
    blocks_removed = 0
    emptied = 0
    produced: set[str] = set()
    ids: dict[str, str] = {}

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                document = json.load(f)
        except (OSError, json.JSONDecodeError):
            warn(f"skipping unreadable {path.name}")
            failed += 1
            continue

        if owners:
            content, removed, kept_whole = strip_boilerplate(document, owners)
            if kept_whole:
                # Repeated text is all this document has, so trimming it would
                # erase the only record of the meeting. Keep it as it is.
                emptied += 1
                if not args.quiet:
                    print(f"keep     {path.name}  (nothing but repeated blocks)")
            elif removed:
                trimmed += 1
                blocks_removed += removed
                document["content"] = content

        said = document.get("identifier", "")
        stamp, artifact_id = sources[said]

        urn, curated = curate_document(document, stamp, artifact_id)
        document_id = build_id(document)

        if document_id in ids:
            warn(
                f"duplicate id {document_id}: {path.name} collides with "
                f"{ids[document_id]}; skipping"
            )
            failed += 1
            continue

        ids[document_id] = path.name
        target = curated_dir / f"{document_id}.json"
        produced.add(target.name)

        payload = json.dumps(curated, indent=2, ensure_ascii=False) + "\n"

        if target.exists():
            try:
                if target.read_text(encoding="utf-8") == payload:
                    unchanged += 1
                    continue
            except OSError:
                pass

        if not args.dry_run:
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(target)

        written += 1
        if not args.quiet:
            print(f"curate   {document_id}  -> {urn}")

    pruned = 0
    if args.prune:
        if not produced:
            warn("--prune skipped: this run produced no documents")
        elif curated_dir.is_dir():
            for path in sorted(curated_dir.glob("*.json")):
                if path.name in produced:
                    continue
                if not args.dry_run:
                    try:
                        path.unlink()
                    except OSError:
                        continue
                pruned += 1
            verb = "would remove" if args.dry_run else "removed"
            print(f"prune: {verb} {pruned} stale curated document(s)")

    if owners:
        print(
            f"boilerplate: trimmed {blocks_removed} block(s) from {trimmed} "
            f"document(s); kept {emptied} document(s) whole because repeated "
            f"text is all they contain"
        )

    print(
        f"done: {written} written, {unchanged} unchanged, "
        f"{failed} failed, {len(produced)} curated in total"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
