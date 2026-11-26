"""Byte integrity, evidence boundaries and repeatability for artifact catalog."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.artifact_records import build_catalog, main


class ArtifactCatalogTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.data = b"example source bytes"
        self.digest = hashlib.sha256(self.data).hexdigest()
        (self.raw / "zoom").mkdir()
        (self.raw / "zoom" / "first.vtt").write_bytes(self.data)
        self.registry = {
            "zoom/first.vtt": {"file_hash": self.digest, "processed_at": "2026-10-01T10:00:00+00:00"}
        }

    def test_digest_and_attribution_are_distinct(self):
        result = build_catalog(self.raw, self.registry)
        record = result[f"sha256:{self.digest}"]
        self.assertEqual(record["artifact_id"], f"sha256:{self.digest}")
        observation = record["observations"][0]
        self.assertIsNone(observation["collected_at"])
        self.assertEqual(observation["provenance_attribution"]["collected_at"], "unknown")
        self.assertEqual(observation["provenance_attribution"]["source_system"], "local_fallback")
        self.assertEqual(observation["registry_first_processed_at"], "2026-10-01T10:00:00+00:00")
        self.assertIsNone(observation["source_identifier"])

    def test_identical_bytes_preserve_distinct_observations(self):
        (self.raw / "zoom" / "second.vtt").write_bytes(self.data)
        self.registry["zoom/second.vtt"] = dict(self.registry["zoom/first.vtt"])
        result = build_catalog(self.raw, self.registry)
        self.assertEqual(len(result), 1)
        observations = result[f"sha256:{self.digest}"]["observations"]
        self.assertEqual([o["registry_path"] for o in observations],
                         ["zoom/first.vtt", "zoom/second.vtt"])

    def test_modified_or_missing_source_fails_closed(self):
        (self.raw / "zoom" / "first.vtt").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            build_catalog(self.raw, self.registry)
        (self.raw / "zoom" / "first.vtt").unlink()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            build_catalog(self.raw, self.registry)

    def test_missing_digest_and_traversal_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid source hash"):
            build_catalog(self.raw, {"zoom/first.vtt": {"file_hash": ""}})
        with self.assertRaisesRegex(ValueError, "unsafe registry path"):
            build_catalog(self.raw, {"../outside": {"file_hash": self.digest}})

    def test_main_is_repeatable_and_does_not_mutate_raw(self):
        registry_file = self.root / "registry.json"
        registry_file.write_text(json.dumps(self.registry), encoding="utf-8")
        out = self.root / "artifacts"
        args = ["--raw", str(self.raw), "--registry", str(registry_file),
                "--output", str(out)]
        self.assertEqual(main(args), 0)
        target = out / f"{self.digest}.json"
        first = target.read_bytes()
        self.assertEqual(main(args), 0)
        self.assertEqual(target.read_bytes(), first)
        self.assertEqual((self.raw / "zoom" / "first.vtt").read_bytes(), self.data)
