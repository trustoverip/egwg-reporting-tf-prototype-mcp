# tests/test_cache_manifest.py

"""The cache states its own content hashes, one manifest per collection unit.

A manifest sits where a unit of collection begins, and covers everything below
it: the Zoom tree is fetched as a whole, so one manifest covers every series
however deep; a Confluence space is exported on its own, so each export carries
its own. Anything a designated root does not cover -- a site's
``export-users.csv``, the cache's LICENSE -- records itself where it sits, so no
file in the cache goes unhashed.
"""

import gzip
import io
from contextlib import redirect_stdout

from src import cache_manifest
from tests.fixtures import CacheFixture


class ManifestFixture(CacheFixture):
    """A cache laid out like the real one, plus the tool under test."""

    def setUp(self) -> None:
        super().setUp()

        self.site = self.raw / "confluence" / "example.atlassian.net"

        self.write_export(self.site / "HOME-CSV")
        self.write_export(
            self.site / "OTHER-CSV", space_key="OTHER", space_name="Other Space"
        )

        # The site's own export, above either space export.
        (self.site / "export-users.csv").write_text(
            "User id,User name\n557058:abc,Example Person\n", encoding="utf-8"
        )

        self.write_zoom(self.raw / "zoom" / "example" / "example_community_call")
        self.write_zoom(self.raw / "zoom" / "other" / "other_tsc_meeting")

        (self.raw / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")

    def roots(self) -> set[str]:
        return {
            root.relative_to(self.raw).as_posix() or "."
            for root in cache_manifest.plan(self.raw)
        }

    def run_tool(self, *extra: str) -> tuple[int, str]:
        output = io.StringIO()

        with redirect_stdout(output):
            code = cache_manifest.main(["--raw", str(self.raw), "--quiet", *extra])

        return code, output.getvalue()


class ManifestsFollowCollectionUnits(ManifestFixture):
    def test_zoom_gets_one_manifest_for_every_child(self) -> None:
        self.assertIn("zoom", self.roots())

        self.assertEqual(
            {root for root in self.roots() if root.startswith("zoom")}, {"zoom"}
        )

    def test_each_space_export_gets_its_own_manifest(self) -> None:
        self.assertEqual(
            {root for root in self.roots() if "CSV" in root},
            {
                "confluence/example.atlassian.net/HOME-CSV",
                "confluence/example.atlassian.net/OTHER-CSV",
            },
        )

    def test_files_no_designated_root_covers_record_themselves(self) -> None:
        self.assertIn("confluence/example.atlassian.net", self.roots())
        self.assertIn(".", self.roots())

    def test_the_zoom_manifest_lists_children_by_relative_path(self) -> None:
        self.run_tool("--write")

        recorded = cache_manifest.read_manifest(self.raw / "zoom")

        self.assertIn(
            "example/example_community_call/GMT20260211-150000_Recording.transcript.vtt",
            recorded,
        )
        self.assertIn("other/other_tsc_meeting/metadata.json", recorded)

    def test_every_file_is_covered_exactly_once(self) -> None:
        on_disk = {
            path
            for path in self.raw.rglob("*")
            if path.is_file()
            and not path.name.startswith(".")
            and path.name != cache_manifest.MANIFEST_NAME
        }

        covered = [
            path
            for files in cache_manifest.plan(self.raw).values()
            for path in files.values()
        ]

        self.assertEqual(sorted(covered), sorted(on_disk))
        self.assertEqual(len(covered), len(set(covered)))


class CheckingReportsWhatMoved(ManifestFixture):
    def setUp(self) -> None:
        super().setUp()

        code, _ = self.run_tool("--write")
        self.assertEqual(code, 0)

    def test_an_unchanged_cache_checks_clean(self) -> None:
        code, output = self.run_tool()

        self.assertEqual(code, 0, output)

    def test_compressing_a_file_in_place_still_checks_clean(self) -> None:
        """The hash is of the contents, so the container may change."""

        transcript = (
            self.raw
            / "zoom"
            / "example"
            / "example_community_call"
            / "GMT20260211-150000_Recording.transcript.vtt"
        )

        plain = transcript.read_bytes()
        transcript.write_bytes(gzip.compress(plain))

        code, output = self.run_tool()

        self.assertEqual(code, 0, output)

    def test_edited_content_is_reported_and_fails(self) -> None:
        (self.site / "export-users.csv").write_text(
            "User id,User name\n557058:abc,Someone Else\n", encoding="utf-8"
        )

        code, output = self.run_tool()

        self.assertEqual(code, 1)
        self.assertIn("CHANGED", output)
        self.assertIn("export-users.csv", output)

    def test_an_unrecorded_file_is_reported_and_fails(self) -> None:
        self.write_zoom(
            self.raw / "zoom" / "example" / "example_community_call",
            stem="GMT20260318-150000_Recording",
        )

        code, output = self.run_tool()

        self.assertEqual(code, 1)
        self.assertIn("MISSING", output)

    def test_a_removed_file_is_reported_and_fails(self) -> None:
        (self.site / "export-users.csv").unlink()

        code, output = self.run_tool()

        self.assertEqual(code, 1)
        self.assertIn("STALE", output)

    def test_a_manifest_covering_nothing_is_reported_and_removed(self) -> None:
        """A manifest outliving its files, as a half-finished move leaves it."""

        export = self.site / "OTHER-CSV"

        for path in export.glob("*.csv"):
            path.unlink()

        code, output = self.run_tool()

        self.assertEqual(code, 1)
        self.assertIn("ORPHAN", output)

        # It names what it used to cover, not just that it covers nothing.
        self.assertIn("STALE", output)
        self.assertIn("bodycontent.csv", output)

        code, output = self.run_tool("--write")

        self.assertEqual(code, 0)
        self.assertIn("removed", output)
        self.assertEqual(self.run_tool()[0], 0)

    def test_the_manifest_is_not_itself_ingested(self) -> None:
        registry = self.ingest()

        self.assertFalse(
            [key for key in registry if key.endswith(cache_manifest.MANIFEST_NAME)]
        )
