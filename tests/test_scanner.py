"""End-to-end scan tests with every command and file faked."""

import functools
import socket
import unittest
from unittest import mock

from endpoint_triage import scanner
from endpoint_triage.models import Severity, Status
from endpoint_triage.runner import ALLOWED_COMMANDS
from tests.helpers import FakeRunner, fake_files, fixture, ok


# Real proxy environment variables on the developer's machine must not leak into tests.
run_scan = functools.partial(scanner.run_scan, environ={})


def resolver(host, port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]


def failing_resolver(host, port):
    raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")


LINUX_FILES = {
    "/etc/os-release": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\n',
    "/proc/uptime": "3600.00 7000.00\n",
    "/proc/meminfo": fixture("linux_meminfo.txt"),
    "/etc/resolv.conf": "nameserver 10.0.0.1\n",
}
PING_OK = "rtt min/avg/max/mdev = 0.398/0.405/0.412/0.007 ms\n"


def linux_runner():
    return FakeRunner({
        "df": ok(fixture("linux_df.txt")),
        "ip -j addr": ok(fixture("linux_ip_addr.json")),
        "ip -j route": ok('[{"dst":"default","gateway":"10.0.0.1","dev":"enp3s0","metric":100}]'),
        "ping": ok(PING_OK),
        "apt": ok("Listing...\n"),
    })


class ScannerTests(unittest.TestCase):
    def test_full_linux_scan(self):
        runner = linux_runner()
        report = run_scan("Linux", run=runner, read_file=fake_files(LINUX_FILES), resolver=resolver)
        statuses = {c.id: c.status for c in report.all_checks()}
        self.assertEqual(len(statuses), 12)
        self.assertTrue(all(s == Status.OK for s in statuses.values()), statuses)
        # /mnt/data in the df fixture is 96% full.
        self.assertEqual(report.findings[0].title, "Critically low disk space on /mnt/data")
        # Every command run is on the read-only allow-list.
        self.assertTrue(all(call[0] in ALLOWED_COMMANDS for call in runner.calls))

    def test_dns_failure_flows_through_to_findings(self):
        report = run_scan("Linux", run=linux_runner(), read_file=fake_files(LINUX_FILES),
                                  resolver=failing_resolver)
        self.assertIn("DNS resolution failed", [f.title for f in report.findings])

    def test_everything_missing_still_produces_a_report(self):
        report = run_scan("Linux", run=FakeRunner(), read_file=fake_files({}), resolver=failing_resolver)
        self.assertEqual(len(report.all_checks()), 12)
        self.assertGreater(len(report.findings), 0)

    def test_scan_that_collected_nothing_is_unknown_not_ok(self):
        # Only DNS works; every command is missing. This must never report "OK".
        report = run_scan("Linux", run=FakeRunner(), read_file=fake_files({}), resolver=resolver)
        self.assertNotIn(report.highest_severity(), (Severity.WARNING, Severity.CRITICAL))
        self.assertEqual(report.overall_status(), "UNKNOWN")
        self.assertIn("resources.disks", report.incomplete_core_checks())

    def test_complete_healthy_scan_is_ok(self):
        files = dict(LINUX_FILES)
        runner = linux_runner()
        runner.responses["df"] = ok("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                                    "/dev/sda1 100000000 10000000 90000000 10% /\n")
        report = run_scan("Linux", run=runner, read_file=fake_files(files), resolver=resolver)
        self.assertEqual(report.overall_status(), "OK")

    def test_collector_crash_is_contained(self):
        with mock.patch("endpoint_triage.collectors.resources.collect", side_effect=RuntimeError("bug")):
            report = run_scan("Linux", run=linux_runner(), read_file=fake_files(LINUX_FILES),
                                      resolver=resolver)
        crashed = report.get("resources.collector")
        self.assertEqual(crashed.status, Status.FAILED)
        self.assertIn("RuntimeError: bug", crashed.error)
        self.assertIn("Traceback", crashed.debug)
        self.assertEqual(report.get("network.gateway").status, Status.OK)  # other sections unaffected

    def test_skip_updates(self):
        runner = linux_runner()
        report = run_scan("Linux", run=runner, read_file=fake_files(LINUX_FILES), resolver=resolver,
                                  skip_updates=True)
        self.assertEqual(report.get("updates.os").status, Status.SKIPPED)
        self.assertFalse(any(call[0] in ("apt", "dnf") for call in runner.calls))
        self.assertTrue(report.options["skip_updates"])
        self.assertNotIn("updates", " ".join(f.title.lower() for f in report.findings))

    def test_progress_messages(self):
        messages = []
        run_scan("Linux", run=linux_runner(), read_file=fake_files(LINUX_FILES), resolver=resolver,
                         progress=messages.append)
        self.assertEqual(len(messages), 5)


if __name__ == "__main__":
    unittest.main()
