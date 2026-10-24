# src/cache_manifest.py

"""Publish and verify the content hashes a raw cache carries.

A ``content-sha256.tsv`` sits at the root of each collection unit, listing the
SHA-256 of every file beneath it by relative path. That hash is the raw
artifact's id: it belongs to Content Acquisition, which owns the raw tree,
rather than only to a consumer's ingestion registry, so the cache states it
once and every consumer agrees on it.

A collection unit is whatever is fetched or exported as a whole, which differs
by source system:

    zoom/content-sha256.tsv                     the whole tree, one fetch
    confluence/<site>/HOME-CSV/content-sha256.tsv   one space export
    confluence/<site>/content-sha256.tsv        the site's own files,
                                                e.g. export-users.csv

So a space export carries its own manifest, because it arrives and is replaced
as a unit, while the Zoom tree carries one for all of its children however deep
they sit. Compression has nothing to do with where a manifest goes: the Zoom
assets are plain text and still get hashed, since the point is to state what
was collected, not to undo a container.

Any directory holding files that no designated root covers becomes a root of
its own, so nothing in the cache goes unrecorded.

    python -m src.cache_manifest --raw ~/W/github-mirror/LFDT-way/mcp-cache
    python -m src.cache_manifest --raw <cache> --write

``--check`` is the default and writes nothing. ``--write`` is the one tool here
allowed to modify the raw tree, and only ever these manifests; the ingestion
engine still refuses to write anything inside it.
"""

import argparse
import os
import sys
from pathlib import Path

from src.content_hash import sha256_content

MANIFEST_NAME = "content-sha256.tsv"

HEADER = (
    "# sha256 of each file's uncompressed contents, so a hash survives "
    "gzip/gunzip\n"
    "# paths are relative to this file and cover every directory below it\n"
)

# How far below a source system's own directory a collection unit begins, and
# so where its manifest goes. Zoom is fetched as one tree, so 0: a single
# manifest covers every series beneath it. Confluence is exported one space at
# a time into <site>/<export dir>, so 2.
MANIFEST_DEPTH = {
    "zoom": 0,
    "confluence": 2,
}

DEFAULT_DEPTH = 0


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _is_hidden(path: Path, raw_dir: Path) -> bool:
    """True for anything under a dot-directory, or a dot-file itself.

    These are the checkout's own bookkeeping rather than collected content, and
    .DS_Store would churn a manifest on every browse.
    """

    return any(part.startswith(".") for part in path.relative_to(raw_dir).parts)


def designated_roots(raw_dir: Path) -> set[Path]:
    """The collection-unit roots MANIFEST_DEPTH calls for, where they exist."""

    roots: set[Path] = set()

    for system_dir in raw_dir.iterdir():
        if not system_dir.is_dir() or _is_hidden(system_dir, raw_dir):
            continue

        depth = MANIFEST_DEPTH.get(system_dir.name.lower(), DEFAULT_DEPTH)

        if depth == 0:
            roots.add(system_dir)
            continue

        candidates = [system_dir]

        for _ in range(depth):
            candidates = [
                child
                for candidate in candidates
                for child in candidate.iterdir()
                if child.is_dir() and not _is_hidden(child, raw_dir)
            ]

        roots.update(candidates)

    return roots


def _enclosing_root(directory: Path, roots: set[Path]) -> Path | None:
    """The deepest designated root at or above a directory."""

    for candidate in (directory, *directory.parents):
        if candidate in roots:
            return candidate

    return None


def plan(raw_dir: Path) -> dict[Path, dict[str, Path]]:
    """Which files each manifest covers, as {root: {relative path: file}}.

    A file is assigned to the deepest designated root above it. One that no
    designated root covers -- a site's own ``export-users.csv``, the cache's
    LICENSE -- makes a root of its own directory, so nothing goes unrecorded.
    """

    designated = designated_roots(raw_dir)
    assigned: dict[Path, dict[str, Path]] = {}

    for file_path in sorted(raw_dir.rglob("*")):
        if not file_path.is_file() or _is_hidden(file_path, raw_dir):
            continue

        if file_path.name == MANIFEST_NAME:
            continue

        root = _enclosing_root(file_path.parent, designated) or file_path.parent
        relative = file_path.relative_to(root).as_posix()

        assigned.setdefault(root, {})[relative] = file_path

    return assigned


def existing_manifests(raw_dir: Path) -> set[Path]:
    """Directories that currently hold a manifest."""

    return {
        path.parent
        for path in raw_dir.rglob(MANIFEST_NAME)
        if path.is_file() and not _is_hidden(path, raw_dir)
    }


def read_manifest(root: Path) -> dict[str, str]:
    """Recorded hashes by relative path, empty if there is no manifest."""

    manifest = root / MANIFEST_NAME

    if not manifest.is_file():
        return {}

    recorded: dict[str, str] = {}

    with open(manifest, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue

            digest, _, relative = line.rstrip("\n").partition("\t")

            if digest and relative:
                recorded[relative] = digest

    return recorded


def write_manifest(root: Path, hashes: dict[str, str]) -> None:
    """Write a manifest, or remove it when it would cover nothing."""

    manifest = root / MANIFEST_NAME

    if not hashes:
        manifest.unlink(missing_ok=True)
        return

    tmp_file = manifest.with_suffix(manifest.suffix + ".tmp")

    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(HEADER)
        for relative in sorted(hashes):
            f.write(f"{hashes[relative]}\t{relative}\n")

    tmp_file.replace(manifest)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.cache_manifest",
        description=(
            "Record or verify the SHA-256 of each cached source file's "
            "uncompressed contents."
        ),
    )

    parser.add_argument(
        "--raw",
        default=os.environ.get("MCP_RAW_DIR", "data/raw"),
        help="raw cache to read (default: data/raw, env MCP_RAW_DIR)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the manifests instead of checking them",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only report manifests that need attention",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_dir = Path(args.raw).expanduser().resolve()

    if not raw_dir.is_dir():
        warn(f"no such raw tree: {raw_dir}")
        return 1

    assigned = plan(raw_dir)
    counts = {"files": 0, "written": 0, "missing": 0, "stale": 0, "changed": 0}

    for root in sorted(assigned):
        files = assigned[root]
        recorded = read_manifest(root)
        label = root.relative_to(raw_dir).as_posix() or "."

        hashes = {
            relative: sha256_content(file_path)
            for relative, file_path in sorted(files.items())
        }

        missing = [key for key in hashes if key not in recorded]
        stale = [key for key in recorded if key not in hashes]
        changed = [
            key
            for key, digest in hashes.items()
            if key in recorded and recorded[key] != digest
        ]

        counts["files"] += len(hashes)
        counts["missing"] += len(missing)
        counts["stale"] += len(stale)
        counts["changed"] += len(changed)

        if args.write:
            if recorded != hashes:
                write_manifest(root, hashes)
                counts["written"] += 1
                print(f"wrote   {label}/{MANIFEST_NAME}  ({len(hashes)} file(s))")
            elif not args.quiet:
                print(f"ok      {label}/{MANIFEST_NAME}  ({len(hashes)} file(s))")

            continue

        for key in changed:
            print(f"CHANGED {label}/{key}")
        for key in missing:
            print(f"MISSING {label}/{key}  (no recorded hash)")
        for key in stale:
            print(f"STALE   {label}/{key}  (recorded but not present)")

        if not (changed or missing or stale) and not args.quiet:
            print(f"ok      {label}/{MANIFEST_NAME}  ({len(hashes)} file(s))")

    # A manifest left behind where files have moved away would claim to cover a
    # collection unit that no longer exists.
    orphaned = sorted(existing_manifests(raw_dir) - set(assigned))

    for root in orphaned:
        label = root.relative_to(raw_dir).as_posix() or "."
        recorded = read_manifest(root)

        counts["stale"] += len(recorded)

        if args.write:
            (root / MANIFEST_NAME).unlink(missing_ok=True)
            print(f"removed {label}/{MANIFEST_NAME}  (covers nothing)")
            continue

        print(f"ORPHAN  {label}/{MANIFEST_NAME}  (covers nothing)")

        # Name what it used to cover: "covers nothing" alone would not say
        # which files left, and that is the point of checking.
        for key in sorted(recorded):
            print(f"STALE   {label}/{key}  (recorded but not present)")

    verb = "wrote" if args.write else "checked"

    print(
        f"done: {verb} {counts['files']} file(s) in {len(assigned)} manifest(s)"
        + (
            f", {counts['written']} updated, {len(orphaned)} removed"
            if args.write
            else (
                f", {counts['changed']} changed, {counts['missing']} unrecorded, "
                f"{counts['stale']} stale, {len(orphaned)} orphaned"
            )
        )
    )

    if args.write:
        return 0

    return (
        1
        if counts["changed"] or counts["missing"] or counts["stale"] or orphaned
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
