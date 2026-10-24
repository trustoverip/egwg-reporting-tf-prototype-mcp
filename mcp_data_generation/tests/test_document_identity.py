# tests/test_document_identity.py

"""A document's identifier changes when its content changes, and never else.

That is the whole invariant. A document's identifier is a SAID over the curated
document, and the curated document carries ``collected_at``, so anything that
re-dates content hands every document it produced a new identifier and
invalidates the index built from the old ones. Keying the timestamp by path
once cost exactly that: renaming the Confluence exports and gzipping them
re-dated 2,103 documents whose text had not changed.

So the cases come in pairs. Content already in the known set keeps its
identifier through anything that touches only the container -- a rename, a move,
compression, a repeated run, ``--force``. Content that is not in the known set
gets a new identifier, and content that is no longer there loses its documents.
"""

from pathlib import Path

from src.content_hash import sha256_content
from tests.fixtures import PAGES, CacheFixture


class KnownContentKeepsItsIdentifier(CacheFixture):
    """The positive cases: nothing about the container confers identity."""

    def setUp(self) -> None:
        super().setUp()

        self.export = self.raw / "confluence" / "example.atlassian.net" / "csv"
        self.write_export(self.export)
        self.write_zoom(self.raw / "zoom" / "example" / "example_community_call")

        self.first = self.ingest()
        self.first_saids = self.saids()
        self.first_stamps = sorted(self.stamps().values())

        # Two pages from the export, one transcript from Zoom.
        self.assertEqual(len(self.first_saids), 3)

    def assert_identity_held(self) -> None:
        self.assertEqual(self.saids(), self.first_saids)
        self.assertEqual(sorted(self.stamps().values()), self.first_stamps)
        self.assertEqual(self.documents_on_disk(), self.first_saids)

    def test_a_repeated_run_changes_nothing_at_all(self) -> None:
        self.ingest()

        self.assertEqual(self.registry(), self.first)

    def test_force_rereads_every_file_and_still_changes_nothing(self) -> None:
        self.ingest("--force")

        self.assertEqual(self.registry(), self.first)

    def test_compressing_the_export_keeps_every_identifier(self) -> None:
        self.gzip_in_place(self.export)

        self.ingest("--prune")
        self.assert_identity_held()

        # The recorded hash is over the contents, so compression does not move
        # it even though every byte on disk changed.
        self.assertEqual(
            {entry["file_hash"] for entry in self.registry().values()},
            {entry["file_hash"] for entry in self.first.values()},
        )

    def test_expanding_it_again_keeps_every_identifier(self) -> None:
        self.gzip_in_place(self.export)
        self.ingest("--prune")
        self.gunzip_in_place(self.export)

        self.ingest("--prune")
        self.assert_identity_held()

    def test_renaming_the_export_directory_keeps_every_identifier(self) -> None:
        renamed = self.export.with_name("HOME-CSV")
        self.export.rename(renamed)

        self.ingest("--prune")
        self.assert_identity_held()

        self.assertIn(
            "confluence/example.atlassian.net/HOME-CSV/content.csv",
            self.registry(),
        )

    def test_renaming_and_compressing_together_keeps_every_identifier(self) -> None:
        """The exact change that re-dated the real corpus."""

        renamed = self.export.with_name("HOME-CSV")
        self.export.rename(renamed)
        self.gzip_in_place(renamed)

        self.ingest("--prune")
        self.assert_identity_held()

    def test_reading_the_same_cache_from_another_path_keeps_identifiers(self) -> None:
        """Registry keys are relative, so a second checkout is the same cache."""

        moved = self.tmp / "second-checkout"
        self.raw.rename(moved)
        self.raw = moved

        self.ingest()
        self.assert_identity_held()


class UnknownContentGetsANewIdentifier(CacheFixture):
    """The negative cases: content outside the known set is not inherited."""

    def setUp(self) -> None:
        super().setUp()

        self.export = self.raw / "confluence" / "example.atlassian.net" / "HOME-CSV"
        self.write_export(self.export)

        self.first = self.ingest()
        self.first_saids = self.saids()
        self.first_stamp = next(iter(self.first.values()))["processed_at"]

    def test_an_added_page_is_the_only_new_identifier(self) -> None:
        added = {
            "id": "1003",
            "title": "Glossary",
            "parent": "1001",
            "created": "2026-03-02 11:00:00.000",
            "body": (
                "<p>Definitions for the terms this working group uses when it "
                "talks about ecosystem governance.</p>"
            ),
        }

        self.write_export(self.export, pages=(*PAGES, added))
        self.ingest("--prune")

        self.assertEqual(len(self.saids()), 3)
        self.assertTrue(self.first_saids < self.saids())

    def test_an_edited_page_replaces_only_its_own_identifier(self) -> None:
        edited = dict(PAGES[1])
        edited["body"] = "<p>Attendees deferred the glossary to the next call.</p>"

        self.write_export(
            self.export,
            pages=(PAGES[0], edited),
            versions={"1002": 2},
        )
        self.ingest("--prune")

        self.assertEqual(len(self.saids()), 2)

        # One identifier survives, one is replaced, and the superseded document
        # does not linger on disk beside its replacement.
        self.assertEqual(len(self.first_saids & self.saids()), 1)
        self.assertEqual(self.documents_on_disk(), self.saids())

    def test_an_unseen_export_is_dated_now_rather_than_inherited(self) -> None:
        other = self.export.with_name("OTHER-CSV")

        self.write_export(
            other,
            space_key="OTHER",
            space_name="Other Space",
            pages=(
                {
                    "id": "2001",
                    "title": "Unrelated Page",
                    "parent": "",
                    "created": "2026-04-01 08:00:00.000",
                    "body": (
                        "<p>Wholly different text that this pipeline has never "
                        "collected before.</p>"
                    ),
                },
            ),
        )

        registry = self.ingest("--prune")
        key = "confluence/example.atlassian.net/OTHER-CSV/content.csv"

        self.assertIn(key, registry)
        self.assertGreater(registry[key]["processed_at"], self.first_stamp)

        # Its documents are its own, and the export beside it is undisturbed.
        self.assertEqual(set(registry[key]["document_saids"]) & self.first_saids, set())
        self.assertTrue(self.first_saids < self.saids())

    def test_unsupported_and_empty_sources_yield_no_document(self) -> None:
        series = self.raw / "zoom" / "example" / "example_community_call"
        self.write_zoom(series)

        (series / "GMT20260211-150000_Recording.mp4").write_bytes(b"\x00\x00\x00 ftyp")
        (series / "GMT20260305-150000_Recording.transcript.vtt").write_text(
            "", encoding="utf-8"
        )

        attachments = self.export / "attachments" / "1001" / "7"
        attachments.mkdir(parents=True)
        (attachments / "1").write_bytes(b"%PDF-1.4 not text this pipeline reads")

        registry = self.ingest("--prune")
        keys = set(registry)

        self.assertFalse([key for key in keys if key.endswith(".mp4")])
        self.assertFalse([key for key in keys if "attachments" in key])
        self.assertFalse(
            [key for key in keys if key.endswith("GMT20260305-150000_Recording.transcript.vtt")]
        )

        # The run still succeeds and the real documents are unaffected.
        self.assertTrue(self.first_saids < self.saids())

    def test_a_vanished_source_loses_its_documents(self) -> None:
        import shutil

        shutil.rmtree(self.export)

        # --prune refuses a run that matched nothing, since every document
        # would look like a stray, so there is something else to find.
        self.write_zoom(self.raw / "zoom" / "example" / "example_community_call")

        self.ingest("--prune")

        self.assertEqual(len(self.first_saids & self.saids()), 0)
        self.assertEqual(self.documents_on_disk(), self.saids())
        self.assertNotIn(
            "confluence/example.atlassian.net/HOME-CSV/content.csv", self.registry()
        )


class RecordedHashesDescribeContent(CacheFixture):
    """The hash the cache publishes is of the contents, not of the file."""

    def test_compression_does_not_move_a_file_hash(self) -> None:
        export = self.raw / "confluence" / "example.atlassian.net" / "HOME-CSV"
        self.write_export(export)

        plain = export / "content.csv"
        before = sha256_content(plain)

        self.gzip_in_place(export)

        self.assertEqual(sha256_content(export / "content.csv.gz"), before)

    def test_a_misleading_gz_suffix_is_read_as_what_it_is(self) -> None:
        """Confluence shipped 49 files named .gz with not one compressed."""

        directory = self.raw / "confluence" / "example.atlassian.net" / "HOME-CSV"
        directory.mkdir(parents=True)

        plain = directory / "content.csv"
        plain.write_text("contentid,title\n1001,Governance Overview\n", encoding="utf-8")

        mislabelled = directory / "mislabelled.csv.gz"
        mislabelled.write_bytes(plain.read_bytes())

        self.assertEqual(sha256_content(mislabelled), sha256_content(plain))

    def test_a_truncated_gzip_is_an_error_not_a_partial_hash(self) -> None:
        directory = self.raw / "zoom"
        directory.mkdir(parents=True)

        broken = directory / "broken.vtt.gz"
        broken.write_bytes(b"\x1f\x8b\x08\x00truncated")

        with self.assertRaises(ValueError):
            sha256_content(broken)
