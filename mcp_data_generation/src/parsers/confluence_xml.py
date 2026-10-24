# parsers/confluence_xml.py

"""Parse a Confluence XML space export into documents.

"Export space as XML" produces an object graph rather than rendered pages:

    confluence/<site>/<any-dir>/
        entities.xml                     every object in the space
        exportDescriptor.properties      space key, export type, build
        attachments/<pageid>/<id>/<ver>  binary attachments

One ``entities.xml`` yields one document per current page, so this parser
returns a list. The directory holding it is not named by Confluence, so the
file is recognised by name rather than by its location.

Why prefer this export over the HTML one, where both exist: it carries pages
the HTML export omits (9 for this space), the real parent/child hierarchy
instead of scraped breadcrumbs, and the page's true creation timestamp rather
than a byline that may show either the created or the last-modified date in the
space's own timezone.

Bodies are not fuller. Measured against the HTML export over the 71 pages both
hold, the text is shorter on 53 and never longer, by a median of 22 characters
-- the raw storage markup is several times larger than the HTML export's page
source, but that is markup, not text.

What it loses, and how that is repaired: a user mention is recorded only as an
account id, so a meeting's participant list would come out empty. Names come
from the site's user directory when one was exported, and otherwise from a
sibling HTML export, which renders the same mentions as display names tagged
with the account id. Either source resolves 56 of the 59 accounts mentioned in
this space; the three misses are deleted users, who have no name to recover.

The markup itself is not handled here. Storage format is shared with the CSV
export, so it lives in :mod:`src.parsers.confluence_storage`.

Historical page versions are skipped, and so are pages that are not live. The
export carries three kinds of page besides the current one:

* superseded versions, marked by an ``originalVersion`` reference -- indexing
  these would crowd search results with the same page many times over;
* pages in the trash, ``contentStatus=deleted`` -- someone removed them, and a
  search that answers from them is quoting retracted material;
* unpublished drafts, ``contentStatus=draft`` -- never visible on the wiki.

For the HOME space that is 14,938 versions, 90 deleted pages and 54 drafts
against 2,169 live ones. A space export is a backup, so it holds everything the
space could restore; only what the wiki actually shows belongs in the corpus.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from src.models import Document
from src.parsers import confluence_html, confluence_users
from src.parsers.confluence_markup import asset_type
from src.parsers.confluence_storage import page_date, page_text

EXPORT_FILE = "entities.xml"


def is_export_file(file_path: Path) -> bool:
    return file_path.name.lower() == EXPORT_FILE


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""

    return "".join(element.itertext()).strip()


def _property(obj: ET.Element, name: str) -> ET.Element | None:
    return obj.find(f'property[@name="{name}"]')


def _property_text(obj: ET.Element, name: str) -> str:
    return _text(_property(obj, name))


def _property_id(obj: ET.Element, name: str) -> str:
    """The id a reference property points at, e.g. a page's parent."""

    prop = _property(obj, name)
    if prop is None:
        return ""

    return _text(prop.find('id[@name="id"]'))


def _object_id(obj: ET.Element) -> str:
    return _text(obj.find('id[@name="id"]'))


def _space_details(root: ET.Element) -> dict:
    for obj in root.findall('object[@class="Space"]'):
        return {
            "key": _property_text(obj, "key") or "",
            "name": _property_text(obj, "name") or "",
        }

    return {"key": "", "name": ""}


def _bodies_by_content_id(root: ET.Element) -> dict[str, str]:
    """Map a content id to its body text.

    The reliable direction is BodyContent -> content, not page -> bodyContents:
    a page's collection lists ids for several relations and reading the first
    one in document order picks up the wrong body.
    """

    bodies: dict[str, str] = {}

    for obj in root.findall('object[@class="BodyContent"]'):
        owner = _property_id(obj, "content")
        if not owner:
            continue

        body = _property_text(obj, "body")
        if body:
            bodies[owner] = body

    return bodies


def _ancestors(page_id: str, pages: dict[str, ET.Element]) -> list[str]:
    """Titles from the root page down to this page's parent."""

    trail: list[str] = []
    seen = {page_id}
    current = _property_id(pages[page_id], "parent")

    while current and current in pages and current not in seen:
        seen.add(current)
        title = _property_text(pages[current], "title")
        if title:
            trail.append(title)
        current = _property_id(pages[current], "parent")

    trail.reverse()

    return trail


def _sibling_html_export(file_path: Path, space_key: str) -> Path | None:
    """An HTML export of the same space beside this one, for mention names."""

    site_root = file_path.parent.parent

    if not site_root.is_dir():
        return None

    for candidate in sorted(site_root.iterdir()):
        if not candidate.is_dir() or not (candidate / "index.html").exists():
            continue
        if confluence_html.space_details(candidate)["key"] == space_key:
            return candidate

    return None


def process(file_path: Path) -> list[Document]:
    if not is_export_file(file_path):
        return []

    try:
        root = ET.parse(file_path).getroot()
    except ET.ParseError as error:
        print(f"Skipping unreadable Confluence export {file_path.name}: {error}")
        return []

    space = _space_details(root)
    bodies = _bodies_by_content_id(root)

    pages = {
        _object_id(obj): obj
        for obj in root.findall('object[@class="Page"]')
        if _object_id(obj)
    }

    # A historical version records the page it superseded; current pages do not.
    # contentStatus then separates a live page from a trashed one or a draft.
    # It is read permissively: an export that does not record it at all is
    # taken at its word rather than discarded.
    current = {}
    not_live = 0

    for page_id, obj in pages.items():
        if _property(obj, "originalVersion") is not None:
            continue

        status = _property_text(obj, "contentStatus")

        if status and status != "current":
            not_live += 1
            continue

        current[page_id] = obj

    # The site directory covers every account on the site and records plain
    # names; a sibling HTML export only covers the mentions rendered in its own
    # space and annotates a departed member as "Name (Deactivated)". So the
    # directory wins where it exists, and the export is the fallback.
    site_root = file_path.parent.parent
    user_names = confluence_users.names(site_root)

    if not user_names:
        html_export = _sibling_html_export(file_path, space["key"])

        if html_export is not None:
            user_names = confluence_html.user_names(html_export)
        else:
            print(
                f"  note: no {confluence_users.DIRECTORY_FILE} for this site "
                f"and no HTML export of space {space['key']} beside "
                f"{file_path.name}; user mentions will be dropped"
            )

    metadata = confluence_html.load_metadata(file_path)

    documents: list[Document] = []
    skipped_empty = 0
    unresolved = 0

    for page_id, obj in sorted(current.items(), key=lambda item: item[0]):
        title = _property_text(obj, "title")
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

        site = site_root.name
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
                date=page_date(title, _property_text(obj, "creationDate")),
                asset_type=asset_type(title),
                content_type="Wiki Page",
                title=title,
                content=content,
            )
        )

    print(
        f"  Confluence XML {space['key']}: {len(documents)} page(s) from "
        f"{len(pages)} object(s) "
        f"({len(pages) - len(current) - not_live} historical, "
        f"{not_live} deleted or draft, "
        f"{skipped_empty} without usable text)"
        + (f", {unresolved} mention(s) unresolved" if unresolved else "")
    )

    return documents
