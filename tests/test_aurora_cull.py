from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aurora_cull.cli import resolve_source_path, unique_target_path


class AuroraCullTests(unittest.TestCase):
    def test_resolve_source_path_rejects_missing_path(self) -> None:
        source, error = resolve_source_path("/tmp/definitely-missing-aurora-cull-path")
        self.assertIsNone(source)
        self.assertIsNotNone(error)
        self.assertIn("does not exist", error)

    def test_resolve_source_path_accepts_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source, error = resolve_source_path(tmp_dir)
            self.assertEqual(source, Path(tmp_dir))
            self.assertIsNone(error)

    def test_unique_target_path_adds_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            keep_dir = Path(tmp_dir)
            src = keep_dir / "image.jpg"
            (keep_dir / "image.jpg").write_bytes(b"1")
            (keep_dir / "image_2.jpg").write_bytes(b"2")
            target = unique_target_path(keep_dir, src)
            self.assertEqual(target.name, "image_3.jpg")


if __name__ == "__main__":
    unittest.main()
