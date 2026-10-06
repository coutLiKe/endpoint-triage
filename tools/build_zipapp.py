"""Build dist/endpoint-triage.pyz, a single-file executable zip of the tool.

A zipapp is a zip file that Python can run directly:

    python endpoint-triage.pyz --help

It needs only a Python 3.11+ interpreter on the endpoint, with no pip and no
install step, so one file can be copied to a machine or pushed by an RMM.
Uses only the standard library (zipapp, hashlib).

Usage:
    python tools/build_zipapp.py
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
TARGET = DIST / "endpoint-triage.pyz"


def build() -> Path:
    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "app"
        shutil.copytree(ROOT / "endpoint_triage", staging / "endpoint_triage",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # The zip's entry point reuses the package's own __main__, which
        # includes the Python version check.
        (staging / "__main__.py").write_text("import endpoint_triage.__main__  # noqa: F401\n", encoding="utf-8")
        zipapp.create_archive(staging, TARGET, interpreter="/usr/bin/env python3", compressed=True)

    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    (DIST / "endpoint-triage.pyz.sha256").write_text(f"{digest}  {TARGET.name}\n", encoding="utf-8")
    return TARGET


if __name__ == "__main__":
    path = build()
    print(f"Built {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    print((DIST / "endpoint-triage.pyz.sha256").read_text().strip())
