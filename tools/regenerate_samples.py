"""Regenerate sample/sample-report.txt and .json from the test fixture report.

The sample files double as golden files: tests/test_reporters.py fails if
the rendered reports no longer match them. After an intentional change to
the report format, run this script and review the diff before committing.

Usage:
    python tools/regenerate_samples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from endpoint_triage.reporters import render_json, render_text  # noqa: E402
from tests.sample_report import build_sample_report  # noqa: E402


def main() -> None:
    report = build_sample_report(debug_detail=False)
    (ROOT / "sample" / "sample-report.txt").write_text(render_text(report), encoding="utf-8")
    (ROOT / "sample" / "sample-report.json").write_text(render_json(report), encoding="utf-8")
    print("Regenerated sample/sample-report.txt and sample/sample-report.json")


if __name__ == "__main__":
    main()
