"""Command-line interface: parse options, run the scan, write reports, exit."""

from __future__ import annotations

import argparse
import ipaddress
import logging
import re
import sys
import tempfile
from pathlib import Path

from endpoint_triage import __version__
from endpoint_triage.reporters import overall_status, write_reports
from endpoint_triage.collectors.connectivity import DNS_TEST_HOSTNAME, PUBLIC_IP_TARGET
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

# RFC 1123 hostname: dot-separated labels of letters, digits and hyphens,
# no label starting or ending with a hyphen.
HOSTNAME_PATTERN = re.compile(r"^(?=.{1,253}\.?$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$")


def ipv4_address(value: str) -> str:
    """Validate --ping-target. Strict validation also stops values such as
    "-f" from being passed to ping as an option (argument injection)."""
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid IPv4 address: {value!r}") from None


def hostname(value: str) -> str:
    if not HOSTNAME_PATTERN.match(value):
        raise argparse.ArgumentTypeError(f"not a valid hostname: {value!r}")
    return value


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
    parser.add_argument("-o", "--output", metavar="DIR",
                        help=f"directory to write reports into (default: ./{DEFAULT_OUTPUT_DIR}, "
                             "or the system temp folder if the current folder is not writable)")
    parser.add_argument("--ping-target", type=ipv4_address, default=PUBLIC_IP_TARGET, metavar="IP",
                        help=f"IPv4 address for the internet ping test (default: {PUBLIC_IP_TARGET})")
    parser.add_argument("--dns-name", type=hostname, default=DNS_TEST_HOSTNAME, metavar="HOST",
                        help=f"hostname for the DNS resolution test (default: {DNS_TEST_HOSTNAME})")
    parser.add_argument("--skip-updates", action="store_true",
                        help="skip the pending-update check (it can take minutes and contacts update servers)")
    parser.add_argument("--debug", action="store_true",
                        help="log commands to stderr and include troubleshooting detail in the reports")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


EXIT_CODES = {"OK": EXIT_OK, "WARNING": EXIT_WARNING, "CRITICAL": EXIT_CRITICAL, "UNKNOWN": EXIT_UNKNOWN}
EXIT_MEANINGS = {
    "OK": "no WARNING or CRITICAL findings",
    "WARNING": "at least one WARNING finding",
    "CRITICAL": "at least one CRITICAL finding",
    "UNKNOWN": "core diagnostics could not be collected",
}


def exit_code_for(status: str) -> int:
    return EXIT_CODES[status]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    # Progress goes to stderr so stdout stays clean for the summary.
    print("Endpoint triage: running read-only diagnostics...", file=sys.stderr)
    try:
        report = run_scan(progress=lambda message: print(f"  - {message}", file=sys.stderr),
                          ping_target=args.ping_target, dns_name=args.dns_name, skip_updates=args.skip_updates)
        text_path, json_path = _write(report, args)
    except KeyboardInterrupt:
        print("\nInterrupted; no report was written.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OSError as exc:
        print(f"error: could not write report: {exc}", file=sys.stderr)
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
    status = report.overall_status()
    print(f"Exit code {exit_code_for(status)}: {status} ({EXIT_MEANINGS[status]})")
    return exit_code_for(status)


def _write(report, args) -> tuple[Path, Path]:
    """Write to --output, or ./triage-reports with a temp-folder fallback.

    The fallback covers running from a folder the user cannot write to
    (for example C:\\Windows\\System32 in an elevated prompt). An explicit
    --output is never silently replaced.
    """
    if args.output:
        return write_reports(report, Path(args.output), debug=args.debug)
    try:
        return write_reports(report, Path(DEFAULT_OUTPUT_DIR), debug=args.debug)
    except OSError as exc:
        # mkdtemp creates a new, uniquely named directory only this user can
        # access, so another user on a shared machine cannot pre-create it.
        fallback = Path(tempfile.mkdtemp(prefix=f"{DEFAULT_OUTPUT_DIR}-"))
        print(f"note: cannot write to ./{DEFAULT_OUTPUT_DIR} ({exc}); using {fallback}", file=sys.stderr)
        return write_reports(report, fallback, debug=args.debug)
