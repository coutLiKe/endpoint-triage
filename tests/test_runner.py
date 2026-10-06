import subprocess
import unittest
from unittest import mock

from endpoint_triage.models import Status
from endpoint_triage.runner import (
    NOT_FOUND,
    OS_ERROR,
    TIMEOUT,
    CommandResult,
    command_failure,
    run_command,
    run_powershell,
)
from tests.helpers import FakeRunner, ok


class RunCommandTests(unittest.TestCase):
    def test_refuses_commands_not_on_allow_list(self):
        with self.assertRaises(ValueError):
            run_command(["rm", "-rf", "/tmp/x"])
        with self.assertRaises(ValueError):
            run_command([])

    @mock.patch("endpoint_triage.runner.subprocess.run")
    def test_success_captures_stdout_and_exit_code(self, fake_run):
        fake_run.return_value = subprocess.CompletedProcess(["df"], 0, "output\n", "")
        result = run_command(["df", "-P"])
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "output\n")
        self.assertEqual(result.returncode, 0)

    @mock.patch("endpoint_triage.runner.subprocess.run")
    def test_never_uses_a_shell(self, fake_run):
        fake_run.return_value = subprocess.CompletedProcess(["df"], 0, "", "")
        run_command(["df"])
        kwargs = fake_run.call_args.kwargs
        self.assertNotIn("shell", kwargs)
        self.assertIn("timeout", kwargs)

    @mock.patch("endpoint_triage.runner.os.name", "posix")
    @mock.patch("endpoint_triage.runner.subprocess.run")
    def test_forces_c_locale_on_posix(self, fake_run):
        fake_run.return_value = subprocess.CompletedProcess(["df"], 0, "", "")
        run_command(["df"])
        self.assertEqual(fake_run.call_args.kwargs["env"]["LC_ALL"], "C")

    @mock.patch("endpoint_triage.runner.subprocess.run")
    def test_non_zero_exit_is_not_ok(self, fake_run):
        fake_run.return_value = subprocess.CompletedProcess(["df"], 1, "", "df: bad\n")
        result = run_command(["df"])
        self.assertFalse(result.ok)
        self.assertIsNone(result.error)
        self.assertEqual(result.describe_failure(), "df exited with code 1: df: bad")

    @mock.patch("endpoint_triage.runner.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_command(self, _):
        result = run_command(["ip", "-j", "addr"])
        self.assertEqual(result.error, NOT_FOUND)
        self.assertEqual(result.describe_failure(), "command not found: ip")

    @mock.patch("endpoint_triage.runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["ping"], 5))
    def test_timeout(self, _):
        result = run_command(["ping", "1.1.1.1"], timeout=5)
        self.assertEqual(result.error, TIMEOUT)
        self.assertFalse(result.ok)

    @mock.patch("endpoint_triage.runner.subprocess.run", side_effect=PermissionError("denied"))
    def test_os_error(self, _):
        result = run_command(["df"])
        self.assertEqual(result.error, OS_ERROR)
        self.assertIn("denied", result.describe_failure())


class CommandFailureTests(unittest.TestCase):
    def test_missing_command_is_unavailable(self):
        check = command_failure("x.y", "X", CommandResult(["apt"], None, error=NOT_FOUND))
        self.assertEqual(check.status, Status.UNAVAILABLE)

    def test_non_zero_exit_is_failed(self):
        check = command_failure("x.y", "X", CommandResult(["apt"], 2, "", "E: lock"))
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("E: lock", check.error)
        self.assertIn("E: lock", check.debug)


class RunPowershellTests(unittest.TestCase):
    def test_runs_non_interactive_powershell(self):
        runner = FakeRunner({"Get-Thing": ok("{}")})
        run_powershell("Get-Thing", run=runner)
        args = runner.calls[0]
        self.assertEqual(args[0], "powershell")
        self.assertIn("-NoProfile", args)
        self.assertIn("-NonInteractive", args)
        self.assertTrue(args[-1].endswith("Get-Thing"))


if __name__ == "__main__":
    unittest.main()
