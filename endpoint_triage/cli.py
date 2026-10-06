"""Command-line interface: parse options, run the scan, write reports, exit."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from endpoint_triage import __version__
from endpoint_triage.reporters import overall_status, write_reports
from endpoint_triage.scanner import run_scan

# Exit codes follow the Nagios/monitoring convention so scripts and RMM
# tools can react to the result without parsing the report.
EXIT_OK = 0          # Scan completed, no WARNING or CRITICAL findings
EXIT_WARNING = 1     # Scan completed, at least one WARNING finding
EXIT_CRITICAL = 2    # Scan completed, at least one CRITICAL finding
EXIT_UNKNOWN = 3     # Core diagnostics missing, or the tool itself failed
EXIT_ERROR = EXIT_UNKNOWN
EXIT_USAGE = 64      # Invalid command-line arguments (EX_USAGE)
EXIT_INTERRUPTED = 130  # Stopped with Ctrl+C

DEFAULT_OUTPUT_DIR = "triage-reports"


class _ArgumentParser(argparse.ArgumentParser):
    """argparse exits with 2 on bad arguments, which would collide with
    EXIT_CRITICAL. Use the conventional usage-error code instead."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="endpoint-triage",
        description="Collect read-only endpoint diagnostics and write a help desk report (.txt and .json).",
        epilog="Exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN (incomplete scan or tool error), 64 usage error.",
    )
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
                        help=f"directory to write reports into (default: ./{DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--debug", action="store_true",
                        help="log commands to stderr and include troubleshooting detail in the reports")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


EXIT_CODES = {"OK": EXIT_OK, "WARNING": EXIT_WARNING, "CRITICAL": EXIT_CRITICAL, "UNKNOWN": EXIT_UNKNOWN}


def exit_code_for(status: str) -> int:
    return EXIT_CODES[status]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    # Progress goes to stderr so stdout stays clean for the summary.
    print("Endpoint triage: running read-only diagnostics...", file=sys.stderr)
    try:
        report = run_scan(progress=lambda message: print(f"  - {message}", file=sys.stderr))
        text_path, json_path = write_reports(report, Path(args.output), debug=args.debug)
    except KeyboardInterrupt:
        print("\nInterrupted; no report was written.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OSError as exc:
        print(f"error: could not write report to {args.output}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        logging.getLogger(__name__).debug("Unexpected error", exc_info=True)
        print(f"error: unexpected failure: {exc}" + ("" if args.debug else " (re-run with --debug for details)"),
              file=sys.stderr)
        return EXIT_ERROR

    print()
    print(f"Overall status: {overall_status(report)}")
    if report.findings:
        print("Findings:")
        for finding in report.findings:
            print(f"  [{finding.severity.value}] {finding.title}")
    else:
        print("No issues detected by the automated checks.")
    print()
    print(f"Text report: {text_path}")
    print(f"JSON report: {json_path}")
    return exit_code_for(report.overall_status())
