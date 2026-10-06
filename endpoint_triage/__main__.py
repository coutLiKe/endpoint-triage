"""Allows running the tool with `python -m endpoint_triage` (and the .pyz build)."""

import sys

# Checked before importing anything else so an old interpreter gets a clear
# message instead of a confusing error from newer syntax.
if sys.version_info < (3, 11):
    sys.stderr.write(f"endpoint-triage requires Python 3.11 or newer (found {sys.version.split()[0]}).\n")
    raise SystemExit(3)

from endpoint_triage.cli import main  # noqa: E402

raise SystemExit(main())
