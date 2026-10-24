"""Walk a tree of raw source files and emit normalized JSON documents.

The raw tree is treated as READ-ONLY. Nothing is ever created, modified or
deleted inside it, so it can point at a shared, durable cache (for example a
checkout of the mcp-cache repository) while the processed documents and the
ingestion registry are written somewhere else entirely.

A parser normally turns one source file into one document, but it may return
several: a Confluence XML export holds a whole space in a single file. The
registry therefore records a list of SAIDs per file.

Paths come from the command line, then the environment, then the defaults:

    --raw        MCP_RAW_DIR        data/raw
    --processed  MCP_PROCESSED_DIR  data/processed
    --registry   MCP_REGISTRY_FILE  data/ingestion_registry.json

Run it as a module so that the ``src`` package resolves:

    python -m src.ingestion_engine
    python -m src.ingestion_engine --raw ~/W/github-mirror/LFDT-way/mcp-cache
"""

from pathlib import Path
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from dataclasses import asdict

from src.cache_manifest import MANIFEST_NAME
from src.content_hash import sha256_content
from src.models import Document
from src.py_saidify import py_saidify

DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_REGISTRY_FILE = Path("data/ingestion_registry.json")
DEFAULT_PROCESSED_DIR = Path("data/processed")

# Names that are never source documents.
SKIP_NAMES = {"metadata.json", ".DS_Store", "Thumbs.db", MANIFEST_NAME}

# Suffixes the walker ignores outright: bookkeeping left beside the assets by
# the fetcher, not content to ingest. Markdown is NOT listed here — AI summaries
# arrive as "<stem>.summary.md" and the parser decides which markdown it knows.
SKIP_SUFFIXES = {".tsv"}

# Directories that never hold text this pipeline can read. A space export's
# "attachments" are the PDFs, slide decks, spreadsheets and video a page links
# to, and "styles" is the export's own CSS. The parsers decline both anyway, so
# walking them only buys the hash: for one space export that is 8,047 files and
# 12.7 GB, about 32 seconds of pure I/O every run.
SKIP_DIRS = {"attachments", "styles"}


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _load_registry(registry_file: Path) -> dict:
    if not registry_file.exists():
        return {}

    with open(registry_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_registry(registry: dict, registry_file: Path) -> None:
    registry_file.parent.mkdir(parents=True, exist_ok=True)

    tmp_file = registry_file.with_suffix(registry_file.suffix + ".tmp")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, sort_keys=True)

    tmp_file.replace(registry_file)


def _entry_saids(entry: dict) -> list[str]:
    """The document SAIDs a registry entry points at.

    A parser may turn one source file into many documents -- a Confluence XML
    export is a whole space in one file -- so entries record a list. Entries
    written before that carry a single "document_said", and are still read.
    """

    saids = entry.get("document_saids")

    if isinstance(saids, list):
        return [said for said in saids if said]

    said = entry.get("document_said")

    return [said] if said else []


class _FirstSeen:
    """When content was first ingested, looked up by what the content is.

    processed_at is inherited by content rather than by registry key, because
    curation copies it into the document as collected_at and the document SAID
    covers it: re-dating content that has not changed hands every document it
    produces a new identifier and invalidates every downstream index.

    Two things identify content, and either is enough:

    * the source file's hash, which survives a rename or a move to another
      directory of the cache;
    * the SAIDs of the documents the file produced, which survive anything that
      changes the container rather than the text -- recompressing an export,
      or re-exporting the same space in a different format.

    A rename alone keeps the hash but gzipping does not, so the second lookup
    is what makes the guarantee hold: a document's identifier changes only when
    its normalized content changes. Where a key is known at several timestamps,
    the earliest wins, since processed_at means first ingested.
    """

    def __init__(self, registry: dict) -> None:
        self._by_hash: dict[str, str] = {}
        self._by_said: dict[str, str] = {}

        for entry in registry.values():
            processed_at = entry.get("processed_at")

            if not processed_at:
                continue

            self._record(entry.get("file_hash"), _entry_saids(entry), processed_at)

    def _keep_earliest(self, index: dict[str, str], key: str, stamp: str) -> None:
        earlier = index.get(key)

        # Every timestamp is written by this module as an ISO-8601 UTC string,
        # so comparing them as text orders them correctly.
        if earlier is None or stamp < earlier:
            index[key] = stamp

    def _record(self, file_hash: str | None, saids: list[str], stamp: str) -> None:
        if file_hash:
            self._keep_earliest(self._by_hash, file_hash, stamp)

        for said in saids:
            self._keep_earliest(self._by_said, said, stamp)

    def stamp_for(self, file_hash: str, saids: list[str]) -> str:
        """The timestamp this content already carries, or now for new content.

        The result is recorded, so a second copy of the same content later in
        the same run inherits it too rather than taking a fresh clock reading.
        """

        known = [self._by_hash[file_hash]] if file_hash in self._by_hash else []
        known += [self._by_said[said] for said in saids if said in self._by_said]

        stamp = min(known) if known else datetime.now(timezone.utc).isoformat()

        self._record(file_hash, saids, stamp)

        return stamp


def _get_source_system(file_path: Path, raw_dir: Path) -> str:
    relative_path = file_path.relative_to(raw_dir)

    return relative_path.parts[0].lower()


def _process_file(file_path: Path, raw_dir: Path, quiet: bool = False):
    source_system = _get_source_system(file_path, raw_dir)

    if source_system == "zoom":
        from src.parsers import zoom

        return zoom.process(file_path)

    if source_system == "confluence":
        from src.parsers import confluence

        return confluence.process(file_path)

    if not quiet:
        print(f"No parser registered for '{source_system}'")

    return None


def _save_document(document: dict, processed_dir: Path) -> Path:
    output_file = processed_dir / f"{document['identifier']}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)

    return output_file


def _prune_processed(
    processed_dir: Path, registry: dict, apply: bool = True
) -> tuple[int, int]:
    """Delete processed documents no registry entry points at any more.

    A document is written as "<said>.json", so changing a parser gives the same
    source file a new SAID and strands the old document. Those strays are what
    this removes.

    Only files this tool recognizes are touched: the JSON has to parse and its
    "identifier" has to equal the filename stem. Anything else is left alone,
    so an unrelated file sharing the directory is never deleted. A document is
    kept whenever its SAID appears in the registry, which means sources that
    were not part of this run keep their documents.

    Returns (removed, left_alone).
    """

    known = {
        said for entry in registry.values() for said in _entry_saids(entry)
    }

    removed = 0
    left_alone = 0

    for path in sorted(processed_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                document = json.load(f)
        except (OSError, json.JSONDecodeError):
            left_alone += 1
            continue

        said = document.get("identifier") if isinstance(document, dict) else None

        if not said or said != path.stem:
            left_alone += 1
            continue

        if said in known:
            continue

        if apply:
            try:
                path.unlink()
            except OSError:
                left_alone += 1
                continue

        removed += 1

    return removed, left_alone


def _drop_vanished(registry: dict, raw_dir: Path) -> list[str]:
    """Forget registry entries whose source file is no longer in the raw tree.

    The walk only ever visits files that exist, so a source that has been
    deleted from the cache is never reconsidered: its entry stays, --prune
    reads that entry as "these documents are still wanted", and the documents
    become permanent. Removing a space export from the cache would otherwise
    leave its pages in the corpus for good.

    This is gated on --prune because it trusts --raw to be the whole cache. A
    run pointed at part of the tree would see the rest as vanished. Nothing is
    lost either way: a later full run re-ingests the file and writes the entry
    again.

    The entries are dropped even under --dry-run, because the stray sweep that
    follows reads the registry to decide what is still wanted: leaving them in
    would report no strays where a real run deletes their documents. Nothing is
    written in that case, so the change stays in memory.

    Returns the keys that were dropped.
    """

    gone = [key for key in sorted(registry) if not (raw_dir / key).exists()]

    for key in gone:
        registry.pop(key, None)

    return gone


def _should_skip(file_path: Path, raw_dir: Path) -> bool:
    relative_parts = file_path.relative_to(raw_dir).parts

    # Anything under a dot-directory is repository plumbing, not content. The
    # cache is a git checkout, so without this the walker hashes every object
    # in .git.
    if any(part.startswith(".") for part in relative_parts):
        return True

    if file_path.name in SKIP_NAMES:
        return True

    # Only parent components are checked, so a file merely named "attachments"
    # is still offered to a parser.
    if SKIP_DIRS & {part.lower() for part in relative_parts[:-1]}:
        return True

    # "fetch-manifest.tsv" sits beside the assets but is bookkeeping, not content.
    return file_path.suffix.lower() in SKIP_SUFFIXES


def _resolve(path_value: str | os.PathLike) -> Path:
    return Path(path_value).expanduser().resolve()


def _assert_raw_is_read_only(
    raw_dir: Path, processed_dir: Path, registry_file: Path
) -> None:
    """Refuse to run if any output would land inside the raw tree."""

    for label, target in (
        ("--processed", processed_dir),
        ("--registry", registry_file),
    ):
        if target == raw_dir or raw_dir in target.parents:
            raise SystemExit(
                f"refusing to run: {label} ({target}) is inside the raw tree "
                f"({raw_dir}); the raw tree is read-only"
            )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingestion_engine",
        description=(
            "Normalize raw source files into JSON documents. The raw tree is "
            "only ever read, so it may be a shared read-only cache."
        ),
    )

    parser.add_argument(
        "--raw",
        default=os.environ.get("MCP_RAW_DIR", DEFAULT_RAW_DIR),
        help="raw source tree to read (default: data/raw, env MCP_RAW_DIR)",
    )
    parser.add_argument(
        "--processed",
        default=os.environ.get("MCP_PROCESSED_DIR", DEFAULT_PROCESSED_DIR),
        help=(
            "where processed JSON is written "
            "(default: data/processed, env MCP_PROCESSED_DIR)"
        ),
    )
    parser.add_argument(
        "--registry",
        default=os.environ.get("MCP_REGISTRY_FILE", DEFAULT_REGISTRY_FILE),
        help=(
            "ingestion registry path "
            "(default: data/ingestion_registry.json, env MCP_REGISTRY_FILE)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="reprocess files even when the registry says they are unchanged",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=(
            "after ingesting, forget registry entries whose source file is "
            "gone from the raw tree, then delete documents in --processed "
            "that no registry entry points at (strays left behind when a "
            "parser change gives a file a new SAID, or when a source is "
            "removed from the cache). Assumes --raw is the whole cache."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be processed; write nothing",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print the closing summary",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    raw_dir = _resolve(args.raw)
    processed_dir = _resolve(args.processed)
    registry_file = _resolve(args.registry)

    if not raw_dir.is_dir():
        raise SystemExit(f"raw tree not found: {raw_dir}")

    _assert_raw_is_read_only(raw_dir, processed_dir, registry_file)

    if not args.dry_run:
        processed_dir.mkdir(parents=True, exist_ok=True)

    registry = _load_registry(registry_file)
    first_seen = _FirstSeen(registry)

    counts = {
        "processed": 0,
        "files": 0,
        "skipped": 0,
        "ignored": 0,
        "no_document": 0,
        "unregistered": 0,
    }

    print(f"raw       {raw_dir}  (read-only)")
    print(f"processed {processed_dir}")
    print(f"registry  {registry_file}")
    if args.dry_run:
        print("dry-run: nothing will be written")

    for file_path in sorted(raw_dir.rglob("*")):
        if not file_path.is_file():
            continue

        if _should_skip(file_path, raw_dir):
            counts["ignored"] += 1
            continue

        # Keyed relative to the raw root so the registry stays valid when the
        # same content is read from a different checkout of the cache.
        registry_key = file_path.relative_to(raw_dir).as_posix()

        # Hashed by contents, not by container: see src.content_hash.
        file_hash = sha256_content(file_path)
        registry_entry = registry.get(registry_key)

        if (
            not args.force
            and registry_entry
            and registry_entry["file_hash"] == file_hash
        ):
            counts["skipped"] += 1
            if not args.quiet:
                print(f"skip     {registry_key}")
            continue

        result = _process_file(file_path, raw_dir, quiet=args.quiet)

        documents = [] if result is None else (
            result if isinstance(result, list) else [result]
        )

        if not documents:
            counts["no_document"] += 1

            # A file that used to yield a document and no longer does leaves a
            # registry entry behind, and --prune treats anything the registry
            # names as still wanted -- which would make the old documents
            # permanent. Dropping the entry lets them be collected.
            if not args.dry_run and registry.pop(registry_key, None) is not None:
                counts["unregistered"] += 1

            continue

        saids = []

        for document in documents:
            document_dict = asdict(document)

            said, document_dict = py_saidify(document_dict, label="identifier")
            saids.append(said)

            if not args.dry_run:
                _save_document(document_dict, processed_dir)

            if not args.quiet:
                label = (
                    f"{registry_key}#{document.source_identifier}"
                    if len(documents) > 1
                    else registry_key
                )
                print(f"process  {label}  -> {said}")

        if not args.dry_run:
            # Keep the original timestamp when the bytes have not changed.
            # processed_at means "when this content was first ingested", and
            # curation copies it into the document as collected_at, which is
            # covered by the document's SAID. Bumping it on a --force run would
            # give every document a new SAID for no change in content, and
            # invalidate every downstream index.
            #
            # The lookup is by content -- the file's hash, or the SAIDs of the
            # documents it produced -- and not by registry key, so renaming,
            # moving or recompressing a source keeps the identifiers of its
            # documents. Keying it by path cost exactly that once already:
            # renaming the Confluence exports and gzipping them re-dated 2,103
            # documents whose text had not changed.
            processed_at = first_seen.stamp_for(file_hash, saids)

            registry[registry_key] = {
                "file_hash": file_hash,
                "document_saids": saids,
                "processed_at": processed_at,
            }

        counts["processed"] += len(documents)
        counts["files"] += 1

    vanished: list[str] = []

    if args.prune:
        vanished = _drop_vanished(registry, raw_dir)

        verb = "would forget" if args.dry_run else "forgot"
        for key in vanished:
            print(f"{verb} {key}  (source file is gone)")

    if not args.dry_run:
        _save_registry(registry, registry_file)

    pruned = 0
    if args.prune:
        seen = counts["processed"] + counts["skipped"]

        if not registry:
            warn("--prune skipped: the registry is empty, so every document "
                 "would look like a stray")
        elif seen == 0:
            warn("--prune skipped: this run matched no source files, so every "
                 "document would look like a stray")
        elif not processed_dir.is_dir():
            pass
        else:
            pruned, left_alone = _prune_processed(
                processed_dir, registry, apply=not args.dry_run
            )
            verb = "would remove" if args.dry_run else "removed"
            print(f"prune: {verb} {pruned} stray document(s)")
            if left_alone:
                print(f"prune: left {left_alone} unrecognized file(s) alone")

    print(
        "done: "
        f"{counts['processed']} document(s) from {counts['files']} file(s), "
        f"{counts['skipped']} unchanged, "
        f"{counts['no_document']} no document, "
        f"{counts['ignored']} ignored"
        + (
            f", {len(vanished)} vanished source(s) forgotten" if vanished else ""
        )
        + (
            f", {counts['unregistered']} stale entry(ies) dropped"
            if counts["unregistered"]
            else ""
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
