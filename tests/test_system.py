import unittest
from datetime import datetime, timezone

from endpoint_triage.collectors import system
from endpoint_triage.models import Status
from tests.helpers import FakeRunner, fail, fake_files, not_found, ok

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

SW_VERS = "ProductName:\t\tmacOS\nProductVersion:\t\t26.6.2\nBuildVersion:\t\t25G83\n"
BOOTTIME = "{ sec = 1790034971, usec = 313222 } Mon Sep 21 19:56:11 2026\n"
OS_RELEASE = 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
WINDOWS_OS = ('{"Caption":"Microsoft Windows 11 Pro","Version":"10.0.26100",'
              '"BuildNumber":"26100","LastBootUpTime":"2026-10-01T08:30:00"}')


def by_id(checks):
    return {c.id: c for c in checks}


class MacOSSystemTests(unittest.TestCase):
    def test_success(self):
        runner = FakeRunner({"sw_vers": ok(SW_VERS), "kern.boottime": ok(BOOTTIME)})
        checks = by_id(system.collect("Darwin", run=runner, now=NOW))
        os_info = checks["system.os"]
        self.assertEqual(os_info.status, Status.OK)
        self.assertEqual(os_info.data["name"], "macOS 26.6.2")
        self.assertEqual(os_info.data["build"], "25G83")
        self.assertIn("hostname", os_info.data)
        self.assertIn("architecture", os_info.data)

        uptime = checks["system.uptime"]
        self.assertEqual(uptime.status, Status.OK)
        self.assertEqual(uptime.data["last_boot"], "2026-09-21T23:56:11+00:00")
        self.assertEqual(uptime.data["uptime_seconds"], int((NOW - datetime(2026, 9, 21, 23, 56, 11, tzinfo=timezone.utc)).total_seconds()))

    def test_malformed_output(self):
        runner = FakeRunner({"sw_vers": ok("garbage"), "kern.boottime": ok("nonsense")})
        checks = by_id(system.collect("Darwin", run=runner, now=NOW))
        self.assertEqual(checks["system.os"].status, Status.FAILED)
        self.assertEqual(checks["system.uptime"].status, Status.FAILED)

    def test_command_failure_and_missing(self):
        runner = FakeRunner({"sw_vers": fail()})
        checks = by_id(system.collect("Darwin", run=runner, now=NOW))
        self.assertEqual(checks["system.os"].status, Status.FAILED)
        self.assertEqual(checks["system.uptime"].status, Status.UNAVAILABLE)


class LinuxSystemTests(unittest.TestCase):
    def test_success(self):
        files = fake_files({"/etc/os-release": OS_RELEASE, "/proc/uptime": "93784.52 180000.00\n"})
        checks = by_id(system.collect("Linux", read_file=files, now=NOW))
        self.assertEqual(checks["system.os"].data["name"], "Ubuntu 24.04.1 LTS")
        self.assertEqual(checks["system.os"].data["version"], "24.04")
        self.assertEqual(checks["system.uptime"].data["uptime_seconds"], 93784)
        self.assertEqual(checks["system.uptime"].data["last_boot"], "2026-10-04T09:56:55+00:00")

    def test_missing_files(self):
        checks = by_id(system.collect("Linux", read_file=fake_files({}), now=NOW))
        # OS info still succeeds with the kernel version; uptime is unavailable.
        self.assertEqual(checks["system.os"].status, Status.OK)
        self.assertIn("note", checks["system.os"].data)
        self.assertEqual(checks["system.uptime"].status, Status.UNAVAILABLE)

    def test_malformed_uptime(self):
        files = fake_files({"/etc/os-release": OS_RELEASE, "/proc/uptime": "not-a-number"})
        checks = by_id(system.collect("Linux", read_file=files, now=NOW))
        self.assertEqual(checks["system.uptime"].status, Status.FAILED)


class WindowsSystemTests(unittest.TestCase):
    def test_success(self):
        runner = FakeRunner({"Win32_OperatingSystem": ok(WINDOWS_OS)})
        checks = by_id(system.collect("Windows", run=runner, now=NOW))
        self.assertEqual(checks["system.os"].data["name"], "Microsoft Windows 11 Pro")
        self.assertEqual(checks["system.os"].data["build"], "26100")
        self.assertEqual(checks["system.os"].data["kernel"], "10.0.26100")
        self.assertEqual(checks["system.uptime"].data["last_boot"], "2026-10-01T08:30:00+00:00")
        self.assertEqual(len(runner.calls), 1, "one PowerShell call should serve both checks")

    def test_powershell_missing(self):
        checks = by_id(system.collect("Windows", run=FakeRunner({"powershell": not_found()}), now=NOW))
        self.assertEqual(checks["system.os"].status, Status.UNAVAILABLE)
        self.assertEqual(checks["system.uptime"].status, Status.UNAVAILABLE)

    def test_malformed_json(self):
        runner = FakeRunner({"Win32_OperatingSystem": ok("<not json>")})
        checks = by_id(system.collect("Windows", run=runner, now=NOW))
        self.assertEqual(checks["system.os"].status, Status.FAILED)
        self.assertEqual(checks["system.uptime"].status, Status.FAILED)

    def test_bad_boot_time(self):
        runner = FakeRunner({"Win32_OperatingSystem": ok(WINDOWS_OS.replace("2026-10-01T08:30:00", "yesterday"))})
        checks = by_id(system.collect("Windows", run=runner, now=NOW))
        self.assertEqual(checks["system.os"].status, Status.OK)
        self.assertEqual(checks["system.uptime"].status, Status.FAILED)


class UnsupportedOSTests(unittest.TestCase):
    def test_unknown_os(self):
        checks = by_id(system.collect("FreeBSD", run=FakeRunner(), now=NOW))
        self.assertEqual(checks["system.os"].status, Status.OK)
        self.assertEqual(checks["system.uptime"].status, Status.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
