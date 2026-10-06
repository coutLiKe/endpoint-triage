import unittest

from endpoint_triage.collectors import updates
from endpoint_triage.models import Status
from endpoint_triage.runner import TIMEOUT, CommandResult
from tests.helpers import FakeRunner, fail, not_found, ok

SOFTWAREUPDATE_PENDING = """Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: macOS Tahoe 26.7-25H10
\tTitle: macOS Tahoe 26.7, Version: 26.7, Size: 3184562KiB, Recommended: YES, Action: restart,
* Label: Safari26.1-26.1
\tTitle: Safari, Version: 26.1, Size: 145020KiB, Recommended: YES,
"""
APT_OUTPUT = """Listing...
openssl/noble-updates,noble-security 3.0.13-0ubuntu3.4 amd64 [upgradable from: 3.0.13-0ubuntu3.1]
tzdata/noble-updates 2024b-0ubuntu0.24.04 all [upgradable from: 2024a-3ubuntu1.1]
"""
DNF_OUTPUT = """
openssl.x86_64                1:3.0.7-28.el9_4          baseos
kernel.x86_64                 5.14.0-427.40.1.el9_4     baseos
Obsoleting Packages
grub2-tools.x86_64            1:2.06-82.el9             baseos
"""


def run_updates(os_name, **responses):
    return updates.collect(os_name, run=FakeRunner(responses))[0]


class MacOSUpdateTests(unittest.TestCase):
    def test_pending_updates(self):
        check = run_updates("Darwin", softwareupdate=ok(SOFTWAREUPDATE_PENDING))
        self.assertEqual(check.status, Status.OK)
        self.assertEqual(check.data["pending_count"], 2)
        self.assertEqual(check.data["updates"], ["macOS Tahoe 26.7", "Safari"])
        self.assertTrue(check.data["restart_required"])

    def test_no_updates_message_on_stderr(self):
        check = run_updates("Darwin", softwareupdate=ok("Software Update Tool\n\nFinding available software\n",
                                                         stderr="No new software available.\n"))
        self.assertEqual(check.data["pending_count"], 0)
        self.assertFalse(check.data["restart_required"])

    def test_unrecognized_output_is_failed(self):
        check = run_updates("Darwin", softwareupdate=ok("Software Update Tool\n\nFinding available software\n"))
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("could not be determined", check.error)

    def test_timeout(self):
        check = run_updates("Darwin", softwareupdate=CommandResult([], None, error=TIMEOUT))
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("timed out", check.error)


class WindowsUpdateTests(unittest.TestCase):
    def test_pending_updates(self):
        check = run_updates("Windows", **{"Microsoft.Update.Session": ok(
            '[{"Title":"2026-10 Cumulative Update for Windows 11 (KB5050001)","RebootRequired":true},'
            '{"Title":"Security Intelligence Update for Microsoft Defender","RebootRequired":false}]')})
        self.assertEqual(check.data["pending_count"], 2)
        self.assertTrue(check.data["restart_required"])
        self.assertEqual(check.data["source"], "Windows Update Agent")

    def test_no_updates(self):
        check = run_updates("Windows", **{"Microsoft.Update.Session": ok("[]")})
        self.assertEqual(check.data["pending_count"], 0)

    def test_com_error(self):
        check = run_updates("Windows", **{"Microsoft.Update.Session": fail(
            stderr="Exception from HRESULT: 0x8024402C")})
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("0x8024402C", check.error)

    def test_malformed_json(self):
        self.assertEqual(run_updates("Windows", **{"Microsoft.Update.Session": ok("[{]")}).status, Status.FAILED)


class LinuxUpdateTests(unittest.TestCase):
    def test_apt(self):
        check = run_updates("Linux", apt=ok(APT_OUTPUT, stderr="WARNING: apt does not have a stable CLI interface."))
        self.assertEqual(check.data["source"], "apt")
        self.assertEqual(check.data["updates"], ["openssl", "tzdata"])
        self.assertIn("stale", check.data["note"])

    def test_dnf_with_updates(self):
        check = run_updates("Linux", dnf=ok(DNF_OUTPUT, returncode=100))
        self.assertEqual(check.status, Status.OK)
        self.assertEqual(check.data["updates"], ["openssl", "kernel"])  # obsoletes ignored

    def test_dnf_no_updates(self):
        check = run_updates("Linux", dnf=ok(""))
        self.assertEqual(check.data["pending_count"], 0)

    def test_dnf_error_without_cache(self):
        check = run_updates("Linux", dnf=fail(returncode=1, stderr="Error: Cache-only enabled but no cache for 'baseos'"))
        self.assertEqual(check.status, Status.FAILED)
        self.assertIn("Cache-only", check.error)

    def test_apt_failure(self):
        check = run_updates("Linux", apt=fail(returncode=100, stderr="E: Could not open lock file"))
        self.assertEqual(check.status, Status.FAILED)

    def test_no_supported_package_manager(self):
        check = run_updates("Linux", apt=not_found(), dnf=not_found())
        self.assertEqual(check.status, Status.UNAVAILABLE)
        self.assertIn("apt or dnf", check.error)


class UnsupportedTests(unittest.TestCase):
    def test_unknown_os(self):
        self.assertEqual(run_updates("SunOS").status, Status.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
