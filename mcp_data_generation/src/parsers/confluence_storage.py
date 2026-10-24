# parsers/confluence_storage.py

"""Render Confluence storage format as text blocks.

Storage format is the XHTML-plus-Confluence-elements markup a page is actually
stored as. Both machine-readable exports carry it verbatim -- the XML export
inside ``entities.xml``, the CSV export in ``bodycontent.csv`` -- so the markup
handling lives here rather than in either format's parser. The HTML export does
not use it: there Confluence has already rendered the page to plain HTML.

Dates live here too, because both exports write timestamps the same way and
both prefer a date in the page title over the page's own creation time.
"""

import re
from datetime import datetime
from html import escape

from src.parsers.confluence_markup import TITLE_DATE, BlockBuilder, clean

# Confluence writes timestamps as "2021-02-09 02:46:43.608".
_TIMESTAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# A page body may itself be wrapped in CDATA by the export, so a CDATA section
# *within* the body cannot close with "]]>" -- that would end the outer wrapper
# early. Confluence writes "]] >" instead.
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\s*\]\s*>", re.DOTALL)


def unwrap_cdata(markup: str) -> str:
    """Replace CDATA sections with their escaped text.

    html.parser mishandles these sections: the spaced "]] >" close leaves it
    mid-declaration, and everything after the first one arrives as raw text
    instead of markup, collapsing the rest of the page into one unparsed blob.
    Escaping the contents first means the parser only ever sees ordinary
    character data.
    """

    return _CDATA.sub(lambda match: escape(match.group(1), quote=False), markup)


def page_date(title: str, creation_date: str) -> str:
    """The page's date: a leading date in its title, else when it was created.

    A meeting page is named for the day it covers, which is what a reader means
    by its date; the creation timestamp can be days off, and for a page copied
    from a template it is meaningless.
    """

    match = TITLE_DATE.match(title or "")
    if match:
        try:
            return datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

    match = _TIMESTAMP.match(creation_date or "")
    if match:
        return match.group(0)

    return ""


class StorageParser(BlockBuilder):
    """Turn storage-format markup into text blocks.

    Storage format is XHTML plus Confluence's own elements. Three groups matter:

    * configuration and placeholders carry no information a reader wants --
      macro parameters, template placeholder text, task ids and statuses;
    * resource references carry their text in an attribute rather than as
      content -- a mention's account id, a linked page's title, an attachment's
      filename;
    * a link's visible text is its ``ac:plain-text-link-body`` when it has one
      and the reference's own title otherwise. These differ far more often than
      not: 48 of 53 such links in the WGPTT space point at one page title while
      displaying different words. Emitting both would duplicate the other five
      and misrepresent the rest, so a link is resolved as a unit when it
      closes;
    * tasks are a list in all but name.

    Everything else behaves like ordinary HTML, which the base class handles.
    """

    skip_content = BlockBuilder.skip_content | {
        "ac:parameter",
        "ac:placeholder",
        "ac:task-id",
        "ac:task-status",
        "ac:image",
    }

    list_tags = BlockBuilder.list_tags | {"ac:task-list"}
    item_tags = BlockBuilder.item_tags | {"ac:task"}

    def __init__(self, user_names: dict[str, str]) -> None:
        super().__init__(capturing=True)

        self._user_names = user_names
        self._links: list[dict] = []
        self.unresolved_mentions = 0

    def _reference_text(self, tag: str, attributes: dict) -> str:
        if tag == "ri:user":
            key = attributes.get("ri:userkey") or attributes.get("ri:account-id") or ""
            name = self._user_names.get(key, "")
            if not name:
                self.unresolved_mentions += 1
            return name

        if tag == "ri:page":
            return attributes.get("ri:content-title", "")

        if tag == "ri:attachment":
            return attributes.get("ri:filename", "")

        return ""

    def handle_extra_starttag(self, tag: str, attributes: dict) -> bool:
        if tag == "ac:link":
            self._links.append({"reference": "", "body": "", "in_body": False})
            return True

        if tag in {"ri:user", "ri:page", "ri:attachment"}:
            text = self._reference_text(tag, attributes)
            if self._links:
                self._links[-1]["reference"] = text
            else:
                self.add_text(text)
            return True

        if tag == "ac:plain-text-link-body":
            if self._links:
                self._links[-1]["in_body"] = True
            return True

        # Confluence template placeholders render as grey prompt text.
        if tag == "span" and "placeholder" in attributes.get("class", ""):
            self.begin_skip(tag)
            return True

        return False

    def handle_extra_endtag(self, tag: str) -> bool:
        if tag == "ac:plain-text-link-body":
            if self._links:
                self._links[-1]["in_body"] = False
            return True

        if tag == "ac:link":
            if self._links:
                link = self._links.pop()
                self.add_text(clean(link["body"]) or link["reference"])
            return True

        return False

    def handle_data(self, data: str) -> None:
        # Inside a link body the text belongs to the link, not to the block, so
        # that the link can choose between it and the reference title.
        if self._links and self._links[-1]["in_body"]:
            self._links[-1]["body"] += data
            return

        super().handle_data(data)


def page_text(markup: str, user_names: dict[str, str]) -> tuple[str, int]:
    """Extract a page body's text. Returns (text, unresolved mention count)."""

    parser = StorageParser(user_names)
    parser.feed(unwrap_cdata(markup))
    parser.close()

    return parser.content, parser.unresolved_mentions
