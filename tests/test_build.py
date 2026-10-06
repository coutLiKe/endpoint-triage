"""The single-file build must be reproducible and runnable."""

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_zipapp  # noqa: E402

from endpoint_triage import __version__  # noqa: E402


class BuildTests(unittest.TestCase):
    def test_build_is_byte_for_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = build_zipapp.build(Path(first)).read_bytes()
            b = build_zipapp.build(Path(second)).read_bytes()
        self.assertEqual(a, b)

    def test_archive_contents_and_fixed_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = build_zipapp.build(Path(tmp))
            self.assertTrue(target.read_bytes().startswith(b"#!/usr/bin/env python3\n"))
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                self.assertEqual(names[0], "__main__.py")
                self.assertIn("endpoint_triage/cli.py", names)
                self.assertFalse(any("__pycache__" in n or "tests/" in n for n in names))
                self.assertTrue(all(i.date_time == build_zipapp.FIXED_TIMESTAMP for i in archive.infolist()))
            checksum = (Path(tmp) / "endpoint-triage.pyz.sha256").read_text()
            self.assertTrue(checksum.endswith("  endpoint-triage.pyz\n"))

    def test_built_file_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = build_zipapp.build(Path(tmp))
            result = subprocess.run([sys.executable, str(target), "--version"], capture_output=True, text=True,
                                    timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(__version__, result.stdout)


if __name__ == "__main__":
    unittest.main()
