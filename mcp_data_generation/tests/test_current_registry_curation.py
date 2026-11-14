"""Normalization must select only current registry-linked processed versions."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from src import curate, ingestion_engine
from tests.fixtures import CacheFixture


class CurrentRegistryCurationTests(CacheFixture):
    def setUp(self):
        super().setUp()
        self.source = self.write_zoom(
            self.raw / "zoom" / "example" / "example_community_call"
        )
        self.ingest()
        self.curated = self.tmp / "curated"

    def run_curate(self, registry=None):
        with redirect_stdout(io.StringIO()):
            return curate.main([
                "--processed", str(self.processed),
                "--registry", str(registry or self.registry_file),
                "--curated", str(self.curated), "--quiet",
            ])

    def curated_documents(self):
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in self.curated.glob("*.json")]

    def test_source_revision_without_processed_pruning_selects_current_said(self):
        self.run_curate()
        initial = self.curated_documents()[0]
        self.source.write_text(
            self.source.read_text(encoding="utf-8") +
            "\n3\n00:00:10.000 --> 00:00:12.000\nNEW_SOURCE_REVISION_MARKER\n",
            encoding="utf-8",
        )
        self.ingest()  # no --prune; earlier processed SAID stays on disk
        self.assertGreaterEqual(len(list(self.processed.glob("*.json"))), 2)
        self.run_curate()
        (current,) = self.curated_documents()
        self.assertIn("NEW_SOURCE_REVISION_MARKER", current["content"]["text"])
        self.assertNotEqual(initial["identifier"], current["identifier"])
        self.assertEqual(current["provenance"]["artifact_id"],
                         "sha256:" + next(iter(self.registry().values()))["file_hash"])

    def test_unavailable_registry_fails_without_rewriting_curated_records(self):
        self.run_curate()
        old = {p.name: p.read_bytes() for p in self.curated.glob("*.json")}
        self.registry_file.unlink()
        with self.assertRaisesRegex(SystemExit, "curation provenance unavailable"):
            self.run_curate()
        self.assertEqual({p.name: p.read_bytes() for p in self.curated.glob("*.json")}, old)

    def test_malformed_registry_and_missing_registered_document_fail_closed(self):
        valid_registry = self.registry_file.read_text(encoding="utf-8")
        self.registry_file.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "curation provenance unavailable"):
            self.run_curate()
        self.registry_file.write_text(valid_registry, encoding="utf-8")
        self.ingest("--force")
        (said,) = self.saids()
        (self.processed / f"{said}.json").unlink()
        with self.assertRaisesRegex(SystemExit, "missing from the processed tree"):
            self.run_curate()
        self.assertFalse(self.curated.exists())

    def test_unreferenced_processed_document_does_not_change_publication(self):
        self.run_curate()
        before = self.curated_documents()[0]
        (self.processed / "urn:said:Estale.json").write_text(
            json.dumps({"identifier": "urn:said:Estale", "title": "stale"}),
            encoding="utf-8",
        )
        self.run_curate()
        self.assertEqual(self.curated_documents()[0], before)

    def test_missing_registry_ingestion_time_cannot_be_forged_from_file_mtime(self):
        registry = self.registry()
        key = next(iter(registry))
        registry[key].pop("processed_at")
        self.registry_file.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "missing ingestion timestamp"):
            self.run_curate()
        self.assertFalse(self.curated.exists())
