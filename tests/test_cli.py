import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from endpoint_triage import __version__, cli
from tests.sample_report import build_sample_report


def run_cli(*argv, report=None, scan_error=None):
    """Run cli.main with the scan replaced by a canned report."""
    stdout, stderr = io.StringIO(), io.StringIO()
    scan = mock.Mock(return_value=report or build_sample_report(), side_effect=scan_error)
    with mock.patch("endpoint_triage.cli.run_scan", scan), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:  # argparse --help/--version/errors
            code = exc.code
    return code, stdout.getvalue(), stderr.getvalue()


class ExitCodeTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(cli.exit_code_for("OK"), 0)
        self.assertEqual(cli.exit_code_for("WARNING"), 1)
        self.assertEqual(cli.exit_code_for("CRITICAL"), 2)
        self.assertEqual(cli.exit_code_for("UNKNOWN"), 3)


class MainTests(unittest.TestCase):
    def test_scan_writes_reports_and_returns_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli("--output", tmp)
            self.assertEqual(code, cli.EXIT_CRITICAL)
            self.assertEqual(len(list(Path(tmp).glob("triage-*.txt"))), 1)
            self.assertEqual(len(list(Path(tmp).glob("triage-*.json"))), 1)
        self.assertIn("Overall status: CRITICAL", out)
        self.assertIn("[CRITICAL] Critically low disk space on C:", out)
        self.assertIn("Text report:", out)
        self.assertIn("read-only", err)  # progress goes to stderr, not stdout

    def test_healthy_scan_returns_zero(self):
        report = build_sample_report()
        report.findings = []
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli("-o", tmp, report=report)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No issues detected", out)

    def test_unwritable_output_returns_tool_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("not a directory")
            code, _, err = run_cli("--output", str(blocker / "reports"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("could not write report", err)

    def test_unexpected_error_hides_traceback_without_debug(self):
        code, _, err = run_cli("-o", "unused", scan_error=RuntimeError("kaboom"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("kaboom", err)
        self.assertIn("--debug", err)
        self.assertNotIn("Traceback", err)

    def test_keyboard_interrupt(self):
        code, _, err = run_cli("-o", "unused", scan_error=KeyboardInterrupt())
        self.assertEqual(code, cli.EXIT_INTERRUPTED)

    def test_version(self):
        code, out, _ = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_help_documents_options(self):
        code, out, _ = run_cli("--help")
        self.assertEqual(code, 0)
        for option in ("--output", "--debug", "--version", "Exit codes"):
            self.assertIn(option, out)

    def test_target_flags_are_passed_to_the_scan(self):
        scan = mock.Mock(return_value=build_sample_report())
        with tempfile.TemporaryDirectory() as tmp, mock.patch("endpoint_triage.cli.run_scan", scan), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["-o", tmp, "--ping-target", "10.0.0.53", "--dns-name", "intranet.corp.example",
                      "--skip-updates"])
        kwargs = scan.call_args.kwargs
        self.assertEqual(kwargs["ping_target"], "10.0.0.53")
        self.assertEqual(kwargs["dns_name"], "intranet.corp.example")
        self.assertTrue(kwargs["skip_updates"])

    def test_invalid_targets_are_rejected(self):
        # The "--flag=value" form is how a value starting with "-" reaches the validator.
        for argv in (["--ping-target", "999.1.1.1"], ["--ping-target=-f"], ["--ping-target", "example.com"],
                     ["--dns-name", "bad_host!"], ["--dns-name=-oProxyCommand=x"], ["--dns-name", "a" * 300]):
            code, _, err = run_cli(*argv)
            self.assertEqual(code, cli.EXIT_USAGE, argv)
            self.assertIn("not a valid", err)

    def test_bad_argument_uses_usage_exit_code(self):
        code, _, err = run_cli("--frobnicate")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("unrecognized arguments", err)


if __name__ == "__main__":
    unittest.main()
