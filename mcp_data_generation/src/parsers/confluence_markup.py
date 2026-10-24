# parsers/confluence_markup.py

"""Turn Confluence markup into blank-line-separated text blocks.

Both Confluence exports hold markup, but not the same markup. An HTML space
export holds rendered pages; an XML export holds Confluence *storage format*,
which is XHTML plus `ac:` and `ri:` elements for macros, links and mentions.
The block-building logic is identical for both, so it lives here and each
parser subclasses it.

Blocks matter downstream. Boilerplate detection compares paragraphs, and search
snippets are read by people, so headings keep their level, lists keep their
nesting, and table rows stay on one line.

The page-title conventions every format shares live here too, since all three
parsers have to read a date out of a title the same way.
"""

import re
from html.parser import HTMLParser

# A page named for the day it covers: "2020-07-07 Meeting",
# "2021-03-15-Meeting-notes".
TITLE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def asset_type(title: str) -> str:
    """"Meeting" for a page named after a date, "Document" otherwise."""

    return "Meeting" if TITLE_DATE.match(title or "") else "Document"

# Tags whose content is markup or configuration, never text.
SKIP_CONTENT = {"style", "script"}

# Tags that end a block of text.
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"}

HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

LIST_TAGS = {"ul", "ol"}

ITEM_TAGS = {"li"}

CELL_TAGS = {"td", "th"}


def clean(text: str) -> str:
    """Collapse whitespace, including the non-breaking spaces Confluence emits."""

    return " ".join(text.replace("\xa0", " ").split())


class BlockBuilder(HTMLParser):
    """Accumulate markup into ``blocks``.

    Subclasses decide *when* to capture by setting ``_capturing``, and may add
    their own tags to the skip, block, list and item sets.
    """

    skip_content = SKIP_CONTENT
    block_tags = BLOCK_TAGS
    heading_tags = HEADING_TAGS
    list_tags = LIST_TAGS
    item_tags = ITEM_TAGS
    cell_tags = CELL_TAGS

    def __init__(self, capturing: bool = True) -> None:
        super().__init__(convert_charrefs=True)

        self._blocks: list[str] = []
        self._lines: list[str] = []
        self._buffer: list[str] = []

        self._skip_depth = 0
        self._skip_tag: str | None = None
        self._list_depth = 0
        self._capturing = capturing

    # -- buffer handling -------------------------------------------------

    def _flush_buffer(self, prefix: str = "") -> str:
        text = clean("".join(self._buffer))
        self._buffer.clear()

        return f"{prefix}{text}" if text else ""

    def _flush_block(self, prefix: str = "") -> None:
        text = self._flush_buffer(prefix)
        if text:
            self._blocks.append(text)

    def _flush_lines(self) -> None:
        if self._lines:
            self._blocks.append("\n".join(self._lines))
            self._lines.clear()

    def _emit_list_item(self) -> None:
        """Emit the pending buffer as a list line, indented by nesting depth."""

        indent = "  " * max(0, self._list_depth - 1)
        line = self._flush_buffer(f"{indent}- ")
        if line:
            self._lines.append(line)

    def begin_skip(self, tag: str) -> None:
        """Ignore everything until ``tag`` closes.

        Subclasses use this for elements that only carry text when an attribute
        says so, such as a span holding Confluence template placeholder text.
        """

        self._skip_tag = tag
        self._skip_depth = 1

    def add_text(self, text: str) -> None:
        """Append text to the current block. Subclasses use this for mentions."""

        if self._capturing and not self._skip_depth:
            self._buffer.append(text)

    # -- hooks for subclasses --------------------------------------------

    def handle_extra_starttag(self, tag: str, attributes: dict) -> bool:
        """Return True when the subclass has fully handled this start tag."""

        return False

    def handle_extra_endtag(self, tag: str) -> bool:
        """Return True when the subclass has fully handled this end tag."""

        return False

    # -- HTMLParser interface --------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return

        if tag in self.skip_content:
            self.begin_skip(tag)
            return

        attributes = {key: (value or "") for key, value in attrs}

        if self.handle_extra_starttag(tag, attributes):
            return

        if not self._capturing:
            return

        if tag in self.block_tags:
            self._flush_buffer()
            return

        if tag in self.list_tags:
            # A nested list lives inside its parent item, so the parent's own
            # text is still pending. Emit it now or it is swallowed into the
            # deepest child item.
            self._emit_list_item()
            self._list_depth += 1
            return

        if tag == "br":
            self.add_text(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return

        if self.handle_extra_endtag(tag):
            return

        if not self._capturing:
            return

        if tag in self.heading_tags:
            self._flush_lines()
            self._flush_block("#" * self.heading_tags[tag] + " ")
            return

        if tag in self.block_tags:
            self._flush_lines()
            self._flush_block()
            return

        if tag in self.item_tags:
            self._emit_list_item()
            return

        if tag in self.list_tags:
            self._list_depth = max(0, self._list_depth - 1)
            if self._list_depth == 0:
                self._flush_lines()
            return

        if tag in self.cell_tags:
            text = self._flush_buffer()
            if text:
                self._lines.append(text)
            return

        if tag == "tr":
            if self._lines:
                self._blocks.append(" | ".join(self._lines))
                self._lines.clear()
            return

        if tag == "table":
            self._flush_lines()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._capturing:
            return

        self._buffer.append(data)

    def unknown_decl(self, data: str) -> None:
        """Capture CDATA, which is how storage format carries code blocks.

        The HTML parser reports ``<![CDATA[...]]>`` here rather than as data.
        """

        if data.startswith("CDATA["):
            self.add_text(data[len("CDATA[") :])

    def close(self) -> None:  # type: ignore[override]
        super().close()
        self._flush_block()
        self._flush_lines()

    @property
    def content(self) -> str:
        return "\n\n".join(block for block in self._blocks if block.strip())
