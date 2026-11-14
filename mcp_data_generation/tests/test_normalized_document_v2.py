"""Schema v2 handoff: real catalog export, membership and atomic failure."""

import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from src import artifact_records, curate
from src.py_saidify import py_verify_urn
from tests.fixtures import CacheFixture


class NormalizedV2Tests(CacheFixture):
    def setUp(self):
        super().setUp()
        self.transcript = self.write_zoom(
            self.raw / "zoom" / "example" / "example_community_call"
        )
        self.ingest()
        self.catalog = self.tmp / "catalog"
        self.curated_v2 = self.tmp / "curated-v2"

    def export_catalog(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(artifact_records.main([
                "--raw", str(self.raw), "--registry", str(self.registry_file),
                "--output", str(self.catalog),
            ]), 0)

    def produce(self, *, dest=None, extra=()):
        args = [
            "--processed", str(self.processed),
            "--curated", str(dest or self.curated_v2),
            "--registry", str(self.registry_file),
            "--artifact-catalog", str(self.catalog),
            "--schema-version", "2", "--quiet", *extra,
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(curate.main(args), 0)
        if "--dry-run" in extra:
            return []
        docs = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (dest or self.curated_v2).glob("*.json")
        ]
        self.assertEqual(len(docs), 1)
        return docs[0]

    def register_extra(self, filename, payload, processed_said):
        path = self.raw / "zoom" / "additional" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        registry = self.registry()
        registry[path.relative_to(self.raw).as_posix()] = {
            "file_hash": digest,
            "processed_at": "2026-10-02T00:00:00+00:00",
            "document_saids": [processed_said] if processed_said else [],
        }
        self.registry_file.write_text(json.dumps(registry), encoding="utf-8")
        return f"sha256:{digest}"

    def test_real_acquisition_export_verifies_v2_identity_and_repeatability(self):
        self.export_catalog()
        doc = self.produce()
        (entry,) = self.registry().values()
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(doc["provenance"]["artifact_ids"], [f"sha256:{entry['file_hash']}"])
        self.assertTrue(py_verify_urn(doc, doc["identifier"], label="identifier"))
        second = self.produce(dest=self.tmp / "second-curated-v2")
        self.assertEqual(doc["identifier"], second["identifier"])
        tampered = dict(doc, provenance={"artifact_ids": ["sha256:" + "f" * 64]})
        self.assertFalse(py_verify_urn(tampered, doc["identifier"], label="identifier"))
        self.assertNotIn("collected_at", doc["provenance"])

    def test_multiple_explicit_sources_sorted_and_unrelated_source_excluded(self):
        (said,) = self.saids()
        extra = self.register_extra("second.vtt", b"a second contributing file", said)
        unrelated = self.register_extra("unrelated.vtt", b"separate content", None)
        self.export_catalog()
        document = self.produce()
        original = f"sha256:{hashlib.sha256(self.transcript.read_bytes()).hexdigest()}"
        expected = sorted([original, extra])
        self.assertEqual(document["provenance"]["artifact_ids"], expected)
        self.assertNotIn(unrelated, document["provenance"]["artifact_ids"])
        self.assertEqual(
            document["identifier"],
            self.produce(dest=self.tmp / "repeat-curated-v2")["identifier"],
        )
        self.assertEqual(len(set(document["provenance"]["artifact_ids"])), 2)

    def test_identical_bytes_deduplicate_artifact_reference(self):
        (said,) = self.saids()
        self.register_extra("same.vtt", self.transcript.read_bytes(), said)
        self.export_catalog()
        document = self.produce()
        self.assertEqual(len(document["provenance"]["artifact_ids"]), 1)

    def test_missing_or_tampered_catalog_evidence_rejected(self):
        self.export_catalog()
        (entry,) = self.registry().values()
        record_file = self.catalog / f"{entry['file_hash']}.json"
        original = json.loads(record_file.read_text(encoding="utf-8"))
        for corrupted in (
            [],
            {**original, "observations": []},
            {**original, "artifact_id": "sha256:" + "f" * 64},
            {**original, "observations": "not-an-array"},
        ):
            record_file.write_text(json.dumps(corrupted), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "source evidence unavailable"):
                self.produce()
            self.assertFalse(self.curated_v2.exists())
        record_file.unlink()
        with self.assertRaisesRegex(SystemExit, "source evidence unavailable"):
            self.produce()

    def test_catalog_export_rejects_changed_raw_bytes(self):
        self.transcript.write_bytes(b"changed after ingest")
        with self.assertRaises(SystemExit):
            self.export_catalog()
        self.assertFalse(self.curated_v2.exists())

    def test_stale_processed_cache_entries_do_not_block_current_v2_curation(self):
        self.export_catalog()
        (self.processed / "zz-invalid.json").write_text("{}", encoding="utf-8")
        (self.processed / "zz-unreadable.json").write_text("{", encoding="utf-8")
        doc = self.produce()
        self.assertEqual(doc["schema_version"], 2)
        self.assertTrue(py_verify_urn(doc, doc["identifier"], label="identifier"))

    def test_missing_registered_processed_document_fails_before_publication(self):
        self.export_catalog()
        (said,) = self.saids()
        (self.processed / f"{said}.json").unlink()
        with self.assertRaisesRegex(SystemExit, "missing from the processed tree"):
            self.produce()
        self.assertFalse(self.curated_v2.exists())

    def test_source_revision_without_pruning_selects_current_v2_document(self):
        self.transcript.write_text(
            self.transcript.read_text(encoding="utf-8")
            + "\\n3\\n00:00:10.000 --> 00:00:12.000\\nNEW_SOURCE_REVISION_MARKER\\n",
            encoding="utf-8",
        )
        self.ingest()
        self.assertGreaterEqual(len(list(self.processed.glob("*.json"))), 2)
        self.export_catalog()
        doc = self.produce()
        self.assertIn("NEW_SOURCE_REVISION_MARKER", doc["content"]["text"])
        self.assertTrue(py_verify_urn(doc, doc["identifier"], label="identifier"))

    def test_missing_processing_timestamp_fails_before_v2_publication(self):
        self.export_catalog()
        registry = self.registry()
        key = next(iter(registry))
        registry[key].pop("processed_at")
        self.registry_file.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "source evidence unavailable"):
            self.produce()
        self.assertFalse(self.curated_v2.exists())

    def test_dry_run_and_existing_destination_never_overwrite(self):
        self.export_catalog()
        self.produce(extra=("--dry-run",))
        self.assertFalse(self.curated_v2.exists())
        self.produce()
        original = {
            p.name: p.read_bytes() for p in self.curated_v2.glob("*.json")
        }
        with self.assertRaisesRegex(SystemExit, "fresh --curated"):
            self.produce()
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.curated_v2.glob("*.json")},
            original,
        )
        with self.assertRaisesRegex(SystemExit, "--prune is unsupported"):
            self.produce(dest=self.tmp / "third-curated-v2", extra=("--prune",))

    def test_missing_registry_does_not_create_output(self):
        self.registry_file.unlink()
        with self.assertRaisesRegex(SystemExit, "source evidence unavailable"):
            self.produce()
        self.assertFalse(self.curated_v2.exists())
