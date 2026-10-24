# parsers/confluence.py

"""Dispatch a Confluence file to the parser for its export format.

Confluence can export a space three ways and the cache may hold all of them, so
the format is decided per file rather than per directory:

* ``content.csv`` (gzipped or not) is a CSV space export -- a dump of the
  tables behind the space, which yields one document per current page.
* ``entities.xml`` is an XML space export -- an object graph covering the whole
  space, which also yields one document per current page.
* any other ``*.html`` under a space directory is a page of an HTML space
  export, which yields one document.

**The formats are not rival copies of the same thing.** Each export in the
cache was taken of a different space, so which format is "better" matters far
less than which space it covers: the CSV export holds the site's real wiki
(2,169 pages), while the XML and HTML exports hold a template space (80).
Nothing is chosen over anything else -- every export contributes its own pages.

Where two exports *do* overlap, as the XML and HTML ones do, they are combined
rather than ranked. The XML export holds pages the HTML export leaves out, the
real page hierarchy and exact timestamps; the HTML export renders macros, so a
page whose body is nothing but a file-list or attachments macro has text there
and none in the XML. So where an XML export covers a space its pages win, and
an HTML page is still parsed when the XML export has nothing for that page id.
Every page survives and none is indexed twice.

User mentions are an account id in both machine-readable formats. Names come
from the site's user directory; see :mod:`src.parsers.confluence_users`.

A page named in ``EXCLUDED_PAGES`` is dropped whichever format it arrives in.
"""

from pathlib import Path

from src.models import Document
from src.parsers import confluence_csv, confluence_html, confluence_xml

# Pages dropped on purpose, by Confluence page id, with the reason. A space
# export is a faithful copy of the space, so a page that does not belong in the
# corpus is named here rather than guessed at from its text -- anything that
# inspected content would also throw away real pages that happen to read oddly.
EXCLUDED_PAGES: dict[str, str] = {
    # Spam posted to the public wiki: an article about cleaning ventilation
    # ducts, unrelated to ToIP.
    "24610187": "spam",
}

# Exclusions already reported, so a page the run meets twice (once per export
# format) is announced once.
_DROPPED: set[str] = set()

# Parsed XML exports, keyed by file. The engine walks an HTML page before it
# reaches entities.xml, so the export is parsed on first need and reused.
_XML_DOCUMENTS: dict[Path, list[Document]] = {}

# Space key -> entities.xml, per site directory.
_XML_BY_SPACE: dict[Path, dict[str, Path]] = {}

# Space key -> content.csv, per site directory.
_CSV_BY_SPACE: dict[Path, dict[str, Path]] = {}

# Current page ids a CSV export holds, keyed by its content.csv.
_CSV_COVERAGE: dict[Path, set[str]] = {}

# Exports whose supersession has already been reported.
_REPORTED: set[Path] = set()


def _xml_documents(export_file: Path) -> list[Document]:
    if export_file not in _XML_DOCUMENTS:
        _XML_DOCUMENTS[export_file] = confluence_xml.process(export_file)

    return _XML_DOCUMENTS[export_file]


def _declared_space_key(export_file: Path) -> str:
    """The space key from exportDescriptor.properties beside the export.

    Reading the descriptor avoids parsing a multi-megabyte entities.xml just to
    learn which space it holds.
    """

    descriptor = export_file.parent / "exportDescriptor.properties"

    if descriptor.exists():
        try:
            for line in descriptor.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "spaceKey" and value.strip():
                    return value.strip()
        except OSError:
            pass

    return ""


def _xml_exports(site_root: Path) -> dict[str, Path]:
    """Map space key to XML export for every export under a site directory."""

    if site_root in _XML_BY_SPACE:
        return _XML_BY_SPACE[site_root]

    exports: dict[str, Path] = {}

    if site_root.is_dir():
        candidates = list(site_root.glob(f"*/{confluence_xml.EXPORT_FILE}"))
        candidates += list(site_root.glob(confluence_xml.EXPORT_FILE))

        for export_file in sorted(candidates):
            key = _declared_space_key(export_file)

            if not key:
                # No descriptor, so fall back to the export's own Space object.
                documents = _xml_documents(export_file)
                key = documents[0].project if documents else ""

            if key and key not in exports:
                exports[key] = export_file

    _XML_BY_SPACE[site_root] = exports

    return exports


def _csv_exports(site_root: Path) -> dict[str, Path]:
    """Map space key to CSV export for every export under a site directory."""

    if site_root in _CSV_BY_SPACE:
        return _CSV_BY_SPACE[site_root]

    exports: dict[str, Path] = {}

    if site_root.is_dir():
        for name in (confluence_csv.CONTENT_FILE, f"{confluence_csv.CONTENT_FILE}.gz"):
            for content_file in sorted(site_root.glob(f"*/{name}")):
                if not confluence_csv.is_export_file(content_file):
                    continue

                key, pages = confluence_csv.covered_pages(content_file)

                if key and key not in exports:
                    exports[key] = content_file
                    _CSV_COVERAGE[content_file] = pages

    _CSV_BY_SPACE[site_root] = exports

    return exports


def _superseded_by_csv(site_root: Path, space_key: str, page_id: str) -> Path | None:
    """The CSV export already holding this page, if there is one.

    A CSV export of a space is preferred over the other formats of the same
    space: measured over the 2,101 pages the HOME exports share, the extracted
    text is identical on 2,098 and the CSV export holds two pages the XML one
    does not, for a thirtieth of the parse time. Without this check both
    exports contribute every page and the space is indexed twice over.
    """

    content_file = _csv_exports(site_root).get(space_key)

    if content_file is None:
        return None

    return content_file if page_id in _CSV_COVERAGE[content_file] else None


def _superseded_by_xml(file_path: Path) -> bool:
    """True when an XML export of this space already covers this page."""

    space_root = file_path.parent
    space_key = confluence_html.space_details(space_root)["key"]
    export_file = _xml_exports(space_root.parent).get(space_key)

    if export_file is None:
        return False

    documents = _xml_documents(export_file)
    covered = {document.source_identifier for document in documents}

    if export_file not in _REPORTED:
        _REPORTED.add(export_file)
        pages = {
            confluence_html.page_id(page)
            for page in space_root.glob("*.html")
            if confluence_html.is_page_file(page)
        }
        kept = sorted(pages - covered)
        print(
            f"  Confluence {space_key}: XML export supersedes "
            f"{len(pages & covered)} HTML page(s); keeping {len(kept)} the XML "
            f"export does not cover"
        )

    return confluence_html.page_id(file_path) in covered


def _excluded(page_id: str) -> bool:
    """True for a page on the exclusion list, reported the first time."""

    reason = EXCLUDED_PAGES.get(page_id)

    if reason is None:
        return False

    if page_id not in _DROPPED:
        _DROPPED.add(page_id)
        print(f"  Confluence page {page_id}: dropped ({reason})")

    return True


def _xml_documents_wanted(file_path: Path) -> list[Document]:
    """An XML export's documents, minus anything a CSV export already covers."""

    documents = _xml_documents(file_path)

    if not documents:
        return documents

    site_root = file_path.parent.parent
    space_key = _declared_space_key(file_path) or documents[0].project
    kept = [
        document
        for document in documents
        if _superseded_by_csv(site_root, space_key, document.source_identifier)
        is None
    ]

    if len(kept) != len(documents) and file_path not in _REPORTED:
        _REPORTED.add(file_path)
        print(
            f"  Confluence {space_key}: CSV export supersedes "
            f"{len(documents) - len(kept)} XML page(s); keeping {len(kept)} "
            f"the CSV export does not cover"
        )

    return kept


def process(file_path: Path) -> Document | list[Document] | None:
    # Every format is filtered here rather than inside the format parsers, so
    # each parser keeps reporting the space as the export actually holds it and
    # an excluded page cannot return through another format.
    if confluence_csv.is_export_file(file_path):
        return [
            document
            for document in confluence_csv.process(file_path)
            if not _excluded(document.source_identifier)
        ]

    if confluence_xml.is_export_file(file_path):
        return [
            document
            for document in _xml_documents_wanted(file_path)
            if not _excluded(document.source_identifier)
        ]

    if confluence_html.is_page_file(file_path):
        page = confluence_html.page_id(file_path)

        if _excluded(page):
            return None

        space_root = file_path.parent
        space_key = confluence_html.space_details(space_root)["key"]

        if _superseded_by_csv(space_root.parent, space_key, page) is not None:
            return None

        if _superseded_by_xml(file_path):
            return None

        return confluence_html.process(file_path)

    return None
