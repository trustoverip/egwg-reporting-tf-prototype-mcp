# parsers/confluence_csv.py

"""Parse a Confluence CSV space export into documents.

"Export space as CSV" produces a dump of the tables behind the space rather
than any kind of page:

    confluence/<site>/<any-dir>/
        content.csv                 one row per page, per version, per draft
        bodycontent.csv             the storage-format body of each of those
        spaces.csv                  the space's key and name
        exportDescriptor.properties export format, space key, build
        <plugin tables>.csv         calendars, inline tasks, sidebar links
        attachments/<pageid>/<id>/<ver>

One export yields one document per current page, so this parser returns a list.
The directory is not named by Confluence, so the export is recognised by its
``content.csv`` rather than by where it sits.

Why this export matters: it is the only one of the three that was taken of the
site's real wiki. It carries 2,169 current pages against the 80 in the XML
export of the template space, with the full parent/child hierarchy and exact
creation and modification timestamps. Bodies are the same storage format the
XML export holds, so the markup handling is shared.

Two traps are worth recording.

**The page count in the export is not the number of pages.**
``space_export_counts.csv`` reports 14,552 pages for this space, and
``content.csv`` really does hold that many rows with ``content_status=current``.
Only 2,169 of them are pages; the other 12,383 are historical versions. The
discriminator is ``spaceid``, which is set on a current page and empty on a
version -- a version belongs to its page, not to the space. ``prevver`` does
not discriminate: it is set on nearly every row, and on a version it points at
the current page rather than at the version before it. Taking the header count
at face value would index seven superseded copies of every page.

**A ``.csv.gz`` suffix says nothing about the contents.** Confluence shipped
this export with every one of its 49 files named ``.gz`` and not one of them
compressed -- all 49 began with ASCII. The same files were later gzipped for
real. So the suffix is never trusted in either direction: each file is sniffed
for the gzip magic bytes and decompressed only if it actually carries them,
which also means ``content.csv`` and ``content.csv.gz`` are both read correctly
whatever is inside them.
"""

import csv
import gzip
import io
import sys
from contextlib import contextmanager
from pathlib import Path

from src.models import Document
from src.parsers import confluence_html, confluence_users
from src.parsers.confluence_markup import asset_type
from src.parsers.confluence_storage import page_date, page_text

CONTENT_FILE = "content.csv"
BODY_FILE = "bodycontent.csv"
SPACES_FILE = "spaces.csv"
DESCRIPTOR_FILE = "exportDescriptor.properties"

GZIP_MAGIC = b"\x1f\x8b"

# A page body runs to tens of thousands of characters, far past csv's default
# 128 KiB field cap. The limit is a module-wide setting in csv, so it is raised
# only for as long as an export is being read.
_FIELD_LIMIT = min(sys.maxsize, 2**31 - 1)


@contextmanager
def _wide_fields():
    previous = csv.field_size_limit()
    csv.field_size_limit(_FIELD_LIMIT)
    try:
        yield
    finally:
        csv.field_size_limit(previous)


def _plain_name(file_path: Path) -> str:
    """The file's name with a trailing ``.gz`` removed."""

    name = file_path.name

    return name[:-3] if name.lower().endswith(".gz") else name


def _sibling(directory: Path, plain_name: str) -> Path | None:
    """A companion export file, with or without the misleading suffix."""

    for candidate in (directory / plain_name, directory / f"{plain_name}.gz"):
        if candidate.is_file():
            return candidate

    return None


def _open_text(file_path: Path) -> io.TextIOBase:
    """Open an export file, decompressing it only if it really is compressed."""

    with open(file_path, "rb") as probe:
        compressed = probe.read(2) == GZIP_MAGIC

    opener = gzip.open if compressed else open

    return opener(
        file_path, mode="rt", encoding="utf-8", errors="replace", newline=""
    )


def is_export_file(file_path: Path) -> bool:
    """True for the ``content.csv`` of a CSV export, which drives the parse."""

    if _plain_name(file_path).lower() != CONTENT_FILE:
        return False

    return _sibling(file_path.parent, BODY_FILE) is not None


def _space_details(export_dir: Path) -> dict:
    """The space's key and name, from spaces.csv or the export descriptor."""

    details = {"key": "", "name": ""}
    spaces = _sibling(export_dir, SPACES_FILE)

    if spaces is not None:
        with _open_text(spaces) as f:
            for row in csv.DictReader(f):
                details["key"] = (row.get("spacekey") or "").strip()
                details["name"] = (row.get("spacename") or "").strip()
                break

    descriptor = _sibling(export_dir, DESCRIPTOR_FILE)

    if not details["key"] and descriptor is not None:
        with _open_text(descriptor) as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip() == "spaceKey" and value.strip():
                    details["key"] = value.strip()
                    break

    if not details["name"]:
        details["name"] = details["key"]

    return details


def _current_pages(content_file: Path) -> tuple[dict[str, dict], int]:
    """Current pages by content id, and how many historical rows were skipped.

    Only the columns a document needs are kept: the file is tens of megabytes
    and holds a column apiece for drafts, plugins and comment threading.
    """

    pages: dict[str, dict] = {}
    historical = 0

    with _wide_fields(), _open_text(content_file) as f:
        for row in csv.DictReader(f):
            if row.get("contenttype") != "PAGE":
                continue
            if row.get("content_status") != "current":
                continue

            # A historical version carries no spaceid: it belongs to the page
            # it superseded, not to the space.
            if not (row.get("spaceid") or "").strip():
                historical += 1
                continue

            content_id = (row.get("contentid") or "").strip()
            if not content_id:
                continue

            pages[content_id] = {
                "title": (row.get("title") or "").strip(),
                "parent": (row.get("parentid") or "").strip(),
                "created": (row.get("creationdate") or "").strip(),
            }

    return pages, historical


def _bodies_for(body_file: Path, wanted: set[str]) -> dict[str, str]:
    """Storage-format bodies for the given content ids.

    bodycontent.csv holds every version's body, which for this space is 183 MB
    against 22 MB for the current pages, so rows for anything not wanted are
    dropped as they are read rather than collected and filtered afterwards.
    """

    bodies: dict[str, str] = {}

    with _wide_fields(), _open_text(body_file) as f:
        for row in csv.DictReader(f):
            content_id = (row.get("contentid") or "").strip()

            if content_id in wanted and row.get("body"):
                bodies[content_id] = row["body"]

    return bodies


def _ancestors(page_id: str, pages: dict[str, dict]) -> list[str]:
    """Titles from the root page down to this page's parent."""

    trail: list[str] = []
    seen = {page_id}
    current = pages[page_id]["parent"]

    while current and current in pages and current not in seen:
        seen.add(current)
        title = pages[current]["title"]
        if title:
            trail.append(title)
        current = pages[current]["parent"]

    trail.reverse()

    return trail


def covered_pages(file_path: Path) -> tuple[str, set[str]]:
    """The space key and current page ids an export holds, without its bodies.

    The dispatcher needs this to tell whether a CSV export already covers a
    page another format is offering. Reading content.csv alone keeps that
    answer cheap: bodycontent.csv is an order of magnitude larger.
    """

    if not is_export_file(file_path):
        return "", set()

    pages, _ = _current_pages(file_path)

    return _space_details(file_path.parent)["key"], set(pages)


def process(file_path: Path) -> list[Document]:
    if not is_export_file(file_path):
        return []

    export_dir = file_path.parent
    body_file = _sibling(export_dir, BODY_FILE)

    if body_file is None:
        return []

    space = _space_details(export_dir)
    pages, historical = _current_pages(file_path)
    bodies = _bodies_for(body_file, set(pages))

    site_root = export_dir.parent
    user_names = confluence_users.names(site_root)

    if not user_names:
        print(
            f"  note: no {confluence_users.DIRECTORY_FILE} for this site; "
            f"user mentions in space {space['key']} will be dropped"
        )

    metadata = confluence_html.load_metadata(file_path)
    site = site_root.name

    documents: list[Document] = []
    skipped_empty = 0
    unresolved = 0

    for page_id, page in sorted(pages.items()):
        title = page["title"]
        body = bodies.get(page_id, "")

        if not title or not body:
            skipped_empty += 1
            continue

        content, missed = page_text(body, user_names)
        unresolved += missed

        if not content.strip():
            skipped_empty += 1
            continue

        units = metadata.get("organizational_unit") or _ancestors(page_id, pages) or [
            space["name"]
        ]

        source_url = metadata.get("source_url") or (
            f"https://{site}/wiki/spaces/{space['key']}/pages/{page_id}"
            if site
            else ""
        )

        documents.append(
            Document(
                project=metadata.get("project") or space["name"],
                organizational_unit=units,
                source_system="Confluence",
                source_identifier=page_id,
                source_url=source_url,
                date=page_date(title, page["created"]),
                asset_type=asset_type(title),
                content_type="Wiki Page",
                title=title,
                content=content,
            )
        )

    print(
        f"  Confluence CSV {space['key']}: {len(documents)} page(s) from "
        f"{len(pages) + historical} row(s) ({historical} historical, "
        f"{skipped_empty} without usable text)"
        + (f", {unresolved} mention(s) unresolved" if unresolved else "")
    )

    return documents
