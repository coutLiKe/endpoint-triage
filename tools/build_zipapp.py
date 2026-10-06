"""Build dist/endpoint-triage.pyz, a single-file executable zip of the tool.

A Python zip application is a zip file with a `__main__.py` at its root,
optionally preceded by a `#!` interpreter line. Python can run it directly:

    python endpoint-triage.pyz --help

It needs only a Python 3.11+ interpreter on the endpoint, with no pip and no
install step, so one file can be copied to a machine or pushed by an RMM.

The build is reproducible: entries are added in sorted order with a fixed
timestamp and fixed permissions, so building the same source twice gives a
byte-identical file and the same SHA-256. Anyone can rebuild a release from
its tag and check that the checksum matches. (This is the same file format
the standard-library `zipapp` module produces; it is written with `zipfile`
directly because `zipapp` copies file modification times into the archive.)

Usage:
    python tools/build_zipapp.py
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PACKAGE = "endpoint_triage"
SHEBANG = b"#!/usr/bin/env python3\n"
# Earliest timestamp the zip format supports; any fixed value works.
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# The archive's entry point reuses the package's own __main__, which
# includes the Python version check.
ENTRY_POINT = "import endpoint_triage.__main__  # noqa: F401\n"


def source_files(root: Path = ROOT) -> list[Path]:
    package = root / PACKAGE
    return sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts)


def _add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16  # regular file, rw-r--r--
    archive.writestr(info, data)


def build(dist: Path = DIST, root: Path = ROOT) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    target = dist / "endpoint-triage.pyz"
    with target.open("wb") as handle:
        handle.write(SHEBANG)
        with zipfile.ZipFile(handle, "w") as archive:
            _add(archive, "__main__.py", ENTRY_POINT.encode("utf-8"))
            for path in source_files(root):
                # Normalize line endings so Windows checkouts build the same bytes.
                data = path.read_bytes().replace(b"\r\n", b"\n")
                _add(archive, path.relative_to(root).as_posix(), data)
    target.chmod(0o755)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (dist / "endpoint-triage.pyz.sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    path = build()
    print(f"Built {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    print((DIST / "endpoint-triage.pyz.sha256").read_text().strip())
    sys.exit(0)
