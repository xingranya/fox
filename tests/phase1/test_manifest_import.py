"""来源 Manifest 标准化与坏数据测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from brand_os.domain import SourceGapRecord
from brand_os.manifest_import import ManifestImportError, load_source_gaps, load_source_manifest


class ManifestImportTest(unittest.TestCase):
    """验证现有两种 Manifest 和统一 v1 契约都能安全读取。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_remote_manifest_derives_stable_id_without_guessing_missing_metadata(self) -> None:
        digest = hashlib.sha256(b"remote").hexdigest()
        manifest = {
            "schema_version": "remote-source-manifest.v1",
            "snapshot_date": "2026-07-21",
            "excluded": ["权限不可读目录"],
            "records": [
                {
                    "role": "project_control",
                    "relative_path": "项目/总控.md",
                    "sha256": digest,
                }
            ],
        }
        batch = load_source_manifest(self.write("remote.json", manifest), origin_ref="remote.json")
        record = batch.records[0]
        expected_id = "HXD-" + hashlib.sha256("项目/总控.md".encode()).hexdigest()[:16].upper()
        self.assertEqual(record.logical_source_id, expected_id)
        self.assertIsNone(record.size_bytes)
        self.assertIsNone(record.confidentiality)
        self.assertEqual(batch.gaps[0].gap_id, "GAP-001")

    def test_sample_manifest_preserves_source_id_and_known_gap(self) -> None:
        manifest = {
            "schema_version": "sample-manifest.v1",
            "record_count": 1,
            "known_source_gap": True,
            "records": [
                {
                    "source_id": "EX-001",
                    "filename": "样本.docx",
                    "sha256": hashlib.sha256(b"sample").hexdigest(),
                    "source_role": "working_document",
                    "confidentiality": "P2",
                    "size_bytes": 8,
                    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            ],
        }
        batch = load_source_manifest(self.write("sample.json", manifest))
        self.assertEqual(batch.records[0].logical_source_id, "EX-001")
        self.assertEqual(batch.gaps[0].gap_id, "GAP-SAMPLE-INCOMPLETE")

    def test_additional_gap_changes_import_digest_but_not_manifest_hash(self) -> None:
        manifest = {"schema_version": "source-import.v1", "records": [], "gaps": []}
        path = self.write("canonical.json", manifest)
        first = load_source_manifest(path)
        second = load_source_manifest(
            path,
            additional_gaps=(
                SourceGapRecord(
                    "GAP-X",
                    "KNOWN_SOURCE_GAP",
                    "尚未取得原件。",
                    "v5",
                    "manual:GAP-X",
                ),
            ),
        )
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertNotEqual(first.import_digest, second.import_digest)

    def test_rejects_count_mismatch_bad_hash_and_unsafe_path(self) -> None:
        count_mismatch = {
            "schema_version": "sample-manifest.v1",
            "record_count": 2,
            "records": [],
        }
        with self.assertRaises(ManifestImportError):
            load_source_manifest(self.write("count.json", count_mismatch))

        bad_hash = {
            "schema_version": "source-import.v1",
            "records": [
                {
                    "logical_source_id": "SRC-1",
                    "sha256": "bad",
                    "relative_path": "材料.md",
                    "source_role": "working_source",
                }
            ],
        }
        with self.assertRaises(ValueError):
            load_source_manifest(self.write("hash.json", bad_hash))

        bad_path = {
            "schema_version": "source-import.v1",
            "records": [
                {
                    "logical_source_id": "SRC-1",
                    "sha256": hashlib.sha256(b"safe").hexdigest(),
                    "relative_path": "../越界.md",
                    "source_role": "working_source",
                }
            ],
        }
        with self.assertRaises(ValueError):
            load_source_manifest(self.write("path.json", bad_path))

    def test_gap_file_requires_version_and_unique_ids(self) -> None:
        valid = {
            "schema_version": "source-gaps.v1",
            "gaps": [
                {
                    "gap_id": "GAP-1",
                    "status": "KNOWN_SOURCE_GAP",
                    "description": "尚未取得原件。",
                    "scope": "v5",
                    "evidence_ref": "manual:GAP-1",
                }
            ],
        }
        self.assertEqual(load_source_gaps(self.write("gaps.json", valid))[0].gap_id, "GAP-1")
        valid["gaps"].append(dict(valid["gaps"][0]))
        with self.assertRaises(ManifestImportError):
            load_source_gaps(self.write("duplicate-gaps.json", valid))


if __name__ == "__main__":
    unittest.main()
