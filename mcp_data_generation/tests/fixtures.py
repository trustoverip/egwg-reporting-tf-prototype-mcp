# tests/fixtures.py

"""Build a small raw cache in a temporary directory.

The fixtures are generated rather than committed: a Confluence CSV export is
four files of mutually consistent tables, and a hand-maintained copy of one in
the repository would drift from what the parser expects without anybody
noticing. Generating them also lets a test say what it is varying -- one page
body, one file name, one compression -- instead of diffing two checked-in
trees.

Everything here is synthetic. No fixture is derived from the real cache, so the
suite needs no network, no credentials and no 227 MB clone.
"""

import csv
import gzip
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src import ingestion_engine

# A page's body has to clear the parser's "is there any text here" bar and read
# like prose, since boilerplate detection downstream counts words.
PAGES = (
    {
        "id": "1001",
        "title": "Governance Overview",
        "parent": "",
        "created": "2026-01-05 10:00:00.000",
        "body": (
            "<p>The governance stack describes how an ecosystem publishes "
            "its rules and who is accountable for them.</p>"
        ),
    },
    {
        "id": "1002",
        "title": "Meeting Notes",
        "parent": "1001",
        "created": "2026-02-11 09:30:00.000",
        "body": (
            "<p>Attendees reviewed the draft and agreed to publish the "
            "revised glossary before the next meeting.</p>"
        ),
    },
)

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
Welcome everyone, let us start with the agenda for today.

2
00:00:04.000 --> 00:00:08.000
The first item is the draft governance framework.
"""

METADATA = {
    "project": "Example Project",
    "organizational_unit": ["Example Working Group"],
    "title": "Example Community Call",
    "source_url": "https://example.invalid/meetings/example",
}


class CacheFixture(unittest.TestCase):
    """A temporary raw cache, plus the one-line way to ingest it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="toip-fixture-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.raw = self.tmp / "raw"
        self.processed = self.tmp / "out" / "processed"
        self.registry_file = self.tmp / "out" / "registry.json"

        self.raw.mkdir(parents=True)

    # -- building a cache ---------------------------------------------------

    def write_export(
        self,
        export_dir: Path,
        space_key: str = "HOME",
        space_name: str = "Example Space",
        pages: tuple[dict, ...] = PAGES,
        versions: dict[str, int] | None = None,
    ) -> Path:
        """Write a Confluence CSV export: the tables, as Confluence dumps them.

        ``versions`` bumps a page's version column, which is what a real export
        does when a page is edited. The parser ignores the column, but it is
        part of content.csv, so an edit changes that file as well as the body
        -- which is how the engine comes to look at the export again.
        """

        export_dir.mkdir(parents=True, exist_ok=True)
        versions = versions or {}

        with open(export_dir / "spaces.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["spacekey", "spacename"])
            writer.writerow([space_key, space_name])

        with open(export_dir / "content.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "contentid",
                    "contenttype",
                    "content_status",
                    "spaceid",
                    "title",
                    "parentid",
                    "creationdate",
                    "version",
                ]
            )

            for page in pages:
                writer.writerow(
                    [
                        page["id"],
                        "PAGE",
                        "current",
                        "55",
                        page["title"],
                        page["parent"],
                        page["created"],
                        versions.get(page["id"], 1),
                    ]
                )

            # A historical version carries no spaceid. Every export has these,
            # seven for every live page in the real one, and indexing them
            # would publish superseded text.
            writer.writerow(
                [
                    "9001",
                    "PAGE",
                    "current",
                    "",
                    pages[0]["title"],
                    "",
                    "2026-01-04 10:00:00.000",
                    1,
                ]
            )

        with open(
            export_dir / "bodycontent.csv", "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.writer(f)
            writer.writerow(["contentid", "body"])

            for page in pages:
                writer.writerow([page["id"], page["body"]])

            writer.writerow(["9001", "<p>A superseded copy of the overview.</p>"])

        return export_dir

    def write_zoom(
        self,
        series_dir: Path,
        stem: str = "GMT20260211-150000_Recording",
        transcript: str = VTT,
    ) -> Path:
        """Write a Zoom series directory: a transcript and its metadata."""

        series_dir.mkdir(parents=True, exist_ok=True)

        (series_dir / "metadata.json").write_text(
            json.dumps(METADATA, indent=2), encoding="utf-8"
        )

        transcript_file = series_dir / f"{stem}.transcript.vtt"
        transcript_file.write_text(transcript, encoding="utf-8")

        return transcript_file

    def gzip_in_place(self, directory: Path, suffix: str = ".csv") -> None:
        """Compress a directory's files, appending .gz as gzip(1) does.

        This is what was done to the real cache to make it clonable, and what
        re-dated 2,103 documents when the hash was taken over the file as it
        sat on disk rather than over its contents.
        """

        for path in sorted(directory.glob(f"*{suffix}")):
            compressed = path.with_suffix(path.suffix + ".gz")

            with open(path, "rb") as plain, gzip.open(compressed, "wb") as f:
                shutil.copyfileobj(plain, f)

            path.unlink()

    def gunzip_in_place(self, directory: Path) -> None:
        """Expand a directory's .gz files back, dropping the suffix."""

        for path in sorted(directory.glob("*.gz")):
            with gzip.open(path, "rb") as f, open(path.with_suffix(""), "wb") as out:
                shutil.copyfileobj(f, out)

            path.unlink()

    # -- running the engine -------------------------------------------------

    def ingest(self, *extra: str) -> dict:
        """Run one ingestion and return the registry it wrote."""

        argv = [
            "--raw",
            str(self.raw),
            "--processed",
            str(self.processed),
            "--registry",
            str(self.registry_file),
            "--quiet",
            *extra,
        ]

        output = io.StringIO()

        with redirect_stdout(output):
            code = ingestion_engine.main(argv)

        self.assertEqual(code, 0, output.getvalue())
        self.output = output.getvalue()

        return self.registry()

    # -- reading the result -------------------------------------------------

    def registry(self) -> dict:
        if not self.registry_file.exists():
            return {}

        with open(self.registry_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def saids(self) -> set[str]:
        """Every document identifier the registry currently points at."""

        return {
            said
            for entry in self.registry().values()
            for said in entry["document_saids"]
        }

    def stamps(self) -> dict[str, str]:
        return {
            key: entry["processed_at"] for key, entry in self.registry().items()
        }

    def documents_on_disk(self) -> set[str]:
        if not self.processed.is_dir():
            return set()

        return {path.stem for path in self.processed.glob("*.json")}
