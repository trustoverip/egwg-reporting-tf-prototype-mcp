"""Turn processed documents into curated ones.

A processed document carries a bare SAID in ``identifier`` and nothing else to
identify it. A curated document adds the three fields consumers expect:

    id               human-readable and unique, e.g.
                     "zoom-hiero-ai-summary-GMT20260407-135959"
    identifier_type  always "SAID"
    collected_at     when the source file was ingested

and re-derives ``identifier`` as a URN SAID (``urn:said:E...``) over the
finished document, so it still self-verifies with ``py_verify_urn``.

Curating also strips recurring boilerplate. LFX copies a meeting series'
standing agenda into every instance of that series, so the same paragraphs
appear in dozens of AI summaries. In a short summary the agenda can be most of
the document, which makes its embedding match the series rather than what the
meeting actually covered. Each repeated block is kept in the earliest document
that carries it and dropped from the rest, so the text stays findable exactly
once instead of filling a result page.

The processed tree is only ever read. Curated documents are named ``<id>.json``,
so re-running overwrites in place rather than accumulating strays; ``--prune``
clears any left by an earlier id scheme.

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
import sys
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

# Field order is part of the document: the SAID is computed over the serialized
# dict, so a stable order keeps the SAID stable.
FIELD_ORDER = (
    "id",
    "project",
    "organizational_unit",
    "source_system",
    "source_identifier",
    "source_url",
    "date",
    "asset_type",
    "content_type",
    "title",
    "identifier",
    "identifier_type",
    "collected_at",
    "content",
)


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


def _collected_at_index(registry_file: Path) -> dict:
    """Map a processed SAID to when its source was ingested."""

    if not registry_file.exists():
        return {}

    try:
        with open(registry_file, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except (OSError, json.JSONDecodeError):
        warn(f"could not read {registry_file}; falling back to file times")
        return {}

    # A file may yield several documents, so an entry records a list of SAIDs.
    # Entries written before that carry a single "document_said".
    stamps = {}

    for entry in registry.values():
        processed_at = entry.get("processed_at", "")
        saids = entry.get("document_saids")

        if not isinstance(saids, list):
            said = entry.get("document_said")
            saids = [said] if said else []

        for said in saids:
            if said:
                stamps[said] = processed_at

    return stamps


def curate_document(document: dict, collected_at: str) -> tuple[str, dict]:
    """Return (urn, curated document) for one processed document."""

    curated = {
        "id": build_id(document),
        "project": document.get("project", ""),
        "organizational_unit": document.get("organizational_unit", []),
        "source_system": document.get("source_system", ""),
        "source_identifier": document.get("source_identifier", ""),
        "source_url": document.get("source_url", ""),
        "date": document.get("date", ""),
        "asset_type": document.get("asset_type", ""),
        "content_type": document.get("content_type", ""),
        "title": document.get("title", ""),
        "identifier": "",
        "identifier_type": "SAID",
        "collected_at": collected_at,
        "content": document.get("content", ""),
    }

    return py_saidify_urn(curated, label="identifier")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.curate",
        description=(
            "Add id / identifier_type / collected_at to processed documents "
            "and re-derive the identifier as a URN SAID."
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
        help="ingestion registry, read for collected_at timestamps",
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    processed_dir = Path(args.processed).expanduser().resolve()
    curated_dir = Path(args.curated).expanduser().resolve()
    registry_file = Path(args.registry).expanduser().resolve()

    if not processed_dir.is_dir():
        raise SystemExit(f"processed tree not found: {processed_dir}")

    if curated_dir == processed_dir:
        raise SystemExit("--curated must differ from --processed")

    collected = _collected_at_index(registry_file)

    if not args.dry_run:
        curated_dir.mkdir(parents=True, exist_ok=True)

    print(f"processed {processed_dir}  (read-only)")
    print(f"curated   {curated_dir}")
    if args.dry_run:
        print("dry-run: nothing will be written")

    paths = sorted(processed_dir.glob("*.json"))

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
        stamp = collected.get(said) or datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc
        ).isoformat()

        urn, curated = curate_document(document, stamp)
        document_id = curated["id"]

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
