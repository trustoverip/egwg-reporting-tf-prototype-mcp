# parsers/confluence_html.py

"""Parse a Confluence HTML space export into documents.

"Export space as HTML" produces a directory per space:

    confluence/<site>/<SPACEKEY>/
        index.html                       space details and page list
        <Page-Title>_<pageid>.html       one file per page
        styles/site.css
        attachments/<pageid>/<id>.<ext>  binary attachments

Only the page files become documents. ``index.html`` is navigation, but it is
still read for the space key and name; the stylesheet is presentation, and
attachments are binaries this parser does not open.

Everything a document needs is derived from the export itself, so a bare export
ingests with no configuration. A ``metadata.json`` anywhere above the page still
wins, which is how ``project`` is pinned to an LFDT project name rather than the
Confluence space name.

This export also renders user mentions as display names, which the XML export
does not, so :func:`user_names` lends them to the XML parser.
"""

import html
import json
import re
from datetime import datetime
from pathlib import Path

from src.models import Document
from src.parsers.confluence_markup import TITLE_DATE, BlockBuilder, asset_type, clean

# "Created by Victor Syntez on Feb 08, 2021", "... last modified on Feb 08, 2021"
_METADATA_DATE = re.compile(r"on (\w{3} \d{1,2}, \d{4})")

_PAGE_ID = re.compile(r"_(\d+)\.html$", re.IGNORECASE)

# <a ... data-account-id="557058:..." ...>Display Name</a>
_USER_MENTION = re.compile(r'data-account-id="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)

_TAGS = re.compile(r"<[^>]+>")


class _PageParser(BlockBuilder):
    """Pull the title, breadcrumbs, byline and body text out of an export page.

    Confluence wraps the real content in ``div#main-content`` and surrounds it
    with navigation, so capture is switched on only inside that div.
    """

    def __init__(self) -> None:
        super().__init__(capturing=False)

        self.title = ""
        self.breadcrumbs: list[str] = []
        self.byline = ""

        self._main_depth = 0
        self._capture: str | None = None
        self._capture_buffer: list[str] = []
        self._in_breadcrumbs = False
        self._in_title_tag = False

    def handle_extra_starttag(self, tag: str, attributes: dict) -> bool:
        element_id = attributes.get("id", "")
        element_class = attributes.get("class", "")

        if tag == "title":
            self._in_title_tag = True
            return True

        if tag == "ol" and element_id == "breadcrumbs":
            self._in_breadcrumbs = True
            return True

        if self._in_breadcrumbs and tag == "a":
            self._capture = "breadcrumb"
            self._capture_buffer.clear()
            return True

        if tag == "div" and element_id == "main-content":
            self._capturing = True
            self._main_depth = 1
            return True

        if tag == "div" and "page-metadata" in element_class:
            self._capture = "byline"
            self._capture_buffer.clear()
            return True

        if self._capturing and tag == "div":
            self._main_depth += 1
            return True

        return False

    def handle_extra_endtag(self, tag: str) -> bool:
        if tag == "title":
            self._in_title_tag = False
            return True

        if tag == "a" and self._capture == "breadcrumb":
            text = clean("".join(self._capture_buffer))
            if text:
                self.breadcrumbs.append(text)
            self._capture = None
            return True

        if tag == "ol" and self._in_breadcrumbs:
            self._in_breadcrumbs = False
            return True

        if tag == "div" and self._capture == "byline":
            self.byline = clean("".join(self._capture_buffer))
            self._capture = None
            return True

        if self._capturing and tag == "div":
            self._main_depth -= 1
            if self._main_depth <= 0:
                self._flush_block()
                self._flush_lines()
                self._capturing = False
            return True

        return False

    def handle_data(self, data: str) -> None:
        if self._in_title_tag:
            self.title += data
            return

        if self._capture:
            self._capture_buffer.append(data)
            return

        super().handle_data(data)


def is_page_file(file_path: Path) -> bool:
    """True for a page export, false for navigation, styles and attachments."""

    if file_path.suffix.lower() != ".html":
        return False

    if file_path.name.lower() == "index.html":
        return False

    # attachments/<pageid>/... and styles/... hold no page text.
    return not {"attachments", "styles"} & {part.lower() for part in file_path.parts}


_SPACE_CACHE: dict[Path, dict] = {}
_NAME_CACHE: dict[Path, dict[str, str]] = {}


def space_details(space_root: Path) -> dict:
    """Space key and name, read from the export's own index.html.

    Keyed by directory and cached, since every page in a space asks for it.
    """

    if space_root in _SPACE_CACHE:
        return _SPACE_CACHE[space_root]

    details = {"key": space_root.name, "name": space_root.name}
    index_file = space_root / "index.html"

    if index_file.exists():
        markup = index_file.read_text(encoding="utf-8", errors="replace")

        # The space details table pairs a <th> label with a <td> value.
        for label, field in (("Key", "key"), ("Name", "name")):
            match = re.search(
                rf">{label}</th>\s*<td[^>]*>(.*?)</td>",
                markup,
                re.DOTALL | re.IGNORECASE,
            )
            if match:
                value = clean(_TAGS.sub("", match.group(1)))
                if value:
                    details[field] = value

    _SPACE_CACHE[space_root] = details

    return details


def user_names(space_root: Path) -> dict[str, str]:
    """Map Atlassian account id to display name, harvested from the pages.

    The XML export records a mention only as an account id, so a meeting's
    participant list comes out empty. This export renders the same mentions as
    names and tags them with the account id, which lets the XML parser put the
    names back. Deleted accounts render as "Former user (Deleted)" and are left
    out, since that is not a name.
    """

    if space_root in _NAME_CACHE:
        return _NAME_CACHE[space_root]

    names: dict[str, str] = {}

    for page in sorted(space_root.glob("*.html")):
        markup = page.read_text(encoding="utf-8", errors="replace")

        for account_id, label in _USER_MENTION.findall(markup):
            name = clean(html.unescape(_TAGS.sub("", label)))
            if name and "Former user" not in name:
                names.setdefault(account_id, name)

    _NAME_CACHE[space_root] = names

    return names


def page_id(file_path: Path) -> str:
    match = _PAGE_ID.search(file_path.name)

    return match.group(1) if match else file_path.stem


def site_host(space_root: Path) -> str:
    """The Atlassian host, which the export layout places above the space."""

    return space_root.parent.name


def _page_title(raw_title: str, space_name: str) -> str:
    """Strip the "Space Name : " prefix Confluence puts on every page title."""

    title = clean(raw_title)
    prefix = f"{space_name} : "

    if title.startswith(prefix):
        return title[len(prefix) :]

    return title


def extract_date(title: str, fallback: str) -> str:
    """Prefer a date in the page title, fall back to the page's own byline.

    Meeting pages are named for the day they cover, which is the date worth
    having. Everything else gets the Confluence created/modified date, and an
    empty string when there is neither.
    """

    match = TITLE_DATE.match(title)
    if match:
        try:
            return datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

    match = _METADATA_DATE.search(fallback or "")
    if match:
        try:
            return datetime.strptime(match.group(1), "%b %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    return ""


def organizational_unit(breadcrumbs: list[str], space_name: str) -> list[str]:
    """The page's ancestor trail, with Confluence's repetitions removed."""

    units: list[str] = []

    for crumb in breadcrumbs:
        if units and units[-1] == crumb:
            continue
        units.append(crumb)

    return units or [space_name]


def load_metadata(file_path: Path) -> dict:
    """An optional metadata.json above the page, overriding derived values."""

    current_path = file_path.parent

    while current_path != current_path.parent:
        metadata_file = current_path / "metadata.json"

        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}

        current_path = current_path.parent

    return {}


def process(file_path: Path) -> Document | None:
    if not is_page_file(file_path):
        return None

    space_root = file_path.parent
    space = space_details(space_root)

    parser = _PageParser()
    parser.feed(file_path.read_text(encoding="utf-8", errors="replace"))
    parser.close()

    content = parser.content

    if not content.strip():
        # A placeholder page, or one whose body is only images and macros.
        print(f"Skipping empty Confluence page: {file_path.name}")
        return None

    title = _page_title(parser.title, space["name"])
    identifier = page_id(file_path)
    site = site_host(space_root)
    metadata = load_metadata(file_path)

    source_url = metadata.get("source_url") or (
        f"https://{site}/wiki/spaces/{space['key']}/pages/{identifier}" if site else ""
    )

    units = metadata.get("organizational_unit") or organizational_unit(
        parser.breadcrumbs, space["name"]
    )

    # A page named for a date is a meeting record; anything else is reference
    # material. Both are wiki pages, which is what content_type says.

    return Document(
        project=metadata.get("project") or space["name"],
        organizational_unit=units,
        source_system="Confluence",
        source_identifier=identifier,
        source_url=source_url,
        date=extract_date(title, parser.byline),
        asset_type=asset_type(title),
        content_type="Wiki Page",
        title=metadata.get("title") or title,
        content=content,
    )
